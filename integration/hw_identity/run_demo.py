"""End-to-end test of the hardware-anchored operator identity with two TPMs and the on-chain LiabilityRegistry.

Starts two software TPMs (swtpm, each with a manufacturer-style EK certificate from its local CA) and an anvil
chain with the LiabilityRegistry (regulator = the admission registry), then runs the honest flow and the attacks:
  enrolment of TPM A for operator account A, on-chain credential;           -> accepted
  agent registrations attested by TPM A, on-chain registerAgent with bond;  -> accepted
  replayed attestation; attestation re-bound to another agent id;           -> refused
  tampered attestation; attestation signed by a software key;               -> refused (without deposit B*)
  enrolment with a forged (self-signed) EK certificate;                     -> refused
  enrolment of an imported software key as AK;                              -> refused (not fixedTPM)
  credential activation attempted by a different TPM;                       -> refused
  same TPM, new AK, second operator account;                                -> refused (one hardware identity)
  same TPM, new AK, same account;                                           -> accepted, same k(O), history kept
  counter deleted and re-created on the TPM;                                -> TPM resumes above the old value
  attestations submitted out of order (cloned / stale state);               -> older one refused
  TPM B (new hardware) for operator account B;                              -> accepted as a NEW identity
Writes results/hw_identity.json. Needs swtpm, swtpm_setup, tpm2-tools, anvil, web3, cryptography.
Usage: python3 run_demo.py [workdir]
"""
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from web3 import Web3

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from tpm import OperatorTPM          # noqa: E402
from admission import AdmissionRegistry  # noqa: E402

CA = Path("/var/lib/swtpm-localca")
ANVIL = os.path.expanduser("~/.foundry/bin/anvil")
PORT_CHAIN = 8665


