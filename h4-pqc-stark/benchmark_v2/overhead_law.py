"""Post-hoc analysis (no new runs): does the v3 post-quantum overhead behave like a
fixed additive cost, so that ratio = 1 + c / classical_cost?

Inputs: the measured v3 rows in results/benchmark_v3.csv (2x2 matmul, dense 165x32)
and results/benchmark_coverage.csv (full H1 MLP, H2 GCN node, H3 rule x64).
Outputs: results/overhead_law.{csv,md,png}.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

R = Path(__file__).parent / "results"
METRICS = [("verify_ms", "verify time (ms)"), ("prove_ms", "prove time (ms)"), ("proof_bytes", "proof size (bytes)")]

d = pd.concat([pd.read_csv(R / "benchmark_v3.csv"), pd.read_csv(R / "benchmark_coverage.csv")])
m = d.groupby(["workload", "system"])[[k for k, _ in METRICS]].mean().unstack("system")

rows, fits = [], {}
for k, _ in METRICS:
    cls, pq = m[(k, "classical")], m[(k, "pq_sis_v3")]
    delta = pq - cls
    c = delta.mean()                      # one-parameter law: pq = classical + c
    a, b = np.polyfit(cls, pq, 1)         # two-parameter check: pq = a*classical + b
    pred = cls + c
    ss_res = ((pq - pred) ** 2).sum()
    r2 = 1 - ss_res / ((pq - pq.mean()) ** 2).sum()
    fits[k] = (c, delta.std(ddof=1), a, b, r2)
    for w in cls.index:
        rows.append({"metric": k, "workload": w, "classical": cls[w], "pq_sis_v3": pq[w],
                     "delta": delta[w], "ratio_observed": pq[w] / cls[w],
                     "ratio_law": 1 + c / cls[w]})

out = pd.DataFrame(rows)
out.to_csv(R / "overhead_law.csv", index=False, float_format="%.4f")

lines = ["# Post-quantum overhead law (post-hoc, no new runs)", "",
         "Law: `pq = classical + c`, so `ratio = 1 + c / classical`. "
         "`c` is the mean added cost over the 5 workloads; the two-parameter fit "
         "`pq = a*classical + b` checks whether the overhead is additive (a near 1) or multiplicative.", "",
         "| metric | c (mean added cost) | sd across workloads | a | b | R^2 of 1-param law |",
         "|---|---|---|---|---|---|"]
for k, _ in METRICS:
    c, sd, a, b, r2 = fits[k]
    lines.append(f"| {k} | {c:.1f} | {sd:.1f} | {a:.3f} | {b:.1f} | {r2:.4f} |")
lines += ["", "| metric | workload | classical | pq_sis_v3 | added | ratio observed | ratio by law |",
          "|---|---|---|---|---|---|---|"]
for r in out.itertuples():
    lines.append(f"| {r.metric} | {r.workload} | {r.classical:.1f} | {r.pq_sis_v3:.1f} | "
                 f"{r.delta:.1f} | {r.ratio_observed:.2f} | {r.ratio_law:.2f} |")
rows_of = d.groupby(["workload", "system"]).trace_rows.first().unstack("system")
padded = rows_of.index[rows_of.pq_sis_v3 > rows_of.classical].tolist()
same = out[(out.metric == "proof_bytes") & ~out.workload.isin(padded)]
lines += ["", "## Note on proof size",
          f"Workloads whose PQ trace is longer than their classical trace ({', '.join(padded)}) "
          "pay a trace-length floor: the SIS block needs its own rows, so a tiny statement is padded up. "
          f"Excluding them, the added size is {same.delta.min():.0f}-{same.delta.max():.0f} bytes "
          f"(mean {same.delta.mean():.0f}), i.e. a ratio of {same.ratio_observed.min():.2f}-"
          f"{same.ratio_observed.max():.2f}."]
(R / "overhead_law.md").write_text("\n".join(lines) + "\n")

fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
for ax, (k, label) in zip(axes, METRICS):
    c = fits[k][0]
    sub = out[out.metric == k]
    xs = np.geomspace(sub.classical.min() * 0.7, sub.classical.max() * 3, 200)
    ax.plot(xs, 1 + c / xs, "k--", lw=1, label=f"1 + {c:.3g}/x")
    ax.scatter(sub.classical, sub.ratio_observed, zorder=3)
    for r in sub.itertuples():
        ax.annotate(r.workload, (r.classical, r.ratio_observed), fontsize=7,
                    xytext=(4, 4), textcoords="offset points")
    ax.set_xscale("log")
    ax.set_xlabel(f"classical {label}")
    ax.set_ylabel("PQ / classical")
    ax.legend(fontsize=8)
fig.suptitle("v3 post-quantum overhead is a near-constant additive cost (post-hoc)")
fig.tight_layout()
fig.savefig(R / "overhead_law.png", dpi=150)
print((R / "overhead_law.md").read_text())
