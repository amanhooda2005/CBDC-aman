"""Simulation of IDF 7.10: verify-then-sign in a trusted execution environment, with remote attestation.

Parts:
  launcher         measures the enclave (enclave.py + the verifier binary it loads), extends the measurement into
                   PCR 16 of the validator machine's TPM, starts the enclave and obtains a TPM quote over PCR 16
                   whose qualifying data is H(enclave signing address);
  attester registry  accepts an enclave signing key only with a quote signed by an enrolled TPM (EK certificate +
                   credential activation, as in integration/hw_identity) whose PCR 16 equals the measurement of the
                   approved verifier build and whose qualifying data names that key;
  settlement check   counts a signature only if its signer is a registered enclave key.

Scenarios: the approved enclave signs the honest request and refuses five falsified ones; an enclave launched
with a modified verifier (one that accepts everything) gets no registration, so its signature on a falsified
request does not count; a replayed quote for another key, a key outside any enclave, and a quote from an
unenrolled TPM are refused; replacing the verifier on disk after launch does not change what the enclave does.

Writes results/tee_sim.json. Usage: python3 tee_sim.py [workdir]
"""
import hashlib
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from eth_account import Account
from eth_account.messages import encode_defunct

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "hw_identity"))
import fixtures as F  # noqa: E402
from tpm import OperatorTPM, AK_HANDLE  # noqa: E402
from admission import AdmissionRegistry, TPM_GENERATED  # noqa: E402
from run_demo import start_swtpm, CA  # noqa: E402

TPM_ST_ATTEST_QUOTE = 0x8018
PCR = 16


def sha(b):
    return hashlib.sha256(b).digest()


def measurement(enclave_file, verifier_bin):
    return sha(sha(open(enclave_file, "rb").read()) + sha(open(verifier_bin, "rb").read()))


