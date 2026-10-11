# H2 matched-recall false-positive test — pre-registration

Committed before any run that produces the scores analysed here. Code: `src/protocol_v2.py scores` (rerun) and `src/matched_recall.py` (analysis). Config: `configs/protocol_v2.yaml` (unchanged except `scores_subdir`).

## Why

Protocol v2 found that FedAvg meets the original H2 criterion (≥ 20% fewer test false positives than the pooled centralized model, Holm p < 0.05) at all 6 scarcity levels. FedAvg also has lower recall and lower F1 at every level. A model that flags fewer transactions will have fewer false positives whether or not it is better.

This test asks the fair question: **does FedAvg have fewer false positives when both models catch the same share of illicit transactions?**

What we already know (disclosed): the v2 default-threshold results above. No per-node scores were saved in v2, so nothing about matched-recall behaviour has been looked at. A pipeline smoke test (2 rounds, 2 seeds) was run only to check that the code runs; its numbers were discarded.

## Rerun

- `main_scarcity` only: methods centralized, local_only, fedavg, fedavg_boundary (λ = 0.05); 6 scarcity levels; seeds 42–51; 3 institutions. Same training, budget and checkpoint selection as v2.
- New: each model's illicit-class softmax score on every validation and test node is saved (`results/v2_scores/scores_seed_<seed>.npz`).
- Reproducibility check (reported, not a pass/fail): default-threshold F1 and false positives of the rerun vs committed v2. If they differ, all matched-recall comparisons still use the rerun only, so both arms come from the same run.

## Operating point (fixed before the run)

For each seed and scarcity level:
1. Target recall r* = centralized model's **validation** recall at the default threshold 0.5.
2. For every method, threshold t = the largest threshold whose **validation** recall is ≥ r*. The centralized model gets its threshold the same way.
3. Apply t once to the **test** set; count false positives and recall.

Thresholds never see test labels. If r* = 0 at a seed/level, that pair is not evaluable and is dropped from that level.

## Primary test

At each scarcity level s ∈ {1.0, 0.5, 0.2, 0.1, 0.05, 0.01}, the original H2 criterion re-evaluated at matched recall:
- FP reduction = (mean FP_centralized − mean FP_fedavg) / mean FP_centralized at the matched thresholds.
- One-sided paired Wilcoxon over seeds, H1: FP_fedavg < FP_centralized. Holm across the 6 levels.
- Criterion met at s iff reduction ≥ 20% **and** Holm p < 0.05 (identical to v2).

**Finding B is supported** if the criterion fails at matched recall at **4 or more** of the 6 levels (only levels where all 10 seeds are evaluable count as failures). This would show the v2 pass came from flagging less, not from detecting better. If the criterion still holds at 3 or more levels, Finding B is not supported and the v2 FP result stands on firmer ground. Either outcome is reported.

## Secondary

- Two-sided paired Wilcoxon of FP_fedavg vs FP_centralized at matched recall, Holm across levels. This tells whether FedAvg is significantly *worse* at equal recall.
- Matching diagnostic: mean test recall of each method at its matched threshold (should be close to centralized's).

## Descriptive only

- Same matched-recall numbers for local_only and fedavg_boundary.
- Test precision–recall curves per method at s ∈ {1.0, 0.1, 0.01}, averaged over seeds, with the default-threshold point marked.
