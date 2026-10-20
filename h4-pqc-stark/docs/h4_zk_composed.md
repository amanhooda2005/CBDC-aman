# H4 v4: zero-knowledge composed decision proof on Plonky3, with an end-to-end security estimate

Code: `h4-pqc-stark/zk_composed/`
Results: `zk_composed/results/` (`bench_zk_composed.csv`, `bench_summary.json`, `security.json`, `info.json`)

## Why v4

The v3 composed proof (`benchmark_v2/src/compose.rs`, Winterfell fork v0.4) is **not zero-knowledge**.
Winterfell's own README states: "NOT perfect zero-knowledge … may not be suitable for use cases where
proofs must not leak any info about secret inputs". So the earlier claim that "the risk score stays
private" was only true in a narrower sense: the score was not a public input, but the proof still leaks
information about it.

v4 re-implements the same statement on Plonky3, using its hiding mode:
- hiding FRI with random codewords;
- salted (hiding) Merkle trees.

## Statement (unchanged semantics, one execution trace of 256 rows)

1. **Model inference.** Exact integer inference of the trained H1 model (W32_B1, `specs/h1_spec.json`),
   using the same streaming layout and constraints as `h1mlp.rs`:
   - range-checked private inputs;
   - every requantization and ReLU proven;
   - interval-bounded accumulators.
2. **Risk flag.** RISK = [logit > T_q] by the two-sided sign window. Over BabyBear (p ≈ 2³¹) the
   window is unique for every in-range logit (|logit| < 2²², far below p − 2³⁰).
3. **Agent decision.** The H3 decision rule with RISK as a violation condition:
   VIOL = (A > M) ∨ (D = sid) ∨ RISK; EXEC = 1 when VIOL = 0 (a compliant transfer is executed), otherwise
   EXEC = [q·VP − p·min(S, slash) > q·CP] (the H3 rule). Before v4.1 the H3 refuse-on-tie also refused every
   compliant transfer, so nothing could ever execute; the integrated runs exposed it.
   - **Change from v3:** M, sid and the slash are **public inputs**, not constants. The settlement layer
     can then pass the registry's escalated slash s·(1+k).
4. **Amount binding (post-quantum variant).** The decision's amount A equals the amount inside the SIS
   commitment c, which is published as public input:
   - A = Σ 2ⁱ·bitᵢ over bits 0–19;
   - bits 20–63 are forced to zero.

**Domain (v4.1).** Comparisons share one 30-bit signed window. It is sound and complete when M, sid,
slash and the stake are below 2²⁰ (amount below 2²⁰ via its bits, payoffs below 2¹²/2¹³ via their
windows): the largest compared term, q·VP + p·min(S, slash) + q·CP, then stays below 2²⁹, and every window
value stays below p − 2³⁰, so each sign decomposition is unique. The verifier rejects statements whose
public M, sid or slash are outside this domain; the prover refuses such witnesses rather than clamping.
v4 used a 22-bit window, which only covered the unescalated slash: with the registry's escalated slash
s(1+k) at 2–4× the base value, honest proofs failed verification (found in the integrated runs,
`integration/README.md`).

Public values: M, sid, slash, EXEC, VIOL and c (48 coordinates). The features, logit, payoffs, stake and
opening stay private, and in the ZK variant they are also hidden by the proof.

**How the SIS opening is checked.** It is checked directly, row by row. The public matrix enters the
AIR as 96 verifier-computed periodic columns, and there are 48 accumulators, each forced equal to cⱼ in
the last row.

Why not v3's random linear combination: it needs a verifier challenge drawn after the opening is
committed, and Plonky3's single-trace STARK (uni-stark) has no such round. Deriving the challenge from
c alone would be unsound: the prover would then face a dimension-5 SIS instance it can solve. The direct
check proves the same statement without that shortcut.

## SIS parameters re-derived for BabyBear

c = G·bits + B·r (mod p), with:
- p = 2³¹ − 2²⁷ + 1;
- bits ∈ {0,1}⁶⁴ and r ∈ {−1,0,1}⁴⁴⁸ (512 coordinates);
- **n = 48** rows.

