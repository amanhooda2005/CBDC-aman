# Part 6A — The integrated system (TRL 4), October 2026

**Added by:** Aman Hooda (PRs #36–#39), after the four tracks were complete. **Independent checks by the mentor:** §6A.7.

Code: `integration/` (its README has every table), `h1-zkopml-v2/` (v2, v2.1), `h3-abt/contracts/`, `h3-abt/docs/PREREGISTRATION_learning.md`, `h4-pqc-stark/zk_composed/`, `h4-pqc-stark/docs/h4_zk_composed.md`, `docs/EVIDENCE.md`, `docs/INDEPENDENT_VALIDATOR.md`.

**What changed in the picture.** Parts 2–5 describe four tracks, each run as a simulation or benchmark. This part covers the later work that runs them together as one system on a real Byzantine-fault-tolerant ledger. It also replaces some statements in Parts 4–5:
- The ABT registry now also exists as an **on-chain Solidity contract** (§6A.3).
- A **settlement-layer integration** now exists at lab scale (§6A.1). It is not a production ledger.
- The composed proof has a second implementation with **zero-knowledge hiding** (§6A.5), whose security estimate is **weaker** than v3's (§6A.5).
- The H1 dispute game now runs on-chain with real proof verifiers (§6A.2), which **changes the latency conclusion** for the shared-verifier design.

## 6A.1 The integrated pipeline

**Design (from `integration/README.md`):**
- **Ledger:** Hyperledger Besu 25.10, 4 validators, QBFT consensus (tolerates 1 faulty), 1-second blocks, chain id 20261, zero gas price.
- **Operator node:** for each transfer, one zero-knowledge Plonky3 STARK proves:
  - the H1 inference (`W32_B1`);
  - the risk flag;
  - the H3 decision under public policy values (limit, sanctioned id, the registry's current escalated slash);
  - the link between the decided amount and an SIS commitment.
- **Validators:** each verifies the proof natively (not in the EVM), the model digest, the commitment opening against the settled amount, the outputs and the current policy (read from its own node). It then signs (EIP-191).
- **Contracts:** `SettlementGate` (quorum of signatures, customer mandates; outcomes Executed / AgentRefused / Blocked + slash / NotCovered) and H3's `LiabilityRegistry`.
- **Fault injection:** a share of requests is deliberately falsified in one of five ways: wrong model, amount swap, stale penalty, corrupted proof, flipped verdict.

**Runs** *(generated from `integration/pipeline/runs/*/summary.json`)*:

{{gen:int_runs}}

**The real-data run in detail** *(generated from `runs/elliptic_1000/summary.json`)*:

{{gen:int_elliptic_detail}}

**Reading.**
- **Integrity held in every run.** Every honest transfer settled as expected, and no falsified request reached the quorum or changed the ledger.
- **Liveness cost:** in the run where a validator node crashed, one honest transfer was not submitted because its validator was unreachable (899 of 900 settled).
- **Detection is weak.**
  - Of 69 illicit transactions, the risk flag caught 50 (recall 0.72), and 8 illicit transfers were executed.
  - The flag catches none of the 7 illicit transactions after time step 43, the known post-shutdown collapse of Elliptic models.
  - The system enforces *what the proven rule says*; it does not make the underlying model better.
- **Speed:** about 2 s per transfer, one operator, sequential.

**Defects found by integration, fixed with regression tests:**
1. The composed proof's 22-bit comparison window could not hold the decision term once the registry escalated the slash. Honest proofs failed (145 of 244 in the first 300-transaction run). It is now 30 bits with a stated domain (policy values and stake below 2²⁰), checked by prover and verifier.
2. The proven rule refused every compliant transfer (the H3 refuse-on-tie rule), and the gate slashed declined violations. Compliant transfers now execute and only attempted violations are slashed.

## 6A.2 H1 v2 and v2.1: a hardened dispute contract with real verifiers

**v2 (`OptimisticInferenceV2.sol`):** full on-chain adjudication; a prefix-consistency check (which blocks a malformed-chain attack that v1 allowed); bonds; per-turn deadlines; per-challenger disputes; slashable delayed withdrawals. Tests: 17 Foundry + 66 Python.

**v2.1:** complete dispute games on a local chain with real EZKL-generated Solidity verifiers and real step proofs. Steps of the same shape share one verifier: the weights become a *hashed public instance* that the contract checks against the step's stored weight hash. The 60-block model needs 4 verifiers instead of 63.

*(generated from `results/gas_v2.json`, `onchain_disputes_B60*.json`, `same_machine_step_vs_mono.json`)*

{{gen:h1_v21}}

**Reading.**
- **The latency conclusion changes.**
  - The 14.9× speedup (Part 2) holds only for the **fixed-weights** design, at a deploy cost of about 251 M gas for 63 verifiers.
  - With the **shared** verifiers (17.2 M deploy gas) a disputed block step takes about as long as the whole-model proof (1.13× faster), and the slowest step (a projection half) is **2.8× slower** than the whole model (0.36×).
  - The same-machine comparison has only 2 trials per row.
- **Gas:** a dispute costs 1.58–1.91 M gas; an undisputed inference is 12.7× cheaper than monolithic verification; the 10× gas target holds only for dispute rates below about 0.79% (worst case) or 0.93% (typical).
- **The attack a shared verifier opens is closed:** a valid proof made with another block's weights verifies on the verifier, but the game contract rejects it (`params instance != step weights`) and the challenger still wins.
- **Limit:** the projection verifier's bytecode (24,644 bytes) is just over the EIP-170 limit of 24,576 bytes, so a permissioned chain must raise the limit (Besu `contractSizeLimit`).

## 6A.3 H3 on-chain registry and the learning-agent study

**`LiabilityRegistry.sol`** (9 Foundry tests): the ABT registry from Part 4 as a contract, including operator-bound history, escalating slash, stake maintenance, registration bond at the break-even, non-transferability, and slashable pending withdrawals.

**Learning-agent study** (pre-registered in `h3-abt/docs/PREREGISTRATION_learning.md`, committed before the run): the agents of Part 4 were myopic best-responders. Here they *learn* by ε-greedy exploration under *imperfect* detection (p = 0.5 and 0.8).

*(generated from `h3-abt/results/learning_report.json`)*

{{gen:h3_learning}}

**Reading.** Both earlier findings hold for learning agents under imperfect detection: stake maintenance beats a flat stake late in the run, and operator-bound history beats id-keyed history. All four pre-registered tests pass with Holm p ≤ 3.2 × 10⁻⁷. With a bond at the break-even, strategic resets disappear; the resets that remain are exploration. Note that at p = 0.5 the *overall* violation rate of the flat regime (14.4%) is not higher than maintenance (14.8%): the difference appears late in the run, as depleted agents become judgment-proof.

## 6A.4 The Plonky3 composed proof (H4 v4 / v4.1)

The composed proof of Part 5 (§5.5) was re-implemented on Plonky3 with **zero-knowledge hiding** (the v3 Winterfell proof had none). SIS was re-derived for the BabyBear field: 48 rows, statistical binding with about 2⁻²⁹⁴·⁷ expected collisions. The opening is checked row by row. 17 tests pass (forged risk flag, wrong outputs, high committed bits, a different amount than the committed one, tampered inference, out-of-domain witnesses, and so on).

*(generated from `h4-pqc-stark/zk_composed/results/`)*

{{gen:h4_v4}}

## 6A.5 Quantum security of the proofs: what the numbers do and do not say

These are three different numbers. Quoting any of them without its qualifier is misleading.

| Quantity | Value | What it covers |
|---|---|---|
| SIS commitment binding (Winterfell v2/v3) | ≥ 2⁶¹⁵ quantum attack cost (lattice estimator); about 2⁻⁶³⁶ expected short collisions | **Only the commitment.** Statistical binding. |
| Whole v3 proof (Winterfell) | 126-bit conjectured; about **63 bits quantum** under the conservative argument | The STARK's soundness (hash-based). |
| Whole v4 proof (Plonky3, zero-knowledge) | 124-bit conjectured, 111-bit proven (classical); about **62 bits quantum** under the conservative QROM bound | The STARK's soundness. |

The v4 proof therefore gains zero-knowledge and loses about 2 bits of conjectured security, set by its 248-bit Poseidon2 digest. The "≥ 2⁶¹⁵" figure must **not** be quoted as the security of a proof. "Post-quantum" here means that the proof system uses hash functions and lattices and has no known efficient quantum attack at these parameters; a conservative estimate of 62–63 bits quantum is below the common 128-bit target.

**Overhead of post-quantum binding under zero-knowledge** (from the table above): size 1.07×, prove 1.10×, verify 0.98× versus the same zero-knowledge proof without the SIS opening. Absolute: about 170 KB, 0.6 s prove, 15 ms verify.

## 6A.6 Audit of the ledger, and the independent-validator run

**Audit of every integrated run against the ledger itself** *(generated from `integration/pipeline/audit/audit_report.json`)*. Aman's script (`audit_chain.py`) reads every block, decodes each `settle` call, recovers the validator signatures, and compares with each run's CSV:

{{gen:int_audit}}

On the Elliptic deployment, the merchant's CBDC balance (1,930,131) equals the sum of the amounts the CSV records as Executed.

**Independent-validator run (20–21 Oct 2026).** All four validator keys in the runs above were the team's. To address that, the run was split across **two people on two machines** (connected over Tailscale), following `docs/INDEPENDENT_VALIDATOR.md`:
- **Host** (a person outside the team, his own MacBook): the 4-node QBFT network, the contract deployment, three validators and the 200 transfers.
- **Reviewer** (Aman Hooda, his own MacBook Air, macOS on Apple Silicon, Besu 25.10.0): a separate Besu node synced from the host's network and the fourth validator. Its key was generated on that machine and never shared. It used its own build of the proof verifier (same model digest).
- The `SettlementGate` was deployed with **threshold 4**, so neither person could settle alone.

*(generated from `integration/evidence/independent_validator/`)*

{{gen:ind_validator}}

**Reading.** The reviewer's validator signed exactly the 166 honest requests and refused exactly the 34 falsified ones, each for the matching reason. The three host validators made identical decisions on all 200 requests. In the first attempt, where the reviewer's validator could not be reached, nothing settled, which shows the strict gate working.

**Limits of this run.**
1. The reviewer is a team member, and the host is an outside person (Aman describes him as a roommate) following the team's instructions. It shows that the gate needs a key the host does not hold, not that an unrelated third party audited the system.
2. Both sides run the team's code. The verifier was built separately on each machine.
3. The ledger was not exported at first; the reviewer's ledger (3,033 blocks) was exported later (PR #41). The mentor decoded the export and checked that it matches its SHA-256, that all 166 settlement transactions carry 4 signatures and match the host's records on amount, EXEC and VIOL, and that no falsified request settled. Re-execution in a fresh Besu node was done by the reviewer (Aman), not by the mentor.

## 6A.7 What the mentor independently verified, and what not

Checks were made between 15 and 21 Oct 2026 on the mentor's own machine, using Aman's evidence files, with no access to his machines.

**Reproduced independently:**

| Check | Result |
|---|---|
| Test suites on a clean checkout of the PR branch | 234 / 234 pass: H3 pytest 99, H1 pytest 66, H4 `zk_composed` 17, H4 `benchmark_v2` 20, Foundry H1 17, H3 9, integration 6 |
| Summaries recomputed from the raw CSVs (all five runs) | identical |
| H1 risk-flag precision, recall, F1 and the decision rule (viol = policy OR risk flag) recomputed from the raw rows | identical (0 exceptions in 889 rows) |
| Exported ledger decoded with an independent RLP decoder | 8,758 blocks (head 8,757); 2,197 settle calls; per-gate counts match the audit report |
| Exported ledger imported into a fresh **Besu 25.10** node (re-executing all 8,757 blocks); Aman's audit script run against that node | head 8,757; audit report identical; per-transaction table of 2,197 rows **byte-identical** |
| Recorded on-chain dispute run: all 116 original *signed* transactions replayed on an empty `anvil` 1.8.5 | 116 / 116 match logged status and gas; 7 / 7 winners agree; all 7 proofs verify on-chain; a copy with one byte flipped is rejected |
| Independent-validator records, rechecked with separately written code | the reviewer signed exactly the 166 honest requests; the 8 amount-swap requests it received carried the committed amount plus 1 and were refused; identical decisions on all 200 requests by all three host validators; 4 signatures on every settled request and 0 on every falsified one |

**Not independently verified:**
- The **live runs themselves** (Besu chain, EZKL proving and key generation, the pipeline) and **all timings**, which come from Aman's machines. Records and ledger were replayed; the executions were not repeated.
- The **Besu re-execution** of the reviewer's exported ledger (done by the reviewer; the mentor checked it by decoding the export, not by re-executing it).
- The **H3 learning-study** and **H4 v4** results beyond their passing test suites.
- `bench_shared_vs_mono.py` (needs EZKL and a 3 GB key setup).

**Problems found during verification and fixed by Aman (PR #38):** the audit script, evidence file and logs were missing from PR #37; the H1 README did not say that the shared-verifier proof is no faster than the whole model; the recorded `anvil` state could not be loaded by current Foundry builds (the replay mode of `verify_dispute_record.py` fixes this); the exhaustive report was not reproducible from its build script.
