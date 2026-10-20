"""For an independent validator: compare what YOUR validator decided with what settled on YOUR copy of the ledger.

Reads your validator's decision log (validator_node.py --log) and every SettlementGate.settle transaction
on your own Besu node, and checks:
  1. every settlement that carries your signature matches a request you logged as signed (same request id);
  2. every request you refused has no settlement carrying your signature;
  3. with a strict gate (threshold = number of attesters, e.g. 4 of 4), nothing settled without your signature,
     so every settled transfer was checked by your own verifier build;
and summarises your decisions (signed / refused, with reasons).

Usage: python check_my_validator.py decisions.jsonl deployment_public.json [--rpc http://127.0.0.1:8545]
Needs only web3 and eth-account, plus the ABIs inside deployment_public.json.
"""
import argparse
import json
from collections import Counter

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("decisions")
    ap.add_argument("deployment")
    ap.add_argument("--rpc", default="http://127.0.0.1:8545")
    a = ap.parse_args()
    dep = json.load(open(a.deployment))
    log = [json.loads(l) for l in open(a.decisions)]
    me = {Web3.to_checksum_address(v["address"]) for v in dep["validators"] if v.get("external")}
    w3 = Web3(Web3.HTTPProvider(a.rpc))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    gate = w3.eth.contract(address=dep["gate"], abi=dep["abi"]["gate"])
    n_attesters, threshold = gate.functions.validatorCount().call(), gate.functions.threshold().call()
    signed_ids = {d["request_id"] for d in log if d["signed"]}
    refused_ids = {d["request_id"] for d in log if not d["signed"]}

    settled = []   # (request id, set of signers)
    selector = None
    for f in dep["abi"]["gate"]:
        if f.get("name") == "settle":
            types = ",".join(c["type"] for c in f["inputs"][0]["components"])
            selector = bytes(Web3.keccak(text=f"settle(({types}),bytes[])")[:4])
    for n in range(w3.eth.block_number + 1):
        for tx in w3.eth.get_block(n, full_transactions=True).transactions:
            if tx["to"] != dep["gate"] or bytes(tx["input"])[:4] != selector:
                continue
            if w3.eth.get_transaction_receipt(tx["hash"]).status != 1:
                continue
            _, args = gate.decode_function_input(bytes(tx["input"]))
            r = args["r"]
            if isinstance(r, dict):
                r = tuple(r[k] for k in ("agentId", "from", "to", "amount", "commitmentHash", "proofHash", "exec", "viol", "nonce"))
            rid = gate.functions.requestId(r).call()
            signers = {Account.recover_message(encode_defunct(primitive=rid), signature=s) for s in args["sigs"]}
            settled.append(("0x" + bytes(rid).hex(), signers))

    with_me = [rid for rid, s in settled if s & me]
    without_me = [rid for rid, s in settled if not s & me]
    report = {
        "my_address": sorted(me), "gate": dep["gate"], "attesters": n_attesters, "threshold": threshold,
        "my_decisions": len(log), "i_signed": len(signed_ids), "i_refused": len(refused_ids),
        "refusal_reasons": dict(Counter(d["reason"].split(":")[0][:60] for d in log if not d["signed"])),
        "settlements_on_my_ledger": len(settled), "settlements_with_my_signature": len(with_me),
        "check1_every_settlement_with_my_signature_was_signed_by_me": all(rid in signed_ids for rid in with_me),
        "check2_no_settlement_with_my_signature_for_a_request_i_refused": not any(rid in refused_ids for rid in with_me),
        "check3_strict_gate_nothing_settled_without_me": (len(without_me) == 0) if threshold == n_attesters else "not applicable (gate is not strict)",
    }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
