# H2: Federated AML Detection Under Label Scarcity

This reproducible research prototype tests—not assumes—the proposition that per-institution scarcity of labelled illicit examples harms federated AML detection and that cross-institution boundary embeddings can mitigate that harm without sharing raw graphs or raw features.

It follows the progression from centralized Elliptic1 GCN AML (Weber et al., 2019), through structural graph context (Bellei et al., 2024), to the cross-institution setting represented by FedGraph-VASP. The gap tested here is scarce local illicit supervision in a federated graph setting.

## Final result (protocol v2)

Pre-registration: [`experiments/PREREGISTRATION_v2.md`](experiments/PREREGISTRATION_v2.md) (committed before any v2 run). 10 seeds, matched 200-epoch budget, validation-based checkpoint selection for every method, pooled centralized baseline on the **identical** scarce label mask, K = 3 institutions. Results: [`results/v2/`](results/v2/). Figures: [`results/v2/figures/`](results/v2/figures/).

Reproduce: `python src/protocol_v2.py run --config experiments/configs/protocol_v2.yaml --seed <s>` for s = 42…51, then `python src/protocol_v2.py aggregate --config experiments/configs/protocol_v2.yaml` and `python src/figures_v2.py`.

### Primary test (original H2): FedAvg vs centralized false positives

One-sided paired Wilcoxon over 10 seeds, Holm across 6 levels. Criterion: FP reduction ≥ 20% and Holm p < 0.05.

| Labels kept | FP centralized | FP FedAvg | FP reduction | Holm p | Criterion |
|---|---|---|---|---|---|
| 100% | 113.7 | 62.3 | 45% | 0.025 | met |
| 50% | 52.7 | 9.5 | 82% | 0.006 | met |
| 20% | 27.1 | 9.0 | 67% | 0.031 | met |
| 10% | 504.3 | 0.5 | 99.9% | 0.008 | met |
| 5% | 500.3 | 24.7 | 95% | 0.012 | met |
| 1% | 515.9 | 26.5 | 95% | 0.006 | met |

### Why this does not mean federation detects laundering better

Same runs, two-sided paired Wilcoxon (secondary, pre-registered as exploratory):

| Labels kept | F1 centralized | F1 FedAvg | ΔF1 (p) | Recall centralized | Recall FedAvg | ΔPR-AUC (p) |
|---|---|---|---|---|---|---|
| 100% | 0.757 | 0.649 | −0.109 (0.002) | 0.712 | 0.526 | −0.034 (0.014) |
| 50% | 0.705 | 0.517 | −0.188 (0.002) | 0.589 | 0.354 | −0.021 (0.014) |
| 20% | 0.495 | 0.314 | −0.181 (0.004) | 0.346 | 0.189 | −0.026 (0.23) |
| 10% | 0.324 | 0.148 | −0.177 (0.002) | 0.331 | 0.080 | +0.164 (0.56) |
| 5% | 0.166 | 0.041 | −0.125 (0.002) | 0.227 | 0.024 | +0.078 (1.0) |
| 1% | 0.096 | 0.030 | −0.066 (0.031) | 0.190 | 0.019 | +0.031 (0.010) |

- FedAvg has **significantly lower F1 at every level** and much lower recall. Its false-positive reduction comes from flagging fewer transactions at the fixed 0.5 threshold, not from better discrimination. A model that flags nothing has zero false positives.
- Threshold-free ranking (PR-AUC) is significantly **worse** for FedAvg with 50–100% labels, and not different at 5–20%.
- With ≤ 10% labels the centralized model is miscalibrated and over-flags (≈500 FP), which inflates the FP reduction. At 1% labels, centralized selected its epoch-1 checkpoint in 8 of 10 seeds (validation F1 stayed at 0).
- FedAvg is also significantly below **local-only** training on F1 at every level.

**Verdict, original H2:** the pre-registered false-positive criterion is met at all six levels, but federation does **not** improve AML detection. The criterion as written can be satisfied by a more conservative classifier, so it should not be used alone. Report it with F1, recall and PR-AUC.

