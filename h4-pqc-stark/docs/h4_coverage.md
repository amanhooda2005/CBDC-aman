# H4 coverage of the H1–H3 model classes — pre-registration and model fidelity

Committed before any timing of these workloads. Circuits: `benchmark_v2/src/h1mlp.rs`, `gcn.rs`, `rule.rs`. Specs: `benchmark_v2/specs/`. Generation scripts: [`../coverage/`](../coverage/README.md).

## What is proven

Each workload proves **complete, exact integer inference** of the actual trained model or rule. These are not timing stand-ins:

| Workload | What the circuit proves | Public output |
|---|---|---|
| `h1_mlp_full` | The trained H1 model `W32_B1`, all layers: 165→32 projection + ReLU, residual block 32→32 + ReLU, head 32→1. One real illicit test transaction. | logit (decision = logit > public threshold) |
| `h2_gcn_node` | The trained H2 centralized GCN (GCNConv 166→64 + ReLU, GCNConv 64→2) for Elliptic test node 36333 (illicit, predicted illicit), over its real receptive field: 3 transactions, PyG normalized adjacency, directed in-edges. | 2 class logits |
| `h3_rule_x64` | H3's `choose_action` + `is_violation` for 64 real decisions of the staked treatment run (confirmatory config, seed 42). | action and violation flag of each decision |

Design properties enforced in the AIR, all covered by tests:
- **Private inputs are range-checked** to the declared input box, so a prover cannot exploit field wrap-around.
- **Every requantization (floor division) and ReLU is proven.** Quotient and remainder windows total ≤ 2²² < p, so the decomposition is unique.
- **No intermediate value can wrap.** Worst-case accumulators over the whole input box were bounded by interval propagation, and all stay below (p − 1)/2.
- **Rejected by the AIR:** a wrong public output; an out-of-range input even when the claimed output matches it; a tampered quotient bit; a forged ReLU output; a forged violation flag. Accepted: honest traces (classical, and v3 with three γ values).

## Fidelity of the proven integer models

Quantization was chosen without touching test data. H1 uses fixed power-of-two scales maximized under the overflow bound. H2 scales and the clip percentile were chosen by **validation** PR-AUC. Test metrics are reported once.

**H1** (test split, n = 16,670; threshold tuned on validation in H1):

| | F1 | Precision | Recall | Accuracy | PR-AUC |
|---|---|---|---|---|---|
| Float model | 0.512 | 0.444 | 0.605 | 92.5% | 0.559 |
| Proven integer model | 0.505 | 0.435 | 0.603 | 92.3% | 0.547 |

Decision agreement: 99.5%. Scales: inputs 2⁸, weights 2⁷, activations 2⁴; worst-case |accumulator| 2,913,698 < 3,670,016.

**H2** (test split, n = 6,986):

| | F1 | Precision | Recall | Accuracy | PR-AUC |
|---|---|---|---|---|---|
| Float model, raw features | 0.743 | 0.805 | 0.691 | 95.3% | 0.797 |
| Float model, clipped features | 0.742 | 0.802 | 0.691 | 95.3% | 0.795 |
| Proven integer model | 0.732 | 0.779 | 0.689 | 95.1% | 0.794 |

Prediction agreement with the raw float model: 99.0%. Features are clipped per feature to their [0.1, 99.9] percentile. This is a declared preprocessing step: the unbounded raw-feature box cannot fit a 23-bit field. The clip level and scales were picked on validation (PR-AUC 0.811). Scales: features 2², adjacency 2³, weights 2⁶ / 2⁴, activations 2³.

**H3** (1,000 decisions of the staked treatment run): the fixed-point rule (money at 2⁸) reproduces the float action and the violation flag on **100%** of decisions. The proven batch has 13 executes and 41 violating offers.

## Pre-registered benchmark

- Systems: classical (workload only) vs PQ v3 (workload + the same SIS opening as v2/v3, random-linear-combination proof). Same proof options as before (sextic, blowup 8, 42 queries, 16-bit grinding, Blake3-256).
- Protocol: 3 warm-up + 30 measured runs per system, interleaved, on an idle machine: `cargo run --release -- 30 coverage`.
- Bounds (unchanged since 2026-10-06): proof size ≤ 20×, prove time ≤ 200×, verify time ≤ 10× (mean PQ/classical).
- Pass/fail per workload, all three metrics. No coverage workload has been timed before this commit; only a smoke run that discarded timings was used to confirm the binary runs.

## Limitations

- One instance per workload: one H1 transaction, one H2 node (the circuit is specialized to its 3-node receptive field), and 64 H3 decisions. Larger GCN neighborhoods need a wider or multi-pass layout.
- The commitment opened alongside each workload is the CBDC amount commitment from v2/v3. It is not bound to the model's inputs.
- Proofs are succinct arguments of knowledge, not zero-knowledge (no ZK masking in this Winterfell fork).
