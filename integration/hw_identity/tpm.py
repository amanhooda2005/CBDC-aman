"""Operator-node side of the hardware-anchored operator identity (TPM 2.0, via tpm2-tools).

The operator node's identity k(O) is its TPM's endorsement key (EK), certified by the TPM manufacturer. An
attestation key (AK) is created inside the TPM under the EK (restricted, fixedTPM: its private part never
leaves the chip). A TPM NV index of type counter (monotonic: it can only be incremented, and a deleted and
re-created counter resumes above the highest value it ever held) orders the node's attestations.

Every new agent identity is registered with an attestation produced by the TPM itself (TPM2_NV_Certify):
the AK signs the counter value together with qualifying data H(nonce || agent id). A software key cannot
produce such a signature, and the counter cannot be rolled back.

All TPM work is done by tpm2-tools against the TCTI in self.tcti (a hardware TPM: "device:/dev/tpmrm0";
the software TPM used for the measurements: "swtpm:host=127.0.0.1,port=2321").
"""
import hashlib
import os
import subprocess
import time

EK_HANDLE = "0x81010001"        # TCG default persistent handle of the RSA-2048 EK
EK_CERT_NV = "0x1c00002"        # TCG NV index of the RSA EK certificate
AK_HANDLE = "0x81010002"
COUNTER_NV = "0x01500016"
COUNTER_ATTRS = "nt=counter|ownerread|ownerwrite|authread|authwrite"


class OperatorTPM:
    def __init__(self, tcti, workdir):
        self.tcti, self.wd = tcti, workdir
        os.makedirs(workdir, exist_ok=True)
        self.env = dict(os.environ, TPM2TOOLS_TCTI=tcti)

    def p(self, name):
        return os.path.join(self.wd, name)

    def run(self, *args, check=True):
        r = subprocess.run(list(args), env=self.env, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(f"{args[0]} failed: {r.stderr.strip()[-300:]}")
        return r

    def handles(self):
        return self.run("tpm2_getcap", "handles-persistent").stdout

    # ------------------------------------------------------------------ provisioning
    def provision(self, new_ak=False):
        """EK (persistent, created by the TPM manufacturer / swtpm_setup), a fresh AK under it, the counter."""
        if EK_HANDLE not in self.handles():
            self.run("tpm2_createek", "-G", "rsa", "-c", EK_HANDLE, "-Q")
        self.run("tpm2_readpublic", "-c", EK_HANDLE, "-o", self.p("ek.tss"), "-Q")
        self.run("tpm2_nvread", EK_CERT_NV, "-o", self.p("ek_cert.der"))
        if new_ak or AK_HANDLE not in self.handles():
            if AK_HANDLE in self.handles():
                self.run("tpm2_evictcontrol", "-C", "o", "-c", AK_HANDLE, "-Q")
            self.run("tpm2_createak", "-C", EK_HANDLE, "-G", "ecc", "-g", "sha256", "-s", "ecdsa",
                     "-c", self.p("ak.ctx"), "-u", self.p("ak.tss"), "-n", self.p("ak.name"), "-Q")
            self.run("tpm2_evictcontrol", "-C", "o", "-c", self.p("ak.ctx"), AK_HANDLE, "-Q")
            self.run("tpm2_flushcontext", "-t")
        self.run("tpm2_readpublic", "-c", AK_HANDLE, "-o", self.p("ak.tss"), "-n", self.p("ak.name"), "-Q")
        self.run("tpm2_readpublic", "-c", AK_HANDLE, "-o", self.p("ak.pem"), "-f", "pem", "-Q")
        if self.run("tpm2_nvreadpublic", COUNTER_NV, check=False).returncode != 0:
            self.define_counter()
        return self.enrolment_material()

    def define_counter(self):
        self.run("tpm2_nvdefine", COUNTER_NV, "-C", "o", "-s", "8", "-a", COUNTER_ATTRS, "-Q")
        self.run("tpm2_nvincrement", COUNTER_NV, "-C", "o")       # a counter must be written once before it can be read

    def delete_counter(self):
        self.run("tpm2_nvundefine", COUNTER_NV, "-C", "o")

    def enrolment_material(self):
        rd = lambda n: open(self.p(n), "rb").read()
        return {"ek_cert_der": rd("ek_cert.der"), "ek_tss": rd("ek.tss"), "ak_tss": rd("ak.tss"), "ak_pem": rd("ak.pem")}

    def activate_credential(self, cred_blob):
        """Prove that the AK lives in the same TPM as the certified EK (TPM2_ActivateCredential)."""
        open(self.p("cred.blob"), "wb").write(cred_blob)
        self.run("tpm2_startauthsession", "--policy-session", "-S", self.p("s.ctx"), "-Q")
        try:
            self.run("tpm2_policysecret", "-S", self.p("s.ctx"), "-c", "e", "-Q")
            self.run("tpm2_activatecredential", "-c", AK_HANDLE, "-C", EK_HANDLE, "-i", self.p("cred.blob"),
                     "-o", self.p("secret.out"), "-P", f"session:{self.p('s.ctx')}")
        finally:
            self.run("tpm2_flushcontext", self.p("s.ctx"), check=False)
        return open(self.p("secret.out"), "rb").read()

    # ------------------------------------------------------------------ attested registration of an agent identity
    def attest_registration(self, nonce: bytes, agent_id: bytes):
        """Increment the monotonic counter, then have the TPM certify its value with the AK, bound to H(nonce || agent id)."""
        t0 = time.perf_counter()
        self.run("tpm2_nvincrement", COUNTER_NV, "-C", "o")
        q = hashlib.sha256(nonce + agent_id).hexdigest()
        self.run("tpm2_nvcertify", "-C", AK_HANDLE, "-g", "sha256", "-s", "ecdsa", "-f", "plain",
                 "-o", self.p("sig.bin"), "--attestation", self.p("attest.bin"), "-q", q,
                 "--size", "8", "-c", "o", COUNTER_NV)
        ms = (time.perf_counter() - t0) * 1e3
        return {"attest": open(self.p("attest.bin"), "rb").read(), "sig": open(self.p("sig.bin"), "rb").read(), "tpm_ms": ms}
