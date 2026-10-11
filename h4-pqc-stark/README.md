# H4 — Post-Quantum ZK-STARK Migration

## Hypothesis

> **“A lattice-based ZK-STARK construction can secure ZKML proof verification for the model classes used in H1-H3 while keeping proof size and verifier latency within a bounded overhead factor relative to classical (non-quantum-resistant) ZK-STARKs, making PQC migration practical without re-architecting the settlement layer.”**

## Final result

| Version | 2×2 matmul (size / prove / verify) | H1 dense 165→32 | Status |
|---|---|---|---|
| **v2** (pre-registered) | 9.10× / 71.5× / **14.10×** → **FAIL** | 1.88× / 9.90× / 4.37× → PASS | Verdict stands as recorded |
| **v3** (same statement, new proof, pre-registered before its run) | 4.97× / 14.20× / **7.40×** → **PASS** | 1.23× / 2.58× / 3.45× → PASS | Final construction; also passes on full H1/H2/H3 models (below) |

Bounds: size ≤ 20×, prove ≤ 200×, verify ≤ 10×. 30 measured runs per system, idle machine, same proof options (125–128-bit conjectured security on both sides).

### Coverage of the H1–H3 model classes (v3)

Complete, exact integer inference of the actual trained models and rule. The circuits are proven correct: inputs are range-checked, requantization and ReLU are proven, interval bounds rule out field wrap-around, and forgeries are rejected. Pre-registered in [`docs/h4_coverage.md`](docs/h4_coverage.md) before any timing. Data: [`benchmark_coverage.csv`](benchmark_v2/results/benchmark_coverage.csv), summary [`benchmark_coverage.md`](benchmark_v2/results/benchmark_coverage.md).

| Workload | What is proven | Size | Prove | Verify | Verdict |
|---|---|---|---|---|---|
| `h1_mlp_full` | trained H1 `W32_B1`, all layers, one real illicit transaction | 1.18× | 1.28× | 1.92× | **PASS** |
| `h2_gcn_node` | trained H2 GCN, node 36333 over its real 3-node receptive field | 1.13× | 1.36× | 1.76× | **PASS** |
| `h3_rule_x64` | H3 `choose_action` + `is_violation`, 64 real decisions | 1.20× | 1.51× | 1.96× | **PASS** |

Fidelity of the proven integer models vs the float models (test splits):
- **H1:** F1 0.505 vs 0.512, decision agreement 99.5%.
- **H2:** F1 0.732 vs 0.743, prediction agreement 99.0%, with features clipped per feature to the 0.1–99.9 percentile (declared; clipping alone costs 0.001 F1).
- **H3:** 100% agreement over 1,000 decisions.

On real model workloads, adding the post-quantum SIS opening costs under 2× on every metric. The opening's cost is roughly fixed, so it matters most for trivially small computations, as the 2×2 case shows.

### Overhead law (post-hoc, no new runs)

Across the five v3 workloads, the post-quantum part behaves like a fixed added cost: **PQ ≈ classical + c**, so **ratio ≈ 1 + c / classical**.
- Verify: c ≈ 12 ms (range 8–15 ms).
- Prove: c ≈ 28 ms (range 15–39 ms).
- Proof size: c ≈ 8.4 KB (range 7.8–10.2 KB). The exception is the 2×2 case: its 8-row trace is padded to 128 rows for the SIS block, which adds 26.5 KB.

The practical reading is simple. The overhead ratio falls as the model grows, so post-quantum binding is cheapest exactly for the real models a CBDC would run. This law was fitted after the fact on existing measurements, so it is descriptive, not a tested prediction. Script: [`overhead_law.py`](benchmark_v2/overhead_law.py). Outputs: [`overhead_law.md`](benchmark_v2/results/overhead_law.md) and [`overhead_law.png`](benchmark_v2/results/overhead_law.png).

**Bottom line:** a STARK proving a short opening of a statistically binding SIS commitment, alongside the workload, stays within all three overhead bounds with the v3 proof: for the 2×2 and dense-layer workloads, and for complete inference of the trained H1, H2 and H3 models. The original v2 proof missed the verify bound on the 2×2 workload. v3 was designed after that miss (disclosed in [`docs/h4_v3.md`](docs/h4_v3.md)), so read v3 as a confirmed engineering iteration, not a replacement of v2's result.

### v3 (final construction)

v3 proves exactly the same commitment as v2 (same uniform matrix, n = 80; a test asserts identical commitments). Profiling showed v2's verify time was dominated by the verifier's DEEP composition over 80 accumulator columns. v3 instead:
- packs s = (bits, r) four coordinates per row (128 rows);
- after s is committed, draws γ from the 138-bit extension field;
- checks all 80 commitment rows with one auxiliary accumulator, Σⱼ γʲ(Aⱼ·s − cⱼ) = 0.

