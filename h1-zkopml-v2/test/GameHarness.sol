// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "../contracts/OptimisticInferenceV2.sol";


/// Models a SOUND step verifier: a "proof" verifies only if it is well-formed ("valid") and the
/// output instance equals the honest output of step k on the given input instances. This is the
/// property the real EZKL verifier provides; it lets the dispute logic be tested without EZKL.
contract MockStepVerifier is IStepVerifier {
    uint256 public immutable k;
    uint32 public immutable txLen;
    int32[] internal ins;

    constructor(uint256 k_, int32[] memory ins_, uint32 txLen_) {
        k = k_;
        ins = ins_;
        txLen = txLen_;
    }

    function verifyProof(bytes calldata proof, uint256[] calldata instances) external view returns (bool) {
        if (keccak256(proof) != keccak256("valid")) return false;
        bytes32[] memory vals = new bytes32[](ins.length);
        uint256 pos;
        for (uint256 t; t < ins.length; t++) {
            if (ins[t] == -1) {
                uint256[] memory x = new uint256[](txLen);
                for (uint256 i; i < txLen; i++) x[i] = instances[pos + i];
                vals[t] = keccak256(abi.encodePacked(x));
                pos += txLen;
            } else {
                vals[t] = bytes32(instances[pos]);
                pos += 1;
            }
        }
        bytes32 out = keccak256(abi.encode(k, vals));   // the "model": honest step function
        return instances.length == pos + 1 && instances[pos] == uint256(out);
    }
}


/// Model graph, trace construction and honest-proof construction shared by the tests and the
/// gas-profiling script.
abstract contract GameHarness {
    // 60-block model: proj0, proj1 (transaction input), join (inputs 0 and 1), 59 blocks, head
    uint256 constant N = 63;
    uint32 constant TXLEN = 4;
    uint256 constant ABOND = 1 ether;
    uint256 constant CBOND = 0.5 ether;
    uint64 constant WINDOW = 1 days;
    uint64 constant TURN = 1 hours;

    int32[][] inputs;
    uint256[] txInput;

    struct Trace {
        bytes32[] vals;      // raw step outputs (head: the output felt as bytes32)
        bytes32[] leaves;
        bytes32[] prefixes;
        bytes32[][] levels;
        bytes32 root;
        bytes32 outputDigest;
        bytes32 inputHash;
    }

    /// Builds the 63-step graph, deploys one mock verifier per step and returns the game parameters.
    function _model() internal returns (OptimisticInferenceV2.Params memory p) {
        address[] memory vs = new address[](N);
        for (uint256 k; k < N; k++) {
            int32[] memory ins;
            if (k <= 1) { ins = new int32[](1); ins[0] = -1; }
            else if (k == 2) { ins = new int32[](2); ins[0] = 0; ins[1] = 1; }
            else { ins = new int32[](1); ins[0] = int32(int256(k - 1)); }
            inputs.push(ins);
            vs[k] = address(new MockStepVerifier(k, ins, TXLEN));
        }
        txInput = [uint256(11), 22, 33, 44];
        p = OptimisticInferenceV2.Params({
            txInputLen: TXLEN, verifiers: vs, paramHashes: new bytes32[](0), inputs: inputs,
            asserterBond: ABOND, challengerBond: CBOND,
            challengeWindow: WINDOW, turnTimeout: TURN, withdrawDelay: 2 days
        });
    }

    // ---------------------------------------------------------------- trace construction
    function _stepOut(uint256 k, bytes32[] memory vals, bytes32 inHash) internal view returns (bytes32) {
        int32[] memory ins = inputs[k];
        bytes32[] memory iv = new bytes32[](ins.length);
        for (uint256 t; t < ins.length; t++) iv[t] = ins[t] == -1 ? inHash : vals[uint256(int256(ins[t]))];
        return keccak256(abi.encode(k, iv));
    }

    /// corruptAt: step whose output is tampered (later steps computed from it), N = none.
    /// garbageAt: step whose prefix is replaced by garbage (later prefixes chained from it), N = none.
    function _trace(uint256 corruptAt, uint256 garbageAt) internal view returns (Trace memory T) {
        T.inputHash = keccak256(abi.encodePacked(txInput));
        T.vals = new bytes32[](N);
        T.leaves = new bytes32[](N);
        T.prefixes = new bytes32[](N);
        bytes32 acc;
        for (uint256 k; k < N; k++) {
            T.vals[k] = k == corruptAt ? keccak256(abi.encode("tampered", k)) : _stepOut(k, T.vals, T.inputHash);
            T.leaves[k] = k == N - 1 ? keccak256(abi.encodePacked(uint256(T.vals[k]))) : T.vals[k];
            acc = k == garbageAt ? keccak256("garbage") : keccak256(abi.encodePacked(acc, T.leaves[k]));
            T.prefixes[k] = acc;
        }
        T.outputDigest = T.leaves[N - 1];
        _tree(T);
    }

    function _tree(Trace memory T) internal pure {
        uint256 size = 1;
        while (size < N) size *= 2;
        bytes32[] memory lvl = new bytes32[](size);
        for (uint256 i; i < size; i++) {
            uint256 j = i < N ? i : N - 1;
            lvl[i] = keccak256(abi.encodePacked(j, T.leaves[j], T.prefixes[j]));
        }
        uint256 depth;
        for (uint256 s = size; s > 1; s >>= 1) depth++;
        T.levels = new bytes32[][](depth + 1);
        T.levels[0] = lvl;
        for (uint256 d = 1; d <= depth; d++) {
            bytes32[] memory prev = T.levels[d - 1];
            bytes32[] memory nxt = new bytes32[](prev.length / 2);
            for (uint256 i; i < nxt.length; i++) nxt[i] = keccak256(abi.encodePacked(prev[2 * i], prev[2 * i + 1]));
            T.levels[d] = nxt;
        }
        T.root = T.levels[depth][0];
    }

    function _proof(Trace memory T, uint256 i) internal pure returns (bytes32[] memory p) {
        p = new bytes32[](T.levels.length - 1);
        for (uint256 d; d < p.length; d++) { p[d] = T.levels[d][i ^ 1]; i >>= 1; }
    }

    /// Builds the instances and input openings of an honest proof of step k (inputs taken from the
    /// honest trace H, which agrees with every prefix before k).
    function _honestProof(Trace memory H, uint256 k)
        internal view returns (uint256[] memory inst, OptimisticInferenceV2.InputOpening[] memory ops)
    {
        int32[] memory ins = inputs[k];
        uint256 len = 1;
        uint256 nOps;
        for (uint256 t; t < ins.length; t++) { if (ins[t] == -1) len += TXLEN; else { len += 1; nOps++; } }
        inst = new uint256[](len);
        ops = new OptimisticInferenceV2.InputOpening[](nOps);
        uint256 pos;
        uint256 o;
        for (uint256 t; t < ins.length; t++) {
            if (ins[t] == -1) {
                for (uint256 i; i < TXLEN; i++) inst[pos++] = txInput[i];
            } else {
                uint256 j = uint256(int256(ins[t]));
                inst[pos++] = uint256(H.leaves[j]);
                bytes32[] memory ls = new bytes32[](k - j);
                for (uint256 i; i < ls.length; i++) ls[i] = H.leaves[j + i];
                ops[o++] = OptimisticInferenceV2.InputOpening(j == 0 ? bytes32(0) : H.prefixes[j - 1], ls);
            }
        }
        inst[pos] = uint256(H.vals[k]);   // honest output (head: the output felt)
    }

}
