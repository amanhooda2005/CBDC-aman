// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "./GameHarness.sol";

contract OptimisticInferenceV2Test is Test, GameHarness {
    OptimisticInferenceV2 game;
    address A = makeAddr("asserter");
    address C = makeAddr("challenger");
    address C2 = makeAddr("challenger2");

    function setUp() public {
        game = new OptimisticInferenceV2(_model());
        vm.deal(A, 100 ether);
        vm.deal(C, 100 ether);
        vm.deal(C2, 10 ether);
        vm.prank(A);
        game.depositBond{value: 5 ether}();
    }

    // ---------------------------------------------------------------- party behaviour
    function _commit(Trace memory T) internal returns (uint256 id, OptimisticInferenceV2.Claim memory c) {
        vm.prank(A);
        id = game.commit(T.root, T.outputDigest, T.inputHash);
        c = OptimisticInferenceV2.Claim(T.root, T.outputDigest, T.inputHash, A, uint64(block.timestamp));
    }

    function _challenge(uint256 id, OptimisticInferenceV2.Claim memory c, address who) internal returns (bytes32 did) {
        vm.prank(who);
        did = game.challenge{value: CBOND}(id, c);
    }

    function _openFinal(bytes32 did, OptimisticInferenceV2.Claim memory c, Trace memory T) internal {
        vm.prank(A);
        game.openFinal(did, c, T.leaves[N - 1], T.prefixes[N - 1], _proof(T, N - 1), T.leaves[N - 2], T.prefixes[N - 2], _proof(T, N - 2));
    }

    /// Plays the bisection: the asserter opens from trace AT, the challenger answers from trace CT.
    function _bisect(bytes32 did, Trace memory AT, Trace memory CT, address chal) internal returns (uint256 rounds) {
        while (true) {
            (OptimisticInferenceV2.Phase ph,,,) = game.status(did);
            uint256 mid = game.currentMid(did);
            if (ph == OptimisticInferenceV2.Phase.AwaitReveal) {
                vm.prank(A);
                game.reveal(did, mid, AT.leaves[mid], AT.prefixes[mid], _proof(AT, mid));
            } else if (ph == OptimisticInferenceV2.Phase.AwaitResponse) {
                (, bytes32 curPrefix,,,) = game.openings(did);
                bool agree = curPrefix == CT.prefixes[mid];
                vm.prank(chal);
                game.respond(did, agree);
                rounds++;
            } else break;
        }
    }

    function _resolveHonest(bytes32 did, OptimisticInferenceV2.Claim memory c, Trace memory H) internal {
        (,, uint256 k,) = game.status(did);
        (uint256[] memory inst, OptimisticInferenceV2.InputOpening[] memory ops) = _honestProof(H, k);
        game.resolve(did, c, "valid", inst, ops);
    }

    function _phase(bytes32 did) internal view returns (OptimisticInferenceV2.Phase ph) { (ph,,,) = game.status(did); }

    function _disputedStep(bytes32 did) internal view returns (uint256 k) { (,, k,) = game.status(did); }

    // ================================================================ tests: outcomes
    function test_honestClaim_acceptedAfterWindow_unchallengeableAfter() public {
        Trace memory H = _trace(N, N);
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(H);
        assertFalse(game.isAccepted(id, c));
        vm.warp(block.timestamp + WINDOW + 1);
        assertTrue(game.isAccepted(id, c));
        vm.prank(C);
        vm.expectRevert(bytes("window closed"));
        game.challenge{value: CBOND}(id, c);
    }

    function test_dishonestAsserter_middleBlock_loses() public {
        Trace memory H = _trace(N, N);
        Trace memory Bad = _trace(17, N);
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(Bad);
        bytes32 did = _challenge(id, c, C);
        _openFinal(did, c, Bad);
        uint256 rounds = _bisect(did, Bad, H, C);
        assertEq(_disputedStep(did), 17);
        assertLe(rounds, 6);
        _resolveHonest(did, c, H);
        assertEq(uint256(_phase(did)), uint256(OptimisticInferenceV2.Phase.ChallengerWon));
        assertEq(game.credit(C), CBOND + ABOND);
        vm.warp(block.timestamp + WINDOW + 1);
        assertFalse(game.isAccepted(id, c));
    }

    function test_dishonestChallenger_loses_and_claimAccepted() public {
        Trace memory H = _trace(N, N);
        Trace memory Liar = _trace(33, N);   // the challenger's (wrong) view
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(H);
        bytes32 did = _challenge(id, c, C);
        _openFinal(did, c, H);
        _bisect(did, H, Liar, C);
        assertEq(_disputedStep(did), 33);
        _resolveHonest(did, c, H);   // the asserter defends with the honest proof
        assertEq(uint256(_phase(did)), uint256(OptimisticInferenceV2.Phase.AsserterWon));
        assertEq(game.credit(A), CBOND);
        vm.warp(block.timestamp + WINDOW + 1);
        assertTrue(game.isAccepted(id, c));
    }

    /// The attack the v1 contract could not stop: honest leaves up to m, a garbage prefix at m, wrong
    /// values afterwards. Bisection lands on m, where the asserter's leaf is honest, so a check of the
    /// step proof alone would uphold the wrong claim. The consistency check rejects it.
    function test_malformedPrefixChainAttack_rejected() public {
        uint256 m = 40;
        Trace memory H = _trace(N, N);
        Trace memory Att = _trace(m + 1, m);
        assertTrue(Att.outputDigest != H.outputDigest);   // the claimed output is wrong
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(Att);
        bytes32 did = _challenge(id, c, C);
        _openFinal(did, c, Att);
        _bisect(did, Att, H, C);
        assertEq(_disputedStep(did), m);
        // what a proof-only adjudication would have seen: the asserter's leaf at m IS honest
        (,,, bytes32 hiLeaf,) = game.openings(did);
        assertEq(hiLeaf, H.leaves[m]);
        assertEq(uint256(_phase(did)), uint256(OptimisticInferenceV2.Phase.ChallengerWon));
    }

    function test_headLeafMustEqualClaimedOutput() public {
        Trace memory H = _trace(N, N);
        vm.prank(A);
        uint256 id = game.commit(H.root, keccak256("some other output"), H.inputHash);
        OptimisticInferenceV2.Claim memory c = OptimisticInferenceV2.Claim(H.root, keccak256("some other output"), H.inputHash, A, uint64(block.timestamp));
        bytes32 did = _challenge(id, c, C);
        _openFinal(did, c, H);
        assertEq(uint256(_phase(did)), uint256(OptimisticInferenceV2.Phase.ChallengerWon));
    }

    function test_disputeOnEachKindOfStep() public {
        uint256[4] memory steps = [uint256(0), 1, 2, N - 1];   // proj halves (tx input), join (two inputs), head
        for (uint256 s; s < steps.length; s++) {
            Trace memory H = _trace(N, N);
            Trace memory Bad = _trace(steps[s], N);
            (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(Bad);
            bytes32 did = _challenge(id, c, C);
            _openFinal(did, c, Bad);
            _bisect(did, Bad, H, C);
            assertEq(_disputedStep(did), steps[s]);
            _resolveHonest(did, c, H);
            assertEq(uint256(_phase(did)), uint256(OptimisticInferenceV2.Phase.ChallengerWon));
        }
    }

    /// Exhaustive: tampering at ANY step is located exactly and the asserter always loses.
    function test_everyStep_locatedAndRejected() public {
        Trace memory H = _trace(N, N);
        uint256 maxRounds;
        for (uint256 s; s < N; s++) {
            vm.prank(A);
            game.depositBond{value: ABOND}();
            Trace memory Bad = _trace(s, N);
            (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(Bad);
            bytes32 did = _challenge(id, c, C);
            _openFinal(did, c, Bad);
            uint256 r = _bisect(did, Bad, H, C);
            if (r > maxRounds) maxRounds = r;
            assertEq(_disputedStep(did), s);
            _resolveHonest(did, c, H);
            assertEq(uint256(_phase(did)), uint256(OptimisticInferenceV2.Phase.ChallengerWon));
        }
        assertLe(maxRounds, 6);   // ceil(log2 63)
    }

    // ================================================================ tests: forged proofs and openings
    function test_forgedInputOpening_reverts() public {
        Trace memory H = _trace(N, N);
        Trace memory Bad = _trace(20, N);
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(Bad);
        bytes32 did = _challenge(id, c, C);
        _openFinal(did, c, Bad);
        _bisect(did, Bad, H, C);
        (uint256[] memory inst, OptimisticInferenceV2.InputOpening[] memory ops) = _honestProof(H, 20);
        // swap in a fake input leaf with a self-consistent but fake start prefix
        bytes32 fake = keccak256("fake input");
        inst[0] = uint256(fake);
        ops[0].leaves[0] = fake;
        ops[0].startPrefix = keccak256("fake start");
        vm.expectRevert(bytes("input chain does not reach agreed prefix"));
        game.resolve(did, c, "valid", inst, ops);
    }

    function test_invalidProof_and_wrongTxInput_revert() public {
        Trace memory H = _trace(N, N);
        Trace memory Bad = _trace(0, N);
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(Bad);
        bytes32 did = _challenge(id, c, C);
        _openFinal(did, c, Bad);
        _bisect(did, Bad, H, C);
        (uint256[] memory inst, OptimisticInferenceV2.InputOpening[] memory ops) = _honestProof(H, 0);
        vm.expectRevert(bytes("invalid step proof"));
        game.resolve(did, c, "forged", inst, ops);
        // a proof that "matches" the asserter's wrong leaf cannot verify
        uint256[] memory inst2 = new uint256[](inst.length);
        for (uint256 i; i < inst.length; i++) inst2[i] = inst[i];
        inst2[inst.length - 1] = uint256(Bad.leaves[0]);
        vm.expectRevert(bytes("invalid step proof"));
        game.resolve(did, c, "valid", inst2, ops);
        // the proof must be about the claimed transaction
        inst[0] = 999;
        vm.expectRevert(bytes("tx input != claimed input"));
        game.resolve(did, c, "valid", inst, ops);
    }

    // ================================================================ tests: turns and timeouts
    function test_timeout_asserterDoesNotOpen() public {
        Trace memory H = _trace(N, N);
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(H);
        bytes32 did = _challenge(id, c, C);
        vm.expectRevert(OptimisticInferenceV2.DeadlineNotPassed.selector);
        game.timeout(did);
        vm.warp(block.timestamp + TURN + 1);
        game.timeout(did);
        assertEq(uint256(_phase(did)), uint256(OptimisticInferenceV2.Phase.ChallengerWon));
    }

    function test_timeout_challengerDoesNotRespond() public {
        Trace memory H = _trace(N, N);
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(H);
        bytes32 did = _challenge(id, c, C);
        _openFinal(did, c, H);
        uint256 mid = game.currentMid(did);
        vm.prank(A);
        game.reveal(did, mid, H.leaves[mid], H.prefixes[mid], _proof(H, mid));
        vm.warp(block.timestamp + TURN + 1);
        vm.prank(C);
        vm.expectRevert(OptimisticInferenceV2.DeadlinePassed.selector);
        game.respond(did, true);
        game.timeout(did);
        assertEq(uint256(_phase(did)), uint256(OptimisticInferenceV2.Phase.AsserterWon));
    }

    function test_timeout_noProofSubmitted_asserterLoses() public {
        Trace memory H = _trace(N, N);
        Trace memory Bad = _trace(5, N);
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(Bad);
        bytes32 did = _challenge(id, c, C);
        _openFinal(did, c, Bad);
        _bisect(did, Bad, H, C);
        assertEq(uint256(_phase(did)), uint256(OptimisticInferenceV2.Phase.AwaitProof));
        vm.warp(block.timestamp + TURN + 1);
        game.timeout(did);
        assertEq(uint256(_phase(did)), uint256(OptimisticInferenceV2.Phase.ChallengerWon));
    }

    function test_accessControl_and_turnOrder() public {
        Trace memory H = _trace(N, N);
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(H);
        bytes32 did = _challenge(id, c, C);
        // challenger cannot open for the asserter
        vm.prank(C);
        vm.expectRevert(OptimisticInferenceV2.NotYourTurn.selector);
        game.openFinal(did, c, H.leaves[N - 1], H.prefixes[N - 1], _proof(H, N - 1), H.leaves[N - 2], H.prefixes[N - 2], _proof(H, N - 2));
        // a wrong Merkle path is rejected
        vm.prank(A);
        vm.expectRevert(OptimisticInferenceV2.BadOpening.selector);
        game.openFinal(did, c, H.leaves[N - 1], H.prefixes[N - 1], _proof(H, 3), H.leaves[N - 2], H.prefixes[N - 2], _proof(H, N - 2));
        _openFinal(did, c, H);
        // responding before the reveal is out of phase; revealing the wrong index is rejected
        vm.prank(C);
        vm.expectRevert(OptimisticInferenceV2.BadPhase.selector);
        game.respond(did, true);
        uint256 mid = game.currentMid(did);
        vm.prank(A);
        vm.expectRevert(OptimisticInferenceV2.BadOpening.selector);
        game.reveal(did, mid + 1, H.leaves[mid + 1], H.prefixes[mid + 1], _proof(H, mid + 1));
        vm.prank(A);
        game.reveal(did, mid, H.leaves[mid], H.prefixes[mid], _proof(H, mid));
        // only the challenger answers
        vm.prank(A);
        vm.expectRevert(OptimisticInferenceV2.NotYourTurn.selector);
        game.respond(did, true);
        vm.prank(C2);
        vm.expectRevert(OptimisticInferenceV2.NotYourTurn.selector);
        game.respond(did, true);
        // challenger cannot dispute twice; asserter cannot dispute itself
        vm.prank(C);
        vm.expectRevert(bytes("already challenged"));
        game.challenge{value: CBOND}(id, c);
        vm.prank(A);
        vm.expectRevert(bytes("self"));
        game.challenge{value: CBOND}(id, c);
    }

    /// A colluding challenger that deliberately loses cannot shield a wrong claim: disputes are
    /// independent and one lost dispute rejects the claim.
    function test_colludingChallenger_cannotBlockHonestOne() public {
        Trace memory H = _trace(N, N);
        Trace memory Bad = _trace(9, N);
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(Bad);
        bytes32 fakeDid = _challenge(id, c, C2);
        bytes32 did = _challenge(id, c, C);
        // the colluder lets its own turn expire, so the asserter "wins" that dispute
        _openFinal(fakeDid, c, Bad);
        uint256 fm = game.currentMid(fakeDid);
        vm.prank(A);
        game.reveal(fakeDid, fm, Bad.leaves[fm], Bad.prefixes[fm], _proof(Bad, fm));
        _openFinal(did, c, Bad);
        _bisect(did, Bad, H, C);
        _resolveHonest(did, c, H);
        vm.warp(block.timestamp + TURN + 1);
        game.timeout(fakeDid);
        assertEq(uint256(_phase(fakeDid)), uint256(OptimisticInferenceV2.Phase.AsserterWon));
        assertEq(uint256(_phase(did)), uint256(OptimisticInferenceV2.Phase.ChallengerWon));
        vm.warp(block.timestamp + WINDOW + 1);
        assertFalse(game.isAccepted(id, c));
    }

    // ================================================================ tests: collateral
    function _col(address a) internal view returns (uint256 dep, uint256 lck) {
        (uint128 d, uint128 l) = game.collateral(a);
        return (d, l);
    }

    function test_bonds_commitNeedsCollateral_withdrawTimelock() public {
        address B = makeAddr("noDeposit");
        Trace memory H = _trace(N, N);
        vm.prank(B);
        vm.expectRevert(bytes("bond"));
        game.commit(H.root, H.outputDigest, H.inputHash);

        Trace memory Bad = _trace(3, N);
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(Bad);
        bytes32 did = _challenge(id, c, C);
        (uint256 dep, uint256 lck) = _col(A);
        assertEq(lck, ABOND);
        // locked collateral cannot be withdrawn
        vm.prank(A);
        vm.expectRevert(bytes("amount"));
        game.requestWithdraw(5 ether);
        // a withdrawal request waits for the delay and no longer backs new claims
        vm.prank(A);
        game.requestWithdraw(3.5 ether);
        assertEq(game.freeDeposit(A), 0.5 ether);
        vm.prank(A);
        vm.expectRevert(bytes("bond"));
        game.commit(H.root, H.outputDigest, H.inputHash);
        vm.prank(A);
        vm.expectRevert(bytes("not yet"));
        game.withdraw();
        // the asserter loses the dispute and its locked bond
        _openFinal(did, c, Bad);
        _bisect(did, Bad, H, C);
        _resolveHonest(did, c, H);
        (dep, lck) = _col(A);
        assertEq(dep, 0.5 ether);
        assertEq(lck, 0);
        uint256 before = C.balance;
        vm.prank(C);
        game.claimCredit();
        assertEq(C.balance - before, CBOND + ABOND);
        vm.warp(block.timestamp + 2 days);
        before = A.balance;
        vm.prank(A);
        game.withdraw();
        assertEq(A.balance - before, 3.5 ether);
    }

    /// Funds in a pending withdrawal stay slashable: a claim made before the request can still be
    /// challenged and the bond is pulled back from the pending amount.
    function test_pendingWithdrawal_staysSlashable() public {
        Trace memory H = _trace(N, N);
        Trace memory Bad = _trace(8, N);
        (uint256 id, OptimisticInferenceV2.Claim memory c) = _commit(Bad);
        vm.prank(A);
        game.requestWithdraw(5 ether);              // tries to run with everything
        assertEq(game.freeDeposit(A), 0);
        bytes32 did = _challenge(id, c, C);
        (, uint256 lck) = _col(A);
        assertEq(lck, ABOND);                       // bond re-locked from the pending withdrawal
        _openFinal(did, c, Bad);
        _bisect(did, Bad, H, C);
        _resolveHonest(did, c, H);
        assertEq(game.credit(C), CBOND + ABOND);
        vm.warp(block.timestamp + 2 days);
        uint256 before = A.balance;
        vm.prank(A);
        game.withdraw();
        assertEq(A.balance - before, 4 ether);      // 5 requested, 1 slashed
    }

    // ================================================================ cross-implementation check
    /// Root and prefix computed by chain.py for leaves keccak("leaf" || uint256(i)), i = 0..62.
    function test_crossCheck_pythonVector() public pure {
        bytes32[] memory leaves = new bytes32[](N);
        for (uint256 i; i < N; i++) leaves[i] = keccak256(abi.encodePacked("leaf", i));
        Trace memory T;
        T.leaves = leaves;
        T.prefixes = new bytes32[](N);
        bytes32 acc;
        for (uint256 i; i < N; i++) { acc = keccak256(abi.encodePacked(acc, leaves[i])); T.prefixes[i] = acc; }
        _tree(T);
        assertEq(T.prefixes[N - 1], 0x133d1deafc426f9f654e1873325b9a6b70b037d7ff2ae932b02e11abdf51259a);
        assertEq(T.root, 0xf761df39b3e6f0841480650b1d05546d95cb001a8df4ce30a06790cb1cb71fd7);
        uint256[] memory three = new uint256[](3);
        three[0] = 1; three[1] = 2; three[2] = 3;
        assertEq(keccak256(abi.encodePacked(three)), 0x6e0c627900b24bd432fe7b1f713f1b0744091a646a9fe4a65a18dfed21f2949c);
    }
}
