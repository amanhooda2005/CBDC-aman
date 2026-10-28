"""Simulated trusted execution environment of a validator node: verify-then-sign (IDF 7.10).

Runs as its own process and talks JSON lines over stdin/stdout; there is no command that signs without
running the checks. At start it copies the verifier binary it was launched with into a private directory,
computes its measurement M = SHA-256(SHA-256(this file) || SHA-256(verifier binary)) and generates its signing
key in memory (never written to disk). Before every use it re-hashes its private copy of the verifier.

  {"cmd": "identity"}                                   -> {"address", "measurement"}
  {"cmd": "attest", "request", "proof", "statement",
   "opening", "policy", "model"}                        -> {"signed", "reason", "signature"}

What a real TEE adds and this simulation does not: memory isolation from a compromised operating system, and a
measurement taken by the CPU itself (here the launcher measures and extends a TPM PCR).
"""
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures  # noqa: E402


def sha(b):
    return hashlib.sha256(b).digest()


def request_digest(req, policy):
    return Web3.keccak(text=json.dumps({"request": req, "policy": policy}, sort_keys=True))


def main():
    verifier_src = sys.argv[1]
    private = tempfile.mkdtemp(prefix="enclave_")
    os.chmod(private, 0o700)
    vbin = os.path.join(private, "verifier")
    shutil.copyfile(verifier_src, vbin)
    os.chmod(vbin, stat.S_IRWXU)
    vhash = sha(open(vbin, "rb").read())
    measurement = sha(sha(open(os.path.abspath(__file__), "rb").read()) + vhash).hex()
    key = Account.create()
    for line in sys.stdin:
        m = json.loads(line)
        if m["cmd"] == "identity":
            out = {"address": key.address, "measurement": measurement}
        elif m["cmd"] == "attest":
            if sha(open(vbin, "rb").read()) != vhash:
                out = {"signed": False, "reason": "verifier code changed since launch"}
            else:
                proof = bytes.fromhex(m["proof"])
                reason = fixtures.check(m["request"], proof, m["statement"], m["opening"], m["policy"], m["model"], prover=vbin)
                out = {"signed": reason is None, "reason": reason}
                if reason is None:
                    out["signature"] = key.sign_message(encode_defunct(primitive=request_digest(m["request"], m["policy"]))).signature.hex()
        else:
            out = {"error": "unknown command"}
        print(json.dumps(out), flush=True)


if __name__ == "__main__":
    main()
