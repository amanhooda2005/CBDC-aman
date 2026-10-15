// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title LiabilityRegistry - durable, Sybil-resistant liability for autonomous agents (H3, on-chain).
///
/// On-chain version of the two protocol fixes found in the H3 durable-liability study
/// (docs/PREREGISTRATION_durable.md, results/durable_report.json):
///   1. Operator-bound history. Each agent id is a non-transferable token bound to the operator
///      credential that registered it. The violation count k is kept per OPERATOR, so a new agent id
///      registered by the same operator inherits k: identity resets do not erase escalation.
///   2. Stake maintenance. An operator's agents may act only while the operator's stake covers the
///      next escalated slash, max(minStake, s * (1 + k)). A depleted operator is blocked, never
///      judgment-proof.
/// Plus:
///   3. Escalating slash s * (1 + k), taken automatically when the settlement layer reports a
///      PROVEN violation (VIOL = 1 from the composed proof), never on an unproven allegation.
///   4. Registration bond B per new agent id, required to be at least the analytic break-even
///      B* = V_max - p * s - C_min, so that even an id-keyed deployment gains nothing from resets.
/// Slashed stake and bonds accrue to a treasury (e.g. a victim-compensation pool), pull-paid.
contract LiabilityRegistry {
    address public immutable regulator;          // issues operator credentials (KYC / licensing)
    address public immutable treasury;
    uint128 public immutable baseSlash;          // s
    uint128 public immutable minStake;           // stake floor S
    uint128 public immutable registrationBond;   // B
    uint64 public immutable withdrawDelay;
    address public slasher;                      // the settlement contract, set once

    struct Operator {
        bool credentialed;
        uint32 violations;          // k(O)
        uint128 stake;
        uint128 pendingWithdrawal;  // still slashable until withdrawn
        uint64 withdrawNotBefore;
    }
    mapping(address => Operator) public operators;

    struct Agent { address operator; uint64 registeredAt; }
    mapping(bytes32 => Agent) public agents;      // non-transferable: no function moves an agent
    uint256 public agentCount;
    uint256 public treasuryBalance;

    event CredentialIssued(address indexed operator);
    event CredentialRevoked(address indexed operator);
    event AgentRegistered(bytes32 indexed agentId, address indexed operator);
    event Staked(address indexed operator, uint256 amount, uint256 stake);
    event Slashed(bytes32 indexed agentId, address indexed operator, uint256 amount, uint256 violationsAfter, bytes32 evidence);
    event WithdrawRequested(address indexed operator, uint256 amount, uint64 notBefore);

    error NotRegulator();
    error NotSlasher();
    error NotCredentialed();
    error NonTransferable();

    struct Params {
        address regulator;
        address treasury;
        uint128 baseSlash;
        uint128 minStake;
        uint128 registrationBond;
        uint64 withdrawDelay;
        // economic envelope used to check B >= B* (payoffs in the same unit as the stake)
        uint128 maxViolationPayoff;   // V_max
        uint128 minCompliantPayoff;   // C_min
        uint16 detectionBps;          // p, in basis points (10000 = certain detection)
    }

    constructor(Params memory p) {
        require(p.regulator != address(0) && p.treasury != address(0), "addresses");
        require(p.detectionBps <= 10000, "detection");
        uint256 bStar = breakEvenBond(p.maxViolationPayoff, p.detectionBps, p.baseSlash, p.minCompliantPayoff);
        require(p.registrationBond >= bStar, "bond below break-even B*");
        regulator = p.regulator;
        treasury = p.treasury;
        baseSlash = p.baseSlash;
        minStake = p.minStake;
        registrationBond = p.registrationBond;
        withdrawDelay = p.withdrawDelay;
    }

    /// B* = V_max - p * s - C_min (floored at 0): the largest one-step gain an identity reset can buy.
    function breakEvenBond(uint256 vMax, uint256 detectionBps, uint256 s, uint256 cMin) public pure returns (uint256) {
        uint256 cost = detectionBps * s / 10000 + cMin;
        return vMax > cost ? vMax - cost : 0;
    }

    // ------------------------------------------------------------------ regulator
    function issueCredential(address op) external {
        if (msg.sender != regulator) revert NotRegulator();
        operators[op].credentialed = true;
        emit CredentialIssued(op);
    }

    function revokeCredential(address op) external {
        if (msg.sender != regulator) revert NotRegulator();
        operators[op].credentialed = false;
        emit CredentialRevoked(op);
    }

    function setSlasher(address s) external {
        if (msg.sender != regulator) revert NotRegulator();
        require(slasher == address(0) && s != address(0), "slasher already set");
        slasher = s;
    }

    // ------------------------------------------------------------------ operators and agents
    function registerAgent(bytes32 agentId) external payable {
        if (!operators[msg.sender].credentialed) revert NotCredentialed();
        require(agents[agentId].operator == address(0), "agent id taken");
        require(msg.value == registrationBond, "registration bond");
        agents[agentId] = Agent(msg.sender, uint64(block.timestamp));
        agentCount++;
        treasuryBalance += msg.value;
        emit AgentRegistered(agentId, msg.sender);
    }

    /// Agent tokens are soulbound (ERC-5192 style): always locked, never transferable.
    function locked(bytes32) external pure returns (bool) { return true; }

    function transferAgent(bytes32, address) external pure { revert NonTransferable(); }

    function topUp() external payable {
        Operator storage o = operators[msg.sender];
        o.stake += uint128(msg.value);
        emit Staked(msg.sender, msg.value, o.stake);
    }

    function requestWithdraw(uint128 amount) external {
        Operator storage o = operators[msg.sender];
        require(amount <= o.stake, "amount");
        o.stake -= amount;
        o.pendingWithdrawal += amount;
        o.withdrawNotBefore = uint64(block.timestamp) + withdrawDelay;
        emit WithdrawRequested(msg.sender, amount, o.withdrawNotBefore);
    }

    function withdraw() external {
        Operator storage o = operators[msg.sender];
        uint256 amt = o.pendingWithdrawal;
        require(amt > 0 && block.timestamp >= o.withdrawNotBefore, "not yet");
        o.pendingWithdrawal = 0;
        (bool ok,) = msg.sender.call{value: amt}("");
        require(ok, "transfer");
    }

    // ------------------------------------------------------------------ liability
    function nextSlash(address op) public view returns (uint256) {
        return uint256(baseSlash) * (1 + operators[op].violations);
    }

    function requiredStake(address op) public view returns (uint256) {
        uint256 n = nextSlash(op);
        return n > minStake ? n : minStake;
    }

    /// An agent may act only if it is registered, its operator is credentialed, and the operator's
    /// stake covers the next escalated slash (stake maintenance).
    function isCovered(bytes32 agentId) public view returns (bool) {
        address op = agents[agentId].operator;
        if (op == address(0)) return false;
        Operator storage o = operators[op];
        return o.credentialed && o.stake >= requiredStake(op);
    }

    function operatorOf(bytes32 agentId) external view returns (address) { return agents[agentId].operator; }

    /// Called by the settlement contract when a composed proof shows VIOL = 1 for this agent's
    /// decision. Takes s * (1 + k(O)) from the operator's stake (then from a pending withdrawal)
    /// and increments k(O).
    function slash(bytes32 agentId, bytes32 evidence) external returns (uint256 taken) {
        if (msg.sender != slasher) revert NotSlasher();
        address op = agents[agentId].operator;
        require(op != address(0), "unknown agent");
        Operator storage o = operators[op];
        uint256 due = nextSlash(op);
        uint256 fromStake = due < o.stake ? due : o.stake;
        o.stake -= uint128(fromStake);
        uint256 rest = due - fromStake;
        uint256 fromPending = rest < o.pendingWithdrawal ? rest : o.pendingWithdrawal;
        o.pendingWithdrawal -= uint128(fromPending);
        taken = fromStake + fromPending;
        o.violations += 1;
        treasuryBalance += taken;
        emit Slashed(agentId, op, taken, o.violations, evidence);
    }

    function payTreasury() external {
        uint256 amt = treasuryBalance;
        treasuryBalance = 0;
        (bool ok,) = treasury.call{value: amt}("");
        require(ok, "transfer");
    }
}
