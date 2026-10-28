"""Registry side of the hardware-anchored operator identity: enrolment of an operator's TPM and admission of
new agent identities, with optional on-chain issuance of the operator credential (LiabilityRegistry).

Enrolment (once per operator node):
  1. the EK certificate must chain to a trusted TPM-manufacturer CA; the EK public key in the TPM must equal
     the one in the certificate; the operator identity is k(O) = SHA-256(EK public key);
  2. the AK must be a restricted, fixedTPM, fixedParent signing key created inside the TPM (attributes read
     from its public area, whose hash is the AK name);
  3. TPM2_MakeCredential encrypts a fresh secret to the EK, bound to the AK name; only the TPM holding that EK
     and that AK can recover it (TPM2_ActivateCredential), which proves the AK lives in the certified TPM;
  4. one hardware identity k(O) is bound to one operator account: a second account for the same TPM is refused,
     and a new AK of the same TPM maps back to the same k(O), so re-keying does not reset the operator's history.

Admission of an agent identity (each new agent id):
  the attestation must be a TPM2_NV_Certify structure (magic TPM_GENERATED) signed by an enrolled AK; its
  qualifying data must equal H(nonce || agent id) for a nonce this registry issued and has not seen used; the
  certified NV index must be the operator's monotonic counter (its name is recomputed from the counter's public
  area, nt = counter); and the counter value must be strictly greater than the last one accepted for k(O).
  An agent identity without such an attestation from an enrolled TPM is admitted only with a deposit of at
  least the admission threshold B*.
"""
import hashlib
import os
import secrets
import struct
import subprocess
import tempfile
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography import x509

TPM_GENERATED = 0xFF544347
TPM_ST_ATTEST_NV = 0x8014
SHA256 = 0x000B
COUNTER_INDEX = 0x01500016
# TPMA_NV of the counter after its first write: ownerwrite|authwrite|nt=counter|ownerread|authread|written
COUNTER_ATTRS = 0x20060016
# TPMA_OBJECT bits an attestation key must have
FIXED_TPM, FIXED_PARENT, SENSITIVE_DATA_ORIGIN, RESTRICTED, SIGN = 0x2, 0x10, 0x20, 0x10000, 0x40000


def counter_name():
    pub = struct.pack(">IHIHH", COUNTER_INDEX, SHA256, COUNTER_ATTRS, 0, 8)
    return struct.pack(">H", SHA256) + hashlib.sha256(pub).digest()


def tpmt_public(tpm2b):
    (size,) = struct.unpack(">H", tpm2b[:2])
    return tpm2b[2:2 + size]


def object_name(tpm2b):
    t = tpmt_public(tpm2b)
    name_alg = struct.unpack(">H", t[2:4])[0]
    assert name_alg == SHA256
    return t[2:4] + hashlib.sha256(t).digest()


def object_attributes(tpm2b):
    return struct.unpack(">I", tpmt_public(tpm2b)[4:8])[0]


def tss_to_der(tss_bytes):
    with tempfile.NamedTemporaryFile(suffix=".tss") as f:
        f.write(tss_bytes); f.flush()
        pem = subprocess.run(["tpm2_print", "-t", "TPM2B_PUBLIC", "-f", "pem", f.name], capture_output=True, check=True).stdout
    return serialization.load_pem_public_key(pem).public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def parse_nv_attest(b):
    """TPMS_ATTEST of type TPM_ST_ATTEST_NV -> dict."""
    o = 0
    def u(fmt):
        nonlocal o
        v = struct.unpack_from(fmt, b, o); o += struct.calcsize(fmt); return v[0]
    def tpm2b():
        nonlocal o
        n = u(">H"); v = b[o:o + n]; o += n; return v
    a = {"magic": u(">I"), "type": u(">H")}
    a["qualified_signer"] = tpm2b()
    a["extra_data"] = tpm2b()
    a["clock"], a["reset_count"], a["restart_count"], a["safe"] = u(">Q"), u(">I"), u(">I"), u(">B")
    a["firmware"] = u(">Q")
    a["index_name"] = tpm2b()
    a["offset"] = u(">H")
    a["nv_contents"] = tpm2b()
    a["consumed"] = o == len(b)
    return a


