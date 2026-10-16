"""Independent audit of the pipeline runs against the ledger itself.

The run CSVs (runs/*/transactions.csv) are what the driver recorded. This script ignores them at first
and reads the Besu chain directly: every `SettlementGate.settle` transaction ever sent, its decoded
request, the validator signatures inside it (recovered with EIP-191 and checked against the QBFT
validator set), the receipt status, gas, the `Settled` event and the registry's `Slashed` events. It then
reconciles that on-chain record with each run's CSV:

  - every honest request the CSV reports as settled has exactly one successful settle transaction with
    the same index, amount, EXEC, VIOL, outcome and gas, signed by >= 3 distinct QBFT validators;
  - no faulty request (and no request the CSV reports as not submitted) has any settle transaction;
  - there are no settle transactions the CSV does not account for;
  - for a deployment used by one run only, the CBDC balances on the gate equal what that run's
    Executed rows imply.

The request nonce is run_start_epoch * 100000 + i, which ties every on-chain request to one CSV row.

Usage (chain running; RPC 127.0.0.1:8545):  python audit_chain.py [out_dir]
Writes <out_dir>/settlements_onchain.csv, slashes_onchain.csv and audit_report.json (default: audit/).
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

HERE = Path(__file__).parent
RPC = "http://127.0.0.1:8545"
OUTCOMES = ["None", "Executed", "AgentRefused", "Blocked", "NotCovered"]


def load_abi(name):
    return json.load(open(HERE.parent / "out" / f"{name}.sol" / f"{name}.json"))["abi"]


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 120}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)   # QBFT blocks carry a long extraData
    gate_abi, reg_abi = load_abi("SettlementGate"), load_abi("LiabilityRegistry")
    proto = w3.eth.contract(abi=gate_abi)
    reg_proto = w3.eth.contract(abi=reg_abi)
    selector = bytes(w3.keccak(text="settle((bytes32,address,address,uint256,bytes32,bytes32,bool,bool,uint64),bytes[])")[:4])
    validators = {Web3.to_checksum_address(a) for a in w3.provider.make_request("qbft_getValidatorsByBlockNumber", ["latest"])["result"]}
    head = w3.eth.block_number

    settlements, slashes, gates = [], [], set()
    for n in range(0, head + 1):
        blk = w3.eth.get_block(n, full_transactions=True)
        for tx in blk.transactions:
            data = bytes(tx["input"])
            if tx["to"] is None or data[:4] != selector:
                continue
            gate = w3.eth.contract(address=tx["to"], abi=gate_abi)
            gates.add(tx["to"])
            _, args = gate.decode_function_input(data)
            r, sigs = args["r"], args["sigs"]
            if isinstance(r, dict):
                r = tuple(r[k] for k in ("agentId", "from", "to", "amount", "commitmentHash", "proofHash", "exec", "viol", "nonce"))
            rc = w3.eth.get_transaction_receipt(tx["hash"])
            rid = gate.functions.requestId(r).call()   # pure function of (chain id, gate address, request); history pruning does not matter
            signers = sorted({Account.recover_message(encode_defunct(primitive=rid), signature=s) for s in sigs})
            ev = gate.events.Settled().process_receipt(rc) if rc.status == 1 else []
            for e in reg_proto.events.Slashed().process_receipt(rc) if rc.status == 1 else []:
                slashes.append({"block": n, "tx": tx["hash"].hex(), "agent": e["args"]["agentId"].hex()[:10],
                                "amount_wei": e["args"]["amount"], "violations_after": e["args"]["violationsAfter"]})
            nonce = r[8]
            settlements.append({
                "block": n, "tx": "0x" + tx["hash"].hex().removeprefix("0x"), "gate": tx["to"], "status": rc.status, "gas": rc.gasUsed,
                "run_start": nonce // 100000, "i": nonce % 100000, "agent": r[0].hex()[:10], "amount": r[3],
                "exec": int(r[6]), "viol": int(r[7]), "outcome": OUTCOMES[ev[0]["args"]["outcome"]] if ev else "-",
                "signatures": len(sigs), "validator_signers": sum(s in validators for s in signers),
                "non_validator_signers": sum(s not in validators for s in signers),
            })
        if n % 2000 == 0:
            print(f"scanned block {n}/{head}, settle txs so far {len(settlements)}", flush=True)

    with open(out / "settlements_onchain.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(settlements[0]))
        w.writeheader()
        w.writerows(settlements)
    if slashes:
        with open(out / "slashes_onchain.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(slashes[0]))
            w.writeheader()
            w.writerows(slashes)

    # ------------------------------------------------------------------ reconcile with each run's CSV
    by_start = defaultdict(list)
    for s in settlements:
        by_start[s["run_start"]].append(s)
    report = {"chain_head": head, "qbft_validators": sorted(validators), "settle_txs_total": len(settlements),
              "settle_txs_reverted": sum(s["status"] == 0 for s in settlements), "gates_seen": sorted(gates), "runs": {}}
    used_starts = set()
    for run_dir in sorted((HERE / "runs").iterdir()):
        rows = list(csv.DictReader(open(run_dir / "transactions.csv")))
        settled_rows = {int(r["i"]): r for r in rows if r["outcome"] not in ("NotSubmitted",)}
        # the run's start epoch: the nonce group whose indices are exactly this run's settled rows
        cands = [st for st, ss in by_start.items() if {s["i"] for s in ss} == set(settled_rows)]
        res = {"rows": len(rows), "honest": sum(not r["fault"] for r in rows), "faulty": sum(bool(r["fault"]) for r in rows),
               "csv_settled": len(settled_rows)}
        if len(cands) != 1:
            res["error"] = f"no unique on-chain nonce group matches (candidates {len(cands)})"
            report["runs"][run_dir.name] = res
            continue
        st = cands[0]
        used_starts.add(st)
        chain_rows = {s["i"]: s for s in by_start[st]}
        mism = []
        for i, r in settled_rows.items():
            s = chain_rows[i]
            for key, a, b in (("amount", int(r["amount"]), s["amount"]), ("exec", int(r["exec"]), s["exec"]),
                              ("viol", int(r["viol"]), s["viol"]), ("outcome", r["outcome"], s["outcome"]),
                              ("gas", int(r["settle_gas"]), s["gas"]), ("status", 1, s["status"])):
                if a != b:
                    mism.append({"i": i, "field": key, "csv": a, "chain": b})
            if s["validator_signers"] < 3 or s["non_validator_signers"]:
                mism.append({"i": i, "field": "signers", "csv": r["signatures"], "chain": (s["validator_signers"], s["non_validator_signers"])})
        faulty_idx = {int(r["i"]) for r in rows if r["fault"]}
        gates_used = sorted({s["gate"] for s in by_start[st]})
        res.update({
            "run_start_epoch": st, "gate": gates_used,
            "onchain_settle_txs": len(chain_rows),
            "onchain_outcomes": dict(Counter(s["outcome"] for s in chain_rows.values())),
            "faulty_requests_with_a_settle_tx": len(faulty_idx & set(chain_rows)),
            "rows_matching_chain_exactly": len(settled_rows) - len({m["i"] for m in mism}),
            "mismatches": mism[:20], "n_mismatches": len(mism),
            "min_validator_signatures": min(s["validator_signers"] for s in chain_rows.values()),
        })
        report["runs"][run_dir.name] = res

    # settle txs that no run accounts for (e.g. functional smoke tests run outside runs/)
    report["settle_txs_not_in_any_run"] = sum(len(v) for k, v in by_start.items() if k not in used_starts)
    report["unaccounted_by_start"] = {str(k): len(v) for k, v in by_start.items() if k not in used_starts}

    # ledger check on every deployment that exactly one run used (here: the Elliptic run's fresh deployment).
    # Customers and merchant are the fixed development accounts used by deploy.py / run_pipeline.py.
    from deploy import DEV_KEYS
    customers = [Account.from_key(k).address for k in DEV_KEYS[2:6]]
    merchant = Account.from_key("0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e").address
    report["ledger_checks"] = []
    for name, res in report["runs"].items():
        if "gate" not in res or len(res["gate"]) != 1:
            continue
        g = res["gate"][0]
        if any(s["gate"] == g for k, ss in by_start.items() if k != res["run_start_epoch"] for s in ss):
            continue   # another run or test also used this deployment: balances mix several runs
        gate = w3.eth.contract(address=g, abi=gate_abi)
        rows = [r for r in csv.DictReader(open(HERE / "runs" / name / "transactions.csv")) if r["outcome"] == "Executed"]
        lc = {"run": name, "gate": g, "executed_amount_sum_csv": sum(int(r["amount"]) for r in rows),
              "merchant_balance_onchain": gate.functions.balanceOf(merchant).call(),
              "customers_total_onchain": sum(gate.functions.balanceOf(c).call() for c in customers),
              "customers_minted": 10**12 * len(customers)}
        lc["balances_consistent"] = (lc["merchant_balance_onchain"] == lc["executed_amount_sum_csv"]
                                     and lc["customers_total_onchain"] + lc["merchant_balance_onchain"] == lc["customers_minted"])
        report["ledger_checks"].append(lc)
    json.dump(report, open(out / "audit_report.json", "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in report.items() if k not in ("runs",)}, indent=1, default=str))
    for k, v in report["runs"].items():
        print(k, {x: v.get(x) for x in ("rows", "csv_settled", "onchain_settle_txs", "rows_matching_chain_exactly", "n_mismatches",
                                         "faulty_requests_with_a_settle_tx", "min_validator_signatures", "error")})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(HERE / "audit"))