**Binding is statistical.** There are at most 5⁵¹² ≈ 2^1188.8 short difference vectors and
p⁴⁸ ≈ 2^1483.5 syndromes, so for a uniform matrix the expected number of short collisions is
**2^−294.7** (test `sis_parameters_are_statistically_binding`). The matrix is expanded by the verifier
from a public seed.

The commitment is binding, not hiding. The amount itself is disclosed to the validator nodes, which
check the opening before attesting; see `integration/`.

## Tests (`cargo test --release`, 17/17 pass)

- **Honest traces** satisfy every constraint for 8 different H3 decisions, in both variants.
- **Prove and verify** work in all four variants (classical, pq, classical_zk, pq_zk).
- **The proof is randomized:** two ZK proofs of the same statement differ, and both verify.
- **Each forgery is rejected:**
  - a forged risk flag;
  - a forged EXEC or VIOL;
  - a decision on an amount different from the committed one (accepted by the classical circuit,
    rejected by the PQ circuit);
  - committed high bits set;
  - non-ternary randomness;
  - a register changed mid-trace;
  - tampered inference cells;
  - an out-of-range input.
- **Mismatched public data is rejected by the verifier:**
  - flipped EXEC or VIOL;
  - a changed policy;
  - a changed commitment;
  - a different model digest.
- **Completeness over the whole policy domain** (v4.1): 135 combinations of escalated slash (up to
  2²⁰ − 1), stake and extreme payoffs satisfy every constraint, and an escalated-slash proof verifies.
- **Out-of-domain** witnesses and statements are refused.
- **EXEC rule:** compliant transfers execute (EXEC = 0 is rejected for them); a violating offer is executed
  under the base slash and declined under a 4× escalated slash.
- **The reference forward pass** reproduces the spec's logit (11,040) and the v3 instance's outcome
  (RISK = 1, VIOL = 1).

## Performance (v4.1; 30 runs after 3 warm-ups, 2-vCPU cloud VM, parallel prover)

| Variant | Proof size | Prove (ms) | Verify (ms) |
|---|---|---|---|
| classical (no commitment, not ZK) | 99,573 B | 179.8 ± 9.3 | 8.75 ± 1.44 |
| pq (SIS + link, not ZK) | 113,037 B | 227.3 ± 21.0 | 10.37 ± 2.10 |
| classical_zk | 159,349 B | 540.3 ± 69.0 | 15.43 ± 4.41 |
| **pq_zk (deployed)** | **170,157 B** | **594.1 ± 82.7** | **15.15 ± 3.97** |

| Ratio | Size | Prove | Verify | Pre-registered bound |
|---|---|---|---|---|
| pq / classical (as in v3) | 1.14× | 1.26× | 1.19× | ≤ 20× / ≤ 200× / ≤ 10× |
| **pq_zk / classical_zk** (cost of PQ binding under ZK) | **1.07×** | **1.10×** | **0.98×** | same |
| classical_zk / classical (cost of ZK) | 1.60× | 3.01× | 1.76× | none |

Absolute times are higher than the v4 table (pq_zk 452.8 ms) because this VM was slower and noisier on
the day; on one machine, back to back, the 22-bit (v4) and 30-bit (v4.1) layouts took 528 ms and 537 ms
for pq_zk, and the proof grew by about 1 %. Constraint count 491 → 499.

All within bounds. These ratios replace v3's 1.15× / 1.28× / 1.77× for the deployed (ZK) setting.

## Security estimate (whole proof, not only the commitment)

### Classical
From Plonky3's p3-security report (`zk_composed security`, `results/security.json`). Parameters:
- BabyBear⁵ challenge field (≈ 154.5 bits);
- FRI blowup 16, 48 queries, 16-bit query grinding;
- Poseidon2 Merkle trees with an 8-element (248-bit) digest.

