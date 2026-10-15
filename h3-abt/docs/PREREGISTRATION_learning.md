# H3 learning agents under imperfect detection: pre-registration

Committed before any run on the seeds below.
- Code: `src/h3_abt/learning.py`, `run_learning.py`.
- Tests: `tests/test_learning.py`. These use seed 1 only.

## Why

The durable-liability result (PREREGISTRATION_durable.md) used **myopic expected-payoff agents** with
**detection probability 1**. Those agents compute their best response exactly, so every violation
rate equals a closed-form expectation: 50.7% for control, 19.7% with a slash of 15. The experiment
therefore confirmed the implementation, not behaviour.

This study asks whether the two protocol fixes still work when:
1. agents do **not** know the payoffs, the slash or the detection probability, and learn from
   experience;
2. detection is **imperfect** (p = 0.5 and p = 0.8);
3. agents can **learn** to reset their identity (in the id-keyed regime), instead of following a
   built-in reset rule.

## Agents

Each agent is an independent ε-greedy tabular learner, a contextual bandit with sample-average value
estimates.

**State (what it observes):**
- whether the offer violates the policy;
- the payoff advantage of executing, bucket min(9, max(0, ⌊(V − C)/3⌋));
- its violation count k, bucketed {0, 1, 2, 3+}. This applies in escalating regimes only; it is 0
  elsewhere.
- whether its stake covers the base slash. This applies in the flat regime only; it is 1 elsewhere.

**Actions:**
- REFUSE;
- EXECUTE;
- in the escalating_id regime only, RESET_EXECUTE: pay bond B, take a fresh id so k = 0, then execute.

**Rewards** (same payoff semantics as the original protocol):
- refuse → C;
- execute a compliant offer → C;
- execute a violating offer → V − slash, where the slash applies only if detected. Detection is drawn
  with probability p; the slash is the regime's slash at the current k, capped by the remaining stake
  in the flat regime;
- RESET_EXECUTE additionally costs B.

**Learning:**
- Q initialised to 0. Ties are broken towards REFUSE, then EXECUTE.
- ε = 0.1, constant.
- Each Q(state, action) is the running mean of the rewards observed for it.
- The agent does not know p, the slash or B. It learns them only through rewards.

## Environment and regimes

- Same opportunity generator and payoff ranges as `configs/experiment.yaml` (stake 100, slash 15).
- **500 steps** per agent, so agents have time to learn.
- **20 agents × 10 new seeds (53–62) = 200 agents per regime and per p.**
- All regimes share the identical opportunity stream and detection stream per seed (paired design).
- Exploration randomness is per agent and seeded identically across regimes.

| Regime | Mechanism |
|---|---|
| control | unstaked |
| flat | stake 100, slash 15, no top-up (judgment-proof when depleted) |
| maintained | stake topped up before each action to cover the next slash |
| escalating_id | maintained + slash 15·(1+k), k keyed by agent id; RESET_EXECUTE available at bond B |
| escalating_operator | as escalating_id, but k keyed by the operator credential (a reset does not clear k) |

p ∈ {0.5, 0.8}. Bond B = 0 in the primary tests.

## Primary tests

Four tests. Unit = agent, one-sided Mann–Whitney (normal approximation, `agent_level.mann_whitney_less`)
with a 100,000-permutation check, and **Holm correction across all four**.

- **T1(p) (maintenance cures wear-out, learning agents):** in the last 50 steps (451–500), maintained
  agents violate less than flat agents. Tested for p = 0.5 and p = 0.8.
- **T2(p) (operator binding cures Sybil resets, learning agents):** over all steps,
  escalating_operator agents violate less than escalating_id agents (B = 0). Tested for p = 0.5 and
  p = 0.8.

**Decision rule:** each test is supported iff its Holm-adjusted p < 0.05.

The finding "the fixes survive learning agents and imperfect detection" is supported iff all four
tests are supported. If only some are, the report says which.

## Secondary (reported, no correction)

- **Deterrence under imperfect detection:** maintained < control over all steps, for each p.
- **Wear-out:** flat < control in steps 451–500, for each p.
- **Learned resets:** the number of RESET_EXECUTE actions per regime and per bond.
- **Bond sweep** (escalating_id), B ∈ {0, ⌈B*(p)⌉, 2⌈B*(p)⌉} with
  B*(p) = V_max − p·s − C_min = 30 − 15p − 1 (21.5 at p = 0.5, 17 at p = 0.8). Prediction: learned
  resets become rare at B ≥ B*(p), and their rate falls with B.
- **Learning curves:** violation rate per 50-step window.
- **Gap to the myopic best response:** for the maintained regime, the myopic optimum violation rate is
  0.6·P(V − p·s > C). The learners' rate also includes exploration.

## Not changed after seeing results

These are fixed now and will not change after results are seen: seeds, horizon, ε, state buckets,
regimes, p values and the tests above. Any additional analysis will be labelled post-hoc.