Extra soundness error is ≤ 79/p⁶ ≈ 2⁻¹³⁰·⁵ (Schwartz–Zippel). Binding is unchanged. Details and disclosure: [`docs/h4_v3.md`](docs/h4_v3.md). Data: [`benchmark_v2/results/benchmark_v3.csv`](benchmark_v2/results/benchmark_v3.csv), summary [`benchmark_v3.md`](benchmark_v2/results/benchmark_v3.md).

| Workload | System | Trace (rows × cols) | Proof size | Prove ms | Verify ms |
|---|---|---|---|---|---|
| matmul_2x2 | classical | 8 × 17 | 6,683 B | 2.60 | 1.43 |
| matmul_2x2 | PQ v3 | 128 × 35* | 33,194 B | 36.91 | 10.57 |
| dense_165x32 | classical | 256 × 33 | 33,710 B | 24.50 | 5.84 |
| dense_165x32 | PQ v3 | 256 × 51* | 41,460 B | 63.29 | 20.14 |

\*Main columns plus one extension-field auxiliary column counted as 6 base columns.

Remaining verify cost in v3 comes mainly from the verifier evaluating the public matrix (80 × 512 entries) at the out-of-domain point. That is inherent to an unstructured matrix; structured matrices that would avoid it were considered and rejected because they admit short collisions.

Reproduce v3: `cd benchmark_v2 && cargo test --release && cargo run --release -- 30 v3 > results/benchmark_v3.csv && python analyze.py results/benchmark_v3.csv`.

## v2 result (pre-registered, kept as recorded)

**The v1 prototype and all its numbers (sections 1–11 below) are withdrawn.** The v1 circuit did not prove what it claimed: the SIS matrix and selectors were prover-controlled, the relation was checked under the wrong modulus with a free quotient, randomness was not range-checked, and both v1 circuits ran at ~11–17 bits of STARK security. Details: [`docs/h4_v2_security.md`](docs/h4_v2_security.md).

v2 (`benchmark_v2/`) proves a workload plus knowledge of a short opening of a public SIS commitment, c = G·bits + B·r mod p. B and selectors are verifier-computed periodic columns, arithmetic is native mod p (7,340,033), bits are boolean, and r is exactly ternary. SIS binding (n = 80, m = 512): statistically binding, about 2⁻⁶³⁶ expected short collisions for a random matrix; the lattice estimator gives ≥ 2⁶¹⁵ (quantum) attack cost ([`docs/h4_lattice_estimator.json`](docs/h4_lattice_estimator.json)). Classical and PQ circuits share the same workload block and the same proof options (sextic extension, blowup 8, 42 queries, 16-bit grinding, Blake3-256), giving 125–128 bits conjectured STARK security. Tests evaluate the AIR directly and reject forged openings, out-of-range r, non-boolean bits, and wrong workload outputs.

Results: 3 warm-up + 30 measured runs per system, interleaved, on an idle machine. Raw data: [`benchmark_v2/results/benchmark_v2.csv`](benchmark_v2/results/benchmark_v2.csv); summary: [`benchmark_v2/results/benchmark_v2.md`](benchmark_v2/results/benchmark_v2.md).

| Workload | Metric | Classical | PQ (SIS) | Ratio | Pre-registered bound | Result |
|---|---|---|---|---|---|---|
| matmul_2x2 (primary) | proof size | 6,683 B | 60,844 B | 9.10× | ≤ 20× | PASS |
| matmul_2x2 | prove time | 2.59 ms | 184.86 ms | 71.50× | ≤ 200× | PASS |
| matmul_2x2 | verify time | 1.37 ms | 19.28 ms | **14.10×** | ≤ 10× | **FAIL** |
| dense_165x32 (H1 layer) | proof size | 33,710 B | 63,539 B | 1.88× | ≤ 20× | PASS |
| dense_165x32 | prove time | 22.39 ms | 221.75 ms | 9.90× | ≤ 200× | PASS |
| dense_165x32 | verify time | 5.15 ms | 22.54 ms | 4.37× | ≤ 10× | PASS |

**Verdict:**
- **Primary workload (2×2 matmul): H4 not confirmed.** Verification overhead is 14.1×, over the pre-registered ≤ 10× bound. Size and prove time pass.
- **H1 dense layer (165 → 32): all three bounds pass.**
- The SIS opening adds a roughly fixed cost (~180–200 ms prove, ~18 ms verify, +30–54 KB proof here), mostly the verifier evaluating 81 public periodic columns. Relative overhead therefore shrinks as the proven workload grows. Overhead stays bounded for a realistic ML layer, but not for a trivially small computation.

