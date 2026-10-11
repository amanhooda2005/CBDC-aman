"""Descriptive figures for H2 protocol v2 (reads results/v2/all_results.csv written by aggregate).

Usage: python src/figures_v2.py
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from data import RESULTS_DIR

METHODS = ("centralized", "local_only", "fedavg", "fedavg_boundary")


def _line(ax, main: pd.DataFrame, metric: str, ylabel: str) -> None:
    for method in METHODS:
        g = main[main.method == method].groupby("scarcity")[metric]
        mean, std = g.mean(), g.std()
        ax.errorbar(mean.index, mean.values, yerr=std.values, marker="o", capsize=3, label=method)
    ax.set_xscale("log")
    ax.set_xlabel("fraction of illicit training labels kept")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.3)


def main() -> None:
    out = RESULTS_DIR / "v2"
    results = pd.read_csv(out / "all_results.csv")
    main_rows = results[results.experiment == "main_scarcity"]
    figures = out / "figures"
    figures.mkdir(exist_ok=True)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    _line(axes[0], main_rows, "f1", "test F1")
    _line(axes[1], main_rows, "false_positives", "test false positives")
    _line(axes[2], main_rows, "recall", "test recall")
    axes[0].legend()
    fig.suptitle("H2 v2: mean ± sd over seeds")
    fig.tight_layout()
    fig.savefig(figures / "h2_v2_scarcity.png", dpi=160)
    plt.close(fig)

    privacy = results[results.experiment == "privacy_sweep"]
    if not privacy.empty:
        g = privacy.groupby("noise_multiplier").agg(epsilon=("epsilon", "first"), f1=("f1", "mean"), f1_sd=("f1", "std"))
        fig, ax = plt.subplots(figsize=(6, 4.2))
        ax.errorbar(g.epsilon, g.f1, yerr=g.f1_sd, marker="o", capsize=3)
        for z, row in g.iterrows():
            ax.annotate(f"z={z:g}", (row.epsilon, row.f1), textcoords="offset points", xytext=(5, 5))
        ax.set_xscale("log")
        ax.set_xlabel("institution-level epsilon (delta = 1e-5, 40 rounds)")
        ax.set_ylabel("test F1 (FedAvg, all labels)")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(figures / "h2_v2_privacy_tradeoff.png", dpi=160)
        plt.close(fig)
    print(f"wrote figures to {figures}")


if __name__ == "__main__":
    main()
