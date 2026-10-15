// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../../contracts/LiabilityRegistry.sol";

contract LiabilityRegistryTest is Test {
    LiabilityRegistry reg;
    address regulator = makeAddr("regulator");
    address treasury = makeAddr("treasury");
    address settlement = makeAddr("settlement");
    address op = makeAddr("operator");
    address op2 = makeAddr("operator2");
    // the H3 experiment's economics, in wei-scaled units: s = 15, S = 100, V_max = 30, C_min = 1, p = 1
    uint128 constant U = 1e15;
    uint128 constant S_SLASH = 15 * U;
    uint128 constant BOND = 14 * U;   // = B* exactly

    function setUp() public {
        reg = new LiabilityRegistry(LiabilityRegistry.Params({
            regulator: regulator, treasury: treasury, baseSlash: S_SLASH, minStake: 100 * U,
            registrationBond: BOND, withdrawDelay: 2 days,
            maxViolationPayoff: 30 * U, minCompliantPayoff: 1 * U, detectionBps: 10000
        }));
        vm.prank(regulator);
        reg.setSlasher(settlement);
        vm.prank(regulator);
        reg.issueCredential(op);
        vm.deal(op, 100 ether);
        vm.deal(op2, 100 ether);
    }

    function _register(address o, bytes32 id) internal {
        vm.prank(o);
        reg.registerAgent{value: BOND}(id);
    }

    function _slash(bytes32 id) internal returns (uint256) {
        vm.prank(settlement);
        return reg.slash(id, keccak256("proof"));
    }

    function test_breakEvenBond_matchesH3Derivation() public view {
        assertEq(reg.breakEvenBond(30, 10000, 15, 1), 14);       // B* = 30 - 15 - 1
        assertEq(reg.breakEvenBond(30, 5000, 15, 1), 22);        // p = 0.5: 30 - 7.5 - 1 = 21.5, rounded up (p*s floored)
    }

    function test_constructor_rejectsBondBelowBreakEven() public {
        vm.expectRevert(bytes("bond below break-even B*"));
        new LiabilityRegistry(LiabilityRegistry.Params({
            regulator: regulator, treasury: treasury, baseSlash: S_SLASH, minStake: 100 * U,
            registrationBond: 13 * U, withdrawDelay: 2 days,
            maxViolationPayoff: 30 * U, minCompliantPayoff: 1 * U, detectionBps: 10000
        }));
    }

    function test_registration_needsCredentialAndBond_andIsNonTransferable() public {
        vm.prank(op2);
        vm.expectRevert(LiabilityRegistry.NotCredentialed.selector);
        reg.registerAgent{value: BOND}("a");
        vm.prank(op);
        vm.expectRevert(bytes("registration bond"));
        reg.registerAgent{value: BOND - 1}("a");
        _register(op, "a");
        vm.prank(op);
        vm.expectRevert(bytes("agent id taken"));
        reg.registerAgent{value: BOND}("a");
        assertEq(reg.operatorOf("a"), op);
        assertTrue(reg.locked("a"));
        vm.expectRevert(LiabilityRegistry.NonTransferable.selector);
        reg.transferAgent("a", op2);
        assertEq(reg.treasuryBalance(), BOND);
    }

    function test_stakeMaintenance_blocksUncoveredAgents() public {
        _register(op, "a");
        assertFalse(reg.isCovered("a"));                 // no stake yet
        vm.prank(op);
        reg.topUp{value: 100 * U}();
        assertTrue(reg.isCovered("a"));
        // after 6 violations the next slash is 15 * 7 = 105 > 100: blocked until topped up
        vm.prank(op);
        reg.topUp{value: 1000 * U}();
        for (uint256 i; i < 6; i++) _slash("a");
        assertEq(reg.nextSlash(op), 105 * U);
        (,, uint128 stake,,) = reg.operators(op);
        vm.prank(op);
        reg.requestWithdraw(stake - 104 * U);             // leave 104 < 105
        assertFalse(reg.isCovered("a"));
        vm.prank(op);
        reg.topUp{value: 1 * U}();
        assertTrue(reg.isCovered("a"));
    }

    function test_escalatingSlash_andHistoryFollowsOperatorAcrossNewIds() public {
        _register(op, "a");
        vm.prank(op);
        reg.topUp{value: 1000 * U}();
        assertEq(_slash("a"), 15 * U);
        assertEq(_slash("a"), 30 * U);
        // Sybil reset: same operator, fresh agent id -> history is NOT reset
        _register(op, "a-reset");
        assertEq(_slash("a-reset"), 45 * U);
        (, uint32 k,,,) = reg.operators(op);
        assertEq(k, 3);
        // a different operator starts clean
        vm.prank(regulator);
        reg.issueCredential(op2);
        _register(op2, "b");
        vm.prank(op2);
        reg.topUp{value: 100 * U}();
        assertEq(_slash("b"), 15 * U);
        assertEq(reg.treasuryBalance(), 3 * BOND + (15 + 30 + 45 + 15) * U);
    }

    function test_onlySettlementCanSlash_slasherSetOnce() public {
        _register(op, "a");
        vm.expectRevert(LiabilityRegistry.NotSlasher.selector);
        reg.slash("a", 0);
        vm.prank(regulator);
        vm.expectRevert(bytes("slasher already set"));
        reg.setSlasher(address(1));
        vm.prank(op);
        vm.expectRevert(LiabilityRegistry.NotRegulator.selector);
        reg.issueCredential(op2);
    }

    function test_pendingWithdrawal_staysSlashable_andWaitsForDelay() public {
        _register(op, "a");
        vm.prank(op);
        reg.topUp{value: 100 * U}();
        vm.prank(op);
        reg.requestWithdraw(100 * U);                     // tries to run before being slashed
        assertFalse(reg.isCovered("a"));
        assertEq(_slash("a"), 15 * U);                    // taken from the pending withdrawal
        vm.prank(op);
        vm.expectRevert(bytes("not yet"));
        reg.withdraw();
        vm.warp(block.timestamp + 2 days);
        uint256 before = op.balance;
        vm.prank(op);
        reg.withdraw();
        assertEq(op.balance - before, 85 * U);
    }

    function test_revokedCredential_blocksAgents() public {
        _register(op, "a");
        vm.prank(op);
        reg.topUp{value: 100 * U}();
        assertTrue(reg.isCovered("a"));
        vm.prank(regulator);
        reg.revokeCredential(op);
        assertFalse(reg.isCovered("a"));
    }

    function test_treasuryPayout() public {
        _register(op, "a");
        vm.prank(op);
        reg.topUp{value: 100 * U}();
        _slash("a");
        reg.payTreasury();
        assertEq(treasury.balance, BOND + 15 * U);
        assertEq(reg.treasuryBalance(), 0);
    }
}
