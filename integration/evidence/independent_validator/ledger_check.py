"""Check the exported two-machine ledger against the host's run record, on a node you run yourself.

Import the ledger (it re-executes every block, so balances and the registry are recomputed by your node):
  gunzip -k independent_chain.blocks.gz
  echo "0x$(openssl rand -hex 32)" > /tmp/ivkey
  besu --data-path=/tmp/iv --genesis-file=reviewer/genesis.json --node-private-key-file=/tmp/ivkey \
       blocks import --from=independent_chain.blocks
  besu --data-path=/tmp/iv --genesis-file=reviewer/genesis.json --node-private-key-file=/tmp/ivkey \
       --rpc-http-enabled --rpc-http-port=8645 --rpc-http-api=ETH,NET,QBFT,WEB3 --p2p-enabled=false &
Then:
  python3 ledger_check.py [--rpc http://127.0.0.1:8645]
  python3 ../../pipeline/check_my_validator.py reviewer/decisions.jsonl reviewer/deployment_public.json --rpc http://127.0.0.1:8645

For every successful SettlementGate.settle transaction on the ledger this script decodes the request,
recovers the signers and reads the Settled event, then requires:
  - its nonce belongs to the settled run (independent_200_b) and maps to one row of the host's CSV;
  - amount, EXEC, VIOL, outcome and gas equal that row; the row is honest (no injected fault);
  - it carries the signatures of all 4 attesters registered on the gate, including the reviewer's;
  - every row the CSV reports as settled has exactly one such transaction, and no falsified row has any;
  - the merchant's balance equals the sum of the Executed amounts, and the customers' balances make up the rest.
Needs web3 and eth-account.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from web3.logs import DISCARD
from web3.middleware import ExtraDataToPOAMiddleware

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent / "pipeline"))
from deploy import DEV_KEYS  # noqa: E402  (fixed development accounts used by deploy.py / run_pipeline.py)

OUTCOMES = ["None", "Executed", "AgentRefused", "Blocked", "NotCovered"]
MERCHANT_KEY = "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default="http://127.0.0.1:8645")
    a = ap.parse_args()
    dep = json.load(open(HERE / "reviewer" / "deployment_public.json"))
    rows = {int(r["i"]): r for r in csv.DictReader(open(HERE / "host" / "runs" / "independent_200_b" / "transactions.csv"))}
    run_start = json.loads(open(HERE / "reviewer" / "decisions.jsonl").readline())["run_start"]
    w3 = Web3(Web3.HTTPProvider(a.rpc, request_kwargs={"timeout": 120}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    gate = w3.eth.contract(address=dep["gate"], abi=dep["abi"]["gate"])
    attesters = {gate.functions.validators(i).call() for i in range(gate.functions.validatorCount().call())}
    reviewer = {Web3.to_checksum_address(v["address"]) for v in dep["validators"] if v.get("external")}
    types = next(",".join(c["type"] for c in f["inputs"][0]["components"]) for f in dep["abi"]["gate"] if f.get("name") == "settle")
    selector = bytes(Web3.keccak(text=f"settle(({types}),bytes[])")[:4])

    head = w3.eth.block_number
    seen, problems, reverted, other_nonces = {}, [], 0, 0
    for n in range(head + 1):
        for tx in w3.eth.get_block(n, full_transactions=True).transactions:
            if tx["to"] != dep["gate"] or bytes(tx["input"])[:4] != selector:
                continue
            rc = w3.eth.get_transaction_receipt(tx["hash"])
            if rc.status != 1:
                reverted += 1
                continue
            _, args = gate.decode_function_input(bytes(tx["input"]))
            r = args["r"]
            if isinstance(r, dict):
                r = tuple(r[k] for k in ("agentId", "from", "to", "amount", "commitmentHash", "proofHash", "exec", "viol", "nonce"))
            if r[8] // 100000 != run_start:
                other_nonces += 1
                continue
            i = r[8] % 100000
            rid = gate.functions.requestId(r).call()
            signers = {Account.recover_message(encode_defunct(primitive=rid), signature=s) for s in args["sigs"]}
            ev = gate.events.Settled().process_receipt(rc, errors=DISCARD)   # the registry's Slashed log is not a gate event
            outcome = OUTCOMES[ev[0]["args"]["outcome"]] if ev else "-"
            row = rows.get(i)
            if row is None:
                problems.append({"i": i, "issue": "no CSV row"})
                continue
            for key, csv_v, chain_v in (("amount", int(row["amount"]), r[3]), ("exec", int(row["exec"]), int(r[6])),
                                        ("viol", int(row["viol"]), int(r[7])), ("outcome", row["outcome"], outcome),
                                        ("gas", int(row["settle_gas"]), rc.gasUsed), ("honest", "", row["fault"])):
                if csv_v != chain_v:
                    problems.append({"i": i, "field": key, "csv": csv_v, "chain": chain_v})
            if signers != attesters:
                problems.append({"i": i, "field": "signers", "chain": sorted(signers)})
            if i in seen:
                problems.append({"i": i, "issue": "settled twice"})
            seen[i] = outcome

    csv_settled = {i for i, r in rows.items() if r["outcome"] != "NotSubmitted"}
    falsified = {i for i, r in rows.items() if r["fault"]}
    merchant = Account.from_key(MERCHANT_KEY).address
    customers = [Account.from_key(k).address for k in DEV_KEYS[2:6]]
    executed_sum = sum(int(r["amount"]) for r in rows.values() if r["outcome"] == "Executed")
    merchant_bal = gate.functions.balanceOf(merchant).call()
    customers_bal = sum(gate.functions.balanceOf(c).call() for c in customers)
    report = {
        "ledger_head": head, "gate": dep["gate"], "attesters_on_gate": sorted(attesters), "threshold": gate.functions.threshold().call(),
        "reviewer_is_attester": reviewer <= attesters,
        "settle_txs_of_this_run": len(seen), "settle_txs_reverted": reverted, "settle_txs_other_runs": other_nonces,
        "outcomes_onchain": {o: list(seen.values()).count(o) for o in sorted(set(seen.values()))},
        "settled_rows_equal_csv_settled": set(seen) == csv_settled,
        "falsified_with_a_settle_tx": len(falsified & set(seen)),
        "rows_with_differences": len({p["i"] for p in problems}), "problems": problems[:20],
        "merchant_balance_onchain": merchant_bal, "executed_amount_sum_csv": executed_sum,
        "balances_consistent": merchant_bal == executed_sum and merchant_bal + customers_bal == 10**12 * len(customers),
    }
    report["all_ok"] = (report["reviewer_is_attester"] and report["settled_rows_equal_csv_settled"] and not problems
                        and report["falsified_with_a_settle_tx"] == 0 and report["balances_consistent"] and reverted == 0)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
