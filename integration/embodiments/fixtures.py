"""Shared pieces for the embodiment simulations: the composed prover/verifier, real Elliptic inputs, and the
validator's checks (the same checks as integration/pipeline/validator_node.py, with the current policy passed in
instead of read from a node)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from web3 import Web3

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "integration" / "pipeline"))
sys.path.insert(0, str(ROOT / "integration" / "hw_identity"))
import sis  # noqa: E402

PROVER = os.environ.get("ZK_COMPOSED", str(ROOT / "h4-pqc-stark/zk_composed/target/release/zk_composed"))
DATA = os.environ.get("ELLIPTIC_DIR", "/home/claude/data/elliptic_raw/elliptic_bitcoin_dataset")
POLICY = {"max_compliant": 12800, "sanctioned_id": 1000}
SLASH = 3840          # registry's current escalated slash for the operator, in rule units (15 * 256)
STAKE = 25600


def model_digest(prover=PROVER):
    return json.loads(subprocess.run([prover, "info"], capture_output=True, text=True, check=True).stdout)["model_digest"]


def licit_inputs(n=4, seed=7):
    """Real Elliptic test-period transactions labelled licit (quantized H1 inputs)."""
    from run_pipeline import elliptic_inputs
    return [x for x, lab, _, _ in elliptic_inputs(DATA, 200, seed) if lab == 0][:n]


def witness(x, amount, dest, r, slash=SLASH):
    return {"x": x, "decision": {"amount": amount, "dest": dest, "compliant": 577, "violation": 1017, "staked": 1, "stake": STAKE},
            "policy": {**POLICY, "slash": slash}, "r": r}


def prove(w, prover=PROVER):
    with tempfile.TemporaryDirectory() as d:
        wp, pp, sp = Path(d) / "w.json", Path(d) / "p.bin", Path(d) / "s.json"
        wp.write_text(json.dumps(w))
        out = subprocess.run([prover, "prove", str(wp), str(pp), str(sp)], capture_output=True, text=True, check=True)
        return pp.read_bytes(), json.loads(sp.read_text()), json.loads(out.stdout)


def verify(proof, st, prover=PROVER):
    with tempfile.TemporaryDirectory() as d:
        pp, sp = Path(d) / "p.bin", Path(d) / "s.json"
        pp.write_bytes(proof); sp.write_text(json.dumps(st))
        out = subprocess.run([prover, "verify", str(pp), str(sp)], capture_output=True, text=True)
        return out.returncode == 0, out.stdout.strip()


def check(req, proof, st, opening, policy, model, prover=PROVER):
    """None if a validator would sign; otherwise the reason (same order of checks as validator_node.Validator.check)."""
    if st.get("model") != model:
        return "statement is about a different model"
    if st.get("variant") != {"pq": True, "zk": True}:
        return "not the deployed (post-quantum, zero-knowledge) variant"
    pub = st["public"]
    m, sid, slash, exec_, viol, c = pub[0], pub[1], pub[2], pub[3], pub[4], pub[5:]
    if Web3.keccak(proof).hex() != Web3.to_bytes(hexstr=req["proofHash"]).hex():
        return "proof hash mismatch"
    if sis.commit(int(opening["amount"]), opening["r"]) != c:
        return "commitment does not open to the stated amount"
    if int(opening["amount"]) != int(req["amount"]):
        return "settled amount differs from committed amount"
    if bool(exec_) != bool(req["exec"]) or bool(viol) != bool(req["viol"]):
        return "request outputs differ from proven outputs"
    if m != policy["max_compliant"] or sid != policy["sanctioned_id"]:
        return "policy limit or sanctioned list differs from the current policy"
    if slash != policy["slash"]:
        return f"proof used slash {slash}, registry requires {policy['slash']}"
    ok, out = verify(proof, st, prover)
    if not ok:
        return "zero-knowledge proof does not verify: " + out[:120]
    return None


def request_for(proof, st, amount):
    return {"amount": amount, "proofHash": "0x" + Web3.keccak(proof).hex(), "exec": bool(st["public"][3]), "viol": bool(st["public"][4])}


def falsified_variants(x, amount, dest, model):
    """(name, request, proof, statement, opening) for each fault the pipeline injects, plus an honest one."""
    r = sis.random_ternary()
    proof, st, _ = prove(witness(x, amount, dest, r))
    base = request_for(proof, st, amount)
    out = [("honest", base, proof, st, {"amount": amount, "r": r})]
    out.append(("forged VIOL", {**base, "viol": not base["viol"]}, proof, st, {"amount": amount, "r": r}))
    out.append(("amount swapped", {**base, "amount": amount + 500}, proof, st, {"amount": amount + 500, "r": r}))
    bad = bytearray(proof); bad[len(bad) // 2] ^= 0xFF
    out.append(("corrupted proof", request_for(bytes(bad), st, amount), bytes(bad), st, {"amount": amount, "r": r}))
    out.append(("wrong model", base, proof, {**st, "model": "00" * 32}, {"amount": amount, "r": r}))
    p2, s2, _ = prove(witness(x, amount, dest, r, slash=SLASH + 256))
    out.append(("outdated slash", request_for(p2, s2, amount), p2, s2, {"amount": amount, "r": r}))
    return out
