"""Simulation of IDF 7.9: offline payment devices with a secure element.

Devices and roles:
  payer device   runs the risk model and the agent rule and generates the composed proof itself (prover program);
                 its secure element (a TPM 2.0, here swtpm) generates the commitment randomness r with its hardware
                 random number generator and, for every payment, increments its monotonic counter and certifies the
                 counter value bound to H(commitment ‖ proof ‖ payee ‖ amount) with a key that never leaves it
                 (TPM2_NV_Certify);
  payee devices  hold no network connection; they check the bundle completely: the validator-signed policy
                 snapshot (3 of 4 validator signatures, not expired, naming the payer's secure element), the secure
                 element's attestation, a counter above the last one seen from that device, the proof (native
                 verifier), the commitment opening, EXEC = 1 and VIOL = 0, and the policy in the proof; accepted
                 bundles are queued;
  ledger         on reconnection, the validators re-check every queued bundle and settle them in the order of the
                 secure element's counter, refusing a (device, counter) or commitment already settled and any payment
                 whose declared cumulative amount does not follow from the earlier ones: overspending the offline
                 allowance is detected at sync and the device is flagged.
Each payment declares the payer's cumulative offline spending (cum), bound into the secure element's attestation;
payees accept only cum <= the offline allowance in the validator-signed snapshot.

Writes results/offline_sim.json. Usage: python3 offline_sim.py [workdir]
"""
import hashlib
import json
import signal
import statistics
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
from web3 import Web3

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "hw_identity"))
import fixtures as F  # noqa: E402
import sis  # noqa: E402
from tpm import OperatorTPM  # noqa: E402
from admission import AdmissionRegistry, parse_nv_attest, counter_name, TPM_GENERATED, TPM_ST_ATTEST_NV  # noqa: E402
from run_demo import start_swtpm, CA  # noqa: E402
from validator_node import keccak_words  # noqa: E402

DAY = 86400


def sha(b):
    return hashlib.sha256(b).digest()


def binding(c, proof, payee, amount, cum):
    return keccak_words(c) + Web3.keccak(proof) + struct.pack(">QQQ", payee, amount, cum)


class SecureElement:
    """The payer device's secure element, backed by a TPM."""
    def __init__(self, tpm: OperatorTPM):
        self.tpm = tpm

    def random_ternary(self):
        out = []
        while len(out) < sis.N_RAND:
            raw = bytes.fromhex(self.tpm.run("tpm2_getrandom", "64", "--hex").stdout.strip())
            out += [b % 3 - 1 for b in raw if b < 255][: sis.N_RAND - len(out)]
        return out

    def certify(self, bind):
        a = self.tpm.attest_registration(bind, b"")      # counter += 1, then TPM2_NV_Certify over H(bind)
        return a


class Payer:
    def __init__(self, se, ak_der, x, snapshot):
        self.se, self.ak_der, self.x, self.snapshot, self.cum = se, ak_der, x, snapshot, 0

    def confirm(self, bundle):
        self.cum = bundle["cum"]

    def pay(self, payee, amount, lie_cum=None):
        """Honest app: refuses locally beyond the allowance. lie_cum: a manipulated app declaring a false total."""
        cum = self.cum + amount if lie_cum is None else lie_cum
        if lie_cum is None and cum > self.snapshot["body"]["offline_allowance"]:
            return None, {"refused_by_device": f"cumulative {cum} would exceed the offline allowance"}
        t0 = time.perf_counter()
        r = self.se.random_ternary()
        proof, st, info = F.prove(F.witness(self.x, amount, payee, r, slash=self.snapshot["body"]["policy"]["slash"]))
        c = st["public"][5:]
        att = self.se.certify(binding(c, proof, payee, amount, cum))
        bundle = {"payee": payee, "amount": amount, "cum": cum, "r": r, "proof": proof, "statement": st, "se_attest": att["attest"],
                  "se_sig": att["sig"], "se_ak": self.ak_der, "snapshot": self.snapshot}
        return bundle, {"prove_ms": info["prove_ms"], "se_ms": att["tpm_ms"], "total_ms": (time.perf_counter() - t0) * 1e3}


