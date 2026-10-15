"""End-to-end lab pipeline: N transactions through the integrated CBDC stack (TRL 4 evidence).

Per transaction:
  1. features -> quantized H1 input x (Elliptic test period, H1 scaler + spec quantization) or a
     synthetic x inside the model's input box (functional testing only);
  2. a transfer offer from the H3 opportunity generator (amount, destination, payoffs; rule units x256);
  3. the operator keeps its stake covering the next escalated slash (stake maintenance, on-chain check);
  4. the operator node builds the zero-knowledge, post-quantum composed proof (zk_composed prove)
     with the registry's current escalated slash as public policy input, and a fresh ternary opening;
  5. the request is sent to the 4 validator nodes; each checks proof, model, commitment opening,
     outputs and policy against its own node and signs; 3 signatures are needed;
  6. SettlementGate.settle on the Besu QBFT chain: execute, agent-refused, blocked + slashed, or
     not covered.
Fault injection (a fraction of transactions): forged VIOL, amount swap, stale slash, corrupted proof,
wrong model. A faulty request must not reach quorum and must not change the ledger.

Usage:
  python run_pipeline.py --n 1000 --source elliptic --data /path/to/elliptic_bitcoin_dataset
  python run_pipeline.py --n 20 --source synthetic            (functional test)
Options: --faults 0.1  --seed 7  --out runs/<name>  (Byzantine / crashed validators: see start_validators.sh)
Writes <out>/transactions.csv and <out>/summary.json.
"""
import argparse
import base64
import concurrent.futures as cf
import csv
import json
import pickle
import random
import statistics as st
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
from eth_account import Account
from web3 import Web3

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "h3-abt/src"))
import sis  # noqa: E402
from deploy import Chain  # noqa: E402
from validator_node import keccak_words  # noqa: E402
from h3_abt.config import load_config  # noqa: E402
from h3_abt.environment import sample_opportunity  # noqa: E402
from h3_abt.protocol import is_violation  # noqa: E402

RULE = 256
FAULTS = ("flip_viol", "amount_swap", "stale_slash", "corrupt_proof", "wrong_model")
H1_SPEC = json.load(open(ROOT / "h4-pqc-stark/zk_composed/specs/h1_spec.json"))


# ------------------------------------------------------------------ transaction sources
def elliptic_inputs(data_dir: str, n: int, seed: int):
    """Quantized H1 inputs for n transactions from the H1 TEST period (time steps 35-49), labelled ones."""
    import pandas as pd
    feat = pd.read_csv(Path(data_dir) / "elliptic_txs_features.csv", header=None)
    cls = pd.read_csv(Path(data_dir) / "elliptic_txs_classes.csv")
    cls.columns = ["tx_id", "class"]
    feat.columns = ["tx_id", "time_step"] + [f"f{i}" for i in range(165)]
    df = feat.merge(cls, on="tx_id")
    df = df[(df["class"].astype(str) != "unknown") & (df["time_step"] > 34)]
    df = df.sample(n=min(n, len(df)), random_state=seed)
    sc = pickle.load(open(ROOT / "h1-zkopml-v2/models/W32_B1.pkl", "rb"))["scaler"]
    X = df[[f"f{i}" for i in range(165)]].values.astype(np.float32)
    Xs = ((np.clip(X, sc["lo"], sc["hi"]) - sc["center"]) / sc["scale"]).astype(np.float32)
    xq = np.clip(np.round(Xs * 2.0 ** H1_SPEC["ax"]).astype(np.int64), H1_SPEC["x_lo"], H1_SPEC["x_hi"])
    labels = (df["class"].astype(str) == "1").astype(int).values
    return [(row.tolist(), int(lab), int(tx), int(t)) for row, lab, tx, t in zip(xq, labels, df["tx_id"].values, df["time_step"].values)]


def synthetic_inputs(n: int, seed: int):
    rng = np.random.default_rng(seed)
    lo, hi = np.array(H1_SPEC["x_lo"]), np.array(H1_SPEC["x_hi"])
    return [(rng.integers(lo, hi + 1).tolist(), -1, -1, -1) for _ in range(n)]


# ------------------------------------------------------------------ helpers
def run_prover(prover, witness):
    with tempfile.TemporaryDirectory() as d:
        wp, pp, sp = Path(d) / "w.json", Path(d) / "p.bin", Path(d) / "s.json"
        wp.write_text(json.dumps(witness))
        t = time.time()
        out = subprocess.run([prover, "prove", str(wp), str(pp), str(sp)], capture_output=True, text=True, check=True)
        wall = (time.time() - t) * 1e3
        info = json.loads(out.stdout)
        return pp.read_bytes(), json.loads(sp.read_text()), info["prove_ms"], wall, int(info.get("risk", -1))