class AdmissionRegistry:
    def __init__(self, trusted_roots, intermediates=(), admission_threshold=14, chain=None):
        self.roots, self.intermediates = list(trusted_roots), list(intermediates)
        self.b_star = admission_threshold
        self.operators = {}        # k(O) -> {"account", "aks": {ak_name: ak_der}, "last_counter"}
        self.ak_owner = {}         # ak name -> k(O)
        self.pending = {}          # account -> (k(O), ak_name, ak_der, secret)
        self.nonces = {}           # nonce -> issued_at; removed when used
        self.chain = chain         # optional (w3, registry contract, regulator account) for on-chain credentials
        self.log = []

    # ------------------------------------------------------------------ enrolment
    def verify_ek_cert(self, cert_der):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "ek.pem")
            open(c, "wb").write(x509.load_der_x509_certificate(cert_der).public_bytes(serialization.Encoding.PEM))
            args = ["openssl", "verify", "-CAfile", self.roots[0]]
            for i in self.intermediates:
                args += ["-untrusted", i]
            r = subprocess.run(args + [c], capture_output=True, text=True)
            return r.returncode == 0, (r.stdout + r.stderr).strip()[-160:]

    def begin_enrolment(self, account, material):
        ok, msg = self.verify_ek_cert(material["ek_cert_der"])
        if not ok:
            return {"ok": False, "reason": "EK certificate does not chain to a trusted TPM manufacturer: " + msg}
        cert_key = x509.load_der_x509_certificate(material["ek_cert_der"]).public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if tss_to_der(material["ek_tss"]) != cert_key:
            return {"ok": False, "reason": "EK public key differs from the certified one"}
        attrs = object_attributes(material["ak_tss"])
        need = FIXED_TPM | FIXED_PARENT | SENSITIVE_DATA_ORIGIN | RESTRICTED | SIGN
        if attrs & need != need:
            return {"ok": False, "reason": f"AK is not a TPM-resident restricted signing key (attributes 0x{attrs:08x})"}
        ak_der = tss_to_der(material["ak_tss"])
        if serialization.load_pem_public_key(material["ak_pem"]).public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != ak_der:
            return {"ok": False, "reason": "AK PEM does not match the AK public area"}
        k = hashlib.sha256(cert_key).hexdigest()
        bound = self.operators.get(k)
        if bound and bound["account"] != account:
            return {"ok": False, "reason": f"this TPM is already the identity of operator {bound['account']}; one hardware identity, one operator"}
        name = object_name(material["ak_tss"])
        secret = secrets.token_bytes(32)
        with tempfile.TemporaryDirectory() as d:
            open(f"{d}/ek.tss", "wb").write(material["ek_tss"]); open(f"{d}/s", "wb").write(secret)
            subprocess.run(["tpm2_makecredential", "-T", "none", "-e", f"{d}/ek.tss", "-s", f"{d}/s", "-n", name.hex(),
                            "-o", f"{d}/cred"], capture_output=True, check=True)
            blob = open(f"{d}/cred", "rb").read()
        self.pending[account] = (k, name, ak_der, secret)
        return {"ok": True, "operator_id": k, "credential_blob": blob, "ak_name": name.hex()}

    def finish_enrolment(self, account, secret):
        if account not in self.pending:
            return {"ok": False, "reason": "no enrolment in progress"}
        k, name, ak_der, expected = self.pending.pop(account)
        if not secrets.compare_digest(secret, expected):
            return {"ok": False, "reason": "credential not activated: the AK is not in the certified TPM"}
        new = k not in self.operators
        op = self.operators.setdefault(k, {"account": account, "aks": {}, "last_counter": 0})
        op["aks"][name.hex()] = ak_der
        self.ak_owner[name.hex()] = k
        tx = None
        if new and self.chain:
            tx = self.issue_credential_onchain(account)
        return {"ok": True, "operator_id": k, "new_operator": new, "aks": len(op["aks"]), "credential_tx": tx}

    def issue_credential_onchain(self, account):
        w3, reg, regulator = self.chain
        h = reg.functions.issueCredential(account).transact({"from": regulator})
        return w3.eth.wait_for_transaction_receipt(h).status

    # ------------------------------------------------------------------ admission of agent identities
    def issue_nonce(self):
        n = secrets.token_bytes(32)
        self.nonces[n] = time.time()
        return n

    def admit_agent(self, agent_id, nonce, attest, sig, deposit=0):
        t0 = time.perf_counter()
        r = self._admit(agent_id, nonce, attest, sig, deposit)
        r["verify_ms"] = round((time.perf_counter() - t0) * 1e3, 3)
        self.log.append(r)
        return r

    def _admit(self, agent_id, nonce, attest, sig, deposit):
        unattested = {"ok": deposit >= self.b_star, "operator_id": None,
                      "reason": f"no valid attestation from an enrolled TPM: admitted only with a deposit >= B* ({self.b_star})"}
        if attest is None:
            return unattested
        try:
            a = parse_nv_attest(attest)
        except (struct.error, AssertionError):
            return {**unattested, "reason": "attestation is not a TPM structure; " + unattested["reason"]}
        if a["magic"] != TPM_GENERATED or a["type"] != TPM_ST_ATTEST_NV or not a["consumed"]:
            return {**unattested, "reason": "attestation was not generated by a TPM (TPM_GENERATED / NV certify); " + unattested["reason"]}
        signer = None
        for name, k in self.ak_owner.items():
            key = serialization.load_der_public_key(self.operators[k]["aks"][name])
            try:
                key.verify(sig, attest, ec.ECDSA(hashes.SHA256()))
                signer = k
                break
            except InvalidSignature:
                continue
        if signer is None:
            return {**unattested, "reason": "signature is not from an enrolled TPM attestation key; " + unattested["reason"]}
        if nonce not in self.nonces:
            return {"ok": False, "operator_id": signer, "reason": "nonce not issued by this registry or already used (replay)"}
        if a["extra_data"] != hashlib.sha256(nonce + agent_id).digest():
            return {"ok": False, "operator_id": signer, "reason": "attestation is bound to a different nonce or agent id"}
        if a["index_name"] != counter_name():
            return {"ok": False, "operator_id": signer, "reason": "certified NV index is not the operator's monotonic counter"}
        counter = int.from_bytes(a["nv_contents"], "big")
        op = self.operators[signer]
        if counter <= op["last_counter"]:
            return {"ok": False, "operator_id": signer, "counter": counter,
                    "reason": f"counter {counter} not above last accepted {op['last_counter']} (stale, reordered or cloned state)"}
        del self.nonces[nonce]
        op["last_counter"] = counter
        return {"ok": True, "operator_id": signer, "account": op["account"], "counter": counter, "reason": "attested by the operator's TPM"}