def bundle_size(b):
    return (len(b["proof"]) + len(json.dumps(b["statement"])) + len(b["r"]) // 4 + 24 + len(b["se_attest"]) + len(b["se_sig"])
            + len(b["se_ak"]) + len(json.dumps(b["snapshot"]["body"])) + 65 * len(b["snapshot"]["sigs"]))


def snapshot_digest(body):
    return Web3.keccak(text=json.dumps(body, sort_keys=True))


def check_bundle(b, validators, model, now, payee_id=None, last_counter=None):
    """All checks a payee device makes offline (and the ledger repeats at sync). Returns (reason or None, counter)."""
    snap = b["snapshot"]
    signers = {Account.recover_message(encode_defunct(primitive=snapshot_digest(snap["body"])), signature=s) for s in snap["sigs"]}
    if len(signers & set(validators)) < 3:
        return "policy snapshot lacks 3 of 4 validator signatures", None
    if now > snap["body"]["expires"]:
        return "policy snapshot expired", None
    if sha(b["se_ak"]).hex() != snap["body"]["device_ak_sha256"]:
        return "secure-element key is not the one the validators certified for this device", None
    if payee_id is not None and b["payee"] != payee_id:
        return "payment is addressed to another payee", None
    a = parse_nv_attest(b["se_attest"])
    if a["magic"] != TPM_GENERATED or a["type"] != TPM_ST_ATTEST_NV or a["index_name"] != counter_name():
        return "not a secure-element counter attestation", None
    try:
        serialization.load_der_public_key(b["se_ak"]).verify(b["se_sig"], b["se_attest"], ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return "secure-element signature invalid", None
    c = b["statement"]["public"][5:]
    if a["extra_data"] != sha(binding(c, b["proof"], b["payee"], b["amount"], b["cum"])):
        return "secure-element attestation does not bind this commitment, proof, payee, amount and total", None
    if not b["amount"] <= b["cum"] <= snap["body"]["offline_allowance"]:
        return "declared cumulative spending exceeds the offline allowance", None
    counter = int.from_bytes(a["nv_contents"], "big")
    if last_counter is not None and counter <= last_counter:
        return f"secure-element counter {counter} not above {last_counter} already seen from this device", counter
    req = F.request_for(b["proof"], b["statement"], b["amount"])
    reason = F.check(req, b["proof"], b["statement"], {"amount": b["amount"], "r": b["r"]}, snap["body"]["policy"], model)
    if reason:
        return reason, counter
    if not (req["exec"] and not req["viol"]):
        return "proof shows a policy violation (VIOL = 1): not accepted offline", counter
    return None, counter


class Payee:
    def __init__(self, pid, validators, model):
        self.id, self.validators, self.model, self.queue, self.last = pid, validators, model, [], {}

    def receive(self, b, now):
        t0 = time.perf_counter()
        dev = b["snapshot"]["body"]["device_ak_sha256"]
        reason, ctr = check_bundle(b, self.validators, self.model, now, self.id, self.last.get(dev))
        ms = (time.perf_counter() - t0) * 1e3
        if reason is None:
            self.last[dev] = ctr
            self.queue.append(b)
        return {"accepted": reason is None, "reason": reason or "accepted offline", "counter": ctr, "check_ms": round(ms, 1)}


class Ledger:
    def __init__(self, validators, model):
        self.validators, self.model = validators, model
        self.seen, self.commitments, self.cum, self.settled, self.flagged = set(), set(), {}, [], set()

    def sync(self, payees, now):
        """On reconnection: re-check every queued bundle, settle per device in secure-element counter order."""
        items = []
        for py in payees:
            for b in py.queue:
                reason, ctr = check_bundle(b, self.validators, self.model, now)
                items.append((b["snapshot"]["body"]["device_ak_sha256"], ctr or 0, py.id, b, reason))
            py.queue = []
        out = []
        for dev, ctr, pid, b, reason in sorted(items, key=lambda i: (i[0], i[1])):
            c = tuple(b["statement"]["public"][5:])
            if reason is None and ((dev, ctr) in self.seen or c in self.commitments):
                reason = "already settled: same secure-element counter or commitment"
            if reason is None and b["cum"] != self.cum.get(dev, 0) + b["amount"]:
                reason = (f"overspend: declared total {b['cum']} does not follow from earlier payments "
                          f"({self.cum.get(dev, 0)} + {b['amount']})")
                self.flagged.add(dev)
            if reason is None:
                self.seen.add((dev, ctr)); self.commitments.add(c); self.cum[dev] = b["cum"]; self.settled.append((pid, b["amount"]))
            out.append({"payee": pid, "amount": b["amount"], "declared_total": b["cum"], "counter": ctr, "settled": reason is None, "reason": reason or "settled"})
        return out


def main():
    wd = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="offline_"))
    procs, res = [], {"scenarios": {}}
    S = res["scenarios"]
    try:
        procs.append(start_swtpm(wd, 2541))
        tpm = OperatorTPM("swtpm:host=127.0.0.1,port=2541", str(wd / "payer_se"))
        se = SecureElement(tpm)
        # device enrolment while online: EK certificate + credential activation, then a validator-signed snapshot
        reg = AdmissionRegistry([str(CA / "swtpm-localca-rootca-cert.pem")], [str(CA / "issuercert.pem")])
        mat = tpm.provision()
        e = reg.begin_enrolment("payer-device", mat)
        enrol = reg.finish_enrolment("payer-device", tpm.activate_credential(e["credential_blob"]))
        ak_der = next(iter(reg.operators[enrol["operator_id"]]["aks"].values()))
        vals = [Account.create() for _ in range(4)]
        model = F.model_digest()
        now = int(time.time())
        body = {"policy": {**F.POLICY, "slash": F.SLASH}, "device_ak_sha256": sha(ak_der).hex(), "offline_allowance": 12000,
                "issued": now, "expires": now + DAY}
        snap = {"body": body, "sigs": [v.sign_message(encode_defunct(primitive=snapshot_digest(body))).signature for v in vals[:3]]}
        V = [v.address for v in vals]
        x = F.licit_inputs(3)[1]
        payer = Payer(se, ak_der, x, snap)
        X, Y, Z = Payee(101, V, model), Payee(102, V, model), Payee(103, V, model)
        ledger = Ledger(V, model)

        # --- offline phase (allowance 12,000 locked on the ledger for offline use)
        pay_t, chk_t = [], []
        def pay_and_receive(key, payee, amount, now_off, **kw):
            b, t = payer.pay(payee.id, amount, **kw)
            if b is None:
                S[key] = {"accepted": False, "reason": t["refused_by_device"]}
                return None
            pay_t.append(t)
            r = payee.receive(b, now_off); S[key] = r
            if r["accepted"]:
                chk_t.append(r["check_ms"])
                if "lie_cum" not in kw:
                    payer.confirm(b)
            return b
        b1 = pay_and_receive("01_honest_payment_X_5000", X, 5000, now + 60)
        S["02_same_bundle_replayed_to_X"] = X.receive(b1, now + 65)
        S["03_bundle_for_X_presented_to_Y"] = Y.receive(b1, now + 66)
        pay_and_receive("04_honest_payment_Y_3000", Y, 3000, now + 70)
        b5, _ = payer.pay(X.id, 2000)
        S["05_amount_raised_in_transit"] = X.receive({**b5, "amount": 9000}, now + 80)
        S["05b_original_bundle"] = X.receive(b5, now + 81); payer.confirm(b5); chk_t.append(S["05b_original_bundle"]["check_ms"])
        SANCT = Payee(F.POLICY["sanctioned_id"], V, model)       # a payee on the sanctioned list
        pay_and_receive("06_payment_to_sanctioned_payee", SANCT, 500, now + 90)
        pay_and_receive("07_honest_app_beyond_allowance_3000", Z, 3000, now + 95)
        pay_and_receive("08_manipulated_app_hides_earlier_spending_Z_3000", Z, 3000, now + 100, lie_cum=3000)
        b9, _ = payer.pay(Z.id, 500)
        S["09_expired_snapshot"] = Z.receive(b9, now + 2 * DAY)
        sk = ec.generate_private_key(ec.SECP256R1())
        S["10_secure_element_signature_forged_by_software"] = Z.receive({**b9, "se_sig": sk.sign(b9["se_attest"], ec.ECDSA(hashes.SHA256()))}, now + 110)
        sk_der = sk.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        S["11_software_wallet_without_certified_secure_element"] = Z.receive({**b9, "se_ak": sk_der, "se_sig": sk.sign(b9["se_attest"], ec.ECDSA(hashes.SHA256()))}, now + 115)
        fake = {"body": {**body, "offline_allowance": 10 ** 6}, "sigs": snap["sigs"]}
        S["12_snapshot_allowance_altered"] = Z.receive({**b9, "snapshot": fake}, now + 120)
        before = X.last.get(body["device_ak_sha256"])
        tpm.delete_counter(); tpm.define_counter()
        b13 = pay_and_receive("13_after_secure_element_counter_deleted_and_recreated", X, 1000, now + 130)
        S["13_after_secure_element_counter_deleted_and_recreated"].update({"last_counter_seen_by_X": before, "counter_reset": (S["13_after_secure_element_counter_deleted_and_recreated"]["counter"] or 0) <= (before or 0)})
        # --- reconnection
        S["14_sync"] = ledger.sync([X, Y, Z], now + 3600)
        S["14_device_flagged"] = body["device_ak_sha256"] in ledger.flagged

        offline_ok = {"01_honest_payment_X_5000", "04_honest_payment_Y_3000", "05b_original_bundle",
                      "08_manipulated_app_hides_earlier_spending_Z_3000", "13_after_secure_element_counter_deleted_and_recreated"}
        acc = {k: v["accepted"] for k, v in S.items() if isinstance(v, dict) and "accepted" in v}
        sync = {(s["payee"], s["amount"]): s for s in S["14_sync"]}
        res["all_as_expected"] = (all(acc[k] == (k in offline_ok) for k in acc)
                                  and not S["13_after_secure_element_counter_deleted_and_recreated"]["counter_reset"]
                                  and [s["settled"] for s in S["14_sync"]].count(True) == 4
                                  and not sync[(103, 3000)]["settled"] and "overspend" in sync[(103, 3000)]["reason"]
                                  and S["14_device_flagged"])
        res["settled_total"] = sum(a for _, a in ledger.settled)
        res["measurements"] = {
            "payer_prove_ms_median": round(statistics.median(t["prove_ms"] for t in pay_t), 1),
            "payer_secure_element_ms_median": round(statistics.median(t["se_ms"] for t in pay_t), 1),
            "payer_total_ms_median": round(statistics.median(t["total_ms"] for t in pay_t), 1),
            "payee_full_offline_check_ms_median": round(statistics.median(chk_t), 1),
            "bundle_bytes": bundle_size(b1),
            "prover_peak_mb_and_verifier_peak_mb": "20.3 / 12.5 (h4-pqc-stark/zk_composed/results/resources.json)",
            "machine": "2-vCPU, 8 GB cloud VM (x86-64), software TPM; not measured on phone or terminal hardware"}
        out = HERE / "results"; out.mkdir(exist_ok=True)
        json.dump(res, open(out / "offline_sim.json", "w"), indent=1, default=lambda o: o.hex() if isinstance(o, bytes) else str(o))
        print(json.dumps({"all_as_expected": res["all_as_expected"], "measurements": res["measurements"],
                          "scenarios": {k: (v.get("reason") if isinstance(v, dict) and "reason" in v else v) for k, v in S.items()}}, indent=1, default=str))
    finally:
        for p in procs:
            p.send_signal(signal.SIGTERM); p.wait(10)


if __name__ == "__main__":
    main()
