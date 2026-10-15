// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../contracts/SettlementGate.sol";

contract SettlementGateTest is Test {
    LiabilityRegistry reg;
    SettlementGate gate;
    address regulator = makeAddr("regulator");
    address cb = makeAddr("centralBank");
    address op = makeAddr("operator");
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");
    uint256[4] vkeys = [uint256(0xA11), 0xB22, 0xC33, 0xD44];
    address[] vals;
    uint128 constant U = 1e15;
    bytes32 constant AGENT = "agent-1";

    function setUp() public {
        reg = new LiabilityRegistry(LiabilityRegistry.Params({
            regulator: regulator, treasury: regulator, baseSlash: 15 * U, minStake: 100 * U,
            registrationBond: 14 * U, withdrawDelay: 2 days,
            maxViolationPayoff: 30 * U, minCompliantPayoff: 1 * U, detectionBps: 10000
        }));
        for (uint256 i; i < 4; i++) vals.push(vm.addr(vkeys[i]));
        gate = new SettlementGate(reg, cb, vals, 3);
        vm.startPrank(regulator);
        reg.setSlasher(address(gate));
        reg.issueCredential(op);
        vm.stopPrank();
        vm.deal(op, 10 ether);
        vm.startPrank(op);
        reg.registerAgent{value: 14 * U}(AGENT);
        reg.topUp{value: 100 * U}();
        vm.stopPrank();
        vm.prank(cb);
        gate.mint(alice, 10_000);
        vm.prank(alice);
        gate.setMandate(AGENT, true);
    }

    function _req(uint256 amount, bool exec, bool viol, uint64 nonce) internal view returns (SettlementGate.Request memory) {
        return SettlementGate.Request(AGENT, alice, bob, amount, keccak256("c"), keccak256("proof"), exec, viol, nonce);
    }

    function _sigs(SettlementGate.Request memory r, uint256 n) internal view returns (bytes[] memory s) {
        bytes32 id = gate.requestId(r);
        bytes32 h = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", id));
        s = new bytes[](n);
        for (uint256 i; i < n; i++) {
            (uint8 v, bytes32 rr, bytes32 ss) = vm.sign(vkeys[i], h);
            s[i] = abi.encodePacked(rr, ss, v);
        }
    }

    function test_compliantDecision_executesTransfer() public {
        SettlementGate.Request memory r = _req(7558, true, false, 1);
        SettlementGate.Outcome o = gate.settle(r, _sigs(r, 3));
        assertEq(uint256(o), uint256(SettlementGate.Outcome.Executed));
        assertEq(gate.balanceOf(bob), 7558);
        assertEq(gate.balanceOf(alice), 10_000 - 7558);
    }

    function test_provenViolation_blocksAndSlashes() public {
        SettlementGate.Request memory r = _req(7558, true, true, 2);
        SettlementGate.Outcome o = gate.settle(r, _sigs(r, 3));
        assertEq(uint256(o), uint256(SettlementGate.Outcome.Blocked));
        assertEq(gate.balanceOf(bob), 0);
        (, uint32 k, uint128 stake,,) = reg.operators(op);
        assertEq(k, 1);
        assertEq(stake, 85 * U);
        // stake maintenance: 85 < max(100, 30) -> the agent can no longer act until topped up
        SettlementGate.Request memory r2 = _req(10, true, false, 3);
        assertEq(uint256(gate.settle(r2, _sigs(r2, 3))), uint256(SettlementGate.Outcome.NotCovered));
        vm.prank(op);
        reg.topUp{value: 15 * U}();
        SettlementGate.Request memory r3 = _req(10, true, false, 4);
        assertEq(uint256(gate.settle(r3, _sigs(r3, 3))), uint256(SettlementGate.Outcome.Executed));
    }

    function test_agentRefusal_movesNothing() public {
        SettlementGate.Request memory r = _req(500, false, false, 5);
        assertEq(uint256(gate.settle(r, _sigs(r, 3))), uint256(SettlementGate.Outcome.AgentRefused));
        assertEq(gate.balanceOf(bob), 0);
    }

    function test_quorum_replay_mandate() public {
        SettlementGate.Request memory r = _req(100, true, false, 6);
        bytes[] memory s = _sigs(r, 2);
        vm.expectRevert(bytes("validator quorum not reached"));
        gate.settle(r, s);
        // duplicated signatures do not count twice
        bytes[] memory dup = new bytes[](3);
        dup[0] = s[0]; dup[1] = s[1]; dup[2] = s[1];
        vm.expectRevert(bytes("validator quorum not reached"));
        gate.settle(r, dup);
        // a non-validator signature does not count
        bytes32 h = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", gate.requestId(r)));
        (uint8 v, bytes32 rr, bytes32 ss) = vm.sign(0xBAD, h);
        dup[2] = abi.encodePacked(rr, ss, v);
        vm.expectRevert(bytes("validator quorum not reached"));
        gate.settle(r, dup);
        // signatures are bound to the exact request (amount changed -> invalid)
        bytes[] memory good = _sigs(r, 3);
        SettlementGate.Request memory tampered = _req(9999, true, false, 6);
        vm.expectRevert(bytes("validator quorum not reached"));
        gate.settle(tampered, good);
        gate.settle(r, good);
        vm.expectRevert(bytes("already settled"));
        gate.settle(r, good);
        // no mandate from the wallet -> rejected
        vm.prank(alice);
        gate.setMandate(AGENT, false);
        SettlementGate.Request memory r2 = _req(1, true, false, 7);
        bytes[] memory s2 = _sigs(r2, 3);
        vm.expectRevert(bytes("no mandate"));
        gate.settle(r2, s2);
    }

    function test_constructorRejectsNonBftThreshold() public {
        vm.expectRevert(bytes("threshold"));
        new SettlementGate(reg, cb, vals, 2);
    }
}