def start_swtpm(wd, port):
    state = wd / f"state{port}"
    shutil.rmtree(state, ignore_errors=True); state.mkdir(parents=True)
    subprocess.run(["swtpm_setup", "--tpm2", "--tpmstate", str(state), "--create-ek-cert", "--create-platform-cert",
                    "--lock-nvram", "--overwrite"], check=True, capture_output=True)
    p = subprocess.Popen(["swtpm", "socket", "--tpm2", "--tpmstate", f"dir={state}", "--server", f"type=tcp,port={port}",
                          "--ctrl", f"type=tcp,port={port + 1}", "--flags", "not-need-init,startup-clear"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    return p


def main():
    wd = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="hwid_"))
    procs = []
    res = {"scenarios": {}}
    try:
        procs.append(start_swtpm(wd, 2341)); procs.append(start_swtpm(wd, 2351))
        tpmA = OperatorTPM("swtpm:host=127.0.0.1,port=2341", str(wd / "A"))
        tpmB = OperatorTPM("swtpm:host=127.0.0.1,port=2351", str(wd / "B"))

        procs.append(subprocess.Popen([ANVIL, "--port", str(PORT_CHAIN), "--silent"]))
        time.sleep(2)
        w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{PORT_CHAIN}"))
        acct = w3.eth.accounts
        regulator, treasury, opA, opA2, opB = acct[0], acct[1], acct[2], acct[3], acct[4]
        art = json.load(open(HERE.parent / "out" / "LiabilityRegistry.sol" / "LiabilityRegistry.json"))
        Reg = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"]["object"])
        # base slash s = 15, detection p = 1, V_max = 30, C_min = 1  ->  B* = 30 - 15 - 1 = 14 (the H3 study's values)
        params = (regulator, treasury, 15, 100, 14, 0, 30, 1, 10000)
        h = Reg.constructor(params).transact({"from": regulator})
        reg = w3.eth.contract(address=w3.eth.wait_for_transaction_receipt(h).contractAddress, abi=art["abi"])
        R = AdmissionRegistry([str(CA / "swtpm-localca-rootca-cert.pem")], [str(CA / "issuercert.pem")],
                              admission_threshold=14, chain=(w3, reg, regulator))
        S = res["scenarios"]

        # 1. enrolment of TPM A
        matA = tpmA.provision()
        e = R.begin_enrolment(opA, matA)
        f = R.finish_enrolment(opA, tpmA.activate_credential(e["credential_blob"]))
        kA = f["operator_id"]
        S["enrol_tpmA"] = {**{k: v for k, v in f.items()}, "onchain_credentialed": reg.functions.operators(opA).call()[0]}

        # 2. attested agent registrations + on-chain registerAgent with the bond
        tpm_ms, ver_ms, counters = [], [], []
        for i in range(20):
            aid = Web3.keccak(text=f"agent-A-{i}")
            n = R.issue_nonce()
            at = tpmA.attest_registration(n, aid)
            r = R.admit_agent(aid, n, at["attest"], at["sig"])
            assert r["ok"], r
            tpm_ms.append(at["tpm_ms"]); ver_ms.append(r["verify_ms"]); counters.append(r["counter"])
            if i < 3:
                h = reg.functions.registerAgent(aid).transact({"from": opA, "value": 14})
                assert w3.eth.wait_for_transaction_receipt(h).status == 1
        S["attested_registrations"] = {"admitted": 20, "counters": [counters[0], counters[-1]], "strictly_increasing": counters == sorted(set(counters)),
                                       "onchain_registerAgent_ok": 3, "attestation_bytes": len(at["attest"]), "signature_bytes": len(at["sig"])}
        res["timing_ms"] = {"tpm_increment_and_certify_median": round(statistics.median(tpm_ms), 1),
                            "registry_verify_median": round(statistics.median(ver_ms), 2), "runs": 20,
                            "note": "software TPM on the 2-vCPU VM; a hardware TPM is typically slower for signing"}
        last = at

        # 3. replay of an accepted attestation (same nonce)
        S["replay_same_nonce"] = R.admit_agent(Web3.keccak(text="agent-A-19"), n, last["attest"], last["sig"])
        # 4. attestation re-bound to another agent id with a fresh nonce
        n2 = R.issue_nonce(); at2 = tpmA.attest_registration(n2, Web3.keccak(text="agent-A-x"))
        S["rebound_to_other_agent"] = R.admit_agent(Web3.keccak(text="agent-A-y"), n2, at2["attest"], at2["sig"])
        # 5. tampered attestation (counter value changed after signing)
        bad = bytearray(at2["attest"]); bad[-1] ^= 0x01
        S["tampered_attestation"] = R.admit_agent(Web3.keccak(text="agent-A-x"), n2, bytes(bad), at2["sig"])
        # 6. attestation forged with a software key
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import hashes
        sk = ec.generate_private_key(ec.SECP256R1())
        S["software_signed_attestation"] = R.admit_agent(Web3.keccak(text="agent-A-x"), n2, at2["attest"], sk.sign(at2["attest"], ec.ECDSA(hashes.SHA256())))
        S["software_signed_with_deposit_B*"] = R.admit_agent(Web3.keccak(text="agent-A-z"), None, None, None, deposit=14)

        # 7. forged EK certificate (self-signed)
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{d}/k", "-out", f"{d}/c.der",
                            "-outform", "DER", "-subj", "/CN=fake-tpm", "-days", "1"], check=True, capture_output=True)
            S["forged_ek_certificate"] = R.begin_enrolment(acct[5], {**matA, "ek_cert_der": open(f"{d}/c.der", "rb").read()})
        # 8. imported software key presented as AK (not created inside the TPM)
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", f"{d}/sw.pem"], check=True, capture_output=True)
            tpmA.run("tpm2_loadexternal", "-C", "n", "-G", "ecc", "-r", f"{d}/sw.pem", "-c", f"{d}/sw.ctx", "-Q")
            tpmA.run("tpm2_readpublic", "-c", f"{d}/sw.ctx", "-o", f"{d}/sw.tss", "-Q"); tpmA.run("tpm2_flushcontext", "-t")
            S["software_key_as_AK"] = {k: v for k, v in R.begin_enrolment(acct[5], {**matA, "ak_tss": open(f"{d}/sw.tss", "rb").read()}).items() if k != "credential_blob"}
        # 9. credential activation attempted by a different TPM (B, provisioned with its own EK and AK) for TPM A's enrolment
        matB = tpmB.provision()
        e = R.begin_enrolment(opA, matA)
        try:
            sec = tpmB.activate_credential(e["credential_blob"])
            S["activation_by_other_tpm"] = R.finish_enrolment(opA, sec)
        except RuntimeError as err:
            R.pending.pop(opA, None)
            import re
            rc = re.findall(r"ErrorCode \((0x[0-9a-fA-F]+)\)", str(err))
            S["activation_by_other_tpm"] = {"ok": False, "tpm_error_codes": rc,
                                            "tpm_b_still_operational": tpmB.run("tpm2_getrandom", "4", "--hex", check=False).returncode == 0,
                                            "reason": "TPM B cannot activate a credential made for TPM A's EK and AK: TPM2_ActivateCredential refuses "
                                                      "(the software TPM reports TPM_RC_FAILURE for the failed seed decryption and stays operational)"}

        # 10. same TPM, new AK, second operator account -> refused; same account -> same identity, history kept
        matA2 = tpmA.provision(new_ak=True)
        S["same_tpm_new_ak_second_account"] = {k: v for k, v in R.begin_enrolment(opA2, matA2).items() if k != "credential_blob"}
        e = R.begin_enrolment(opA, matA2)
        f = R.finish_enrolment(opA, tpmA.activate_credential(e["credential_blob"]))
        S["same_tpm_new_ak_same_account"] = {**f, "same_operator_id": f["operator_id"] == kA, "last_counter_kept": R.operators[kA]["last_counter"],
                                             "second_account_credentialed_onchain": reg.functions.operators(opA2).call()[0]}

        # 11. counter deleted and re-created on the TPM
        before = R.operators[kA]["last_counter"]
        tpmA.delete_counter(); tpmA.define_counter()
        n3 = R.issue_nonce(); at3 = tpmA.attest_registration(n3, Web3.keccak(text="agent-A-after-reset"))
        r3 = R.admit_agent(Web3.keccak(text="agent-A-after-reset"), n3, at3["attest"], at3["sig"])
        S["counter_deleted_and_recreated"] = {"last_counter_before": before, "counter_after": r3.get("counter"), "admitted": r3["ok"],
                                              "reset_to_low_value": (r3.get("counter") or 0) <= before}
        # 12. out-of-order submission (what a cloned or restored node state would produce)
        na, nb = R.issue_nonce(), R.issue_nonce()
        ata = tpmA.attest_registration(na, Web3.keccak(text="agent-A-a")); atb = tpmA.attest_registration(nb, Web3.keccak(text="agent-A-b"))
        S["newer_first"] = R.admit_agent(Web3.keccak(text="agent-A-b"), nb, atb["attest"], atb["sig"])
        S["older_after"] = R.admit_agent(Web3.keccak(text="agent-A-a"), na, ata["attest"], ata["sig"])

        # 13. new hardware: TPM B for operator account B -> a new identity (needs its own bonds per agent)
        e = R.begin_enrolment(opB, matB)
        f = R.finish_enrolment(opB, tpmB.activate_credential(e["credential_blob"]))
        S["enrol_tpmB_new_hardware"] = {**f, "different_operator_id": f["operator_id"] != kA,
                                        "onchain_credentialed": reg.functions.operators(opB).call()[0]}

        expect_ok = {"enrol_tpmA", "same_tpm_new_ak_same_account", "newer_first", "enrol_tpmB_new_hardware", "software_signed_with_deposit_B*"}
        res["summary"] = {k: bool(v.get("ok")) for k, v in S.items() if isinstance(v, dict) and "ok" in v}
        res["all_as_expected"] = (all(res["summary"][k] == (k in expect_ok) for k in res["summary"])
                                  and S["attested_registrations"]["strictly_increasing"]
                                  and not S["counter_deleted_and_recreated"]["reset_to_low_value"]
                                  and S["same_tpm_new_ak_same_account"]["same_operator_id"]
                                  and not S["same_tpm_new_ak_same_account"]["second_account_credentialed_onchain"])
        res["environment"] = {"tpm": "swtpm 0.7.3 (software TPM 2.0, EK certificates from swtpm-localca)",
                              "tpm2_tools": subprocess.run(["tpm2_startup", "--version"], capture_output=True, text=True).stdout.split()[1],
                              "chain": "anvil, LiabilityRegistry.sol (h3-abt) with the regulator role held by the admission registry"}
        out = HERE / "results"; out.mkdir(exist_ok=True)
        json.dump(res, open(out / "hw_identity.json", "w"), indent=1, default=lambda o: o.hex() if isinstance(o, bytes) else str(o))
        print(json.dumps({"summary": res["summary"], "all_as_expected": res["all_as_expected"], "timing_ms": res["timing_ms"],
                          "counter_reset": S["counter_deleted_and_recreated"]}, indent=1))
    finally:
        for p in procs:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
