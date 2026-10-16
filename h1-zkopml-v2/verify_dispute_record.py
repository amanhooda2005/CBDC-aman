"""Checks a recorded on-chain dispute run (onchain_dispute.py --record DIR) without trusting its summary.

It starts a local anvil node from the recorded chain state (DIR/anvil_state.json) and then, for the
deployed game contract and the four step verifiers it finds there:
  1. every transaction in DIR/transactions.jsonl exists on that chain with the logged status and gas;
  2. each dispute's final phase, read from the game contract, is the one the summary reports
     (5 = asserter won, 6 = challenger won);
  3. every recorded step proof is re-run through its on-chain verifier (eth_call of verifyProof) and
     returns true, including the "other block's weights" proof, which the verifier accepts and the
     game rejected (its resolve transaction is on the chain with status 0).

Usage: python3 verify_dispute_record.py DIR      (needs Foundry's anvil and web3)
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from web3 import Web3

BIN = os.environ.get("FOUNDRY_BIN", os.path.expanduser("~/.foundry/bin"))
PORT = int(os.environ.get("ANVIL_PORT", 8655))
PHASES = {5: "asserter", 6: "challenger"}


def main(d):
    d = Path(d)
    res = json.load(open(d / "result.json"))
    txs = [json.loads(l) for l in open(d / "transactions.jsonl")]
    anvil = subprocess.Popen([f"{BIN}/anvil", "--port", str(PORT), "--silent", "--code-size-limit", "1000000",
                              "--gas-limit", "300000000", "--load-state", str(d / "anvil_state.json")])
    time.sleep(3)
    report = {"transactions": len(txs), "tx_found_with_logged_status_and_gas": 0, "tx_missing_or_different": [],
              "scenarios": {}, "proofs": {}}
    try:
        w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{PORT}"))
        for t in txs:
            try:
                rc = w3.eth.get_transaction_receipt(t["tx"])
                ok = rc.status == t["status"] and rc.gasUsed == t["gas"]
            except Exception as e:  # noqa: BLE001
                ok, rc = False, str(e)[:80]
            if ok:
                report["tx_found_with_logged_status_and_gas"] += 1
            else:
                report["tx_missing_or_different"].append(t["tx"])
        game_abi = [{"name": "status", "type": "function", "stateMutability": "view", "inputs": [{"name": "did", "type": "bytes32"}],
                     "outputs": [{"type": "uint8"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"}]}]
        game = w3.eth.contract(address=res["contracts"]["game"], abi=game_abi)
        for sid, sc in res["scenarios"].items():
            phase = game.functions.status(bytes.fromhex(sc["dispute_id"][2:])).call()[0]
            report["scenarios"][sid] = {"scenario": sc["scenario"], "summary_winner": sc["winner"],
                                        "onchain_winner": PHASES.get(phase, f"phase {phase}"),
                                        "agrees": PHASES.get(phase) == sc["winner"]}
        steps = {s: v for s, v in res["contracts"]["verifiers"].items()}
        for f in sorted((d / "proofs").glob("*_verifyProof_calldata.bin")):
            tag = f.name.replace("_verifyProof_calldata.bin", "")
            sid = tag.split("_")[0][1:]
            sc = res["scenarios"].get(sid, {})
            shape = sc.get("shape")
            if not shape:
                continue
            out = w3.eth.call({"to": steps[shape], "data": "0x" + f.read_bytes().hex()})
            report["proofs"][tag] = {"verifier": shape, "verifyProof_returns_true": int.from_bytes(out[-32:], "big") == 1}
    finally:
        anvil.send_signal(signal.SIGINT)
        anvil.wait(30)
    report["all_transactions_match"] = not report["tx_missing_or_different"]
    report["all_winners_match"] = all(s["agrees"] for s in report["scenarios"].values())
    report["all_proofs_verify_onchain"] = all(p["verifyProof_returns_true"] for p in report["proofs"].values())
    json.dump(report, open(d / "verification_report.json", "w"), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
