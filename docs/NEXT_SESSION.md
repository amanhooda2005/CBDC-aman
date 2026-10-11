# Next session: state and plan

Written 2026-10-11. All experiments are merged on `main` (PRs up to #27); no open PRs. The top-level `README.md` has the Final Results table.

## Where things stand

| | Verdict | Where |
|---|---|---|
| H1 | Partly supported: 10× latency only at 60 blocks; gas fails per dispute | `h1-zkopml-v2/README.md`, `WRITEUP.md` |
| H2 | Pre-registered FP criterion met, but federation detects worse (lower F1/recall); boundary channel hurts; DP kills utility with 3 institutions | `h2-federated-aml/README.md` (protocol v2) |
| H3 | Deterrence supported (agent-level p ≈ 1e-6); Sybil resistance fails | `h3-abt/README.md` |
| H4 | v3 passes all bounds incl. complete trained H1/H2/H3 inference (≤ 2× overhead); v2 missed 2×2 verify; v1 withdrawn | `h4-pqc-stark/README.md`, `docs/h4_*.md` |

## Two findings that would make the paper (proposed, not started)

### Finding A — one post-quantum proof for an entire CBDC transaction decision

This is the proposal's three-layer architecture as a single artifact. One STARK proves, for one transaction:
1. the H1 risk model's inference on the transaction's features;
2. the H3 agent's execute/refuse decision, with the H1 risk flag feeding the rule (e.g. treated as a policy-violation condition alongside amount > limit and sanctioned destination);
3. that the amount the rule compared is the amount inside the post-quantum SIS commitment (link the committed amount bits to the rule's amount input).

Why it matters: no prior work proves ML inference, agent policy, and a PQ-binding commitment together. The measured number is the overhead of the composed proof vs the same composition without the commitment, and vs three separate proofs.

Plan:
- Reuse the existing blocks (`h1mlp.rs`, `rule.rs`, `rlc.rs` SIS block).
- **Width limit:** Winterfell caps traces at 255 columns, and H1 (166) + H3 (108) + SIS (12) = 286 is too wide. Build a narrow single-decision rule that reuses one bit-decomposition column set across consecutive rows (one comparison per row), about 30 columns.
- Wiring: H1 logit → risk flag (logit > threshold, via a sign decomposition) → rule input. Amount: the rule's amount column equals Σ 2^t·bit_t of the SIS amount bits (one constraint at the row holding those bits).
- Tests: honest accept; forging the risk flag, the decision, or using a different amount than the committed one is rejected.
- Pre-register (bounds as before), then run 30 measured runs on an idle machine.

### Finding B — the false-positive criterion in federated AML is gameable; a calibration-controlled answer

H2 showed FedAvg "meets" the ≥20%-fewer-false-positives criterion only by flagging less: recall drops and F1 is lower. Turn that into a methodological finding with a fair test:
- Compare false positives **at matched recall**: FedAvg's FP at the threshold where it reaches the centralized model's recall, with thresholds set on validation.
- Report precision–recall curves per method.
- Plan: make `protocol_v2.py` also save per-node test and validation scores. Re-run `main_scarcity` only (10 seeds; ~2–3 h with 4 workers). Pre-register the matched-recall analysis first (one-sided Wilcoxon, Holm).
- Expected message: "FP-reduction targets for federated AML must be stated at fixed recall; otherwise a more conservative model satisfies them." Either outcome is publishable.

### Cheap extra (no new runs): an overhead law for PQ commitments in ZKML

Fit verify/prove time = classical + fixed SIS cost across the five measured workloads (2×2, dense, H1 MLP, H2 GCN, H3 rule) in `h4-pqc-stark/benchmark_v2/results/*.csv`. State it as: ratio ≈ 1 + c / workload cost. One plot plus one paragraph.

## Practical notes (things that bit us)

- Data: Elliptic CSVs from `https://data.pyg.org/datasets/elliptic/<name>.csv.zip`. Never commit them.
- Rust: `~/.cargo/bin` (rustup, stable). Use a short `CARGO_TARGET_DIR` (e.g. `C:\cbdc_t`), or MSVC linking fails with LNK1104 (path length).
- **Never switch branches in a working tree that a running experiment imports from.** Use `git worktree add`.
- Winterfell fork constraints: width ≤ 255; f23201 field is 23 bits. Use the sextic extension for security. Constant-only traces break the composition-degree check, so add a clock column.
- Lattice estimator: `docker run --rm -v "$PWD:/work" sagemath/sagemath:latest sage -python /work/est_h4.py`. Script in `h4-pqc-stark/docs/`.
- Discipline used throughout: commit parameters, bounds and analysis code **before** confirmatory runs; label anything post-hoc as such.
