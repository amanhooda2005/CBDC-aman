"""Validator attestation service (one process per validator node).

A validator co-signs a settlement request only if, independently and against its OWN view of the chain:
  1. the zero-knowledge composed proof verifies (native Rust verifier, `zk_composed verify`);
  2. the statement is about the expected model (model digest) and is the deployed variant (pq + zk);
  3. the SIS commitment in the statement opens to the amount being settled (opening sent privately);
  4. the request's EXEC / VIOL equal the proven outputs, its commitment and proof hashes match;
  5. the policy inside the proof is the current one: limit M, sanctioned id, and the operator's
     escalated slash read from the LiabilityRegistry (stake maintenance / escalation are on-chain).
Then it signs requestId(r) (EIP-191) with its validator key; the SettlementGate needs 3 of 4.

Usage (team validators, keys from the local deployment.json):
  python validator_node.py <index> <deployment.json> [port] [--byzantine]
Usage (an independent validator on another machine; see docs/INDEPENDENT_VALIDATOR.md):
  python validator_node.py --key-file my_validator.key --deployment deployment_public.json \
         --rpc http://127.0.0.1:8545 --prover /path/to/own/zk_composed --host 0.0.0.0 --port 9200 \
         --log decisions.jsonl
  The key never leaves that machine, the chain is read from that machine's own Besu node, the proof is
  checked by that machine's own build of the verifier, and the model digest is taken from that build
  (not from the deployment file). Every decision is appended to --log.
"""
import base64
import json
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

sys.path.insert(0, str(Path(__file__).parent))
import sis  # noqa: E402

RULE_SCALE = 256            # H3 rule fixed-point scale (payoffs, amounts, stake, slash)


def retry(fn, attempts=3, pause=0.3):
    """Chain reads: retry transient node errors (a JSON-RPC 'Internal error', a restarting node)."""
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(pause)


def keccak_words(values):
    return Web3.keccak(b"".join(int(v).to_bytes(32, "big") for v in values))


