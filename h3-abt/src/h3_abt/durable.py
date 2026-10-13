"""Durable liability: does ABT deterrence survive stake depletion and identity resets?

Pre-registered in docs/PREREGISTRATION_durable.md. Same opportunity generator, the same
myopic expected-payoff decision as protocol.choose_action, and the same seed stream for
every regime (paired design). Regimes:

  control              unstaked (expected slash 0)
  flat                 the confirmatory treatment: stake S, slash s, no top-up
                       (a fully slashed agent is judgment-proof)
  maintained           stake topped back up to the requirement before each action
  escalating_id        maintained + repeat-offender slash s*(1+k), k = violations on the
                       current identity; the registry is keyed by agent id, so an agent
                       may reset to a fresh id (k = 0) by paying registration bond B
  escalating_operator  as escalating_id, but history is keyed by the operator credential:
                       a new id under the same operator keeps k

Reset rule (rational, one step ahead): before deciding, an agent resets iff
    -B + best_payoff(k_after_reset) > best_payoff(k),
where best_payoff(k) = max(execute payoff with slash s*(1+k), refuse payoff).
Detection probability is the configured value; with p = 1 detection is certain.
Assumption: operators always have capital to top up (stated in the pre-registration).
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

from h3_abt.environment import iter_agent_opportunities
from h3_abt.protocol import is_violation
from h3_abt.types import Opportunity, SimulationConfig, TieBreak

REGIMES = ("control", "flat", "maintained", "escalating_id", "escalating_operator")


@dataclass
class _Agent:
    stake: float
    k_identity: int = 0
    k_operator: int = 0
    identities: int = 1
    bonds_paid: float = 0.0
    capital_topped_up: float = 0.0


def slash_for(regime: str, config: SimulationConfig, k: int) -> float:
    if regime.startswith("escalating"):
        return config.slash_amount * (1 + k)
    return config.slash_amount


def _requirement(regime: str, config: SimulationConfig, k: int) -> float:
    """Stake an agent must hold before acting (maintained regimes): enough to cover the next slash."""
    return max(config.stake_amount, slash_for(regime, config, k))


def _expected_slash(regime: str, config: SimulationConfig, agent: _Agent, k: int, p: float) -> float:
    if regime == "control":
        return 0.0
    if regime == "flat":
        return p * min(config.slash_amount, max(agent.stake, 0.0))
    return p * slash_for(regime, config, k)  # maintained regimes always cover the slash


def _execute_payoff(opp: Opportunity, config: SimulationConfig, expected_slash: float) -> float:
    if not is_violation(opp.transfer, config):
        return opp.compliant_payoff
    return opp.violation_payoff - expected_slash


def _decide(execute: float, refuse: float, config: SimulationConfig) -> bool:
    if execute != refuse:
        return execute > refuse
    return config.tie_break is TieBreak.EXECUTE


def _k(regime: str, agent: _Agent) -> int:
    return agent.k_operator if regime == "escalating_operator" else agent.k_identity


def run_regime(config: SimulationConfig, regime: str, bond: float = 0.0, n_agents: int | None = None) -> list[dict]:
    """One row per action. `n_agents` defaults to config.n_staked."""
    if regime not in REGIMES:
        raise ValueError(f"unknown regime {regime}")
    n = n_agents or config.n_staked
    agents = [_Agent(stake=0.0 if regime == "control" else config.stake_amount) for _ in range(n)]
    rows: list[dict] = []
    rng = Random(config.seed)
    enforcement = Random(config.seed + 1_000_003)
    for step, index, opp in iter_agent_opportunities(config, n, rng):
        agent = agents[index]
        p = opp.detection_probability
        refuse = opp.compliant_payoff
        reset = False
        if regime.startswith("escalating") and is_violation(opp.transfer, config):
            k_now = _k(regime, agent)
            k_reset = agent.k_operator if regime == "escalating_operator" else 0
            stay = max(_execute_payoff(opp, config, _expected_slash(regime, config, agent, k_now, p)), refuse)
            moved = max(_execute_payoff(opp, config, _expected_slash(regime, config, agent, k_reset, p)), refuse) - bond
            if moved > stay:
                reset = True
                agent.identities += 1
                agent.bonds_paid += bond
                agent.k_identity = 0
        k = _k(regime, agent)
        if regime in ("maintained", "escalating_id", "escalating_operator"):
            need = _requirement(regime, config, k)
            if agent.stake < need:
                agent.capital_topped_up += need - agent.stake
                agent.stake = need
        stake_before = agent.stake
        execute = _decide(_execute_payoff(opp, config, _expected_slash(regime, config, agent, k, p)), refuse, config)
        committed = execute and is_violation(opp.transfer, config)
        detected = enforcement.random() < p
        slashed = 0.0
        if committed and detected and regime != "control":
            slashed = min(slash_for(regime, config, k), agent.stake)
            agent.stake -= slashed
            agent.k_identity += 1
            agent.k_operator += 1
        rows.append({
            "regime": regime, "bond": bond, "seed": config.seed, "step": step, "agent": index,
            "offer_violates": is_violation(opp.transfer, config), "is_violation": committed,
            "reset": reset, "stake_before": stake_before, "slashed": slashed,
            "identities": agent.identities, "bonds_paid": agent.bonds_paid,
            "capital_topped_up": agent.capital_topped_up,
        })
    return rows


def break_even_bond(config: SimulationConfig) -> float:
    """Bond at or above which a reset is never strictly profitable.

    Gain from a reset = max(V - p*s, C) - max(V - p*s*(1+k), C) <= max(0, V - p*s - C)
    <= V_max - p*s - C_min. A reset happens only if gain > B, so B >= B* blocks every reset."""
    return config.violation_payoff_max - config.compliant_payoff_min - config.detection_probability * config.slash_amount
