# Verifiable, Accountable and Quantum-Safe AI for CBDC Systems — Complete Results

**Project:** CBDC – AI – Blockchain research (proposal: `docs/CBDC_AI_Blockchain_Research_Proposal.pdf`)
**Repository:** github.com/maanas1234/CBDC (all results on `main`, PRs #1–#34)
**Team:** Aman (H1), Indrakshi (H2), Anjali (H3), Simran (H4); mentor Maanas Tyagi
**Status:** all experiments complete. This document collects every result, how it was obtained, what it does and does not show, and where the raw evidence lives.

---

## Abstract

We test four falsifiable hypotheses about running AI inside a central-bank digital currency (CBDC) stack, and three cross-cutting findings that emerged from them. All confirmatory runs were pre-registered: parameters, bounds and analysis code were committed before the runs.

| Hypothesis | Verdict |
|---|---|
| **H1, verifiable off-chain inference** | **Partly supported.** Bisection-based dispute resolution cuts proof latency 14.9× on a 60-block model, with ≤ 0.13 pp accuracy loss. It misses 10× on shallower models, and a single dispute costs 2.4× more gas than monolithic verification. |
| **H2, federated AML under label scarcity** | **Formally met, substantively not supported.** The pre-registered "≥ 20% fewer false positives" criterion holds at every scarcity level, but only because federated models flag less. At matched recall the criterion fails at every evaluable level, and with ample labels federation produces 67–80% *more* false positives. |
| **H3, cryptoeconomic liability (AgentBound Tokens)** | **Supported,** with a 41% fewer violations (agent-level p ≈ 10⁻⁶). The original design wears out as stakes are slashed to zero, and is defeated by identity resets. Stake maintenance plus operator-bound history restores durable deterrence (violations 50.7% → 2.0%). A registration bond at an analytically derived break-even value does the same. |
| **H4, post-quantum ZK-STARK migration** | **Supported.** A STARK that also proves a short opening of a statistically binding lattice (SIS) commitment stays within all pre-registered overhead bounds on the complete trained H1, H2 and H3 models (≤ 1.2× size, ≤ 1.5× prove, ≤ 2.0× verify). |

The H4 result also includes **one proof covering H1 risk inference → H3 decision → the post-quantum-committed amount**, at 1.15× / 1.28× / 1.77× overhead.

---

## 1. Contributions (candidate claims for the paper, strongest first)

1. **C1 — End-to-end post-quantum verifiable CBDC decision.** A single hash-based STARK proves four things together:
   - exact integer inference of a trained AML model, with the risk score kept private;
   - the risk flag;
   - an autonomous agent's policy decision that uses the flag;
   - that the amount decided on is the amount inside a lattice commitment with ≥ 2⁶¹⁵ quantum attack cost.

   Overhead versus the same proof without post-quantum binding: 1.15× size, 1.28× prove, 1.77× verify. Without the commitment, a decision on a different amount than the committed one is accepted; with it, it is rejected. (§5.4)
2. **C2 — Post-quantum binding in ZKML has a near-constant additive cost.** Across six workloads, the commitment adds about +12 ms verify, +28 ms prove and +8.4 KB, so the overhead ratio is about 1 + c / classical cost and falls as models grow. Fitted post-hoc on five workloads; predicted the sixth within 0.02× for size and prove time. (§5.5)
3. **C3 — Slashing-based agent liability wears out and is Sybil-fragile; two protocol fixes make it durable.** Pre-registered on 200 fresh agents per regime. (§4)
   - Flat stakes lose most of their effect as agents are slashed to zero: 19% → 45% violations, against control's 50%.
   - Free identity resets nullify escalating penalties exactly.
   - Stake maintenance (z = −13.1) and operator-bound history (z = −18.5) fix both problems.
   - A registration bond blocks resets at exactly the derived break-even B* = V_max − p·s − C_min.
4. **C4 — False-positive targets for federated AML are gameable.** A pre-registered matched-recall analysis shows the "≥ 20% fewer false positives" criterion is met only because federated models flag less. At equal recall, federation is never significantly better, and it is significantly worse with ample labels (+67–80% false positives). Such targets must be stated at fixed recall. (§3.3)
5. **C5 — Optimistic bisection for ZKML.** It isolates a tampered step in ⌈log₂ n⌉ rounds (7/7 scenarios) with zero accuracy cost from bisection itself. The 10× latency gain needs deep models (crossover between 24 and 60 blocks). On-chain, it only pays off if fewer than ≈ 1.2% of inferences are disputed. (§2)

Negative and boundary results are part of the contribution. In particular:
- H2's federated detection is worse than centralized.
- Institution-level differential privacy with three institutions destroys utility (ε ≈ 50 → F1 0.29).
- H4 v2 missed its verify bound on the smallest workload.

---

## 2. H1 — Verifiable off-chain inference (zk-OPML)

**Hypothesis.** Restructuring AML inference as a bisectable ONNX computation graph under an optimistic dispute protocol reduces proof-generation latency and on-chain verification cost by ≥ 10× relative to a monolithic ZKML proof, with ≤ 1 pp accuracy loss.

### Setup

- **Data:** Elliptic, 46,564 labelled transactions. Temporal split: time steps 1–30 train, 31–34 validation, 35–49 test. Features clipped at the 1st/99th percentile.
- **Model:** residual MLP of width 32 (165→32 projection, B residual blocks, 32→1 head), B ∈ {1, 8, 24, 60}, 3 seeds each. All selection was done on validation.
- **Proof system:** EZKL 23.0.5 (Halo2/KZG), fixed-point scale 2⁹.
- **Bisection:** the model is split into steps (63 for B = 60, with the projection split into two halves).
  - Each step's output is Poseidon-committed; the leaves are chained by a running hash under a Merkle root.
  - A dispute binary-searches for the first differing step, then proves only that step on agreed inputs.

### Results

| Blocks | Steps | Full proof | Slowest step | Worst-case speedup | Bisection rounds |
|---|---|---|---|---|---|
| 1 | 4 | 2.11 s | 1.98 s | 1.07× | 2 |
| 8 | 11 | 7.67 s | 1.98 s | 3.88× | 4 |
| 24 | 27 | 15.16 s | 1.98 s | 7.67× | 5 |
| 60 | 63 | 29.43 s | 1.98 s | **14.9×** (single-core); 12.6× multi-core | 6 |

- **Dispute game, B = 60.** 7 of 7 tampering scenarios were localized exactly in ≤ 6 rounds, and the correct party won every time. Two of the scenarios flipped the AML decision (illicit ↔ licit); both were caught.
- **Accuracy.** The fixed-point circuit loses at most 0.13 pp accuracy (0.32 pp F1), with ≥ 99.81% of decisions unchanged. Bisection itself adds zero error.
- **Gas (local anvil chain):**

| Item | Gas |
|---|---|
| Monolithic verification | 696,984 |
| Optimistic commit | 49,962 (13.9× cheaper) |
| Full dispute (6 rounds + one step verification) | 1.26 M typical, 1.67 M worst (1.8–2.4× *more* than monolithic) |

**Break-even:** the 10× gas target holds on average only if the dispute rate is below ≈ 1.2% (worst case) or ≈ 1.6% (typical).

### Verdict against the pre-registered criteria

| Criterion | Result |
|---|---|
| ≥ 10× latency | **PASS only at 60 blocks**; FAIL at ≤ 24 |
| ≥ 10× on-chain cost | **PASS amortized** at a dispute rate below about 1.2%; **FAIL per dispute** |
| ≤ 1 pp accuracy loss | **PASS** (0.13 pp) |
| Bounded localization | **PASS** (⌈log₂ n⌉ rounds) |

### Limitations

- The 60-block model is a depth benchmark, not a better AML model: its F1 is 0.516, versus 0.77 for a random forest.
- Only 4 depths were measured, so the crossover depth lies somewhere between 24 and 60 blocks.
- Preprocessing is outside the circuit.
- The dispute game has no bonds or timeouts.
- The latency gain is in proof generation, not in time-to-finality.

Evidence: `h1-zkopml-v2/WRITEUP.md`, `h1-zkopml-v2/results/`.

---

## 3. H2 — Federated AML detection under label scarcity

**Hypothesis (original).** Under per-institution scarcity of illicit labels, federated GCN training across institutions achieves ≥ 20% fewer false positives than a centralized baseline. A boundary-embedding channel mitigates scarcity, and a privacy bound is achievable.

### 3.1 Protocol v2 (pre-registered: `h2-federated-aml/experiments/PREREGISTRATION_v2.md`)

- **Data and institutions:** Elliptic1 with 203,769 nodes and 234,355 edges. K = 3 institutions from a graph-aware partition.
- **Seeds:** 10 (42–51). The earlier v1 used 5 seeds, which cannot reach two-sided Wilcoxon p < 0.05.
- **Matched budget:** 200 epochs for every method (FedAvg = 40 rounds × 5 local epochs). Every method keeps its best checkpoint by validation F1.
- **Centralized baseline:** pooled training on the *identical* scarce label mask the federated run sees.
- **Scarcity levels:** 100%, 50%, 20%, 10%, 5% and 1% of illicit training labels kept.
- **Primary test:** one-sided paired Wilcoxon on test false positives, Holm correction over the 6 levels. The criterion is met when FP reduction ≥ 20% and Holm p < 0.05.

### 3.2 Results (default 0.5 threshold)

| Labels kept | FP centralized | FP FedAvg | FP reduction | Holm p | Criterion | F1 centralized | F1 FedAvg | ΔF1 (p) | Recall centralized → FedAvg |
|---|---|---|---|---|---|---|---|---|---|
| 100% | 113.7 | 62.3 | 45% | 0.025 | met | 0.757 | 0.649 | −0.109 (0.002) | 0.712 → 0.526 |
| 50% | 52.7 | 9.5 | 82% | 0.006 | met | 0.705 | 0.517 | −0.188 (0.002) | 0.589 → 0.354 |
| 20% | 27.1 | 9.0 | 67% | 0.031 | met | 0.495 | 0.314 | −0.181 (0.004) | 0.346 → 0.189 |
| 10% | 504.3 | 0.5 | 99.9% | 0.008 | met | 0.324 | 0.148 | −0.177 (0.002) | 0.331 → 0.080 |
| 5% | 500.3 | 24.7 | 95% | 0.012 | met | 0.166 | 0.041 | −0.125 (0.002) | 0.227 → 0.024 |
| 1% | 515.9 | 26.5 | 95% | 0.006 | met | 0.096 | 0.030 | −0.066 (0.031) | 0.190 → 0.019 |

FedAvg also has significantly lower F1 than **local-only** training at every level.

- **PR-AUC:** FedAvg is significantly worse with 50–100% labels and not different at 5–20%.
- **Boundary channel (refined H2):** **not supported.** It lowers F1 at 5 of 6 levels (p ≤ 0.037). In the λ ablation at 10% labels, F1 falls monotonically from 0.148 at λ = 0 to 0.029 at λ = 1.
- **Institution count (10% labels):** FedAvg F1 is 0.126 / 0.148 / 0.134 for K = 2 / 3 / 5, against centralized 0.31–0.32. The deficit does not depend on K.
- **Privacy:** institution-level DP with clip C = 1.5, Gaussian noise, 40 rounds, RDP accounting and δ = 1e-5.

| Noise z | ε | F1 | PR-AUC | False positives |
|---|---|---|---|---|
| 0 (no DP) | — | 0.649 | 0.782 | 62.3 |
| 0.01 | 2.0 × 10⁵ | 0.653 | 0.786 | 47.0 |
| 0.1 | 2.3 × 10³ | 0.527 | 0.495 | 511.5 |
| 1.0 | 50.3 | 0.287 | 0.164 | 1,810.7 |

With three institutions, the noise averages over only three updates, so no setting gives both meaningful privacy and usable detection.

### 3.3 Finding C4 — the false-positive criterion is gameable

Pre-registered in `h2-federated-aml/experiments/PREREGISTRATION_matched_recall.md` before the rerun.

**Rerun:** `main_scarcity` for the same 10 seeds, with per-node scores saved. The rerun reproduces v2 exactly: false positives are identical in 240 of 240 seed × method × level cases, and F1 differs by at most 0.017.

**Matching procedure:**
1. Target recall = the centralized model's *validation* recall at the 0.5 threshold.
2. Every method uses the largest threshold that reaches that target on validation.
3. Test false positives are counted once at that threshold.

Matched test recalls differ by ≤ 0.009.

| Labels kept | FP reduction at 0.5 | FP centralized (matched) | FP FedAvg (matched) | FP reduction (matched) | Holm p (one-sided) | Criterion | FedAvg significantly worse? (Holm, two-sided) |
|---|---|---|---|---|---|---|---|
| 100% | 45% | 112.9 | 203.7 | −80% | 1.0 | **fails** | yes, p = 0.012 |
| 50% | 82% | 52.7 | 87.8 | −67% | 1.0 | **fails** | yes, p = 0.049 |
| 20% | 67% | 27.0 | 51.6 | −91% | 1.0 | **fails** | no (p = 0.78) |
| 10% | 99.9% | 503.5 | 100.5 | +80% | 0.070 | **fails** (not significant) | — |
| 5% | 95% | 499.4 | 401.6 | +20% | 1.0 | **fails** | no |
| 1% | 95% | 993.8 | 1,135.0 | −14% | 1.0 | not evaluable (5/10 seeds)* | no |

\*In 5 seeds the centralized model had zero validation recall at 1% labels, so no recall could be matched. Per the pre-registration, this level does not count toward the verdict.

**Pre-registered verdict: supported.** The criterion fails at all 5 fully evaluable levels, where at the default threshold it passed at all 6.

**Honest exception:** at 10% labels, FedAvg's whole precision–recall curve sits above centralized's (figure `h2-federated-aml/results/v2_scores/pr_curves.png`). At matched recall it has 80% fewer false positives (unadjusted p = 0.012), but this does not survive the Holm correction (p = 0.070). In this regime the pooled model trained on few labels is the weak one.

### H2 verdict

- The original criterion is met as written but **not supported in substance**: federation detects worse and its false-positive advantage is a threshold artefact.
- The boundary channel is not supported.
- DP has no useful operating point with K = 3.

### Limitations

- Institutions are simulated by partitioning one dataset.
- One architecture (a 2-layer GCN).
- The DP guarantee covers the model-update channel only.

Evidence: `h2-federated-aml/README.md`, `results/v2/`, `results/v2_scores/`.

---

## 4. H3 — Cryptoeconomic liability via AgentBound Tokens

**Hypothesis.** Binding autonomous financial agents to staked, non-transferable AgentBound Tokens (ABTs) with coded slashing conditions reduces policy-violating actions relative to an unstaked control.

**Separate test:** does it resist Sybil attacks (identity resets)?

### 4.1 Setup

- **Agents:** 20 staked and 20 unstaked, 50 steps each.
- **Decision rule:** identical myopic expected-payoff rule for both groups. The only difference is the expected slash.
- **Violation (machine-checkable):** amount > 50 or a sanctioned destination.
- **Economics:** stake 100, slash 15, detection probability 1.
- **Payoffs:** compliant payoff in [1, 10], violation payoff in [1, 30].
- **Paired design:** both groups receive the identical opportunity stream.

### 4.2 Confirmatory result (seed 42)

| Group | Violations | Rate |
|---|---|---|
| Control (unstaked) | 542 / 1,000 | 54.2% |
| Treatment (staked) | 319 / 1,000 | 31.9% |

- **Agent-level tests (the correct unit):** Mann–Whitney U = 25.5, z = −4.73, p = 1.1 × 10⁻⁶; permutation p ≤ 10⁻⁵.
- **Action-level test:** z-test p = 3.7 × 10⁻²⁴. It is pseudo-replicated, so do not cite it.
- **Majority bar (post-hoc definition: rate ≤ 50%):** staked 19 / 20, control 7 / 20.
- **Sybil probe:** `SYBIL_SUCCEEDED`. The registry is keyed by agent id, so a slashed agent re-registers with a fresh ABT, fresh stake and a clean history.

### 4.3 Finding C3 — durable liability

Pre-registered in `h3-abt/docs/PREREGISTRATION_durable.md`.

**Post-hoc observation that motivated it:** on seed 42, 19 of 20 staked agents were slashed to zero stake. Once depleted, they violated at 53%, against control's 54%.

**Design:**
- 10 new seeds (43–52) × 20 agents = **200 agents per regime**, all regimes paired on the same opportunity stream.
- The simulator reproduces the confirmatory seed-42 counts (542 / 319) exactly.
- Regimes:

| Regime | What it adds |
|---|---|
| control | unstaked |
| flat | the original design: no top-up |
| maintained | stake topped back up before each action, enough to cover the next slash |
| escalating_id | maintained + repeat-offender slash 15·(1+k), with violation history k keyed by agent id; an agent may reset to a fresh id when that is strictly better one step ahead |
| escalating_operator | as escalating_id, but history k is keyed by the operator credential, so a new id keeps it |

| Regime | Violations | Steps 1–10 | Steps 41–50 | Resets | Mean slashed / agent |
|---|---|---|---|---|---|
| Control | 50.7% | 49.8% | 50.5% | 0 | 0 |
| Flat ABT (original) | 30.6% | 19.1% | **45.2%** | 0 | 98.3 (180 / 200 agents depleted) |
| + stake maintenance | 19.6% | 19.1% | 18.3% | 0 | 146.8 |
| + escalation, id-keyed (free reset) | 19.6% | 19.1% | 18.3% | 1,757 | 146.8 |
| + escalation, **operator-bound** | **2.0%** | 8.7% | 0.0% | 0 | 15.0 |

**Primary tests** (one-sided agent-level Mann–Whitney, Holm across both; permutation p ≤ 10⁻⁵ for each):
- **T1:** maintenance beats flat in steps 41–50: 18.4% vs 45.2%, z = −13.1. **Supported.**
- **T2:** operator-bound escalation beats id-keyed escalation with free resets: 2.0% vs 19.6%, z = −18.5. **Supported.**

**Secondary results:**
- Flat < control replicates on the new seeds over all steps: 30.6% vs 50.7%, z = −15.0.
- In steps 41–50 the flat-vs-control gap shrinks to −5 pp (z = −2.7, p = 0.003).
- Maintenance beats control in steps 41–50 (z = −15.3).

**Registration-bond sweep** (id-keyed registry, bond B paid per new identity):
- Analytic break-even: B* = V_max − p·s − C_min = 30 − 15 − 1 = 14.
- The prediction matched exactly: zero resets at B ≥ 14.

| B | 0 | 5 | 10 | **14** | 20 | 40 |
|---|---|---|---|---|---|---|
| Resets | 1,757 | 833 | 176 | **0** | 0 | 0 |
| Violation rate | 19.6% | 10.3% | 3.8% | **2.0%** | 2.0% | 2.0% |

Figure: `h3-abt/results/durable_figure.png`.

### H3 verdict

- Deterrence is **supported**.
- In its original form it **wears out**, and **Sybil resistance fails** with id-keyed identity.
- Both are fixed by stake maintenance together with operator-bound history, or with a registration bond ≥ B*.
- Combined, the fixes reduce violations from 50.7% to 2.0%, and each operator pays only one slash (15) over 50 steps.

### Limitations

- Simulation only, with myopic agents and a one-step-ahead reset rule.
- Detection probability is 1.
- Operators are assumed to have unlimited capital for top-ups.
- The operator credential is assumed to exist; establishing it is a regulatory question.
- There is no on-chain contract.

Evidence: `h3-abt/README.md`, `results/confirmatory_seed42.*`, `results/agent_level_seed42.json`, `results/durable_*`.

---

## 5. H4 — Post-quantum ZK-STARK migration

**Hypothesis.** A lattice-based ZK-STARK construction can secure ZKML proof verification for the H1–H3 model classes, keeping proof size and verifier latency within a bounded overhead factor relative to classical ZK-STARKs.

**Pre-registered bounds (2026-10-06, unchanged):** mean PQ/classical proof size ≤ 20×, prove time ≤ 200×, verify time ≤ 10×.

### 5.1 Construction

- **Proof system:** Winterfell (fork with field f23201, p = 7,340,033).
  - Sextic extension (138-bit), blowup 8, 42 queries, 16-bit grinding, Blake3-256.
  - Conjectured security 125–128 bits for both classical and PQ proofs.
- **Statement:** the workload, plus knowledge of a short opening of a public SIS commitment c = G·bits + B·r mod p:
  - n = 80 rows, 64 boolean amount bits and 448 ternary r;
  - G and B are verifier-computed and uniform.
- **Binding is statistical:** the expected number of short collisions is ≈ 2⁻⁶³⁶. The lattice estimator (MATZOV / ADPS16 / ChaLoy21 cost models) puts the best attack at ≥ 2⁶¹⁵ quantum cost.
- **v3 proof technique:** after s = (bits, r) is committed, the verifier draws γ ∈ F_{p⁶}. One extension-field accumulator then checks Σ γʲ(Aⱼ·s − cⱼ) = 0. The extra soundness error is ≤ 2⁻¹³⁰·⁵.
- **Hiding:** the commitment is binding only; no zero-knowledge masking is claimed.

### 5.2 Version history (kept as recorded)

- **v1 — withdrawn as unsound:**
  - the SIS matrix was prover-controlled;
  - the relation was checked under the wrong modulus;
  - r was not range-checked;
  - security was only about 11–17 bits.
- **v2** (pre-registered). Results below.
- **v3** — same commitment, new proof. It was pre-registered after the v2 miss, and that is disclosed.

| Version | 2×2 matmul (size / prove / verify) | H1 dense 165→32 |
|---|---|---|
| v2 | 9.10× / 71.5× / **14.10× (FAIL)** | 1.88× / 9.90× / 4.37× (PASS) |
| v3 | 4.97× / 14.20× / 7.40× (PASS) | 1.23× / 2.58× / 3.45× (PASS) |

### 5.3 Coverage of the complete trained H1–H3 models (v3; pre-registered in `docs/h4_coverage.md`)

Exact integer circuits with the following properties:
- inputs are range-checked;
- every requantization and ReLU is proven, with windows ≤ 2²² < p so each decomposition is unique;
- interval bounds keep all accumulators below (p − 1) / 2.

| Workload | What is proven | Size | Prove | Verify | Verdict | Fidelity vs float |
|---|---|---|---|---|---|---|
| H1 MLP (full) | trained W32_B1, all layers, one real illicit transaction | 1.18× | 1.28× | 1.92× | PASS | F1 0.505 vs 0.512; 99.5% agreement |
| H2 GCN | trained 2-layer GCN, node 36333, 3-node field | 1.13× | 1.36× | 1.76× | PASS | F1 0.732 vs 0.743; 99.0% agreement (declared 0.1–99.9 percentile clip) |
| H3 rule ×64 | `choose_action` + `is_violation`, 64 real decisions | 1.20× | 1.51× | 1.96× | PASS | 100% agreement |

### 5.4 Finding C1 — one post-quantum proof for a whole CBDC transaction decision

Pre-registered in `h4-pqc-stark/docs/h4_composed.md` before timing. One trace, one proof:
1. **H1 inference:** full inference of the trained H1 model on one real transaction. The logit stays private.
2. **Risk flag:** RISK = [logit > T_q], proven by a two-sided sign window. A test checks the window is unique over the circuit's whole interval-bounded logit range.
3. **H3 decision:** the H3 rule for one real decision, with RISK added as a violation condition: VIOL = (amount > limit) ∨ sanctioned ∨ RISK.
4. **Amount binding:** the rule's amount equals Σ 2ⁱ·bitᵢ of the SIS-committed amount, and committed bits 16–63 must be zero.

**Instance:** decision 1 (amount 7,558, compliant under the original rule) with the flagged H1 transaction. The risk flag turns a compliant transfer into a violation.

**Width:** a narrow single-decision rule (19 register columns plus one shared 22-bit window reused across 7 rows) keeps the trace within Winterfell's 255-column limit: 207 columns classical, 219 PQ.

**Tests: 20 / 20 pass.** Each of the following is rejected:
- a forged risk flag;
- a wrong EXEC or VIOL output;
- committed high bits;
- a register changed mid-trace;
- a decision made on an amount different from the committed one (the **classical circuit accepts this one; the PQ circuit rejects it**).

| Metric | Classical (no commitment) | PQ (SIS v3 + link) | Ratio | Bound | Result |
|---|---|---|---|---|---|
| Proof size | 61,659 B | 71,191 B | 1.15× | ≤ 20× | PASS |
| Prove time | 111.0 ms | 142.2 ms | 1.28× | ≤ 200× | PASS |
| Verify time | 19.6 ms | 34.8 ms | 1.77× | ≤ 10× | PASS |

Measured over 30 runs, with conjectured security of 126 bits.

Marginal cost of adding the decision and the link on top of H1-only PQ inference: about +6.0 KB, +32 ms prove and +3.4 ms verify (unpaired runs).

### 5.5 Finding C2 — overhead law (post-hoc)

Across the v3 workloads, PQ ≈ classical + c. The ratio is about 1 + c / classical cost, so it falls as the proven computation grows.

| Metric | c (added cost) | Range across workloads |
|---|---|---|
| Verify | ≈ 12 ms | 8–15 ms |
| Prove | ≈ 28 ms | 15–39 ms |
| Size | ≈ 8.4 KB | 7.8–10.2 KB, excluding the 2×2 workload |

The 2×2 workload is the exception: its 8-row trace is padded to 128 rows for the SIS block, which adds 26.5 KB.

**Out-of-sample check on the composed workload:**

| Metric | Predicted ratio | Measured ratio |
|---|---|---|
| Size | 1.14× | 1.15× |
| Prove | 1.26× | 1.28× |
| Verify | 1.61× (underpredicted) | 1.77× |

Figure: `h4-pqc-stark/benchmark_v2/results/overhead_law.png`.

### H4 verdict

**Supported (v3)** for every workload, including complete H1, H2 and H3 inference and the composed transaction proof. v2's primary-workload miss stays recorded.

### Limitations

- One instance per model class.
- One machine; the ratios are the reported quantity.
- The commitment provides binding, not hiding.
- No settlement-layer integration.
- H1's input features are not tied to the amount (Elliptic features are anonymized).

Evidence: `h4-pqc-stark/README.md`, `docs/h4_*.md`, `benchmark_v2/results/`.

---

## 6. How the pieces fit (the system story for the paper)

A CBDC transfer passes through four layers:
1. A risk model scores the transaction (H1, trained with or without federation, H2).
2. An autonomous agent decides whether to execute it, with financial liability attached (H3).
3. The decision is verifiable off-chain, with disputes settled by bisection (H1).
4. The cryptography stays secure against quantum adversaries (H4).

C1 makes this concrete: one post-quantum proof binds the model's output, the agent's decision and the committed amount. The overhead is under 2×, and per C2 it shrinks for larger models.

The negative results define where the design must change:
- federation needs a recall-anchored target, and more than three parties for DP to be usable (H2);
- liability needs stake maintenance and operator-bound identity (H3);
- on-chain dispute settlement only pays off at low dispute rates (H1).

---

## 7. Methodology and threats to validity

**Pre-registration.** The following were committed before their confirmatory runs, with commit hashes in each PR:
- H2 v2 and its matched-recall test;
- H3 durable liability;
- H4 v2, v3, coverage and the composed proof.

**Post-hoc work (labelled as such everywhere):**
- H3's majority-bar definition;
- the H3 wear-out observation on seed 42 (then confirmed on fresh seeds);
- the H4 overhead law.

**Disclosed iterations.** H4 v3 was designed after v2 missed its bound. Superseded results stay in the repository: H2 v1 and H4 v1.

**Statistics:**
- The unit of analysis is the seed (H2) or the agent (H3).
- Holm correction is applied across levels and primary tests.
- Paired designs are used throughout.
- The H3 action-level p-value is not cited (pseudo-replication).

**Main threats:**
- **Single dataset:** all ML results use Elliptic.
- **Simulation:** H2 institutions are simulated, and the H3 agents are simulated and myopic.
- **Hardware:** H4 timings come from one machine.
- **Synthetic composition:** the composed proof pairs a real H1 transaction with a real H3 decision; Elliptic has no amount field.

---

## 8. Reproducibility index

| Result | Command (from the hypothesis folder) | Output |
|---|---|---|
| H1 latency, accuracy, gas | see `h1-zkopml-v2/README.md` | `h1-zkopml-v2/results/` |
| H2 v2 | `python src/protocol_v2.py run --config experiments/configs/protocol_v2.yaml --seed <42..51>`, then `aggregate` | `results/v2/` |
| H2 matched recall | `python src/protocol_v2.py scores ... --seed <42..51>`; `python src/matched_recall.py --config experiments/configs/protocol_v2.yaml` | `results/v2_scores/` |
| H3 confirmatory | `python run_experiment.py --config configs/experiment.yaml`; `python run_agent_level.py` | `results/confirmatory_seed42.*`, `agent_level_seed42.json` |
| H3 durable liability | `python run_durable.py` | `results/durable_report.json`, `durable_actions.csv`, `durable_figure.png` |
| H3 Sybil probe | `python run_sybil.py` | stdout |
| H4 v2 / v3 / coverage / composed | `cd benchmark_v2 && cargo test --release && cargo run --release -- 30 [v3\|coverage\|composed] > results/benchmark_<x>.csv && python analyze.py results/benchmark_<x>.csv` | `benchmark_v2/results/` |
| H4 overhead law | `python benchmark_v2/overhead_law.py` | `results/overhead_law.*` |
| H4 lattice estimate | `docker run --rm -v "$PWD:/work" sagemath/sagemath:latest sage -python /work/est_h4.py` (in `docs/`) | `docs/h4_lattice_estimator.json` |

**Data:** Elliptic, from `https://data.pyg.org/datasets/elliptic/`. It is not committed.

**Toolchain:**
- Python 3.10, PyTorch 2.x, torch_geometric 2.8;
- Rust stable, with Winterfell fork `bwesterb/winterfell@b964cfd`;
- EZKL 23.0.5.