### Matched-recall test: the false-positive criterion is gameable (Finding B, pre-registered)

Pre-registration: [`experiments/PREREGISTRATION_matched_recall.md`](experiments/PREREGISTRATION_matched_recall.md), committed before the rerun. We reran `main_scarcity` for 10 seeds and saved per-node scores. The rerun reproduces v2: test false positives were identical in all 240 seed × method × level cases, and F1 differed by at most 0.017.

**Fair comparison.** Both models must catch the same share of illicit transactions:
- The target recall is the centralized model's validation recall at the 0.5 threshold.
- Each method uses the largest threshold that reaches that recall on **validation**.
- Test false positives are counted once, at that threshold.

| Labels kept | FP reduction at 0.5 (v2 criterion) | FP centralized, matched | FP FedAvg, matched | FP reduction, matched | Holm p (one-sided) | Criterion at matched recall | FedAvg worse? Holm p (two-sided) |
|---|---|---|---|---|---|---|---|
| 100% | 45% | 112.9 | 203.7 | −80% | 1.0 | **fails** | yes, 0.012 |
| 50% | 82% | 52.7 | 87.8 | −67% | 1.0 | **fails** | yes, 0.049 |
| 20% | 67% | 27.0 | 51.6 | −91% | 1.0 | **fails** | n.s. (0.78) |
| 10% | 99.9% | 503.5 | 100.5 | +80% | 0.070 | **fails** (n.s.) | — |
| 5% | 95% | 499.4 | 401.6 | +20% | 1.0 | **fails** | n.s. |
| 1% | 95% | 993.8 | 1135.0 | −14% | 1.0 | not met; only 5/10 seeds evaluable* | n.s. |

\*In 5 seeds the centralized model caught no illicit validation transactions at 1% labels, so no recall could be matched. Under the pre-registered rule, this level does not count toward the verdict.

Matching worked: mean test recall at the matched threshold differs by at most 0.009 between the two models at every level.

- **Pre-registered verdict: Finding B supported.** The criterion that passed at all 6 levels at the default threshold fails at matched recall at all 5 fully evaluable levels.
- **With plenty of labels (50–100%), FedAvg makes significantly *more* false positives** than the centralized model when both catch the same share of laundering: 67–80% more.
- **One honest exception:** at 10% labels FedAvg's whole precision–recall curve sits above centralized (figure). At matched recall it has 80% fewer false positives (unadjusted p = 0.012), but this does not survive Holm correction (p = 0.070). In this regime the pooled model trained on very few labels is the weak one.
- **Methodological message:** a false-positive reduction target for federated AML must be stated at a fixed recall (or as a full precision–recall curve). Otherwise a model that simply flags less will meet it.

Outputs, in [`results/v2_scores/`](results/v2_scores/):
- `matched_recall_primary.csv`, `matched_recall_by_method.csv`, `matched_recall_per_seed.csv`, `matched_recall_report.json`;
- per-node scores, `scores_seed_*.npz`;
- figure: [`pr_curves.png`](results/v2_scores/pr_curves.png).

Reproduce:
- `python src/protocol_v2.py scores --config experiments/configs/protocol_v2.yaml --seed <s>` for s = 42…51 (about 1.5 h per seed with 3 threads; we ran 4 in parallel);
- then `python src/matched_recall.py --config experiments/configs/protocol_v2.yaml`.

### Refined H2 (boundary channel mitigates scarcity): not supported

The boundary-alignment channel (λ = 0.05) **lowers** F1 versus plain FedAvg at 5 of 6 levels (p ≤ 0.037), and never reduces degradation in a meaningful way. The only nominally significant "mitigation" (1% labels, p = 0.019) is a floor effect: both methods score F1 = 0.030. See `results/v2/h2_refined_boundary_mitigation.csv`.

### Privacy (institution-level DP)

Clip C = 1.5, Gaussian noise per institution per round, 40 rounds, δ = 1e-5, RDP accounting (`src/dp_accounting.py`). All labels, boundary channel off.

