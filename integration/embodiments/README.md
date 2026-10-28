# Simulated embodiments: verify-then-sign TEE (IDF 7.10) and offline payment devices (IDF 7.9)

Both use the real composed prover/verifier (`h4-pqc-stark/zk_composed`), real Elliptic test-period
transactions, the validator checks of `integration/pipeline/validator_node.py` (`fixtures.py`), and software
TPMs (swtpm) enrolled as in `integration/hw_identity`.

```bash
sudo apt install swtpm swtpm-tools tpm2-tools
cd h4-pqc-stark/zk_composed && cargo build --release && cd -
python3 integration/embodiments/tee_sim.py        # -> results/tee_sim.json
python3 integration/embodiments/offline_sim.py    # -> results/offline_sim.json
```
Set `ELLIPTIC_DIR` to the folder containing `elliptic_txs_features.csv` and `elliptic_txs_classes.csv`.

## Verify-then-sign in a trusted execution environment (`tee_sim.py`, `enclave.py`)
The environment is a separate process. It copies the verifier into a private directory at launch, generates
its signing key in memory, and has no command that signs without running the checks. The launcher measures it
(SHA-256 of `enclave.py` and the verifier binary) into PCR 16 of the machine's TPM. A TPM quote over PCR 16,
whose qualifying data names the environment's key, is its remote attestation. The attester registry accepts
a key only with a quote from an enrolled TPM showing an approved verifier build.

Result (`"all_as_expected": true`):
- the honest request was signed;
- 5 falsified requests were refused, each for its reason: forged VIOL, amount swapped, corrupted proof, wrong
  model, outdated slash;
- a patched verifier that accepts everything was not registered, so its signature on a corrupted proof did not
  count;
- a replayed quote for another key, a host key, and an unenrolled TPM's quote were refused;
- replacing the verifier on disk after launch had no effect.

Quote about 18–32 ms; check and sign about 22 ms per request.

**Limits:** no hardware memory isolation, and the launcher, not the CPU, takes the measurement.

## Offline payment devices with a secure element (`offline_sim.py`)
- **Payer device:** generates the composed proof itself. Its secure element (a TPM) generates the commitment
  randomness and, for each payment, certifies its monotonic counter bound to commitment, proof, payee, amount
  and cumulative offline spending.
- **Payee devices (no network):** check the validator-signed snapshot (3 of 4 signatures, expiry, the payer's
  certified secure-element key, offline allowance), the attestation and counter freshness, the proof, the
  commitment opening, EXEC = 1 / VIOL = 0, and the recipient.
- **On reconnection:** validators re-check everything and settle in counter order, refusing duplicates and any
  payment whose cumulative spending does not follow from earlier ones.

Result (`"all_as_expected": true`):
- 4 honest payments were accepted offline and settled;
- refused offline:
  - a replay;
  - a bundle presented to another payee;
  - an amount raised in transit;
  - a payment to a sanctioned payee (VIOL = 1);
  - an expired or altered snapshot;
  - a forged secure-element signature;
  - a software wallet;
- a manipulated app hiding earlier spending was accepted offline (no network) but refused at sync as an
  overspend, and the device was flagged;
- a deleted and re-created counter resumed above its old value.

Timings: proof about 0.62 s and secure element about 46 ms per payment; payee check about 64–89 ms;
bundle about 170 KB.

**Limits:** these timings are from a cloud VM with a software TPM, not phone or terminal hardware.
