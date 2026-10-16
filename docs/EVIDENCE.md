# Evidence for the integrated runs and the on-chain disputes

This page is for a reviewer who wants to confirm the TRL-4 numbers instead of taking them from our
summaries. It lists the raw records, the checks we ran against the ledger itself, and how to repeat
each check, from cheapest to strongest.

| Claim | Where the number comes from | How to check it yourself |
|---|---|---|
| Integrated runs (Elliptic 1,000 tx and the four synthetic runs): every honest request settled as recorded, no faulty request settled | `integration/pipeline/runs/*/transactions.csv` (driver record) | **Level 2:** replay the exported Besu chain and run `audit_chain.py` (10 min) |
| H1 on-chain disputes with real EZKL verifiers: 7/7 scenarios correct | `h1-zkopml-v2/results/onchain_disputes_B60*.json` | **Level 2:** load the recorded chain state and run `verify_dispute_record.py` (2 min); **Level 3:** re-run `onchain_dispute.py --record` |
| Test suites | the repository | `forge test`, `pytest`, `cargo test` (already confirmed by the reviewer) |

## Level 1 – the raw records

**Integrated pipeline (`integration/`)**
- `pipeline/runs/<run>/transactions.csv` – one row per request: index, Elliptic tx id and label, injected
  fault, amount, proven EXEC/VIOL, number of validator signatures, each refusing validator's reason, outcome,
  gas and timings. `summary.json` is computed from it.
- `evidence/run_logs/run_*.log` – the driver's console output of the last three long runs
  (`run_elliptic.log` = Elliptic 1,000; `run_1000.log` = synthetic 1,000; `run_byz3.log` = 300 with a
  Byzantine validator). The 60- and 40-transaction runs printed their summary to the terminal only; their
  CSVs and summaries are in `runs/`.
- `evidence/besu_node_logs/node{0..3}.log.gz` – the four Besu validators' own logs (warning level).
- `evidence/cbdc_besu_chain.blocks.gz` + `evidence/genesis.json` – **the whole ledger**: every block
  (8,757) and every transaction of all runs, exported from validator node 0 with `besu blocks export`.

**On-chain disputes (`h1-zkopml-v2/`)**
- `evidence/original_run_logs/` – console logs of the runs behind the committed result files (one
  line per scenario with the located step, winner and gas).
- `evidence/recorded_rerun_elliptic/` – a re-run made on 16 Oct 2026 with recording switched on, on the
  same real Elliptic transaction (test index 3,915): `transactions.jsonl` (all 116 transactions with
  hashes, status and gas), `proofs/` (every step proof and the exact `verifyProof` calldata),
  `anvil_state.json` (the complete chain state), `result.json` and `verification_report.json`.
  The original dispute runs used a throw-away local chain and kept only the outcome per scenario, which
  is why this recorded re-run exists.

## Level 2 – checks against the ledger (no trust in our CSVs or summaries)

### Integrated runs: `integration/pipeline/audit_chain.py`
The script reads every block of the chain, finds every `SettlementGate.settle` transaction, decodes the
request inside it, recovers the validator signatures (EIP-191) and checks them against the QBFT
validator set, reads the receipt, gas and `Settled` event, and only then compares with each run's CSV.
The request nonce is `run_start_time × 100000 + index`, which ties every on-chain request to one CSV row.

Result (`pipeline/audit/audit_report.json`; per-transaction table in `settlements_onchain.csv`):

| Run | CSV rows settled | `settle` transactions on the chain | Rows matching the chain exactly (index, amount, EXEC, VIOL, outcome, gas) | Faulty requests with any `settle` transaction | Fewest validator signatures on a settled request |
|---|---|---|---|---|---|
| elliptic_1000 | 889 | 889 | **889** | **0 of 111** | 4 |
| synthetic_1000 | 899 | 899 | 899 | 0 of 100 | 3 (node 3 was down for the first 373 tx) |
| synthetic_300_byz3 | 244 | 244 | 244 | 0 of 56 | 4 |
| synthetic_60_byz23 | 26 | 26 | 26 | 0 of 34 | 4 |
| synthetic_40_down1 | 25 | 25 | 25 | 0 of 15 | 3 (one validator off by design) |

