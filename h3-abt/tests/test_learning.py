"""Unit tests for learning.py (seed 1 only; the study's seeds 53-62 are not touched here)."""

from dataclasses import replace

from h3_abt.config import load_config
from h3_abt.learning import (REGIMES, LearningAgent, break_even_bond, gap_bucket, myopic_rate,
                             run_learning_regime)

BASE = replace(load_config("configs/experiment.yaml"), seed=1, n_steps=200, detection_probability=0.5)


def rate(rows):
    return sum(sum(r["violations"]) for r in rows) / sum(sum(r["actions"]) for r in rows)


def test_reproducible_and_shapes():
    a = run_learning_regime(BASE, "maintained")
    b = run_learning_regime(BASE, "maintained")
    assert a == b
    assert len(a) == BASE.n_staked
    assert all(sum(r["actions"]) == BASE.n_steps for r in a)
    assert len(a[0]["violations"]) == BASE.n_steps // 50


def test_control_is_never_slashed_and_flat_gets_depleted():
    assert all(r["slashed"] == 0 for r in run_learning_regime(BASE, "control"))
    flat = run_learning_regime(BASE, "flat")
    assert all(r["slashed"] <= BASE.stake_amount + 1e-9 for r in flat)
    assert sum(r["slashed"] >= BASE.stake_amount - 1e-9 for r in flat) > 0


def test_resets_only_where_available_and_cost_bond():
    for regime in REGIMES:
        rows = run_learning_regime(BASE, regime, bond=0.0)
        if regime != "escalating_id":
            assert all(r["resets"] == 0 for r in rows)
    rows = run_learning_regime(BASE, "escalating_id", bond=0.0)
    assert sum(r["resets"] for r in rows) > 0   # exploration alone produces some


def test_learning_reduces_violations_when_slash_dominates():
    # with certain detection and a slash larger than any gain, staked learners mostly stop violating
    # (the remaining rate is the exploration floor: epsilon * P(violating offer) * 1/2 ~ 3%)
    cfg = replace(BASE, detection_probability=1.0, slash_amount=40.0, n_steps=400)
    rows = run_learning_regime(cfg, "maintained")
    assert rate(rows) < 0.05
    assert rate(rows) < rate(run_learning_regime(cfg, "control")) / 3


def test_paired_streams():
    # identical exploration and opportunity streams: with slash 0 the flat and maintained regimes equal
    # control action for action (escalating regimes differ: their state includes the violation count k)
    cfg = replace(BASE, slash_amount=0.0)
    ctrl = run_learning_regime(cfg, "control")
    for regime in ("flat", "maintained"):
        assert [r["violations"] for r in run_learning_regime(cfg, regime)] == [r["violations"] for r in ctrl]


def test_helpers():
    assert gap_bucket(30, 1) == 9 and gap_bucket(1, 10) == 0 and gap_bucket(7, 1) == 2
    assert break_even_bond(replace(BASE, detection_probability=1.0)) == 14.0
    assert break_even_bond(BASE) == 21.5
    a = LearningAgent(stake=0)
    a.learn("s", 1, 4.0)
    a.learn("s", 1, 2.0)
    assert a.value("s", 1) == 3.0 and a.value("s", 0) == 0.0
    assert abs(myopic_rate(replace(BASE, detection_probability=1.0), 15.0, 200_000) - 0.197) < 0.005
