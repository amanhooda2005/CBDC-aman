# Integration: the three hypotheses as one settlement system on a BFT ledger

This folder joins H1 (risk model), H3 (liability registry) and H4 (post-quantum ZK proof) into one
working pipeline on a local permissioned blockchain. It is the lab validation for TRL 4: the
components are no longer tested one at a time; they run together and every transfer goes through all of
them.

## What runs

| Part | What it is | Code |
|---|---|---|
| Ledger | Hyperledger Besu 25.10, 4 validators, QBFT consensus (tolerates 1 faulty), 1 s blocks, chain id 20261, zero gas price | `chain/` |
| Liability registry (H3) | Operator credentials, soulbound agent ids, registration bond B ≥ B\*, stake maintenance max(S, s(1+k)), escalating slash s(1+k) keyed by operator, delayed slashable withdrawals | `../h3-abt/contracts/LiabilityRegistry.sol` |
| Settlement gate | CBDC ledger (lab), customer mandates, 3-of-4 validator quorum, outcome rules below | `contracts/SettlementGate.sol` |
| Prover (operator node) | Composed proof, Plonky3 STARK, zero-knowledge + SIS amount commitment: H1 inference → risk flag → H3 decision on the committed amount | `../h4-pqc-stark/zk_composed/` |
| Validator service (one per validator node) | Checks the proof natively, the model digest, the commitment opening, the outputs and the current policy read from its own chain node; signs the request (EIP-191) | `pipeline/validator_node.py` |
| Driver | Transactions in, settlement out, fault injection, CSV + summary | `pipeline/run_pipeline.py` |

### One transaction

1. Features → quantized H1 input (Elliptic test period, or a synthetic input inside the model's box).
2. A transfer offer from the H3 opportunity generator (amount, destination, payoffs).
3. The operator keeps its stake above the next escalated slash (top-up when needed; the gate checks it too).
4. The operator node proves, in zero knowledge, that the trained model was run on the transaction, that the
   risk flag and the agent's EXEC/VIOL follow from it under the **current** policy (limit, sanctioned id,
   the registry's escalated slash), and that the decision was made on the amount inside the commitment.
5. The four validator services check the request independently; 3 signatures are needed.
6. `SettlementGate.settle` on the QBFT chain:

| Proven outputs | Outcome |
|---|---|
| EXEC = 1, VIOL = 1 | **Blocked**; operator slashed s(1+k), k increases |
| EXEC = 0, VIOL = 1 | **AgentRefused**: the agent declined a violating transfer; nothing moves, no slash |
| agent not covered | **NotCovered** (stake maintenance) |
| EXEC = 1, VIOL = 0 | **Executed** on the CBDC ledger |
| EXEC = 0, VIOL = 0 | AgentRefused (cannot arise from the proven rule, which executes every compliant transfer) |

## Results

Lab setup: one 2-vCPU cloud VM running the four Besu nodes, the four validator services and the operator
node together. All runs use the deployed variant (post-quantum + zero-knowledge).

| Run | Validators | Tx | Honest tx settled as the rules require | Faulty tx | Faulty tx reaching quorum | Ledger changes from faulty tx |
|---|---|---|---|---|---|---|
| `runs/synthetic_1000` | 4 honest; one node crashed mid-run and was restarted (see below) | 1,000 | **899 / 900** | 100 | **0** | **0** |
| `runs/synthetic_300_byz3` | 3 honest + 1 Byzantine (signs everything) | 300 | **244 / 244** | 56 | **0** | **0** |
| `runs/synthetic_60_byz23` | 2 honest + 2 Byzantine | 60 | 26 / 26 | 34 | 0 | 0 |
| `runs/synthetic_40_down1` | 3 honest, 1 crashed | 40 | 25 / 25 (3 signatures each) | 15 | 0 | 0 |

**The 1,000-transaction run had a real crash.** Before it started, Besu node 3 had been killed by the
kernel's out-of-memory killer while an EZKL proving job ran on the same 8 GB machine. The chain kept
finalizing blocks with 3 of 4 validators, and validator 3 (which reads from node 3) refused to sign, so
transactions 0–372 settled with 3 signatures. Node 3 was then restarted, re-synced within seconds, and
from transaction 373 every honest transaction got 4 signatures. One honest transaction (no. 77) did not
settle: while node 3 was down, a second node answered one read with a transient JSON-RPC "Internal
error", leaving 2 of 4 signatures, beyond the one fault the network tolerates. Nothing moved (a safe
failure). Validators now retry transient read errors (3 attempts).

Injected faults (each refused by every honest validator, with the reason recorded per transaction):

