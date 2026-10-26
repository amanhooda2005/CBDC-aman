# Part 6 — Cross-cutting findings and the system picture

## 6.1 Findings for the paper (strongest first)

| ID | Finding | Status | Evidence |
|---|---|---|---|
| C1 | **One post-quantum proof for a whole CBDC transaction decision.** It covers H1 inference (private score), the risk flag, the H3 agent decision, and the amount bound to a lattice commitment (binding attack cost ≥ 2⁶¹⁵ quantum; this covers the commitment only, the whole proof is about 63 bits quantum, Part 6A). Overhead vs the same proof without PQ binding: 1.15× / 1.28× / 1.77×. A decision on an amount other than the committed one passes the classical proof and fails the PQ proof. | Confirmatory | §5.5 |
| C2 | **PQ binding in ZKML has a near-constant additive cost:** about +12 ms verify, +28 ms prove, +8.4 KB. The ratio ≈ 1 + c / classical falls as models grow; it predicted an unseen workload within 0.02× for size and prove time. | Post-hoc | §5.6 |
| C3 | **Slashing-based agent liability wears out and is Sybil-fragile; two protocol changes make it durable.** Stake maintenance (z = −13.1) and operator-bound history (z = −18.5) take violations from 50.7% to 2.0%. A registration bond blocks resets exactly at the derived break-even B* = 14. | Confirmatory | §4.5 |
| C4 | **False-positive targets for federated AML are gameable.** The ≥ 20% FP criterion holds at threshold 0.5 at every level but fails at matched recall at every evaluable level. With ample labels, federation makes 67–80% more false positives at equal recall. | Confirmatory | §3.5 |
| C5 | **Optimistic bisection for ZKML works mechanically but pays off only for deep models and rare disputes.** Faults are localized in ⌈log₂ n⌉ rounds (7/7). The ≥ 10× latency gain appears only at 60 blocks, and the gas gain only below a ≈ 0.8% dispute rate. | Confirmatory for mechanism; latency and gas as measured | §2 |

## 6.2 Negative and boundary results (part of the contribution)

- **H1:** a single dispute costs 1.8–2.4× more gas than monolithic verification. The latency target fails at ≤ 24 blocks. v1's layer-split partial proving was stuck at about 2.9× by the fixed proving floor.
- **H2:**
  - Federated F1 is below centralized and below local-only at every level.
  - The boundary channel hurts.
  - Institution-level DP with K = 3 has no usable operating point.
  - Five seeds cannot reach significance (v1).
- **H3:**
  - Flat stakes wear out.
  - The id-keyed registry is Sybil-breakable.
  - The action-level p-value is pseudo-replicated.
- **H4:**
  - The first lattice prototype was unsound, with 11–17-bit security.
  - v2 missed the verify bound on a trivial workload.
  - A PQ commitment cannot be made cheap for tiny statements, because the trace is padded to the SIS block's 128 rows.

## 6.3 How the layers compose

A CBDC transfer passes through:
1. a **risk model**: H1's MLP, or an H2 federated GCN;
2. an **autonomous agent** that decides whether to execute, under staked liability (H3);
3. **verification** of both, by optimistic bisection off-chain (H1) or by one STARK (H4 composed);
4. **post-quantum binding** of the transaction amount (H4).

The results say what each layer needs in order to work:

| Layer | Requirement |
|---|---|
| Detection | Federated detection needs recall-anchored targets, and more parties for DP to be affordable |
| Liability | Stake maintenance and operator-bound identity |
| Verification | Bisection for deep models and rare disputes; one composed STARK for full end-to-end binding |
| PQ cost | A roughly fixed add-on, cheapest relative to large models |