**Limitations:**
- SIS binding is statistical under the assumption that the PRG-generated G and B behave like uniformly random matrices. Confirmed with the lattice estimator (≥ 2⁶¹⁵ quantum).
- Commitments are binding only. Hiding is not claimed, and this Winterfell fork does not add zero-knowledge masking.
- Coverage (v2 run): one linear layer of the H1 MLP. Complete inference of the trained H1, H2 and H3 models was added later with v3; see "Coverage of the H1–H3 model classes" above. No settlement-layer integration.
- Timings are from one machine. The ratios are the reported quantity.

Reproduce: `cd benchmark_v2 && cargo test --release && cargo run --release -- 30 > results/benchmark_v2.csv && python analyze.py results/benchmark_v2.csv`.

---

*Sections below document v1 and are kept for the record only.*

## Objective

H4 investigates whether a STARK proof system can incorporate a lattice-based, post-quantum relation while maintaining practical proof size and verifier latency relative to a classical STARK performing the same computation.

The current prototype evaluates this question using a small matrix-multiplication workload combined with an SIS-style lattice relation.

This repository records the implementation, validation tests, and benchmark results used for the H4 feasibility study.

## Repository Structure

```text
h4-pqc-stark/
├── classical/
│   ├── matrix_stark/
│   └── matched_matrix_stark/
├── pq/
│   └── matched_matrix_stark/
├── experiments/
│   └── benchmark_results.csv
├── docs/
└── README.md
```

## 1. Classical STARK Baseline

The original baseline proves a 2×2 matrix multiplication computation using Winterfell v0.13.1.

```text
A = [1 2]       B = [5 6]       C = [19 22]
    [3 4]           [7 8]           [43 50]
```

The AIR enforces all four matrix-product equations:

```text
C00 = A00*B00 + A01*B10
C01 = A00*B01 + A01*B11
C10 = A10*B00 + A11*B10
C11 = A10*B01 + A11*B11
```

### Original baseline benchmark

10 successful runs produced:

- Proof size: 7,677 bytes
- Mean proving time: 6.363 ms
- Mean verification time: 2.244 ms
- Verification runs: 10/10

## 2. Matched Classical Baseline

A second classical implementation uses the same older Winterfell `f23` environment used by the lattice-backed prototype.

The matched comparison uses the same matrix workload and proof configuration.

10 successful runs produced:

- Proof size: 2,233 bytes
- Mean proving time: 0.448 ms
- Mean verification time: 0.187 ms
- Verification runs: 10/10

## 3. Lattice-Backed STARK Prototype

The PQ prototype combines the same 2×2 matrix multiplication computation with an SIS-style lattice relation inside a single STARK AIR.

The lattice relation is:

```text
C = A*s + B*r (mod q)
```

where:

- `A` and `B` are lattice matrices
- `C` is the public commitment
- `s` and `r` form the private witness
- `q` is the lattice modulus

The matrix computation and lattice relation are enforced by the same STARK proof.

The production-candidate parameter profile evaluated in this phase is:

```text
Profile: research-candidate-128x256
q       = 8,380,417
M       = 128
N       = 256
β       = 131,071
```

The profile is treated as a research candidate rather than a production cryptographic parameter set. A specific post-quantum security category is not claimed without a concrete SIS attack-cost analysis.

## 4. Validation

The matched PQ prototype was tested using the valid 2×2 matrix multiplication shown above.

### Test 1 — Valid matrix

Result: PASS

### Test 2 — Tampered matrix output

The first output element was changed from 19 to 20.

Result: PASS — correctly rejected

### Test 3 — Tampered matrix input

The first input element was changed from 1 to 2.

Result: PASS — correctly rejected

These tests demonstrate that the current AIR rejects invalid matrix computations.

## 5. Post-hoc Exploratory Bounds

The following overhead bounds are **post-hoc exploratory bounds** relative to the matched classical STARK baseline. They were documented after the existing benchmark results and therefore must not be treated as pre-registered or confirmatory thresholds.

| Metric | Post-hoc exploratory bound |
|---|---:|
| Proof size | ≤ 20× |
| Proving time | ≤ 200× |
| Verification time | ≤ 10× |

These bounds define a practical feasibility threshold rather than a cryptographic security target. They are retained for transparent reporting of the existing experiment, not as evidence of a confirmatory H4 result.

The verification-time bound is the most operationally constrained because proof verification is on the critical path for settlement validation. A 10× bound over the matched classical verification time of 0.187 ms corresponds to approximately 1.87 ms, which remains in the low-millisecond range.

A looser 200× bound is used for proving time because proving is expected to occur off the critical settlement-verification path.

The 20× proof-size bound allows substantial post-quantum overhead while keeping the resulting proof below approximately 45 KB relative to the 2,233-byte matched classical proof.

## 6. Matched PQ Benchmark

