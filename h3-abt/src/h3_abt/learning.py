"""Learning agents under imperfect detection (pre-registered in docs/PREREGISTRATION_learning.md).

Each agent is an epsilon-greedy tabular learner (contextual bandit, sample-average values). It does
not know the payoffs, the slash, the detection probability or the bond; it learns them from rewards.
Same opportunity generator and payoff semantics as protocol.py; same regimes as durable.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random

from h3_abt.environment import iter_agent_opportunities
from h3_abt.protocol import is_violation
from h3_abt.types import SimulationConfig

REGIMES = ("control", "flat", "maintained", "escalating_id", "escalating_operator")
REFUSE, EXECUTE, RESET_EXECUTE = 0, 1, 2
EPSILON = 0.1
WINDOW = 50


@dataclass
class LearningAgent:
    stake: float
    k_identity: int = 0
    k_operator: int = 0
    identities: int = 1
    resets: int = 0
    slashed: float = 0.0
    capital_topped_up: float = 0.0
    q: dict = field(default_factory=dict)  # (state, action) -> [reward_sum, count]

    def value(self, state, action) -> float:
        s = self.q.get((state, action))
        return s[0] / s[1] if s else 0.0

    def learn(self, state, action, reward: float) -> None:
        s = self.q.setdefault((state, action), [0.0, 0])
        s[0] += reward
        s[1] += 1


def gap_bucket(v: float, c: float) -> int:
    return min(9, max(0, int((v - c) // 3)))


def k_bucket(k: int) -> int:
    return min(k, 3)


def slash_for(regime: str, config: SimulationConfig, k: int) -> float:
    if regime == "control":
        return 0.0
    if regime.startswith("escalating"):
        return config.slash_amount * (1 + k)
    return config.slash_amount


def _k(regime: str, a: LearningAgent) -> int:
    return a.k_operator if regime == "escalating_operator" else a.k_identity


def run_learning_regime(config: SimulationConfig, regime: str, bond: float = 0.0, n_agents: int | None = None) -> list[dict]:
    """Returns one summary dict per agent: violations and actions per 50-step window, resets, slashes."""
    if regime not in REGIMES:
        raise ValueError(regime)
    n = n_agents or config.n_staked
    p = config.detection_probability
    agents = [LearningAgent(stake=0.0 if regime == "control" else config.stake_amount) for _ in range(n)]
    explore = [Random(config.seed * 1_000 + i + 77) for i in range(n)]
    detection = Random(config.seed + 1_000_003)
    n_windows = (config.n_steps + WINDOW - 1) // WINDOW
    viol = [[0] * n_windows for _ in range(n)]
    acts = [[0] * n_windows for _ in range(n)]
    for step, i, opp in iter_agent_opportunities(config, n, Random(config.seed)):
        a = agents[i]
        u, pick = explore[i].random(), explore[i].random()   # always two draws: streams stay aligned across regimes
        detected = detection.random() < p                       # one draw per action, every regime
        violates = is_violation(opp.transfer, config)
        c, v = opp.compliant_payoff, opp.violation_payoff
        k = _k(regime, a)
        if regime in ("maintained", "escalating_id", "escalating_operator"):
            need = max(config.stake_amount, slash_for(regime, config, k))
            if a.stake < need:
                a.capital_topped_up += need - a.stake
                a.stake = need
        state = (
            violates,
            gap_bucket(v, c) if violates else 0,
            k_bucket(k) if regime.startswith("escalating") else 0,
            (a.stake >= config.slash_amount) if regime == "flat" else True,
        )
        actions = [REFUSE, EXECUTE] + ([RESET_EXECUTE] if regime == "escalating_id" else [])
        if u < EPSILON:
            action = actions[min(int(pick * len(actions)), len(actions) - 1)]
        else:
            action = max(actions, key=lambda act: (a.value(state, act), -act))  # ties: REFUSE, then EXECUTE
        reward = c
        committed = False
        if action in (EXECUTE, RESET_EXECUTE):
            if action == RESET_EXECUTE:
                a.k_identity = 0
                a.identities += 1
                a.resets += 1
                reward -= bond
                k = _k(regime, a)
            if violates:
                committed = True
                reward += v - c
                if detected and regime != "control":
                    s = slash_for(regime, config, k)
                    if regime == "flat":
                        s = min(s, a.stake)
                    a.stake -= s
                    a.slashed += s
                    reward -= s
                    a.k_identity += 1
                    a.k_operator += 1
        a.learn(state, action, reward)
        w = (step - 1) // WINDOW
        acts[i][w] += 1
        viol[i][w] += committed
    return [
        {"regime": regime, "bond": bond, "seed": config.seed, "p": p, "agent": i,
         "violations": viol[i], "actions": acts[i], "resets": agents[i].resets,
         "identities": agents[i].identities, "slashed": agents[i].slashed,
         "capital_topped_up": agents[i].capital_topped_up}
        for i in range(n)
    ]


def break_even_bond(config: SimulationConfig) -> float:
    """B*(p) = V_max - p*s - C_min (see durable.break_even_bond)."""
    return config.violation_payoff_max - config.detection_probability * config.slash_amount - config.compliant_payoff_min


def myopic_rate(config: SimulationConfig, slash: float, samples: int = 400_000, seed: int = 0) -> float:
    """Violation rate of the myopic best response with expected slash p*slash (Monte Carlo)."""
    rng = Random(seed)
    from h3_abt.environment import sample_opportunity
    hits = 0
    for _ in range(samples):
        o = sample_opportunity(rng, config)
        hits += is_violation(o.transfer, config) and (o.violation_payoff - config.detection_probability * slash > o.compliant_payoff)
    return hits / samples
