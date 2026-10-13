"""Durable-liability experiment (pre-registered in docs/PREREGISTRATION_durable.md).

Usage: python run_durable.py [configs/experiment.yaml]
Writes results/durable_actions.csv and results/durable_report.json.
"""
import csv
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from h3_abt.agent_level import mann_whitney_less, permutation_less  # noqa: E402
from h3_abt.config import load_config  # noqa: E402
from h3_abt.durable import REGIMES, break_even_bond, run_regime  # noqa: E402

SEEDS = range(43, 53)  # 10 seeds not used by the confirmatory run (seed 42)
LATE = (41, 50)  # last 10 of 50 steps
BONDS = [0.0, 5.0, 10.0, 14.0, 20.0, 40.0]
PERMUTATIONS = 100_000


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    out, running = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        out[i] = running
    return out


def agent_rates(rows, window=None):
    acts, viols = {}, {}
    for r in rows:
        if window and not (window[0] <= r["step"] <= window[1]):
            continue
        key = (r["seed"], r["agent"])
        acts[key] = acts.get(key, 0) + 1
        viols[key] = viols.get(key, 0) + r["is_violation"]
    return [viols[k] / acts[k] for k in sorted(acts)]


def compare(name, treat, ctrl):
    u, z, p = mann_whitney_less(treat, ctrl)
    return {"test": name, "n": len(treat), "mean_first": sum(treat) / len(treat), "mean_second": sum(ctrl) / len(ctrl),
            "mann_whitney_z": z, "p_one_sided": p, "permutation_p": permutation_less(list(treat), list(ctrl), PERMUTATIONS, 42)}


def main(path):
    base = load_config(path)
    rows = {r: [] for r in REGIMES}
    bond_rows = {b: [] for b in BONDS}
    for seed in SEEDS:
        cfg = replace(base, seed=seed)
        for r in REGIMES:
            rows[r] += run_regime(cfg, r)
        for b in BONDS:
            bond_rows[b] += run_regime(cfg, "escalating_id", bond=b)
    rates = {r: agent_rates(rows[r]) for r in REGIMES}
    late = {r: agent_rates(rows[r], LATE) for r in REGIMES}

    primary = [
        compare("T1 maintained < flat, steps 41-50", late["maintained"], late["flat"]),
        compare("T2 escalating_operator < escalating_id (B=0), all steps", rates["escalating_operator"], rates["escalating_id"]),
    ]
    for row, adj in zip(primary, holm([t["p_one_sided"] for t in primary])):
        row["p_holm"] = adj
    secondary = [
        compare("flat < control, all steps (replication)", rates["flat"], rates["control"]),
        compare("flat < control, steps 41-50", late["flat"], late["control"]),
        compare("maintained < control, steps 41-50", late["maintained"], late["control"]),
        compare("escalating_operator < maintained, all steps", rates["escalating_operator"], rates["maintained"]),
    ]
    windows = [(1, 10), (11, 20), (21, 30), (31, 40), (41, 50)]
    def overall(rs, w=None):
        sel = [r for r in rs if not w or w[0] <= r["step"] <= w[1]]
        return sum(r["is_violation"] for r in sel) / len(sel)
    descriptive = {
        r: {"rate": overall(rows[r]), "by_window": [overall(rows[r], w) for w in windows],
            "depleted_agents": len({(x["seed"], x["agent"]) for x in rows[r] if r != "control" and x["stake_before"] <= 0}),
            "resets": sum(x["reset"] for x in rows[r])}
        for r in REGIMES
    }
    bonds = [{"bond": b, "rate": overall(bond_rows[b]), "resets": sum(x["reset"] for x in bond_rows[b])} for b in BONDS]
    report = {"seeds": list(SEEDS), "agents_per_regime": len(rates["control"]), "late_window": LATE,
              "break_even_bond": break_even_bond(base), "primary": primary, "secondary": secondary,
              "descriptive": descriptive, "bond_sweep": bonds}

    out = Path(__file__).parent / "results"
    with open(out / "durable_actions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows["control"][0].keys()))
        w.writeheader()
        for r in REGIMES:
            w.writerows(rows[r])
    (out / "durable_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "configs/experiment.yaml")