def post(url, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except OSError as e:   # crashed / unreachable validator: counts as a missing signature
        return {"validator": url, "signed": False, "reason": f"unreachable: {e.__class__.__name__}"}


def as_tuple(req):
    return (Web3.to_bytes(hexstr=req["agentId"]), Web3.to_checksum_address(req["from"]), Web3.to_checksum_address(req["to"]),
            int(req["amount"]), Web3.to_bytes(hexstr=req["commitmentHash"]), Web3.to_bytes(hexstr=req["proofHash"]),
            bool(req["exec"]), bool(req["viol"]), int(req["nonce"]))


OUTCOMES = ["None", "Executed", "AgentRefused", "Blocked", "NotCovered"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--source", choices=["synthetic", "elliptic"], default="synthetic")
    ap.add_argument("--data", default=None)
    ap.add_argument("--faults", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None)
    ap.add_argument("--validators", default="http://127.0.0.1:9100,http://127.0.0.1:9101,http://127.0.0.1:9102,http://127.0.0.1:9103")
    a = ap.parse_args()
    dep = json.load(open(HERE / "deployment.json"))
    out = Path(a.out or HERE / f"runs/{a.source}_{a.n}_seed{a.seed}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "transactions.partial.csv").unlink(missing_ok=True)
    ch = Chain(dep["rpc"][0])
    w3 = ch.w3
    gate = w3.eth.contract(address=dep["gate"], abi=dep["abi"]["gate"])
    registry = w3.eth.contract(address=dep["registry"], abi=dep["abi"]["registry"])
    vurls = a.validators.split(",")
    unit = dep["registry_unit"]
    op = dep["operator"]
    h3cfg = load_config(ROOT / "h3-abt/configs/experiment.yaml")
    rng = random.Random(a.seed)
    env_rng = random.Random(a.seed + 1)
    txs = elliptic_inputs(a.data, a.n, a.seed) if a.source == "elliptic" else synthetic_inputs(a.n, a.seed)
    merchants = [Account.from_key(k).address for k in (
        "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e",
        "0x4bbbf85ce3377467afe5d46f804f221813b2bb87f24d81f60f1fcdbf7cbf4356")]
    nonce0 = int(time.time())
    rows = []
    pool = cf.ThreadPoolExecutor(len(vurls))
    t_run = time.time()
    for i, (x, label, tx_id, time_step) in enumerate(txs):
        t0 = time.time()
        agent = dep["agents"][i % len(dep["agents"])]
        customer = dep["customers"][i % len(dep["customers"])]
        opp = sample_opportunity(env_rng, h3cfg)
        sanctioned = opp.transfer.destination in h3cfg.sanctioned_destinations
        dest_id = dep["policy"]["sanctioned_id"] if sanctioned else int(opp.transfer.destination.split("_")[-1])
        to = merchants[0] if not sanctioned else merchants[1]
        # stake maintenance by the operator (the gate also enforces it)
        top_up_gas = 0
        if not registry.functions.isCovered(Web3.to_bytes(hexstr=agent)).call():
            need = registry.functions.requiredStake(op).call() - registry.functions.operators(op).call()[2]
            rc = ch.wait(ch.send(dep["operator_key"], registry.functions.topUp(), value=need))
            top_up_gas = rc.gasUsed
        policy_block = w3.eth.block_number
        stake_units = registry.functions.operators(op).call()[2] * RULE // unit
        slash_units = registry.functions.nextSlash(op).call() * RULE // unit
        amount = min(int(round(opp.transfer.amount * RULE)), (1 << 20) - 1)
        r = sis.random_ternary()
        witness = {"x": x, "decision": {"amount": amount, "dest": dest_id, "compliant": int(round(opp.compliant_payoff * RULE)),
                                        "violation": int(round(opp.violation_payoff * RULE)), "staked": 1, "stake": min(stake_units, (1 << 20) - 1)},
                   "policy": {"max_compliant": dep["policy"]["max_compliant"], "sanctioned_id": dep["policy"]["sanctioned_id"],
                              "slash": slash_units}, "r": r}
        fault = rng.choice(FAULTS) if rng.random() < a.faults else None
        if fault == "stale_slash":
            witness["policy"]["slash"] = slash_units + RULE    # proof made against an outdated slash
        proof, stmt, prove_ms, prove_wall_ms, risk = run_prover(dep["prover_bin"], witness)
        c = stmt["public"][5:]
        req = {"agentId": agent, "from": customer["address"], "to": to, "amount": amount,
               "commitmentHash": "0x" + keccak_words(c).hex(), "proofHash": "0x" + Web3.keccak(proof).hex(),
               "exec": bool(stmt["public"][3]), "viol": bool(stmt["public"][4]), "nonce": nonce0 * 100000 + i}
        opening = {"amount": amount, "r": r}
        if fault == "flip_viol":
            stmt["public"][4] ^= 1
            req["viol"] = not req["viol"]
        elif fault == "amount_swap":
            req["amount"] = amount + 1
            opening["amount"] = amount + 1
        elif fault == "corrupt_proof":
            b = bytearray(proof)
            b[len(b) // 2] ^= 0xFF
            proof = bytes(b)
            req["proofHash"] = "0x" + Web3.keccak(proof).hex()
        elif fault == "wrong_model":
            stmt["model"] = "0" * 64
        # policy_block: the block at which the operator read the escalated slash; validators wait until
        # their own node has reached it (QBFT finality is immediate, but nodes import blocks at slightly different times)
        body = {"request": req, "proof_b64": base64.b64encode(proof).decode(), "statement": stmt, "opening": opening,
                "policy_block": policy_block}
        t1 = time.time()
        res = list(pool.map(lambda u: post(u + "/attest", body), vurls))
        attest_ms = (time.time() - t1) * 1e3
        sigs = [Web3.to_bytes(hexstr=x["signature"]) for x in res if x.get("signed")]
        reasons = sorted({x.get("reason", "") for x in res if not x.get("signed")})
        settle_gas, outcome, settle_ms, reverted = 0, "NotSubmitted", 0.0, ""
        t2 = time.time()
        if len(sigs) >= dep["threshold"]:
            fn = gate.functions.settle(as_tuple(req), sigs)
            try:
                fn.call({"from": op})
                rc = ch.wait(ch.send(dep["operator_key"], fn))
                settle_gas = rc.gasUsed
                ev = gate.events.Settled().process_receipt(rc)
                outcome = OUTCOMES[ev[0]["args"]["outcome"]] if ev else "?"
            except Exception as e:   # e.g. a byzantine signature set that the contract still rejects
                reverted = str(e)[:120]
                outcome = "Reverted"
        else:
            # show the gate refuses it too: an attempt with the signatures that were given reverts
            try:
                gate.functions.settle(as_tuple(req), sigs).call({"from": op})
                reverted = "UNEXPECTED: accepted"
            except Exception as e:
                reverted = "quorum not reached" if "quorum" in str(e) else str(e)[:120]
        settle_ms = (time.time() - t2) * 1e3
        rows.append({
            "i": i, "tx_id": tx_id, "time_step": time_step, "label_illicit": label, "fault": fault or "", "agent": agent[:10],
            "amount": amount, "sanctioned": int(sanctioned), "over_limit": int(amount > dep["policy"]["max_compliant"]),
            "offer_violates_policy": int(is_violation(opp.transfer, h3cfg)),
            "h1_risk": risk,
            "exec": int(req["exec"]), "viol": int(req["viol"]), "slash_units": slash_units, "stake_units": stake_units,
            "proof_bytes": len(proof), "prove_ms": round(prove_ms, 2), "prove_wall_ms": round(prove_wall_ms, 2),
            "attest_ms": round(attest_ms, 2), "validator_check_ms": round(st.mean(x.get("check_ms", 0) for x in res), 2),
            "signatures": len(sigs), "refusal_reasons": " | ".join(r for r in reasons if r),
            "settle_ms": round(settle_ms, 2), "settle_gas": settle_gas, "top_up_gas": top_up_gas,
            "outcome": outcome, "gate_rejection": reverted, "end_to_end_ms": round((time.time() - t0) * 1e3, 2),
        })
        with open(out / "transactions.partial.csv", "a", newline="") as f:   # survives an interrupted run
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            if i == 0:
                w.writeheader()
            w.writerow(rows[-1])
        if (i + 1) % 25 == 0 or i + 1 == len(txs):
            print(f"{i + 1}/{len(txs)}  last: {outcome:12s} sigs={len(sigs)} fault={fault}  {rows[-1]['end_to_end_ms']:.0f} ms", flush=True)
    wall = time.time() - t_run
    with open(out / "transactions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "transactions.partial.csv").unlink(missing_ok=True)
    summary = summarize(rows, wall, a, dep, registry, op)
    json.dump(summary, open(out / "summary.json", "w"), indent=1)
    print(json.dumps(summary, indent=1))


def risk_metrics(rows):
    """H1 risk flag (operator-local, from the prover) against the Elliptic label, where one exists;
    also split at time step 43 (the dark-market shutdown after which H1's recall collapses)."""
    out = {"viol_1": sum(r["viol"] for r in rows), "h1_risk_1": sum(r["h1_risk"] == 1 for r in rows)}
    out.update(_confusion([r for r in rows if r["label_illicit"] in (0, 1)]))
    if any(r["time_step"] > 0 for r in rows):
        out["time_steps_35_42"] = _confusion([r for r in rows if r["label_illicit"] in (0, 1) and 35 <= r["time_step"] <= 42])
        out["time_steps_43_49"] = _confusion([r for r in rows if r["label_illicit"] in (0, 1) and r["time_step"] >= 43])
    return out


def _confusion(lab):
    out = {}
    if lab:
        tp = sum(r["h1_risk"] == 1 and r["label_illicit"] == 1 for r in lab)
        fp = sum(r["h1_risk"] == 1 and r["label_illicit"] == 0 for r in lab)
        fn = sum(r["h1_risk"] == 0 and r["label_illicit"] == 1 for r in lab)
        tn = len(lab) - tp - fp - fn
        prec, rec = (tp / (tp + fp) if tp + fp else 0.0), (tp / (tp + fn) if tp + fn else 0.0)
        out.update({"labelled": len(lab), "illicit": tp + fn, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                    "precision": round(prec, 4), "recall": round(rec, 4),
                    "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0,
                    "illicit_blocked": sum(r["label_illicit"] == 1 and r["outcome"] == "Blocked" for r in lab),
                    "illicit_executed": sum(r["label_illicit"] == 1 and r["outcome"] == "Executed" for r in lab)})
    return out


def summarize(rows, wall, a, dep, registry, op):
    honest = [r for r in rows if not r["fault"]]
    faulty = [r for r in rows if r["fault"]]
    def m(key, rs):
        v = [r[key] for r in rs if r[key]]
        return {"mean": round(st.mean(v), 2), "median": round(st.median(v), 2), "p95": round(sorted(v)[int(0.95 * (len(v) - 1))], 2)} if v else None
    by = lambda rs, k: {v: sum(1 for r in rs if r[k] == v) for v in sorted({r[k] for r in rs})}
    settled = [r for r in honest if r["outcome"] in ("Executed", "AgentRefused", "Blocked", "NotCovered")]
    def want(r):   # SettlementGate outcome rules (stake maintenance keeps every agent covered)
        if r["viol"]:
            return "Blocked" if r["exec"] else "AgentRefused"
        return "Executed" if r["exec"] else "AgentRefused"
    expected = [r for r in settled if r["outcome"] == want(r)]
    opstate = registry.functions.operators(op).call()
    return {
        "source": a.source, "n": len(rows), "faults_injected": len(faulty), "wall_s": round(wall, 1),
        "throughput_tx_per_min": round(60 * len(rows) / wall, 1),
        "honest": {"n": len(honest), "outcomes": by(honest, "outcome"),
                   "settled_with_expected_outcome": len(expected), "settled": len(settled),
                   "all_signatures": sum(r["signatures"] == 4 for r in honest)},
        "faulty": {"n": len(faulty), "by_type": by(faulty, "fault"), "outcomes": by(faulty, "outcome"),
                   "reached_quorum": sum(r["signatures"] >= dep["threshold"] for r in faulty),
                   "ledger_changed": sum(r["outcome"] in ("Executed", "Blocked") for r in faulty)},
        "risk_flags": risk_metrics(honest),
        "timing_ms": {k: m(k, honest) for k in ("prove_ms", "attest_ms", "validator_check_ms", "settle_ms", "end_to_end_ms")},
        "gas": {"settle": m("settle_gas", honest), "top_up": m("top_up_gas", rows)},
        "proof_bytes": m("proof_bytes", honest),
        "operator_final": {"violations_k": opstate[1], "stake_units": opstate[2] * RULE // dep["registry_unit"]},
    }


if __name__ == "__main__":
    main()