The production-parameter PQ implementation was benchmarked against the matched classical baseline.

| Metric | Matched classical | PQ production candidate | PQ / Classical | Post-hoc exploratory bound | Result |
|---|---:|---:|---:|---:|---|
| Proof size | 2,233 B | 31,792 B | 14.2× | ≤ 20× | PASS |
| Mean proving time | 0.448 ms | ~70 ms | ~156× | ≤ 200× | PASS |
| Mean verification time | 0.187 ms | 1.10 ms | 5.9× | ≤ 10× | PASS |

The observed overheads therefore remain within the **post-hoc exploratory bounds** for the evaluated 2×2 matrix-multiplication workload.

These measurements apply only to the current prototype, parameterization, and workload. They do not establish the security or production readiness of the parameter set.

## 7. Current Findings

For the evaluated 2×2 matrix-multiplication workload, incorporating the lattice relation with the production-candidate parameters resulted in approximately:

- 14.2× proof-size overhead
- 156× proving-time overhead
- 5.9× verification-time overhead

All three observed overheads are within the **post-hoc exploratory bounds**.

Because the bounds were defined after the existing results, these passes are **exploratory rather than confirmatory evidence** for H4.

The verification overhead is approximately 1.10 ms, remaining in the low-millisecond range despite the addition of the lattice-backed relation.

The results do not establish the complete H4 hypothesis because the model classes used in H1-H3 have not yet been evaluated and a concrete post-quantum security analysis of the parameter set has not yet been completed.

## 8. Security Assessment

The current parameter profile is:

```text
research-candidate-128x256
q = 8,380,417
M = 128
N = 256
β = 131,071
```

This profile is designated a **research candidate**.

No specific NIST post-quantum security category is claimed for these parameters.

The dimensions, modulus, and bound alone do not establish a 128-bit or other specific post-quantum security level. A concrete SIS security analysis, including an appropriate attack-cost estimate for the instantiated parameters, is required before assigning a defensible security category or considering the parameters production-ready.

Therefore, the current benchmark should be interpreted as a performance and feasibility evaluation, not as evidence of production-grade post-quantum cryptographic security.

## 9. Limitations

1. The `research-candidate-128x256` parameter profile is a research candidate and is not a production cryptographic parameter set.
2. The current H4 test covers only a 2×2 matrix multiplication and does not cover the model classes used in H1-H3.
3. Consequently, the bounded-overhead result applies only to the evaluated 2×2 workload and cannot yet be generalized to the H1-H3 model classes.
4. The current prototype is not an externally reviewed post-quantum cryptographic construction.
5. The settlement-layer migration claim has not been validated through a production CBDC implementation.
6. The current benchmark does not establish end-to-end ZKML performance for realistic model sizes or inference workloads.
7. The existing overhead bounds are post-hoc exploratory thresholds because they were documented after the benchmark results; they are not confirmatory acceptance criteria.

## 10. Next Research Step

For a **confirmatory H4 result**, the next phase should first commit new overhead bounds before running the new experiment. The new parameters and workload should then be benchmarked without changing those bounds based on the observed results.

The next experimental phase should investigate realistic lattice parameters corresponding to a defensible post-quantum security target.

The same matched workload should then be benchmarked again to measure the effect on:

- proof size
- proving time
- verifier latency

The results should be compared against the matched classical baseline.

A subsequent phase should extend the workload beyond the current 2×2 matrix multiplication to representative model classes used in H1-H3. This is necessary before the bounded-overhead result can be generalized to the full H4 hypothesis.

## 11. Reproducibility

### Original classical baseline

```bash
cd classical/matrix_stark
cargo run --release
```

### Matched classical baseline

```bash
cd classical/matched_matrix_stark
cargo run --release
```

### Matched lattice-backed prototype

```bash
cd pq/matched_matrix_stark
cargo run --release
```

### Benchmark data

```text
experiments/benchmark_results.csv
```

## Conclusion

For the currently evaluated 2×2 matrix-multiplication workload, the production-candidate lattice-backed STARK remains within all three **post-hoc exploratory bounds**:

- **Proof size:** 14.2× ≤ 20× — PASS
- **Proving time:** ~156× ≤ 200× — PASS
- **Verification time:** 5.9× ≤ 10× — PASS

These passes should be interpreted as **exploratory, not confirmatory**, because the bounds were documented after the existing benchmark results.

The results therefore support the bounded-overhead component of H4 only as an **exploratory finding** for this specific experimental workload.

However, H4 is **not yet fully established**. The current experiment does not cover the model classes used in H1-H3, and the `research-candidate-128x256` parameters have not been assigned a defensible post-quantum security level through a concrete SIS security analysis.

Accordingly, these results should be interpreted as a **research feasibility result**, not a production-readiness or cryptographic-security claim.