- 2,197 `settle` transactions in total, none reverted, every one signed by ≥ 3 distinct QBFT validators
  and by nobody else.
- **Ledger balances:** the Elliptic run used a fresh deployment. On that contract the merchant's CBDC
  balance is 1,930,131, exactly the sum of the amounts the CSV records as Executed. The customers hold
  the rest of the 4 × 10¹² minted, so nothing else moved.
- **114 `settle` transactions belong to no run in `runs/`.** They are, by start time:
  - 4 from the first 12-transaction functional test (15 Oct, 08:57 IST);
  - 99 from the first 300-transaction attempt (09:00 IST), the run that exposed the composed-proof
    completeness bug (145 honest proofs failed; 99 settled). Its folder was deleted when the run was
    repeated after the fix; the bug and its numbers are reported in `integration/README.md`;
  - 6 from a smoke test after the validator-retry change (10:56 IST);
  - 5 from a smoke test of the Elliptic path (13:43 IST).

**To repeat it** (needs Hyperledger Besu 25.x, Java 21, Python with `web3`; Foundry only for `forge build`):
```bash
cd integration && forge build                      # contract ABIs for the decoder
gunzip -k evidence/cbdc_besu_chain.blocks.gz
besu --data-path=/tmp/cbdc-replay --genesis-file=evidence/genesis.json blocks import --from=evidence/cbdc_besu_chain.blocks
besu --data-path=/tmp/cbdc-replay --genesis-file=evidence/genesis.json --rpc-http-enabled \
     --rpc-http-api=ETH,NET,QBFT,WEB3 --p2p-enabled=false &        # read-only node on 127.0.0.1:8545
cd pipeline && python audit_chain.py /tmp/cbdc-audit             # compare with pipeline/audit/audit_report.json
```
Importing re-executes every block, so the replayed state (balances, slashes, registry) is recomputed
by your own node, not copied from ours. We ran exactly these commands on a fresh data directory; the
report was identical to the one from the original node.

### On-chain disputes: `h1-zkopml-v2/verify_dispute_record.py`
It starts `anvil` from the recorded state and checks that all 116 logged transactions exist with the
logged status and gas; that each dispute's final phase, read from the game contract, names the winner
the summary reports; and that every recorded step proof, sent to its on-chain verifier, returns true.

Result (`evidence/recorded_rerun_elliptic/verification_report.json`): 116/116 transactions match,
7/7 winners match, 7/7 proofs verify on-chain, including the proof made with another block's weights.
The verifier accepts that proof; the game's `resolve` transaction with it is on the chain with status 0
(reverted: "params instance != step weights").

```bash
cd h1-zkopml-v2 && python3 verify_dispute_record.py evidence/recorded_rerun_elliptic
```

Game gas in the recorded re-run differs from the committed result file by at most 0.2 % per scenario,
because zero-knowledge proofs are randomized and calldata gas depends on the proof bytes. Bisection
rounds, located steps and winners are identical.

## Level 3 – re-run it (strongest)

Levels 1–2 confirm that the records and the ledger agree. They cannot rule out that we built the
ledger dishonestly: all four validator keys were ours. Re-running on your own machine removes that.

**Disputes, about 10–20 minutes including a one-time setup (about 3 minutes, 3 GB of keys):**
```bash
cd h1-zkopml-v2 && forge build
python3 prepare_data.py /path/to/elliptic_bitcoin_dataset     # writes X.npy, y.npy, ts.npy
python3 onchain_dispute.py --data . --record /tmp/my_run
python3 verify_dispute_record.py /tmp/my_run
```

**Integrated pipeline, a 100-transaction run takes about 4 minutes once built:**
```bash
cd h4-pqc-stark/zk_composed && cargo build --release
cd ../../integration && forge build && BESU=/path/to/besu chain/start_chain.sh
cd pipeline && python deploy.py && ./start_validators.sh
python run_pipeline.py --n 100 --source elliptic --data /path/to/elliptic_bitcoin_dataset --faults 0.1 --out runs/review_100
python audit_chain.py /tmp/review-audit
```
For an even stronger check, run one or more of the validator services on another machine you control,
pointed at your own Besu node.