def parse_quote(b):
    o = 0
    def u(fmt):
        nonlocal o
        v = struct.unpack_from(fmt, b, o); o += struct.calcsize(fmt); return v[0]
    def tpm2b():
        nonlocal o
        n = u(">H"); v = b[o:o + n]; o += n; return v
    q = {"magic": u(">I"), "type": u(">H"), "signer": tpm2b(), "extra": tpm2b()}
    o += 8 + 4 + 4 + 1 + 8                      # clockInfo, firmwareVersion
    sel = []
    for _ in range(u(">I")):
        h = u(">H"); n = u(">B"); bm = b[o:o + n]; o += n
        sel.append((h, [i for i in range(8 * n) if bm[i // 8] >> (i % 8) & 1]))
    q["selection"], q["pcr_digest"] = sel, tpm2b()
    return q


class Enclave:
    def __init__(self, verifier_bin):
        self.p = subprocess.Popen([sys.executable, str(HERE / "enclave.py"), verifier_bin], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, env=dict(os.environ))

    def call(self, **m):
        self.p.stdin.write(json.dumps(m) + "\n"); self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def close(self):
        self.p.terminate(); self.p.wait(10)


def launch(tpm, verifier_bin):
    """Launcher: measure, extend PCR 16, start the enclave, quote PCR 16 bound to the enclave's key."""
    M = measurement(HERE / "enclave.py", verifier_bin)
    tpm.run("tpm2_pcrreset", str(PCR))
    tpm.run("tpm2_pcrextend", f"{PCR}:sha256={M.hex()}")
    enc = Enclave(verifier_bin)
    ident = enc.call(cmd="identity")
    assert ident["measurement"] == M.hex(), "enclave and launcher disagree on the measurement"
    t0 = time.perf_counter()
    qual = sha(bytes.fromhex(ident["address"][2:])).hex()
    tpm.run("tpm2_quote", "-c", AK_HANDLE, "-l", f"sha256:{PCR}", "-q", qual, "-m", tpm.p("q.msg"), "-s", tpm.p("q.sig"),
            "-f", "plain", "-g", "sha256", "-Q")
    quote = {"msg": open(tpm.p("q.msg"), "rb").read(), "sig": open(tpm.p("q.sig"), "rb").read()}
    return enc, ident["address"], quote, (time.perf_counter() - t0) * 1e3


class AttesterRegistry:
    def __init__(self, platform_registry: AdmissionRegistry, approved_measurements):
        self.platforms = platform_registry          # enrolled TPMs (EK certificate + credential activation)
        self.approved = {hashlib.sha256(bytes(32) + m).digest(): m.hex() for m in approved_measurements}   # expected PCR 16
        self.attesters = {}

    def register(self, address, quote):
        q = parse_quote(quote["msg"])
        if q["magic"] != TPM_GENERATED or q["type"] != TPM_ST_ATTEST_QUOTE:
            return {"ok": False, "reason": "not a TPM-generated quote"}
        signer = None
        for name, k in self.platforms.ak_owner.items():
            try:
                serialization.load_der_public_key(self.platforms.operators[k]["aks"][name]).verify(quote["sig"], quote["msg"], ec.ECDSA(hashes.SHA256()))
                signer = k
                break
            except InvalidSignature:
                continue
        if signer is None:
            return {"ok": False, "reason": "quote not signed by an enrolled TPM"}
        if q["extra"] != sha(bytes.fromhex(address[2:])):
            return {"ok": False, "reason": "quote is bound to a different signing key"}
        if q["selection"] != [(0x000B, [PCR])]:
            return {"ok": False, "reason": "quote does not cover PCR 16"}
        pcr_ok = [m for pcr, m in self.approved.items() if sha(pcr) == q["pcr_digest"]]
        if not pcr_ok:
            return {"ok": False, "reason": "enclave measurement is not an approved verifier build"}
        self.attesters[address] = {"platform": signer, "measurement": pcr_ok[0]}
        return {"ok": True, "platform": signer, "measurement": pcr_ok[0]}


def counts(registry, req, policy, sig):
    """Settlement side: does this signature count?"""
    from enclave import request_digest
    signer = Account.recover_message(encode_defunct(primitive=request_digest(req, policy)), signature=bytes.fromhex(sig.removeprefix("0x")))
    return signer in registry.attesters, signer


def main():
    wd = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="tee_"))
    procs, encs, res = [], [], {"scenarios": {}}
    S = res["scenarios"]
    try:
        procs += [start_swtpm(wd, 2441), start_swtpm(wd, 2451)]
        tpm = OperatorTPM("swtpm:host=127.0.0.1,port=2441", str(wd / "V"))          # validator machine's TPM
        other = OperatorTPM("swtpm:host=127.0.0.1,port=2451", str(wd / "X"))        # an unenrolled machine
        plat = AdmissionRegistry([str(CA / "swtpm-localca-rootca-cert.pem")], [str(CA / "issuercert.pem")])
        mat = tpm.provision()
        e = plat.begin_enrolment("validator-1", mat)
        S["platform_tpm_enrolled"] = {k: v for k, v in plat.finish_enrolment("validator-1", tpm.activate_credential(e["credential_blob"])).items()}
        other.provision()

        approved_bin = str(wd / "zk_composed_approved"); shutil.copyfile(F.PROVER, approved_bin); os.chmod(approved_bin, 0o755)
        reg = AttesterRegistry(plat, [measurement(HERE / "enclave.py", approved_bin)])
        model = F.model_digest()
        policy = {**F.POLICY, "slash": F.SLASH}
        x = F.licit_inputs(3)[1]
        variants = F.falsified_variants(x, 5000, 7, model)

        # 1. approved enclave
        enc, addr, quote, quote_ms = launch(tpm, approved_bin); encs.append(enc)
        S["approved_enclave_registered"] = reg.register(addr, quote)
        decisions, times = {}, []
        for name, req, proof, st, op in variants:
            t0 = time.perf_counter()
            r = enc.call(cmd="attest", request=req, proof=proof.hex(), statement=st, opening=op, policy=policy, model=model)
            times.append((time.perf_counter() - t0) * 1e3)
            decisions[name] = {"signed": r["signed"], "reason": r["reason"]}
            if r["signed"]:
                decisions[name]["signature_counts_at_settlement"] = counts(reg, req, policy, r["signature"])[0]
        S["approved_enclave_decisions"] = decisions
        honest = variants[0]

        # 2. enclave launched with a modified verifier that accepts every proof
        evil = wd / "zk_composed_patched"
        evil.write_text("#!/bin/sh\nif [ \"$1\" = verify ]; then echo '{\"valid\":true}'; exit 0; fi\nexec " + approved_bin + " \"$@\"\n")
        os.chmod(evil, 0o755)
        enc2, addr2, quote2, _ = launch(tpm, str(evil)); encs.append(enc2)
        S["patched_verifier_registration"] = reg.register(addr2, quote2)
        name, req, proof, st, op = variants[3]                                  # corrupted proof
        r2 = enc2.call(cmd="attest", request=req, proof=proof.hex(), statement=st, opening=op, policy=policy, model=model)
        S["patched_verifier_signs_corrupted_proof"] = {"signed_by_patched_enclave": r2["signed"],
                                                       "signature_counts_at_settlement": counts(reg, req, policy, r2["signature"])[0] if r2["signed"] else False}

        # 3. approved quote replayed for a key outside any enclave
        host = Account.create()
        S["replayed_quote_for_host_key"] = reg.register(host.address, quote)
        # 4. host signs a falsified request with its own key
        from enclave import request_digest
        hs = host.sign_message(encode_defunct(primitive=request_digest(req, policy))).signature.hex()
        S["host_key_signature_counts"] = counts(reg, req, policy, hs)[0]
        # 5. quote from an unenrolled TPM (same approved enclave code)
        enc3, addr3, quote3, _ = launch(other, approved_bin); encs.append(enc3)
        S["quote_from_unenrolled_tpm"] = reg.register(addr3, quote3)
        # 6. verifier replaced on disk after launch: the enclave keeps its private copy
        shutil.copyfile(evil, approved_bin)
        r6 = enc.call(cmd="attest", request=req, proof=proof.hex(), statement=st, opening=op, policy=policy, model=model)
        r6h = enc.call(cmd="attest", request=honest[1], proof=honest[2].hex(), statement=honest[3], opening=honest[4], policy=policy, model=model)
        S["verifier_replaced_on_disk_after_launch"] = {"corrupted_proof_signed": r6["signed"], "reason": r6["reason"], "honest_still_signed": r6h["signed"]}

        dec = S["approved_enclave_decisions"]
        res["all_as_expected"] = (S["approved_enclave_registered"]["ok"] and dec["honest"]["signed"] and dec["honest"]["signature_counts_at_settlement"]
                                  and not any(v["signed"] for k, v in dec.items() if k != "honest")
                                  and not S["patched_verifier_registration"]["ok"]
                                  and S["patched_verifier_signs_corrupted_proof"]["signed_by_patched_enclave"]
                                  and not S["patched_verifier_signs_corrupted_proof"]["signature_counts_at_settlement"]
                                  and not S["replayed_quote_for_host_key"]["ok"] and not S["host_key_signature_counts"]
                                  and not S["quote_from_unenrolled_tpm"]["ok"]
                                  and not S["verifier_replaced_on_disk_after_launch"]["corrupted_proof_signed"]
                                  and S["verifier_replaced_on_disk_after_launch"]["honest_still_signed"])
        res["timing_ms"] = {"tpm_quote": round(quote_ms, 1), "enclave_check_and_sign_median": round(sorted(times)[len(times) // 2], 1)}
        res["limits"] = ("simulation: the enclave is a separate process, not hardware-isolated memory, and the launcher (not the CPU) "
                         "measures it; TPMs are software TPMs (swtpm) with a local CA")
        out = HERE / "results"; out.mkdir(exist_ok=True)
        json.dump(res, open(out / "tee_sim.json", "w"), indent=1, default=str)
        print(json.dumps({"all_as_expected": res["all_as_expected"], "timing_ms": res["timing_ms"],
                          "decisions": {k: v["reason"] or "signed" for k, v in dec.items()},
                          "others": {k: (v.get("reason") if isinstance(v, dict) and "reason" in v else v) for k, v in S.items()
                                     if k not in ("approved_enclave_decisions", "platform_tpm_enrolled")}}, indent=1, default=str))
    finally:
        for e in encs:
            e.close()
        for p in procs:
            p.send_signal(signal.SIGTERM); p.wait(10)


if __name__ == "__main__":
    main()
