// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "h3/LiabilityRegistry.sol";

/// @title SettlementGate - CBDC settlement gated by a verified AI decision (reference numeral 118).
///
/// A transfer requested by an autonomous agent settles only if a quorum of the network's validator
/// nodes attests that the composed post-quantum proof for this transfer verifies, i.e. that
///   - the trained risk model was run on the transaction (risk score private),
///   - the risk flag and the agent's EXEC / VIOL outputs follow from it, and
///   - the decision was made on exactly the amount inside the lattice commitment c,
/// and that c opens to the amount being settled. Validators run the STARK verifier natively (it is
/// not executed in the EVM, which keeps verification hash-based and post-quantum).
///
/// Outcome per request:
///   VIOL = 1                 -> transfer blocked, operator slashed through the LiabilityRegistry
///   agent not covered        -> transfer refused (stake maintenance: the operator must top up)
///   EXEC = 1, VIOL = 0       -> transfer executed on the CBDC ledger
///   EXEC = 0, VIOL = 0       -> agent refused the transfer; nothing moves
contract SettlementGate {
    LiabilityRegistry public immutable registry;
    address public immutable centralBank;       // mints CBDC (lab ledger)
    uint256 public immutable threshold;         // signatures required, e.g. 3 of 4 (BFT quorum)
    address[] public validators;
    mapping(address => bool) public isValidator;

    mapping(address => uint256) public balanceOf;              // lab CBDC ledger
    mapping(address => mapping(bytes32 => bool)) public mandate; // wallet -> agents allowed to move its funds
    mapping(bytes32 => Outcome) public outcome;                 // requestId -> outcome

    enum Outcome { None, Executed, AgentRefused, Blocked, NotCovered }

    struct Request {
        bytes32 agentId;
        address from;
        address to;
        uint256 amount;
        bytes32 commitmentHash;   // keccak256 of the SIS commitment vector c
        bytes32 proofHash;        // hash of the composed proof the validators checked
        bool exec;                // proven agent decision
        bool viol;                // proven violation flag (over-limit OR sanctioned OR risk)
        uint64 nonce;
    }

    bytes32 public constant DOMAIN = keccak256("CBDC-SettlementGate-v1");

    event Minted(address indexed to, uint256 amount);
    event MandateSet(address indexed wallet, bytes32 indexed agentId, bool allowed);
    event Settled(bytes32 indexed requestId, bytes32 indexed agentId, Outcome outcome, uint256 amount);

    constructor(LiabilityRegistry registry_, address centralBank_, address[] memory validators_, uint256 threshold_) {
        require(threshold_ > validators_.length / 2 && threshold_ <= validators_.length, "threshold");
        registry = registry_;
        centralBank = centralBank_;
        threshold = threshold_;
        for (uint256 i; i < validators_.length; i++) {
            require(validators_[i] != address(0) && !isValidator[validators_[i]], "validator");
            isValidator[validators_[i]] = true;
            validators.push(validators_[i]);
        }
    }

    // ------------------------------------------------------------------ lab CBDC ledger
    function mint(address to, uint256 amount) external {
        require(msg.sender == centralBank, "central bank only");
        balanceOf[to] += amount;
        emit Minted(to, amount);
    }

    /// A wallet holder authorises (or revokes) an agent to initiate transfers from the wallet.
    function setMandate(bytes32 agentId, bool allowed) external {
        mandate[msg.sender][agentId] = allowed;
        emit MandateSet(msg.sender, agentId, allowed);
    }

    // ------------------------------------------------------------------ settlement
    function requestId(Request calldata r) public view returns (bytes32) {
        return keccak256(abi.encode(DOMAIN, block.chainid, address(this), r));
    }

    /// `sigs` are 65-byte ECDSA signatures by distinct validators over the EIP-191 hash of requestId(r).
    function settle(Request calldata r, bytes[] calldata sigs) external returns (Outcome out) {
        bytes32 id = requestId(r);
        require(outcome[id] == Outcome.None, "already settled");
        _checkQuorum(id, sigs);
        require(mandate[r.from][r.agentId], "no mandate");

        if (r.viol) {
            registry.slash(r.agentId, r.proofHash);
            out = Outcome.Blocked;
        } else if (!registry.isCovered(r.agentId)) {
            out = Outcome.NotCovered;
        } else if (r.exec) {
            require(balanceOf[r.from] >= r.amount, "balance");
            balanceOf[r.from] -= r.amount;
            balanceOf[r.to] += r.amount;
            out = Outcome.Executed;
        } else {
            out = Outcome.AgentRefused;
        }
        outcome[id] = out;
        emit Settled(id, r.agentId, out, out == Outcome.Executed ? r.amount : 0);
    }

    function validatorCount() external view returns (uint256) { return validators.length; }

    function _checkQuorum(bytes32 id, bytes[] calldata sigs) private view {
        bytes32 h = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", id));
        uint256 ok;
        address[] memory seen = new address[](sigs.length);
        for (uint256 i; i < sigs.length; i++) {
            address v = _recover(h, sigs[i]);
            if (!isValidator[v]) continue;
            bool dup;
            for (uint256 j; j < ok; j++) if (seen[j] == v) { dup = true; break; }
            if (dup) continue;
            seen[ok++] = v;
        }
        require(ok >= threshold, "validator quorum not reached");
    }

    function _recover(bytes32 h, bytes calldata sig) private pure returns (address) {
        if (sig.length != 65) return address(0);
        bytes32 r = bytes32(sig[0:32]);
        bytes32 s = bytes32(sig[32:64]);
        uint8 v = uint8(sig[64]);
        if (v < 27) v += 27;
        // reject malleable high-s signatures (EIP-2)
        if (uint256(s) > 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0) return address(0);
        return ecrecover(h, v, r, s);
    }
}
