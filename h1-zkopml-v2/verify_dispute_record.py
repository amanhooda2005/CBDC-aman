"""Checks a recorded on-chain dispute run (onchain_dispute.py --record DIR) without trusting its summary.

It starts a local anvil node from the recorded chain state (DIR/anvil_state.json) and then, for the
deployed game contract and the four step verifiers it finds there:
  1. every transaction in DIR/transactions.jsonl exists on that chain with the logged status and gas;
  2. each dispute's final phase, read from the game contract, is the one the summary reports
     (5 = asserter won, 6 = challenger won);
  3. every recorded step proof is re-run through its on-chain verifier (eth_call of verifyProof) and
     returns true, including the "other block's weights" proof, which the verifier accepts and the
     game rejected (its resolve transaction is on the chain with status 0).

Two ways to rebuild the chain:
  --state  load DIR/anvil_state.json. The state-file format is Foundry-version specific: it was
           written by Foundry 1.5.1-stable (commit b0a9dd9, Dec 2025) and newer releases may refuse it.
  --replay (default) start an empty anvil (hardfork Prague, chain id 31337, the recorded block gas limit) and send
           the recorded signed transactions (DIR/raw_transactions.jsonl) in their original order. This
           works with any Foundry version that supports Prague: the transactions are signed, so their hashes
           and the contract addresses are reproduced exactly, and every contract call is re-executed by your
           own EVM. Tested with Foundry 1.5.1-stable and 1.8.5 (both give identical results; 1.8.5
           cannot load the state file).

Usage: python3 verify_dispute_record.py DIR [--state]      (needs Foundry's anvil and web3)
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


def replay(w3, d):
    """Send the recorded signed transactions in order; one transaction per block, as in the original run."""
    sent = 0
    for line in open(d / "raw_transactions.jsonl"):
        t = json.loads(line)
        # same block timestamps as the original run: the game commits to the commit-block time (claim digest)
        r = w3.provider.make_request("evm_setNextBlockTimestamp", [t["timestamp"]])
        assert "error" not in r, r
        h = w3.eth.send_raw_transaction(t["raw"])
        assert "0x" + bytes(h).hex() == t["tx"], "replayed transaction hash differs"
        w3.eth.wait_for_transaction_receipt(h)
        sent += 1
    return sent


def main(d, mode="replay"):
    d = Path(d)
    res = json.load(open(d / "result.json"))
    txs = [json.loads(l) for l in open(d / "transactions.jsonl")]
    base = [f"{BIN}/anvil", "--port", str(PORT), "--silent", "--code-size-limit", "1000000", "--gas-limit", "300000000"]
    if mode == "state":
        cmd = base + ["--load-state", str(d / "anvil_state.json")]
    else:
        first = json.loads(open(d / "raw_transactions.jsonl").readline())["timestamp"]
        # genesis just before the first recorded block, so the original block timestamps can be replayed
        cmd = base + ["--hardfork", "prague", "--chain-id", "31337", "--timestamp", str(first - 1)]
    anvil = subprocess.Popen(cmd)
    time.sleep(3)
    if anvil.poll() is not None:
        sys.exit("anvil could not start" + (" from the state file (written by Foundry 1.5.1-stable; newer Foundry "
                 "versions use another format). Run without --state to replay the signed transactions instead."
                 if mode == "state" else "."))
    version = subprocess.run([f"{BIN}/anvil", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    report = {"mode": mode, "anvil": version, "transactions": len(txs), "tx_found_with_logged_status_and_gas": 0, "tx_missing_or_different": [],
              "scenarios": {}, "proofs": {}}
    try:
        w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{PORT}"))
        if mode == "replay":
            report["replayed_transactions"] = replay(w3, d)
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
    json.dump(report, open(d / f"verification_report{'_replay' if mode == 'replay' else ''}.json", "w"), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], "state" if "--state" in sys.argv[2:] else "replay")
