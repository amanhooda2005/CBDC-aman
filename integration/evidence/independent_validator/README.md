# Independent-validator run, 20–21 Oct 2026

This run answers the review comment "All four validator keys are yours." It was split across **two
people on two machines**, connected over Tailscale, following
[`docs/INDEPENDENT_VALIDATOR.md`](../../../docs/INDEPENDENT_VALIDATOR.md):

| | Host (a person outside the team, his own MacBook) | Reviewer (Aman Hooda, his own MacBook Air) |
|---|---|---|
| Ran | the 4-node QBFT network, the contract deployment, three validators, the 200 transfers | one Besu node synced from the host's network, one validator |
| Held | the three host validator keys | the fourth validator key, generated on his machine and never sent |
| Verifier | his own build of `zk_composed` | his own build of `zk_composed` (same model digest `5b0d4d3a…`) |

The `SettlementGate` was deployed with **4 of 4** attestations required (`--threshold 4`), so neither
person could settle anything alone. The reviewer's validator read the ledger only from his own node and
checked every proof with his own binary. Reviewer machine: macOS 27 on Apple Silicon (arm64), Besu 25.10.0,
Python 3.14.7 (`reviewer/my_machine.txt`).

## Result (run `independent_200_b`: 200 real Elliptic transactions, 34 of them deliberately falsified)

| | |
|---|---|
| Requests the reviewer's validator checked | 200 |
| Signed / refused | 166 / 34 |
| Recorded on the ledger | 166 (65 Executed, 100 AgentRefused, 1 Blocked): exactly the 166 the reviewer signed, and exactly the 166 honest ones |
| Signatures on each settlement | 4 (all 166) |
| Falsified requests that reached the quorum | 0 of 34 (each had 0 signatures) |
| Reviewer's three checks on his own ledger | all `true` (`reviewer/my_check.json`) |
| Host validators vs reviewer | same 200 request ids, same sign/refuse decision on every one |
| Median end-to-end per transfer | 0.99 s, with one validator across the internet |

Each falsification was refused for the matching reason:

| Falsification | Count | Reviewer's refusal reason |
|---|---|---|
| wrong model | 10 | statement is about a different model |
| amount swapped | 8 | commitment does not open to the stated amount |
| stale penalty | 6 | proof used slash 7936, registry requires 7680 |
| corrupted proof | 6 | zero-knowledge proof does not verify (deserialization error) |
| flipped verdict | 4 | zero-knowledge proof does not verify (invalid opening) |

A first attempt (`host/runs/independent_200`) ran while the reviewer's validator could not be reached
from the host. All three host validators signed the 166 honest requests, but **nothing settled** (0 of
200), because the gate needed the fourth signature. That also confirms the strict gate in practice.

## Files
- `reviewer/decisions.jsonl`: every decision of the reviewer's validator (request id, amount, EXEC/VIOL,
  signed or refused, reason, check time).
- `reviewer/my_check.json`: output of `check_my_validator.py` against the reviewer's own node.
- `reviewer/deployment_public.json`, `reviewer/genesis.json`: what the host sent (no private keys).
- `host/runs/independent_200_b/`, `host/runs/independent_200/`: the host's run records.
- `host/logs/decisions_v{0,1,2}.jsonl`: the host validators' decisions (both attempts).
- `cross_check.py` → `cross_check.json`: reconciles the two sides (standard library only;
  `python3 cross_check.py`). Result: `"all_ok": true`.
- `independent_chain.blocks.gz`: **the whole ledger of this run** (3,033 blocks), exported on 26 Oct 2026
  from the reviewer's own node with `besu blocks export` (SHA-256 `66ea5c4c…4cb4bf4`).
- `ledger_check.py` → `ledger_check.json`: checks that ledger against the host's run record (below).
- `reviewer/my_check_on_imported_ledger.json`: the reviewer's `check_my_validator.py`, rerun against a
  fresh node that imported the exported ledger.

No private keys are included.

## Checking the exported ledger yourself
Importing re-executes every block, so balances and the registry are
recomputed by your node, not copied:
```bash
gunzip -k independent_chain.blocks.gz
echo "0x$(openssl rand -hex 32)" > /tmp/ivkey                     # any node key; this node never signs
besu --data-path=/tmp/iv --genesis-file=reviewer/genesis.json --node-private-key-file=/tmp/ivkey \
     blocks import --from=independent_chain.blocks
besu --data-path=/tmp/iv --genesis-file=reviewer/genesis.json --node-private-key-file=/tmp/ivkey \
     --rpc-http-enabled --rpc-http-port=8645 --rpc-http-api=ETH,NET,QBFT,WEB3 --p2p-enabled=false &
python3 ledger_check.py --rpc http://127.0.0.1:8645
python3 ../../pipeline/check_my_validator.py reviewer/decisions.jsonl reviewer/deployment_public.json --rpc http://127.0.0.1:8645
```
Result on a fresh Besu 25.10.0 node (26 Oct 2026), `"all_ok": true`:
- 166 successful settle transactions, 0 reverted, none from any other run;
- they are exactly the rows the host's CSV reports as settled, with the same amount, EXEC, VIOL, outcome
  and gas (65 Executed, 100 AgentRefused, 1 Blocked); 0 falsified requests have a settle transaction;
- every one carries the signatures of all four attesters registered on the gate, including the reviewer's;
- the merchant's balance (467,712) equals the sum of the Executed amounts, and the customers hold the rest;
- the reviewer's three checks come out identical to his self-reported `my_check.json`.
