# Hardware-anchored operator identity (TPM 2.0)

The operator identity k(O) of the liability registry is the operator node's TPM: k(O) = SHA-256 of the
TPM's endorsement-key (EK) public key, whose certificate chains to the TPM manufacturer. Every new agent
identity is registered with an attestation the TPM itself produces (TPM2_NV_Certify): an attestation key
(AK) that never leaves the TPM signs the value of a TPM monotonic counter together with H(nonce ‖ agent id).

- `tpm.py`: operator node side (tpm2-tools): EK and EK certificate, AK under the EK, the NV counter,
  credential activation, attested registration.
- `admission.py`: registry side: EK-certificate chain, EK/AK binding by TPM2_MakeCredential, AK attributes
  (restricted, fixedTPM, fixedParent), one hardware identity per operator account, and admission checks for
  agent identities (TPM-generated structure, enrolled AK signature, nonce, agent id, counter index name,
  strictly increasing counter). Without a valid attestation, an identity is admitted only with a deposit of
  at least B*. On enrolment of a new hardware identity it issues the operator credential on the
  `LiabilityRegistry` contract (regulator role).
- `run_demo.py`: the end-to-end test below, with two software TPMs (swtpm 0.7.3, EK certificates from its
  local CA) and the contract on anvil. Writes `results/hw_identity.json`.

```bash
sudo apt install swtpm swtpm-tools tpm2-tools          # Ubuntu 24.04
cd integration && forge build && cd hw_identity && python3 run_demo.py
```
On a machine with a hardware TPM, use `OperatorTPM("device:/dev/tpmrm0", ...)` and trust the TPM
manufacturer's CA certificates instead of the swtpm local CA.

## Result (28 Oct 2026, `"all_as_expected": true`)

| Scenario | Outcome |
|---|---|
| TPM A enrolled for operator account A | accepted; credential issued on-chain |
| 20 agent registrations attested by TPM A | all admitted; counter strictly increasing; 3 registered on-chain with the bond |
| replayed attestation (same nonce) | refused: nonce already used |
| attestation re-bound to another agent id | refused: bound to a different nonce or agent id |
| attestation modified after signing | refused: not signed by an enrolled TPM key (admitted only with deposit ≥ B*) |
| attestation signed by a software key | refused, as above |
| no attestation, deposit B* = 14 | admitted (the bond path) |
| forged, self-signed EK certificate | refused: does not chain to a trusted manufacturer |
| imported software key presented as the AK | refused: not a TPM-resident restricted key (attributes 0x00060040) |
| credential for TPM A activated by TPM B | refused by the TPM (TPM2_ActivateCredential fails; TPM B stays operational) |
| same TPM, new AK, second operator account | refused: one hardware identity, one operator; no credential on-chain |
| same TPM, new AK, same account | accepted as the same k(O); counter history kept |
| counter deleted and re-created on the TPM | the TPM resumes above the old value (21 → 24), so the history cannot be reset |
| two attestations submitted out of order (stale / cloned state) | newer accepted, older refused (counter not above the last accepted) |
| TPM B (new hardware) for operator account B | accepted as a new identity, needing its own bonds |

Timing (software TPM on the 2-vCPU VM, median of 20): counter increment + TPM certification 44–51 ms;
registry verification 0.4 ms. Attestation 149 bytes, signature 71 bytes. A hardware TPM is usually slower
at signing; this was not measured.

Limits: the TPMs are software TPMs with certificates from a local CA, not hardware TPMs from a
manufacturer; the agent-level attestation check runs in the admission registry off-chain (the contract
receives the operator credential), not inside the contract.
