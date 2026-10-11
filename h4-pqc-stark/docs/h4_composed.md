# H4 composed proof: one transaction through the whole stack — pre-registration

Committed before any measured timing of this workload. Circuit: `benchmark_v2/src/compose.rs`, plus the amount link in `benchmark_v2/src/rlc.rs`. Tests: `cargo test --release` (20/20).

## What one proof shows

A single post-quantum STARK, one trace, proves all of the following for one CBDC transaction:

1. **H1 risk inference.** The full trained H1 model (`W32_B1`, same circuit as the coverage run) runs on one real Elliptic transaction. The logit stays **private**.
2. **Risk flag.** RISK = [logit > T_q], with T_q = 2474 being the H1 decision threshold. This is proven with a two-sided sign window. The test `composed_reference_and_risk_uniqueness` checks that the window is unique over the H1 circuit's whole interval-bounded logit range.
3. **H3 agent decision.** The H3 rule runs on one real decision, with RISK added as a policy-violation condition: VIOL = (amount > limit) OR (sanctioned destination) OR RISK. Otherwise its semantics are exactly those of `rule.rs`. The public outputs are EXEC and VIOL.
4. **Amount binding.** The amount the rule compares equals the amount inside the post-quantum SIS commitment: Σ 2ⁱ·bitᵢ over bits 0–15, with committed bits 16–63 equal to zero. The commitment and its opening are unchanged from v2/v3.

The instance is decision 1 of the H3 spec (amount 7558, compliant under the original rule) with the H1 example transaction (flagged, logit 11040). So the risk flag turns a compliant transfer into a violation. Both components are real; pairing them in one transaction is our construction, because Elliptic features are anonymized and contain no amount.

Narrow layout: H1 needs 166 columns, and the original 64-decision rule needs 108, so 286 columns would exceed Winterfell's 255 limit. The single-decision rule here keeps its values in 19 register columns, constant down the trace. One shared 22-bit window column set proves a different comparison or range check at each of 7 rows, selected by periodic columns, for 41 columns in total. Main trace widths: classical 207, PQ 219, both with 256 rows.

## Tests (all pass)

- Honest: classical and PQ AIR accept the trace (PQ with 3 values of γ), and the end-to-end proof verifies.
- Rejected forgeries:
  - a forged risk flag, both with the honest outputs and with outputs recomputed for the forged flag;
  - a wrong EXEC or VIOL;
  - a decision made on amount + 1 while committing to the true amount (the **classical circuit accepts this; the PQ circuit rejects it**);
  - a committed amount with a bit above 2¹⁶ set;
  - a register changed mid-trace.
- The end-to-end PQ proof with an uncommitted amount is rejected. Conjectured security is ≥ 120 bits.

## Measurement (fixed now)

- Systems: `classical` = same composed circuit with no commitment; `pq_sis_v3` = composed circuit + SIS opening (v3 RLC) + amount link.
- Protocol as before: same proof options; 3 warm-up runs, then 30 measured runs, interleaving classical and PQ each run; release build; nothing else running on the machine.
- Command: `cargo run --release -- 30 composed > results/benchmark_composed.csv`, then `python analyze.py results/benchmark_composed.csv`.
- **Bounds (unchanged since 2026-10-06):** mean PQ/classical proof size ≤ 20×, prove time ≤ 200×, verify time ≤ 10×. Pass iff all three hold.
- Prediction from the overhead law (`results/overhead_law.md`, post-hoc): the ratio is about 1 + c/classical, with c ≈ 12 ms verify, ≈ 28 ms prove, ≈ 8.4 KB size, plus the link's one column and three constraints. This is reported next to the measured ratio, as a check of the law on a workload it was not fitted on.
- Descriptive only: the marginal cost of adding the decision and the link on top of H1-only inference (`h1_mlp_full` rows in `benchmark_coverage.csv`). These are different runs, so they are not paired.

Disclosure: one unmeasured smoke run (`-- 1 composed`) was made while another experiment was loading the machine, only to check that the binary runs. Its numbers are not used.

## Limitations

- One transaction and one decision per proof. Batching decisions would need the original multi-row rule, which does not fit next to H1 within 255 columns.
- H1's input features are not tied to the amount. Elliptic's features are anonymized, so no feature is the amount.
- The SIS binding is statistical, and the PQ security assumptions are those of `h4_v2_security.md`. The STARK itself is hash-based.
