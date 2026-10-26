# Next session: state and plan

Updated 2026-10-21. Everything is merged on `main` (PRs up to #39; Aman's TRL 4 integration work is PRs #36-#39, described in `docs/FULL_REPORT.md` §6A and `docs/EXHAUSTIVE_REPORT.md` Part 6A). Full write-up: `docs/FULL_REPORT.md`; exhaustive record: `docs/EXHAUSTIVE_REPORT.md` (built by `docs/exhaustive/build.py`; PDF on Desktop: CBDC_Exhaustive_Report.pdf) (PDF copy on Maanas's Desktop: CBDC_Full_Results_Report.pdf). No experiments are running and no PRs are open.

## Where things stand

| | Verdict | Where |
|---|---|---|
| H1 | Partly supported: 10× latency only at 60 blocks; gas fails per dispute | `h1-zkopml-v2/README.md`, `WRITEUP.md` |
| H2 | FP criterion met at the 0.5 threshold, but **fails at matched recall** (Finding B, pre-registered); F1 lower than centralized; boundary channel hurts; DP kills utility | `h2-federated-aml/README.md` |
| H3 | Deterrence supported; wears out + Sybil-fragile as designed; fixed by stake maintenance + operator-bound history (Finding C, PR #33) | `h3-abt/README.md` |
| H4 | v3 passes all bounds: 2×2, dense, full H1/H2/H3 models, and **one composed proof of H1 → risk → H3 → committed amount** (Finding A, 1.15×/1.28×/1.77×) | `h4-pqc-stark/README.md`, `docs/h4_*.md` |

The top-level `README.md` has the Final Results table and a "Cross-cutting findings" section (A, B, overhead law).

## Done this session

- **Overhead law** (PR #29, post-hoc): the PQ cost is roughly fixed (+12 ms verify, +28 ms prove, +8.4 KB). It predicted the composed workload's size and prove ratio within 0.02×, and underpredicted verify (1.61× predicted, 1.77× measured).
- **Finding B** (PR #30): at matched validation recall, the ≥ 20% FP-reduction criterion fails at all 5 fully evaluable levels. With 50–100% labels, FedAvg has 67–80% more false positives (Holm p = 0.012 and 0.049). Exception: at 10% labels, FedAvg has 80% fewer, but this is not significant after Holm (p = 0.070). The 1% level is not evaluable (5 of 10 seeds have centralized validation recall of 0).
- **Finding A** (PR #31): composed circuit `h4-pqc-stark/benchmark_v2/src/compose.rs`, amount link in `rlc.rs`, 20/20 tests, PASS.

## Open items

- ~~Export the independent-validator ledger~~ done in PR #41 (mentor decoded it; Besu re-execution by the reviewer only).
- A reviewer who is not a team member for the two-machine run.
- Independent re-run of the live Besu/EZKL runs and the timings (verified so far: records, ledger replay, dispute replay, tests).

## If more work is wanted (optional)

- **Finding A at scale:** batch several decisions per proof (needs a second trace segment or a wider-field fork), or bind H1's input to the transaction record.
- **H2:** report the matched-recall test for local-only and boundary as well (the numbers are already in `results/v2_scores/matched_recall_by_method.csv`, descriptive only).

## Practical notes (things that bit us)

- Data: Elliptic CSVs from `https://data.pyg.org/datasets/elliptic/<name>.csv.zip` into `h2-federated-aml/data/elliptic1/`, then `python src/data.py prepare`. Never commit them.
- An H2 `scores` seed takes about 1.5 h with 3 threads. 10 seeds with 4 workers took about 6 h.
- Rust: `~/.cargo/bin`. Use a short `CARGO_TARGET_DIR` (e.g. `C:\cbdc_t`), or MSVC linking fails with LNK1104. Tests run with `cargo test --release`: declared constraint degrees are upper bounds, and debug builds check exact degrees.
- **Never switch branches in a working tree that a running experiment imports from.** Use `git worktree add`.
- Winterfell fork: width ≤ 255; the f23201 field is 23 bits; use the sextic extension. Constant-only traces break the composition-degree check.
- Lattice estimator: `docker run --rm -v "$PWD:/work" sagemath/sagemath:latest sage -python /work/est_h4.py` (script in `h4-pqc-stark/docs/`).
- Discipline: commit parameters, bounds and analysis code **before** confirmatory runs; label post-hoc work as such.
