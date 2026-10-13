"""Durable-liability simulation checks. Uses only seed 42 (already-published confirmatory seed)."""
from h3_abt.config import load_config
from h3_abt.durable import break_even_bond, run_regime

CFG = load_config("configs/experiment.yaml")


def _violations(rows):
    return sum(r["is_violation"] for r in rows)


def test_flat_and_control_reproduce_the_confirmatory_run():
    assert _violations(run_regime(CFG, "control")) == 542
    assert _violations(run_regime(CFG, "flat")) == 319


def test_maintained_stake_always_covers_the_slash():
    assert all(r["stake_before"] >= CFG.slash_amount for r in run_regime(CFG, "maintained"))


def test_flat_agents_become_judgment_proof():
    assert any(r["stake_before"] <= 0 for r in run_regime(CFG, "flat"))


def test_free_identity_reset_erases_escalation():
    free = run_regime(CFG, "escalating_id", bond=0.0)
    maintained = run_regime(CFG, "maintained")
    assert [r["is_violation"] for r in free] == [r["is_violation"] for r in maintained]
    assert sum(r["reset"] for r in free) > 0


def test_operator_binding_makes_resets_pointless():
    rows = run_regime(CFG, "escalating_operator", bond=0.0)
    assert sum(r["reset"] for r in rows) == 0
    assert _violations(rows) < _violations(run_regime(CFG, "escalating_id", bond=0.0))


def test_bond_at_break_even_blocks_every_reset():
    b = break_even_bond(CFG)
    assert b == CFG.violation_payoff_max - CFG.compliant_payoff_min - CFG.slash_amount
    assert sum(r["reset"] for r in run_regime(CFG, "escalating_id", bond=b)) == 0
