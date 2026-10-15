"""Learning agents under imperfect detection (pre-registered in docs/PREREGISTRATION_learning.md).

Usage: python run_learning.py [configs/experiment.yaml]
Writes results/learning_agents.csv, results/learning_report.json, results/learning_figure.png.
"""
import csv
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from h3_abt.agent_level import mann_whitney_less, permutation_less  # noqa: E402
from h3_abt.config import load_config  # noqa: E402
from h3_abt.learning import REGIMES, WINDOW, break_even_bond, myopic_rate, run_learning_regime  # noqa: E402

SEEDS = range(53, 63)
P_VALUES = (0.5, 0.8)
N_STEPS = 500
LATE_WINDOW = N_STEPS // WINDOW - 1   # steps 451-500
PERMUTATIONS = 100_000


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    out, running = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        out[i] = running
    return out


def rates(rows, window=None):
    if window is None:
        return [sum(r["violations"]) / sum(r["actions"]) for r in rows]
    return [r["violations"][window] / r["actions"][window] for r in rows]


def compare(name, treat, ctrl):
    u, z, p = mann_whitney_less(treat, ctrl)
    return {"test": name, "n": len(treat), "mean_first": sum(treat) / len(treat), "mean_second": sum(ctrl) / len(ctrl),
            "mann_whitney_z": z, "p_one_sided": p, "permutation_p": permutation_less(list(treat), list(ctrl), PERMUTATIONS, 42)}


def main(path):
    base = replace(load_config(path), n_steps=N_STEPS)
    out = {"design": {"seeds": list(SEEDS), "steps": N_STEPS, "agents_per_seed": base.n_staked, "p_values": P_VALUES,
                      "epsilon": 0.1, "window": WINDOW}, "by_p": {}}
    all_rows = []
    primary = []
    for p in P_VALUES:
        cfg_p = replace(base, detection_probability=p)
        rows = {r: [] for r in REGIMES}
        b_star = break_even_bond(cfg_p)
        bonds = [0.0, float(math.ceil(b_star)), float(2 * math.ceil(b_star))]
        bond_rows = {b: [] for b in bonds}
        for seed in SEEDS:
            cfg = replace(cfg_p, seed=seed)
            for r in REGIMES:
                rows[r] += run_learning_regime(cfg, r)
            for b in bonds:
                bond_rows[b] += run_learning_regime(cfg, "escalating_id", bond=b)
        for r in REGIMES:
            all_rows += rows[r]
        allr = {r: rates(rows[r]) for r in REGIMES}
        late = {r: rates(rows[r], LATE_WINDOW) for r in REGIMES}
        primary.append(compare(f"T1(p={p}) maintained < flat, steps 451-500", late["maintained"], late["flat"]))
        primary.append(compare(f"T2(p={p}) escalating_operator < escalating_id (B=0), all steps",
                               allr["escalating_operator"], allr["escalating_id"]))
        secondary = [
            compare(f"maintained < control, all steps (p={p})", allr["maintained"], allr["control"]),
            compare(f"flat < control, steps 451-500 (p={p})", late["flat"], late["control"]),
        ]

        def pooled(rs, w=None):
            if w is None:
                return sum(sum(r["violations"]) for r in rs) / sum(sum(r["actions"]) for r in rs)
            return sum(r["violations"][w] for r in rs) / sum(r["actions"][w] for r in rs)

        n_w = N_STEPS // WINDOW
        out["by_p"][str(p)] = {
            "secondary": secondary,
            "descriptive": {r: {"rate": pooled(rows[r]), "by_window": [pooled(rows[r], w) for w in range(n_w)],
                                "resets": sum(x["resets"] for x in rows[r]),
                                "agents_depleted": sum(x["slashed"] >= base.stake_amount - 1e-9 for x in rows[r]) if r == "flat" else 0,
                                "slashed_per_agent": sum(x["slashed"] for x in rows[r]) / len(rows[r])} for r in REGIMES},
            "bond_sweep": {"break_even": b_star,
                           "rows": [{"bond": b, "rate": pooled(bond_rows[b]), "resets": sum(x["resets"] for x in bond_rows[b]),
                                     "exploration_only_resets_expected": round(0.1 / 3 * N_STEPS * base.n_staked * len(SEEDS))} for b in bonds]},
            "myopic_best_response": {"control": myopic_rate(cfg_p, 0.0), "maintained": myopic_rate(cfg_p, base.slash_amount)},
            "capital_topped_up_per_agent": {r: sum(x["capital_topped_up"] for x in rows[r]) / len(rows[r]) for r in REGIMES},
        }
    for row, adj in zip(primary, holm([t["p_one_sided"] for t in primary])):
        row["p_holm"] = adj
        row["supported"] = adj < 0.05
    out["primary"] = primary
    out["finding_supported"] = all(t["supported"] for t in primary)
    Path("results").mkdir(exist_ok=True)
    json.dump(out, open("results/learning_report.json", "w"), indent=2)
    with open("results/learning_agents.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["p", "regime", "seed", "agent", "violations_total", "actions_total", "late_window_violations",
                    "resets", "slashed", "capital_topped_up"] + [f"viol_w{i}" for i in range(N_STEPS // WINDOW)])
        for r in all_rows:
            w.writerow([r["p"], r["regime"], r["seed"], r["agent"], sum(r["violations"]), sum(r["actions"]),
                        r["violations"][LATE_WINDOW], r["resets"], round(r["slashed"], 3), round(r["capital_topped_up"], 3)]
                       + r["violations"])
    figure(out)
    print(json.dumps({"primary": primary, "finding_supported": out["finding_supported"]}, indent=1))


def figure(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, len(out["by_p"]), figsize=(11, 3.8), sharey=True)
    colors = {"control": "#777777", "flat": "#d95f02", "maintained": "#1b9e77", "escalating_id": "#7570b3", "escalating_operator": "#e7298a"}
    for ax, (p, d) in zip(axes, out["by_p"].items()):
        for r, s in d["descriptive"].items():
            xs = [WINDOW * (i + 1) for i in range(len(s["by_window"]))]
            ax.plot(xs, s["by_window"], marker="o", ms=3, label=r, color=colors[r])
        ax.set_title(f"learning agents, detection p = {p}")
        ax.set_xlabel("step (end of 50-step window)")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("violation rate")
    axes[-1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig("results/learning_figure.png", dpi=150)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "configs/experiment.yaml")