class Validator:
    def __init__(self, index, dep: dict, key=None, rpc=None, prover=None, log=None):
        self.index = index
        self.dep = dep
        self.key = Account.from_key(key or dep["validators"][index]["key"])
        self.w3 = Web3(Web3.HTTPProvider(rpc or dep["rpc"][index]))      # this validator's own node
        self.gate = self.w3.eth.contract(address=dep["gate"], abi=dep["abi"]["gate"])
        self.registry = self.w3.eth.contract(address=dep["registry"], abi=dep["abi"]["registry"])
        self.prover = prover or dep["prover_bin"]
        self.model = dep["model_digest"]
        if prover:   # independent validator: trust its own verifier build for the model digest
            own = json.loads(subprocess.run([prover, "info"], capture_output=True, text=True, check=True).stdout)["model_digest"]
            if own != self.model:
                print(f"WARNING: deployment model digest {self.model} differs from this build's {own}; using this build's", flush=True)
            self.model = own
        self.policy = dep["policy"]
        self.unit = dep["registry_unit"]
        self.log = open(log, "a") if log else None
        listed = self.gate.functions.isValidator(self.key.address).call()
        print(f"validator {self.key.address} on {rpc or dep['rpc'][index]}, gate {dep['gate']} "
              f"({'listed as attester' if listed else 'NOT an attester on this gate: its signatures will not count'}), "
              f"threshold {self.gate.functions.threshold().call()}, model {self.model[:16]}…", flush=True)

    def check(self, req: dict, proof: bytes, st: dict, opening: dict) -> str | None:
        if st.get("model") != self.model:
            return "statement is about a different model"
        if st.get("variant") != {"pq": True, "zk": True}:
            return "not the deployed (post-quantum, zero-knowledge) variant"
        pub = st["public"]
        m, sid, slash, exec_, viol, c = pub[0], pub[1], pub[2], pub[3], pub[4], pub[5:]
        if Web3.keccak(proof).hex() != Web3.to_bytes(hexstr=req["proofHash"]).hex():
            return "proof hash mismatch"
        if keccak_words(c).hex() != Web3.to_bytes(hexstr=req["commitmentHash"]).hex():
            return "commitment hash mismatch"
        if sis.commit(int(opening["amount"]), opening["r"]) != c:
            return "commitment does not open to the stated amount"
        if int(opening["amount"]) != int(req["amount"]):
            return "settled amount differs from committed amount"
        if bool(exec_) != bool(req["exec"]) or bool(viol) != bool(req["viol"]):
            return "request outputs differ from proven outputs"
        if m != self.policy["max_compliant"] or sid != self.policy["sanctioned_id"]:
            return "policy limit or sanctioned list differs from the current policy"
        op = retry(lambda: self.registry.functions.operatorOf(Web3.to_bytes(hexstr=req["agentId"])).call())
        expected_slash = retry(lambda: self.registry.functions.nextSlash(op).call()) * RULE_SCALE // self.unit
        if slash != expected_slash:
            return f"proof used slash {slash}, registry requires {expected_slash}"
        with tempfile.TemporaryDirectory() as d:
            pp, sp = Path(d) / "p.bin", Path(d) / "s.json"
            pp.write_bytes(proof)
            sp.write_text(json.dumps(st))
            out = subprocess.run([self.prover, "verify", str(pp), str(sp)], capture_output=True, text=True)
        if out.returncode != 0:
            return "zero-knowledge proof does not verify: " + out.stdout.strip()[:200]
        return None

    byzantine = False   # fault-injection mode: sign everything without checking

    def wait_for_block(self, n: int, timeout: float = 10.0):
        end = time.time() + timeout
        while retry(lambda: self.w3.eth.block_number) < n and time.time() < end:
            time.sleep(0.05)

    def attest(self, body: dict) -> dict:
        t = time.time()
        self.wait_for_block(int(body.get("policy_block", 0)))   # read the registry at least at the operator's block
        req = body["request"]
        proof = base64.b64decode(body["proof_b64"])
        reason = None if self.byzantine else self.check(req, proof, body["statement"], body["opening"])
        ms = (time.time() - t) * 1e3
        rid = retry(lambda: self.gate.functions.requestId(tuple_request(req)).call())
        self._log(req, rid, reason, ms)
        if reason:
            return {"validator": self.key.address, "signed": False, "reason": reason, "check_ms": ms}
        sig = Account.sign_message(encode_defunct(primitive=rid), self.key.key)
        return {"validator": self.key.address, "signed": True, "signature": sig.signature.hex(), "check_ms": ms}

    def _log(self, req, rid, reason, ms):
        if self.log:
            nonce = int(req["nonce"])
            self.log.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "request_id": "0x" + bytes(rid).hex(),
                                       "run_start": nonce // 100000, "i": nonce % 100000, "amount": int(req["amount"]),
                                       "exec": bool(req["exec"]), "viol": bool(req["viol"]), "signed": reason is None,
                                       "reason": reason or "", "check_ms": round(ms, 1)}) + "\n")
            self.log.flush()


def tuple_request(req: dict):
    return (Web3.to_bytes(hexstr=req["agentId"]), Web3.to_checksum_address(req["from"]), Web3.to_checksum_address(req["to"]),
            int(req["amount"]), Web3.to_bytes(hexstr=req["commitmentHash"]), Web3.to_bytes(hexstr=req["proofHash"]),
            bool(req["exec"]), bool(req["viol"]), int(req["nonce"]))


def serve(index, dep_path: str, port: int, byzantine: bool = False, host="127.0.0.1", key=None, rpc=None, prover=None, log=None):
    v = Validator(index, json.load(open(dep_path)), key, rpc, prover, log)
    v.byzantine = byzantine

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:
                res = v.attest(body)
            except Exception as e:  # malformed request: refuse, never crash
                res = {"validator": v.key.address, "signed": False, "reason": f"error: {e}"}
            data = json.dumps(res).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    ThreadingHTTPServer((host, port), H).serve_forever()


if __name__ == "__main__":
    if "--key-file" in sys.argv:   # independent validator
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--key-file", required=True)
        ap.add_argument("--deployment", required=True)
        ap.add_argument("--rpc", default="http://127.0.0.1:8545")
        ap.add_argument("--prover", required=True)
        ap.add_argument("--host", default="0.0.0.0")
        ap.add_argument("--port", type=int, default=9200)
        ap.add_argument("--log", default="decisions.jsonl")
        a = ap.parse_args()
        serve(None, a.deployment, a.port, False, a.host, open(a.key_file).read().strip(), a.rpc, a.prover, a.log)
    else:
        i = int(sys.argv[1])
        args = [x for x in sys.argv[3:] if not x.startswith("--")]
        serve(i, sys.argv[2], int(args[0]) if args else 9100 + i, "--byzantine" in sys.argv, log=f"logs/decisions_v{i}.jsonl")
