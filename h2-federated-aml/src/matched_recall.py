"""Matched-recall false-positive analysis (pre-registered in experiments/PREREGISTRATION_matched_recall.md).

Reads results/v2_scores/scores_seed_*.npz written by `protocol_v2.py scores`.

Usage:
    python src/matched_recall.py --config experiments/configs/protocol_v2.yaml
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import precision_recall_curve

from data import RESULTS_DIR
from protocol_v2 import _holm, _paired

METHODS = ("centralized", "local_only", "fedavg", "fedavg_boundary")


def threshold_for_recall(scores: np.ndarray, labels: np.ndarray, target: float) -> float:
    """Largest threshold t whose recall (score >= t) on these nodes is at least `target`."""
    positives = np.sort(scores[labels == 1])[::-1]
    k = max(1, math.ceil(target * len(positives) - 1e-9))
    return float(positives[k - 1])


def operating_point(scores, labels, t):
    flagged = scores >= t
    tp, fp = int((flagged & (labels == 1)).sum()), int((flagged & (labels == 0)).sum())
    return {"threshold": t, "false_positives": fp, "recall": tp / max(int((labels == 1).sum()), 1),
            "precision": tp / max(tp + fp, 1)}


def per_seed(npz: Path, levels) -> list[dict]:
    z = np.load(npz); seed = int(npz.stem.split("_")[-1]); rows = []
    for s in levels:
        c_val = z[f"centralized@{s}@val"]
        target = float(((c_val >= 0.5) & (z["val_y"] == 1)).sum() / (z["val_y"] == 1).sum())
        for m in METHODS:
            val, test = z[f"{m}@{s}@val"], z[f"{m}@{s}@test"]
            row = {"seed": seed, "scarcity": s, "method": m, "target_recall_val": target}
            if target == 0:
                rows.append({**row, "evaluable": False}); continue
            matched = operating_point(test, z["test_y"], threshold_for_recall(val, z["val_y"], target))
            default = operating_point(test, z["test_y"], 0.5)
            rows.append({**row, "evaluable": True, **{f"matched_{k}": v for k, v in matched.items()},
                         **{f"default_{k}": v for k, v in default.items() if k != "threshold"}})
    return rows


def pr_figure(score_files, levels, out: Path) -> None:
    grid = np.linspace(0.01, 1.0, 100)
    fig, axes = plt.subplots(1, len(levels), figsize=(4.2 * len(levels), 3.8), sharey=True)
    for ax, s in zip(np.atleast_1d(axes), levels):
        for m in METHODS:
            curves, points = [], []
            for f in score_files:
                z = np.load(f); y, sc = z["test_y"], z[f"{m}@{s}@test"]
                prec, rec, _ = precision_recall_curve(y, sc)
                curves.append([prec[rec >= r].max() for r in grid])
                op = operating_point(sc, y, 0.5); points.append((op["recall"], op["precision"]))
            line, = ax.plot(grid, np.mean(curves, axis=0), label=m)
            r, p = np.mean(points, axis=0); ax.scatter([r], [p], color=line.get_color(), zorder=3)
        ax.set_title(f"label fraction {s}"); ax.set_xlabel("recall")
    np.atleast_1d(axes)[0].set_ylabel("precision (interpolated, mean over seeds)")
    np.atleast_1d(axes)[0].legend(fontsize=8)
    fig.suptitle("Test precision-recall per method; dots = default 0.5 threshold")
    fig.tight_layout(); fig.savefig(out, dpi=150)


def analyze(cfg: dict) -> dict:
    d = RESULTS_DIR / cfg["scores_subdir"]; files = sorted(d.glob("scores_seed_*.npz"))
    if not files: raise FileNotFoundError(f"no score files in {d}")
    levels = cfg["scarcity_levels"]
    rows = pd.DataFrame([r for f in files for r in per_seed(f, levels)])
    rows.to_csv(d / "matched_recall_per_seed.csv", index=False)
    ok = rows[rows.evaluable]

    def series(method, s, col):
        return ok[(ok.method == method) & (ok.scarcity == s)].set_index("seed")[col]

    primary = []
    for s in levels:
        fp_c, fp_f = series("centralized", s, "matched_false_positives"), series("fedavg", s, "matched_false_positives")
        n, mean_diff, p_less = _paired(fp_f, fp_c, "less")
        _, _, p_two = _paired(fp_f, fp_c, "two-sided")
        mc, mf = float(fp_c.mean()), float(fp_f.mean())
        dc, df = series("centralized", s, "default_false_positives"), series("fedavg", s, "default_false_positives")
        primary.append({"scarcity": s, "n_seeds": n,
                        "fp_centralized_matched": mc, "fp_fedavg_matched": mf,
                        "fp_reduction_matched": (mc - mf) / mc if mc > 0 else float("nan"),
                        "fp_reduction_default": (dc.mean() - df.mean()) / dc.mean() if dc.mean() > 0 else float("nan"),
                        "test_recall_centralized": float(series("centralized", s, "matched_recall").mean()),
                        "test_recall_fedavg": float(series("fedavg", s, "matched_recall").mean()),
                        "p_one_sided": p_less, "p_two_sided": p_two})
    for key, out in (("p_one_sided", "p_holm"), ("p_two_sided", "p_two_sided_holm")):
        adj = _holm([1.0 if math.isnan(r[key]) else r[key] for r in primary])
        for r, a in zip(primary, adj): r[out] = a
    crit = cfg["criterion"]
    for r in primary:
        r["criterion_met_matched"] = bool(r["fp_reduction_matched"] >= crit["min_fp_reduction"] and r["p_holm"] < crit["alpha"])
    pd.DataFrame(primary).to_csv(d / "matched_recall_primary.csv", index=False)

    descriptive = ok.groupby(["scarcity", "method"])[["matched_false_positives", "matched_recall", "matched_precision",
                                                       "default_false_positives", "default_recall"]].mean().reset_index()
    descriptive.to_csv(d / "matched_recall_by_method.csv", index=False)

    # Reproducibility check: rerun default-threshold numbers vs the committed v2 run.
    rerun = pd.concat([pd.read_csv(p) for p in sorted(d.glob("seed_*.csv"))])
    v2 = pd.read_csv(RESULTS_DIR / cfg["results_subdir"] / "all_results.csv")
    v2 = v2[v2.experiment == "main_scarcity"]
    j = rerun.merge(v2, on=["seed", "method", "scarcity"], suffixes=("_rerun", "_v2"))
    repro = {"pairs": len(j), "max_abs_f1_diff": float((j.f1_rerun - j.f1_v2).abs().max()),
             "identical_fp_fraction": float((j.false_positives_rerun == j.false_positives_v2).mean())}

    pr_figure(files, [1.0, 0.1, 0.01], d / "pr_curves.png")
    # A level counts as a failure of the criterion only if every seed was evaluable there.
    supported = sum(r["n_seeds"] == len(files) and not r["criterion_met_matched"] for r in primary)
    report = {"n_seeds": len(files), "levels_where_criterion_fails_at_matched_recall": supported,
              "finding_b_supported": supported >= 4, "reproducibility": repro, "primary": primary}
    (d / "matched_recall_report.json").write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--config", required=True); args = ap.parse_args()
    print(json.dumps(analyze(yaml.safe_load(open(args.config, encoding="utf-8"))), indent=2, default=float))
