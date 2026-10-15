// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Interface of an EZKL-generated Solidity verifier for one model step.
interface IStepVerifier {
    function verifyProof(bytes calldata proof, uint256[] calldata instances) external returns (bool);
}

/// @title OptimisticInferenceV2 - zk-OPML dispute game with full on-chain adjudication.
///
/// An asserter posts the result of a model inference optimistically: the Merkle root R over
/// (i, leaf_i, P_i) for every step i of the bisectable model, where
///     leaf_i = hash of step i's output (Poseidon for inner steps, keccak of the public output
///              felts for the head step, so the head leaf IS the claimed output digest), and
///     P_i    = keccak256(P_{i-1} || leaf_i), P_{-1} = 0  (running prefix hash).
/// Any number of challengers may dispute a claim during the challenge window. Each dispute is an
/// independent two-party game:
///   1. Opening: the asserter opens steps n-1 and n-2. The head leaf must equal the claimed output
///      digest and P_{n-1} must equal keccak(P_{n-2} || leaf_{n-1}); otherwise the asserter loses.
///      Opening a dispute means the challenger disagrees with P_{n-1}.
///   2. Bisection: invariant P_lo agreed (lo = -1 means the empty prefix), P_hi disputed. The
///      asserter opens the midpoint, the challenger answers agree/disagree. Strict turn order and
///      a deadline per turn; whoever misses a deadline loses.
///   3. Consistency check: when hi = lo + 1 = k the contract requires P_k = keccak(P_{k-1} || leaf_k).
///      A malformed prefix chain loses immediately. (Without this check an asserter could commit
///      honest leaves up to k, a garbage P_k and wrong values afterwards, steer the search to a step
///      it computed honestly, and win with a wrong output.)
///   4. Single-step proof: anyone submits ONE proof of step k. Its input instances must be the
///      transaction input (hash equal to the claimed input hash) or earlier leaves, each opened by a
///      hash chain that recomputes to the agreed prefix P_{k-1}. The proven output is compared with
///      the asserter's leaf_k: equal -> challenger loses, different -> asserter loses. If nobody
///      submits a valid proof before the deadline the asserter loses (the burden is on the claim).
/// The loser's bond goes to the winner (pull payments).
///
/// Gas design: a dispute keeps only five 32-byte values (current opening, agreed lower prefix,
/// disputed upper leaf/prefix) instead of storing every opening; the claim fields other than the
/// root are re-supplied as calldata and checked against the single per-claim digest.
contract OptimisticInferenceV2 {
    // ------------------------------------------------------------------ model (fixed at deploy)
    uint32 public immutable nSteps;
    uint32 public immutable txInputLen;   // number of public input felts of a transaction-input step
    uint32 public immutable treeDepth;    // Merkle depth, ceil(log2(nSteps))
    address[] public stepVerifier;        // verifier contract per step (may repeat for identical steps)
    /// Per step: Poseidon hash of the step's weights when its verifier is SHARED across steps of the
    /// same shape (EZKL param_visibility = "hashed"; the hash is a public instance after the hashed
    /// inputs and before any public transaction input), or 0 when the weights are fixed inside a
    /// step-specific verifier.
    bytes32[] public stepParamHash;
    int32[][] internal stepInputs;        // per step: -1 = transaction input, j >= 0 = output of step j

    // ------------------------------------------------------------------ economics and timing
    uint256 public immutable asserterBond;
    uint256 public immutable challengerBond;
    uint64 public immutable challengeWindow;
    uint64 public immutable turnTimeout;
    uint64 public immutable withdrawDelay;

    struct Collateral { uint128 deposit; uint128 locked; }   // one slot per asserter
    mapping(address => Collateral) public collateral;
    struct Withdrawal { uint128 amount; uint64 notBefore; }  // still slashable until withdrawn
    mapping(address => Withdrawal) public pendingWithdrawal;
    mapping(address => uint256) public credit;               // winnings, withdrawn with claimCredit()

    // ------------------------------------------------------------------ claims
    struct Claim { bytes32 root; bytes32 outputDigest; bytes32 inputHash; address asserter; uint64 committedAt; }
    mapping(uint256 => bytes32) public claimDigest;   // one storage slot per inference
    uint256 public nClaims;
    struct ClaimState { uint32 openDisputes; bool rejected; }
    mapping(uint256 => ClaimState) public claimState;

    // ------------------------------------------------------------------ disputes
    enum Phase { None, AwaitOpening, AwaitReveal, AwaitResponse, AwaitProof, AsserterWon, ChallengerWon }
    struct Dispute {
        address asserter;     // slot 0
        uint40 deadline;
        uint32 lo1;           // lo + 1, so 0 encodes the empty prefix (lo = -1)
        address challenger;   // slot 1
        uint32 hi;
        Phase phase;
        uint48 claimId;
        bytes32 root;         // slot 2
        uint256 lockedBond;   // slot 3
        bytes32 curLeaf;      // last opening (at the current midpoint)
        bytes32 curPrefix;
        bytes32 loPrefix;     // agreed P_lo
        bytes32 hiLeaf;       // asserter's disputed leaf_hi / P_hi
        bytes32 hiPrefix;
    }
    mapping(bytes32 => Dispute) public disputes;      // key = keccak(claimId, challenger)

    /// Opening of one earlier step's leaf used as an input of the disputed step: the prefix before
    /// it and the leaves from that step up to k-1, which must hash forward to the agreed P_{k-1}.
    struct InputOpening { bytes32 startPrefix; bytes32[] leaves; }

    struct Params {
        uint32 txInputLen;
        address[] verifiers;
        bytes32[] paramHashes;   // empty = every verifier has its weights fixed in the circuit
        int32[][] inputs;
        uint256 asserterBond;
        uint256 challengerBond;
        uint64 challengeWindow;
        uint64 turnTimeout;
        uint64 withdrawDelay;
    }

    /// Watchers read root, output digest and input hash from the commit transaction's calldata.
    event Committed(uint256 indexed id);
    event Challenged(uint256 indexed id, bytes32 indexed disputeId, address indexed challenger);
    event Opened(bytes32 indexed disputeId, uint256 idx, bytes32 leaf, bytes32 prefix);
    event Responded(bytes32 indexed disputeId, uint256 mid, bool agree);
    event BisectionDone(bytes32 indexed disputeId, uint256 step);
    event Resolved(bytes32 indexed disputeId, bool asserterWon, string reason);

    error BadPhase();
    error NotYourTurn();
    error DeadlinePassed();
    error DeadlineNotPassed();
    error BadOpening();
    error BadClaim();

    constructor(Params memory p) {
        uint256 n = p.verifiers.length;
        require(n >= 2 && n == p.inputs.length, "model shape");
        require(p.paramHashes.length == 0 || p.paramHashes.length == n, "param hashes");
        require(p.withdrawDelay >= p.challengeWindow, "withdraw delay < window");
        require(p.turnTimeout > 0 && p.challengeWindow > 0, "timing");
        require(p.asserterBond < 2 ** 128 && p.challengerBond < 2 ** 128, "bond size");
        nSteps = uint32(n);
        txInputLen = p.txInputLen;
        uint32 d;
        while ((uint256(1) << d) < n) d++;
        treeDepth = d;
        for (uint256 k; k < n; k++) {
            require(p.verifiers[k] != address(0), "verifier");
            require(p.inputs[k].length > 0, "inputs");
            stepVerifier.push(p.verifiers[k]);
            stepParamHash.push(p.paramHashes.length == 0 ? bytes32(0) : p.paramHashes[k]);
            bool sawTx;
            stepInputs.push();
            for (uint256 t; t < p.inputs[k].length; t++) {
                int32 j = p.inputs[k][t];
                require(j >= -1 && j < int32(int256(k)), "inputs must be earlier steps");
                // hashed inputs precede the public transaction input in EZKL's instance layout
                require(!(sawTx && j >= 0 && stepParamHash[k] != bytes32(0)), "hashed inputs must come first");
                if (j == -1) sawTx = true;
                stepInputs[k].push(j);
            }
        }
        asserterBond = p.asserterBond;
        challengerBond = p.challengerBond;
        challengeWindow = p.challengeWindow;
        turnTimeout = p.turnTimeout;
        withdrawDelay = p.withdrawDelay;
    }

    // ================================================================== collateral
    function depositBond() external payable {
        collateral[msg.sender].deposit += uint128(msg.value);
    }

    function freeDeposit(address a) public view returns (uint256) {
        Collateral memory c = collateral[a];
        return c.deposit - c.locked;
    }

    /// Moves `amount` out of the free deposit into a pending withdrawal. Pending funds can no longer
    /// back new claims but remain slashable by disputes opened before they are withdrawn.
    function requestWithdraw(uint256 amount) external {
        Collateral storage c = collateral[msg.sender];
        require(amount <= c.deposit - c.locked, "amount");
        c.deposit -= uint128(amount);
        Withdrawal storage w = pendingWithdrawal[msg.sender];
        w.amount += uint128(amount);
        w.notBefore = uint64(block.timestamp) + withdrawDelay;
    }

    function withdraw() external {
        Withdrawal memory w = pendingWithdrawal[msg.sender];
        require(w.amount > 0 && block.timestamp >= w.notBefore, "not yet");
        delete pendingWithdrawal[msg.sender];
        (bool ok,) = msg.sender.call{value: w.amount}("");
        require(ok, "transfer");
    }

    function claimCredit() external {
        uint256 c = credit[msg.sender];
        credit[msg.sender] = 0;
        (bool ok,) = msg.sender.call{value: c}("");
        require(ok, "transfer");
    }

    // ================================================================== optimistic path
    /// Per-inference cost: one new storage slot, one collateral read and a one-topic event.
    function commit(bytes32 root, bytes32 outputDigest, bytes32 inputHash) external returns (uint256 id) {
        Collateral memory c = collateral[msg.sender];
        require(c.deposit - c.locked >= asserterBond, "bond");
        id = nClaims++;
        claimDigest[id] = keccak256(abi.encode(root, outputDigest, inputHash, msg.sender, uint64(block.timestamp)));
        emit Committed(id);
    }

    /// True once the challenge window has passed and every dispute was won by the asserter.
    function isAccepted(uint256 id, Claim calldata c) external view returns (bool) {
        _checkClaim(id, c);
        ClaimState memory s = claimState[id];
        return block.timestamp > c.committedAt + challengeWindow && s.openDisputes == 0 && !s.rejected;
    }

    function disputeId(uint256 claimId, address challenger) public pure returns (bytes32) {
        return keccak256(abi.encode(claimId, challenger));
    }

    // ================================================================== dispute game
    function challenge(uint256 id, Claim calldata c) external payable returns (bytes32 did) {
        _checkClaim(id, c);
        require(msg.value == challengerBond, "challenger bond");
        require(block.timestamp <= c.committedAt + challengeWindow, "window closed");
        require(msg.sender != c.asserter, "self");
        require(id < 2 ** 48, "claim id");
        did = disputeId(id, msg.sender);
        Dispute storage d = disputes[did];
        require(d.phase == Phase.None, "already challenged");

        d.lockedBond = _lockBond(c.asserter);
        d.asserter = c.asserter;
        d.challenger = msg.sender;
        d.claimId = uint48(id);
        d.root = c.root;
        d.hi = nSteps - 1;
        d.phase = Phase.AwaitOpening;
        d.deadline = uint40(block.timestamp + turnTimeout);
        claimState[id].openDisputes += 1;
        emit Challenged(id, did, msg.sender);
    }

    /// Step 1: asserter opens the last two steps; binds the head leaf to the claimed output.
    function openFinal(
        bytes32 did, Claim calldata c,
        bytes32 leafLast, bytes32 prefixLast, bytes32[] calldata proofLast,
        bytes32 leafPrev, bytes32 prefixPrev, bytes32[] calldata proofPrev
    ) external {
        Dispute storage d = _turn(did, Phase.AwaitOpening, disputes[did].asserter);
        _checkClaim(d.claimId, c);
        uint256 last = nSteps - 1;
        _verifyOpening(d.root, last, leafLast, prefixLast, proofLast);
        _verifyOpening(d.root, last - 1, leafPrev, prefixPrev, proofPrev);
        emit Opened(did, last, leafLast, prefixLast);
        d.hiLeaf = leafLast;
        d.hiPrefix = prefixLast;
        if (leafLast != c.outputDigest) return _finish(did, false, "head leaf != claimed output");
        if (prefixLast != keccak256(abi.encodePacked(prefixPrev, leafLast))) return _finish(did, false, "malformed prefix chain at head");
        _advance(did, d);
    }

    /// Step 2a: asserter opens the current midpoint.
    function reveal(bytes32 did, uint256 idx, bytes32 leaf, bytes32 prefix, bytes32[] calldata proof) external {
        Dispute storage d = _turn(did, Phase.AwaitReveal, disputes[did].asserter);
        if (idx != _mid(d)) revert BadOpening();
        _verifyOpening(d.root, idx, leaf, prefix, proof);
        d.curLeaf = leaf;
        d.curPrefix = prefix;
        d.phase = Phase.AwaitResponse;
        d.deadline = uint40(block.timestamp + turnTimeout);
        emit Opened(did, idx, leaf, prefix);
    }

    /// Step 2b: challenger agrees or disagrees with the opened prefix at the midpoint.
    function respond(bytes32 did, bool agree) external {
        Dispute storage d = _turn(did, Phase.AwaitResponse, disputes[did].challenger);
        uint256 mid = _mid(d);
        if (agree) {
            d.lo1 = uint32(mid + 1);
            d.loPrefix = d.curPrefix;
        } else {
            d.hi = uint32(mid);
            d.hiLeaf = d.curLeaf;
            d.hiPrefix = d.curPrefix;
        }
        emit Responded(did, mid, agree);
        _advance(did, d);
    }

    /// Step 4: anyone submits the single-step proof of the disputed step.
    function resolve(bytes32 did, Claim calldata c, bytes calldata proof, uint256[] calldata instances, InputOpening[] calldata openings) external {
        Dispute storage d = disputes[did];
        if (d.phase != Phase.AwaitProof) revert BadPhase();
        if (block.timestamp > d.deadline) revert DeadlinePassed();
        _checkClaim(d.claimId, c);
        uint256 k = d.hi;
        uint256 pos = _checkInputs(d, k, c.inputHash, instances, openings);

        // --- output instance(s)
        require(pos < instances.length, "no output");
        bytes32 provenOut;
        if (k == nSteps - 1) provenOut = _hashSlice(instances, pos, instances.length - pos);
        else {
            require(instances.length == pos + 1, "output shape");
            provenOut = bytes32(instances[pos]);
        }

        // --- the proof itself (an invalid proof reverts; it does not decide the dispute)
        require(IStepVerifier(stepVerifier[k]).verifyProof(proof, instances), "invalid step proof");

        if (provenOut == d.hiLeaf) _finish(did, true, "step proof matches asserter leaf");
        else _finish(did, false, "step proof contradicts asserter leaf");
    }

    /// Anyone may end a dispute whose current party missed its deadline.
    function timeout(bytes32 did) external {
        Dispute storage d = disputes[did];
        Phase ph = d.phase;
        if (ph == Phase.None || ph == Phase.AsserterWon || ph == Phase.ChallengerWon) revert BadPhase();
        if (block.timestamp <= d.deadline) revert DeadlineNotPassed();
        if (ph == Phase.AwaitResponse) _finish(did, true, "challenger timed out");
        else if (ph == Phase.AwaitProof) _finish(did, false, "no valid step proof before deadline");
        else _finish(did, false, "asserter timed out");
    }

    // ================================================================== views for the parties
    function currentMid(bytes32 did) external view returns (uint256) { return _mid(disputes[did]); }

    function status(bytes32 did) external view returns (Phase phase, uint256 lo1, uint256 hi, uint256 deadline) {
        Dispute storage d = disputes[did];
        return (d.phase, d.lo1, d.hi, d.deadline);
    }

    function openings(bytes32 did) external view returns (bytes32 curLeaf, bytes32 curPrefix, bytes32 loPrefix, bytes32 hiLeaf, bytes32 hiPrefix) {
        Dispute storage d = disputes[did];
        return (d.curLeaf, d.curPrefix, d.loPrefix, d.hiLeaf, d.hiPrefix);
    }

    function stepInputsOf(uint256 k) external view returns (int32[] memory) { return stepInputs[k]; }

    // ================================================================== internals
    function _turn(bytes32 did, Phase want, address who) private view returns (Dispute storage d) {
        d = disputes[did];
        if (d.phase != want) revert BadPhase();
        if (msg.sender != who) revert NotYourTurn();
        if (block.timestamp > d.deadline) revert DeadlinePassed();
    }

    function _mid(Dispute storage d) private view returns (uint256) {
        // lo = lo1 - 1, mid = floor((lo + hi) / 2); returns hi once the search has ended (hi = lo + 1)
        if (d.hi == d.lo1) return d.hi;
        return (uint256(d.lo1) + uint256(d.hi) - 1) / 2;
    }

    function _advance(bytes32 did, Dispute storage d) private {
        if (d.hi == d.lo1) {
            // hi = lo + 1: disputed step found; check the asserter's chain is consistent at it
            bytes32 prev = d.lo1 == 0 ? bytes32(0) : d.loPrefix;
            emit BisectionDone(did, d.hi);
            if (d.hiPrefix != keccak256(abi.encodePacked(prev, d.hiLeaf))) {
                return _finish(did, false, "malformed prefix chain at disputed step");
            }
            d.phase = Phase.AwaitProof;
        } else {
            d.phase = Phase.AwaitReveal;
        }
        d.deadline = uint40(block.timestamp + turnTimeout);
    }

    function _lockBond(address asserter) private returns (uint256 lockAmt) {
        Collateral storage c = collateral[asserter];
        uint256 free = c.deposit - c.locked;
        if (free < asserterBond) {
            // pull back funds the asserter is trying to withdraw: they stay slashable until withdrawn
            Withdrawal storage w = pendingWithdrawal[asserter];
            uint256 t = asserterBond - free;
            if (t > w.amount) t = w.amount;
            w.amount -= uint128(t);
            c.deposit += uint128(t);
            free += t;
        }
        lockAmt = free < asserterBond ? free : asserterBond;
        c.locked += uint128(lockAmt);
    }

    function _verifyOpening(bytes32 root, uint256 idx, bytes32 leaf, bytes32 prefix, bytes32[] calldata proof) private view {
        if (idx >= nSteps || proof.length != treeDepth) revert BadOpening();
        bytes32 h = keccak256(abi.encodePacked(idx, leaf, prefix));
        uint256 k = idx;
        for (uint256 i; i < proof.length; i++) {
            h = (k & 1) == 0 ? keccak256(abi.encodePacked(h, proof[i])) : keccak256(abi.encodePacked(proof[i], h));
            k >>= 1;
        }
        if (h != root) revert BadOpening();
    }

    function _finish(bytes32 did, bool asserterWins, string memory reason) private {
        Dispute storage d = disputes[did];
        uint256 lb = d.lockedBond;
        Collateral storage col = collateral[d.asserter];
        col.locked -= uint128(lb);
        ClaimState storage s = claimState[d.claimId];
        s.openDisputes -= 1;
        if (asserterWins) {
            d.phase = Phase.AsserterWon;
            credit[d.asserter] += challengerBond;
        } else {
            d.phase = Phase.ChallengerWon;
            col.deposit -= uint128(lb);
            credit[d.challenger] += challengerBond + lb;
            s.rejected = true;
        }
        emit Resolved(did, asserterWins, reason);
    }

    /// Checks the input instances of step k and returns the index of the first output instance.
    function _checkInputs(Dispute storage d, uint256 k, bytes32 inputHash, uint256[] calldata instances, InputOpening[] calldata ops)
        private view returns (uint256 pos)
    {
        bytes32 agreedPrev = d.lo1 == 0 ? bytes32(0) : d.loPrefix;
        // EZKL instance layout: [hashed inputs][weight hash, if shared verifier][public inputs][outputs]
        bytes32 ph = stepParamHash[k];
        uint256 o;
        int32[] storage ins = stepInputs[k];
        for (uint256 t; t < ins.length; t++) {
            if (ins[t] == -1) {
                if (ph != bytes32(0)) { pos = _checkParam(ph, instances, pos); ph = bytes32(0); }
                require(_hashSlice(instances, pos, txInputLen) == inputHash, "tx input != claimed input");
                pos += txInputLen;
            } else {
                uint256 j = uint256(int256(ins[t]));
                require(o < ops.length, "missing opening");
                _checkOpening(ops[o++], j, k, instances, pos, agreedPrev);
                pos += 1;
            }
        }
        require(o == ops.length, "extra openings");
        if (ph != bytes32(0)) pos = _checkParam(ph, instances, pos);
    }

    /// A shared verifier proves "some weights with this hash": the hash must be step k's registered one.
    function _checkParam(bytes32 ph, uint256[] calldata instances, uint256 pos) private pure returns (uint256) {
        require(pos < instances.length && bytes32(instances[pos]) == ph, "params instance != step weights");
        return pos + 1;
    }

    function _checkOpening(InputOpening calldata op, uint256 j, uint256 k, uint256[] calldata instances, uint256 pos, bytes32 agreedPrev)
        private pure
    {
        require(op.leaves.length == k - j, "opening length");
        require(j != 0 || op.startPrefix == bytes32(0), "start prefix");
        require(pos < instances.length && op.leaves[0] == bytes32(instances[pos]), "input instance != opened leaf");
        bytes32 p = op.startPrefix;
        for (uint256 i; i < op.leaves.length; i++) p = keccak256(abi.encodePacked(p, op.leaves[i]));
        require(p == agreedPrev, "input chain does not reach agreed prefix");
    }

    function _checkClaim(uint256 id, Claim calldata c) private view {
        if (claimDigest[id] == bytes32(0) ||
            claimDigest[id] != keccak256(abi.encode(c.root, c.outputDigest, c.inputHash, c.asserter, c.committedAt))) revert BadClaim();
    }

    /// keccak256 of instances[from : from+len] as consecutive 32-byte words
    /// (= keccak256(abi.encodePacked(uint256[] slice))).
    function _hashSlice(uint256[] calldata a, uint256 from, uint256 len) private pure returns (bytes32 h) {
        require(len > 0 && from + len <= a.length, "instance range");
        assembly ("memory-safe") {
            let ptr := mload(0x40)
            calldatacopy(ptr, add(a.offset, mul(from, 32)), mul(len, 32))
            h := keccak256(ptr, mul(len, 32))
        }
    }
}