| Noise multiplier z | ε | F1 (mean ± sd) | PR-AUC | False positives |
|---|---|---|---|---|
| 0 (no DP, FedAvg) | — | 0.649 | 0.782 | 62.3 |
| 0.001 | 2.0 × 10⁷ | 0.627 ± 0.018 | 0.792 | 32.1 |
| 0.01 | 2.0 × 10⁵ | 0.653 ± 0.028 | 0.786 | 47.0 |
| 0.1 | 2.3 × 10³ | 0.527 ± 0.038 | 0.495 | 511.5 |
| 1.0 | 50.3 | 0.287 ± 0.046 | 0.164 | 1810.7 |

With three institutions, the noise is averaged over only three updates. No tested setting gives both meaningful privacy and usable detection. At ε ≈ 50 (already weak), F1 drops from 0.65 to 0.29. The guarantee is institution-level, model-update channel only. It does not cover individual transactions, the boundary channel, or FedAvg sample-count weights.

### Limitations

- Institutions are simulated by graph partitioning of Elliptic1; no real VASP ownership.
- One dataset, one architecture (2-layer GCN), K = 3 for the main sweep.
- Decision threshold fixed at 0.5 for all methods. FP comparisons depend on it; F1 and PR-AUC are reported for that reason.
- v1 results (`results/*.csv`, 5 seeds) are superseded: 5 seeds cannot reach p < 0.05 with a two-sided paired Wilcoxon (minimum p = 0.0625), and v1 trained methods for unequal budgets.

## Data and labels

The project uses the supplied Elliptic1 files. Transaction IDs are mapped once to contiguous node indices. Labels are `1` illicit, `0` licit and `-1` unknown. Unknown nodes are **never** cast as licit labels, included in supervised loss, or included in evaluation. The raw dataset is excluded from Git.

## Local environment

Use the existing project environment already configured in the local VS Code terminal. This repository does not require an environment rebuild to run the experiments below.

## Run order

Run commands from the project root:

```powershell
python src/main.py inspect-data
python src/main.py prepare-data
python src/main.py centralized
python src/main.py federated --institutions 3 --partition graph_aware
pytest -q tests
python src/main.py full-experiment --config experiments/configs/default.yaml
```

`python src/main.py full-experiment --config experiments/configs/default.yaml` is the complete H2 command. It runs the K=3 Local-only/FedAvg/FedAvg+Boundary scarcity comparison; the six-value lambda ablation at fixed 10% scarcity; and the K=2/3/5 method comparison at fixed 10% scarcity, for every seed listed in the YAML `seeds` array. It writes only measured rows, plus `scarcity_summary.csv` (mean/sample standard deviation) and `paired_significance.csv` (matched-seed two-sided Wilcoxon signed-rank tests).

## Experimental design

The labelled nodes have one deterministic stratified 70/15/15 train/validation/test split. That split and all test labels remain fixed across methods and scarcity levels. Scarcity applies only to illicit *training* labels at 100%, 50%, 20%, 10%, 5%, and 1%; licit training labels and all validation/test labels are unchanged. Metrics are accuracy, precision, recall, F1, PR-AUC, ROC-AUC, and a confusion matrix. F1 and PR-AUC should be primary given the imbalance.

Elliptic1 has no real VASP ownership. Institutions are therefore explicitly **simulated**, not claimed to be real. The default graph-aware partition uses deterministic neighbour-vote propagation; `random` is a baseline. K=2, 3, and 5 are supported. Per-institution node, label, illicit, licit, boundary, and cross-edge statistics are saved.

Local GCNs train on each institution's induced graph. FedAvg aggregates only model parameters, weighted by local supervised sample count. The centralized architecture remains the supplied two-layer GCN (default hidden size 64).

## Boundary signal and identity simulation

A boundary node is an endpoint of a cross-institution edge. For each such edge, the simulation records the pair of globally unique, contiguous node indices `(local endpoint, foreign endpoint)`. This is the explicit assumed shared identity channel; two copied nodes are not silently treated as naturally identifiable. These IDs are a simulation-specific matching token, not an assertion that real VASPs can exchange raw transaction identifiers.