| Measure | Bits |
|---|---|
| Low-degree test alone (ethSTARK conjecture: 4·48 + 16) | 208 |
| **End-to-end, conjectured** (capped by the 124-bit collision resistance of the digest) | **124** |
| **End-to-end, proven** (list-decoding regime) | **111** |
| End-to-end, proven (unique-decoding regime) | 59 |

How the parameters were chosen:

| Blowup / queries | Conjectured | Proven (list decoding) | Prove time | Note |
|---|---|---|---|---|
| 8× / 38 | 124 | 72 | — | rejected: too few proven bits |
| **16× / 48** | **124** | **111** | — | **chosen** |
| 32× / 40 | 124 | 113 | ~2× the chosen setting | rejected: double the prove time for 2 bits |

A degree-4 challenge field gave only 103 conjectured bits, which is why the field is degree 5.

### Quantum
The proof uses no number-theoretic assumption: only hashing, polynomial identity testing and SIS.

- **Commitment binding.** Statistical (2^−294.7). It holds against unbounded, including quantum,
  adversaries.
- **Hash.** The 248-bit Poseidon2 digest has these quantum collision costs:
  - about 2^82.7 with Brassard–Høyer–Tapp, which needs about 2^83 quantum-accessible memory;
  - about 2^99.2 with the Chailloux–Naya-Plasencia–Schrottenloher algorithm, without quantum memory;
  - about 2^124 for Grover second preimages.
- **Fiat–Shamir in the quantum random-oracle model.** For hash-based (BCS) arguments the generic
  bound loses a square (Chiesa–Manohar–Spooner, TCC 2019). A t-query quantum adversary succeeds with
  probability about t²·ε, so the quantum bits are about half the classical bits. Grinding is covered by
  the same factor: Grover halves the 16-bit proof of work.

| End-to-end, against a quantum adversary | Bits |
|---|---|
| Conservative (generic QROM loss applied to the 124-bit conjectured figure) | **≈ 62** |
| Conservative (generic QROM loss applied to the 111-bit proven figure) | ≈ 55 |
| Without the QROM loss, limited by the best quantum collision attack on the hash | ≈ 99 |

**Comparison with the earlier construction (state this when quoting v4):** the v4 proof's quantum estimate
(≈ 62 bits) is **weaker** than v3's under the same argument (≈ 63 bits; v3 is 126-bit conjectured against
v4's 124), and far below the "≥ 2⁶¹⁵ quantum" quoted earlier, which described only the commitment. v4 gains
zero-knowledge and pays about 2 bits of conjectured security, set by the 248-bit Poseidon2 digest.

**Single number to state:** **124-bit classical (conjectured) / 111-bit proven**, and **≈ 62-bit
quantum security under the conservative generic QROM bound.** The lattice commitment's binding is
unconditional.

The earlier "≥ 2⁶¹⁵ quantum" figure described only the commitment and should not be quoted as the
security of the proof. v3's Winterfell configuration (126-bit conjectured, 23-bit field, Blake3-256)
would get about 63 bits under the same QROM argument.

### Upgrade path to ~128-bit quantum (not implemented)
The QROM argument needs about 2λ classical bits for λ quantum bits:
1. **Challenge field:** BabyBear⁸, 247 bits (supported by Plonky3).
2. **Low-degree test:** ≥ 256 bits, e.g. blowup 16 with 60 queries and 16-bit grinding.
3. **Hash:** a Merkle hash and transcript hash with ≥ 512-bit output, e.g. SHA-512 or BLAKE3 in
   extended-output mode wired into Plonky3's byte-hash commitment scheme (MMCS). This requires custom
   hasher and compression-function implementations.

Expected cost: roughly 1.3–1.6× proof size and verify time. This is a parameter change only; the AIR
is unchanged.

## Limitations

- **One proof per transaction.** There is no batching or recursion yet.
- **The amount is visible to validators.** The commitment binds the amount but does not hide it.
- **Model features are not bound to an on-chain transaction record.** Elliptic has no amounts, and
  the pipeline pairs features with decisions.
- **Timings come from one 2-vCPU VM.** Ratios are the reported quantity.
