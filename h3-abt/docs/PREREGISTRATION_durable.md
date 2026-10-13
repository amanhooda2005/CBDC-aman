# H3 durable liability — pre-registration

Committed before any run on the seeds below. Code: `src/h3_abt/durable.py`, `run_durable.py`. Tests: `tests/test_durable.py` (seed 42 only).

## Why

Two weaknesses of the confirmatory H3 result (seed 42):

1. **Deterrence wears out (observed post-hoc, seed 42).**
   - 19 of 20 staked agents were slashed down to zero stake.
   - Once depleted, staked agents violated on 53% of actions, against 54% for unstaked control.
   - By steps 41–50 the treatment rate was 0.495 against control's 0.545.
   - Slashing uses up the deterrent. This is the "judgment-proof" problem.
2. **Sybil reset.** The registry is keyed by agent id, so a penalised agent can re-register with a clean history (`SYBIL_SUCCEEDED`).

This experiment tests whether two protocol changes fix these weaknesses:
- **stake maintenance**, which fixes the first;
- **operator-bound history**, which fixes the second.

The regimes differ only in these mechanisms.

## Design

- Same config as the confirmatory run (`configs/experiment.yaml`): 20 agents, 50 steps, stake 100, slash 15, detection 1.0, same payoff ranges.
- **New seeds 43–52** (10 seeds × 20 agents = 200 agents per regime). Seed 42 was already seen and is used only in tests.
- All regimes get the identical opportunity stream per seed (paired), and the same myopic expected-payoff rule as `protocol.choose_action`.

| Regime | Mechanism |
|---|---|
| control | unstaked |
| flat | confirmatory treatment: stake 100, slash 15, no top-up |
| maintained | stake topped back up before every action to cover the next slash |
| escalating_id | maintained + repeat-offender slash 15·(1+k); history k keyed by agent id; an agent resets to a fresh id when that is strictly better one step ahead, paying bond B (B = 0 in primary tests) |
| escalating_operator | same, but k is keyed by the operator credential, so a new id keeps k |

Assumption: operators always have capital to top up. The capital topped up is recorded per agent.

## Primary tests

Unit = agent. One-sided Mann–Whitney (normal approximation, as in `agent_level.py`), with a 100,000-permutation check. Holm correction across the two tests.

- **T1 (maintenance cures wear-out):** in steps 41–50, maintained agents violate less than flat agents.
- **T2 (operator binding cures Sybil resets):** over all steps, escalating_operator agents violate less than escalating_id agents with free resets (B = 0).

Each test is supported iff the Holm-adjusted p < 0.05.

## Secondary (reported, no correction)

- Replication of the original H3 on new seeds: flat < control, all steps.
- Wear-out: flat < control in steps 41–50 (predicted not significant, or much weaker than over all steps).
- maintained < control, steps 41–50.
- escalating_operator < maintained (does escalation add deterrence beyond maintenance?).

## Descriptive

- Violation rate per regime in 10-step windows.
- Number of agents ever depleted.
- Number of resets.
- **Bond sweep** for escalating_id, with B ∈ {0, 5, 10, 14, 20, 40}.
  - Analytic break-even B* = V_max − p·s − C_min = 30 − 15 − 1 = 14. At B ≥ B*, no reset is ever strictly profitable (derivation in `durable.break_even_bond`).
  - Prediction: zero resets at B ≥ 14, and a violation rate equal to escalating_operator.

## Predictions (stated in advance)

- T1 supported.
- T2 supported.
- escalating_id with B = 0 is identical to maintained: free resets erase the escalation completely. This is confirmed on seed 42 by a test.

## Limitations

- Myopic agents and a one-step-ahead reset rule.
- Unlimited operator capital.
- Detection probability 1.
- Operator binding is modelled as a credential the registry can check; how a CBDC issuer would establish that credential (e.g. a licensed-entity registry) is out of scope.