After local training, an institution exports only `{boundary node ID, learned embedding}`. For an outgoing boundary edge, the receiving party matches the foreign endpoint ID and applies `1 - cosine_similarity(h_local, h_foreign)`. The foreign embedding is detached and no foreign illicit/licit label is in this objective. The mechanism is therefore a **label-free cross-institution signal**, embedded in an otherwise weakly supervised system because each local classifier uses its own labels.

In a real VASP deployment, an agreed privacy-preserving counterparty/transfer identifier and authenticated exchange would be required; raw on-chain graph access and simulated global IDs cannot be assumed.

## Privacy scope and limitations

`boundary.py` validates the research payload shape and prevents this code path from carrying raw features or neighbourhoods. The simulation exchanges model updates and learned boundary embeddings; optional update perturbation is described below. It does **not** implement secure aggregation, encryption, a privacy proof, or production authentication.

Optional institution-level update perturbation is available via `privacy.enabled` in the experiment config, or `--institution-privacy` for `src/main.py federated`. Each institution's whole model delta is jointly L2-clipped and Gaussian-noised before weighted FedAvg, and boundary embedding vectors are separately clipped/noised before export. This is institution-level update privacy engineering, not transaction-level differential privacy. The simulated boundary IDs and sample counts used as aggregation weights remain visible; model outputs and metrics remain observable. There is no accountant or calibrated end-to-end epsilon/delta, secure aggregation, encryption, or formal privacy guarantee. Configure `clip_norm` and `noise_multiplier` explicitly; do not claim transaction-level privacy or a quantified DP guarantee. *Update (protocol v2): `src/dp_accounting.py` now provides RDP accounting (institution-level, model-update channel, full participation, δ = 1e-5) and the v2 privacy sweep reports ε; see the protocol v2 results in `README.md`. The boundary-embedding channel and FedAvg weights remain unaccounted.*

## Status and recorded findings (v1, superseded by protocol v2 above)

*Single-seed v1 numbers, kept for the record. Use the protocol v2 results above.*

Committed results: `results/*.csv`, seed 42 only, K=3 unless stated.

| Setting | Centralized GCN | Local-only | FedAvg | FedAvg + boundary |
|---|---|---|---|---|
| 100% labels, F1 | 0.743 | 0.499 | 0.401 | 0.402 |
| 100% labels, false positives | 114 | 323 | 1394 | 1391 |
| 20% labels, F1 | not run | 0.214 | 0.374 | 0.374 |
| 10% labels, F1 | not run | 0.009 | 0.147 | 0.128 |

What these show:
- At full labels, FedAvg produces about 12× the false positives of the centralized model and a lower F1. Federation costs accuracy here.
- The "federation degrades less under scarcity" pattern is partly an artifact. Federated F1 starts lower, so its drop is smaller. It is not evidence that federation beats the baseline.
- The boundary channel does not help. Differences from FedAvg are within ±0.02 and change sign across scarcity levels. Raising lambda from 0 to 1.0 lowers F1 monotonically (0.147 → 0.012).

**Original H2 criterion not yet tested at v1** (it was tested in protocol v2, see "Final result (protocol v2)" above). It requires a federated vs centralized comparison under each scarcity level, ≥20% fewer false positives, multiple seeds, and a significance test. Missing:
1. Centralized GCN run at each scarcity level (only the 100% run exists).
2. Multiple seeds (all results are seed 42).
3. A significance test on false-positive counts.
4. Differential privacy. The code does not implement DP, so the ε bound in the original hypothesis is unmeasured.

## Interpreting H2

For every measured scarcity level, compare local-only, FedAvg, and FedAvg + boundary alignment. Degradation is `metric(100%) - metric(level)`; positive mitigation is `degradation(FedAvg) - degradation(boundary)`. Run multiple seeds and report variation before drawing an inference. A positive mitigation alone is not proof: the result may support, partially support, fail to support, or falsify H2 under the tested conditions. Do not change the evaluation set or tune against a desired conclusion.