| Fault | Refused because |
|---|---|
| forged VIOL (`flip_viol`) | the proof does not verify against the claimed outputs |
| amount swapped after proving (`amount_swap`) | the commitment does not open to the settled amount |
| proof made with an outdated slash (`stale_slash`) | the policy slash differs from the registry's current s(1+k) |
| corrupted proof bytes (`corrupt_proof`) | the proof does not deserialize / verify |
| a different model (`wrong_model`) | the statement's model digest is not the deployed model |

Honest outcomes in the 300-transaction run: 97 Executed, 146 AgentRefused (violating offers declined),
1 Blocked (the operator's first violation; after the escalation to 2s the agents decline every violating
offer they are given).

Timing and cost (honest transactions, mean):

| Stage | 300-tx run (all nodes up) | 1,000-tx run (incl. the node outage) |
|---|---|---|
| Prove (operator node) | 720 ms | 697 ms |
| Attest (4 validators in parallel, incl. network and native verification) | 277 ms | 239 ms |
| Settle (transaction included in a 1 s QBFT block) | 903 ms | 1,527 ms |
| **End to end** | **1.96 s** (p95 2.20 s) | **2.51 s** (median 2.01 s, p95 5.12 s) |
| Throughput (sequential driver, one operator) | 34 tx/min | 26 tx/min |

| | |
|---|---|
| Settle gas (300-tx run) | Executed 103,261 · AgentRefused 84,009 · Blocked (incl. slash) 107,773 |
| Settle gas (1,000-tx run) | Executed 99,973 · AgentRefused 81,209 |
| Proof size | ~170 KB (sent to validators off-chain; only its hash goes on-chain) |

### Defects found by the integrated runs (fixed)

1. **Completeness bug in the composed proof.** The AIR's shared comparison window was 22 bits. Once the
   registry escalated the slash to 2–4× the base value, the term q·VP − p·min(S, slash) − q·CP left the
   window; the honest prover clamped it and produced proofs that failed verification (145 of 244 honest
   transactions in the first 300-transaction run). Fix: the window is 30 bits, the statement domain is
   stated (policy values and stake < 2²⁰, checked by the verifier on the public values and by the prover
   on the witness), and the prover refuses out-of-domain witnesses instead of clamping. Regression test:
   `escalated_slash_and_extreme_payoffs_stay_complete` (fails on the 22-bit layout, passes now). Cost:
   +8 trace columns, +1 % proof size, ≈ +2 % proving time measured on the same machine.
2. **No transfer could ever execute.** The proven rule used H3's refuse-on-tie, so a compliant transfer
   (execute payoff = refuse payoff) was always refused; and the gate slashed every VIOL = 1 request, even
   one the agent had declined. Fix: EXEC = 1 for compliant transfers, the H3 rule for violating ones; the
   gate slashes only an attempted violation (EXEC = 1, VIOL = 1). Tests:
   `compliant_transfers_execute_and_violating_ones_follow_the_rule`,
   `test_declinedViolation_neitherMovesNorSlashes`.
3. Validators now wait until their own node has reached the block at which the operator read the policy,
   so a node that imports a block a few milliseconds late does not reject a correct proof.

## Running it

Requirements: Besu 25.x (Java 21), Foundry, Python 3.11+ with `web3`, `eth-account`, `numpy`, `pandas`;
the prover binary (`cd ../h4-pqc-stark/zk_composed && cargo build --release`).

```bash
cd integration
forge build                                   # contracts -> out/
BESU=/path/to/besu chain/start_chain.sh       # 4-node QBFT network
cd pipeline
python deploy.py                              # registry + gate, operator, 4 agents, customers
./start_validators.sh                         # BYZANTINE=3 or DOWN=1 for fault runs
python run_pipeline.py --n 300 --source synthetic --faults 0.2 --seed 11 --out runs/my_run
python run_pipeline.py --n 1000 --source elliptic --data /path/to/elliptic_bitcoin_dataset --faults 0.1
./stop_validators.sh; ../chain/stop_chain.sh
```

The chain keys in `chain/networkFiles/` are generated locally and are not committed. The funded
accounts are the well-known Anvil/Hardhat development keys: lab use only.

## Limits (what this is not yet)

- One machine, loopback network; no geographic distribution, no adversarial network delays.
- The CBDC ledger is a contract balance map, not a central-bank ledger; no privacy for amounts towards
  validators (they see the opening).
- The driver is sequential with one operator; throughput is not optimized (proving is the bottleneck,
  and proofs for different transactions can run in parallel).
- Synthetic inputs never trigger the H1 risk flag; the Elliptic run exercises it (pending: the Elliptic
  CSVs were not in this workspace).
- The prover, the four nodes, the validators and other jobs share one 8 GB machine; that is what let an
  unrelated proving job take down a node.
