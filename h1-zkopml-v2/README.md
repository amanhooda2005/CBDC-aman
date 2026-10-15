# H1 zk-OPML prototype (v2)

This prototype tests an optimistic dispute protocol for AML inference on the Elliptic dataset:
- The model runs off-chain, and every intermediate step is committed with a Poseidon hash.
- A dispute is bisected down to a single step, and only that step is proven with EZKL.
- The result is compared against a monolithic EZKL proof of the whole model.

Results, method and pass/fail verdict: **[WRITEUP.md](WRITEUP.md)**.

## Verdict summary

| Criterion | Result |
|---|---|
| ≥10× proof latency | **Pass only at 60 blocks** (14.9× single-core, 12.6× multi-core; multi-core measured at 60 blocks only). **Fail** at 24 blocks (7.7×), 8 blocks (3.9×) and 1 block (1.07×), all single-core. |
| ≥10× on-chain cost | **Pass amortized** (13.9× for undisputed inferences). **Fail per dispute** (up to 2.4× more gas than monolithic in the worst case; about 1.8× typical). Holds on average only if fewer than about 1.2% of inferences are disputed (worst-case dispute cost; about 1.6% with the typical cost). |
| ≤1 pp accuracy drop | **Pass** (worst case 0.13 pp). |
| Bisection isolates the fault in bounded rounds | **Pass** (≤6 rounds for 63 steps; 7 of 7 scenarios). |

Caveats to report with any speedup number:
- The 10× latency result depends on model depth. The 60-block model is a benchmark, not a better AML model (its F1 matches the shallow one).
- Speedup depends on hardware (14.9× single-core, 12.6× multi-core, both at 60 blocks). Report the hardware with the number.
- The gas target is an average over disputes, not a per-dispute win. The 2.4× and 1.2% figures are worst-case; the typical figures are 1.8× and 1.6%.
- The gain is in proof generation, not time-to-finality (the challenge window still applies).
- Only four depths were measured (1, 8, 24, 60). The worst-case speedups for 1, 8 and 24 blocks reuse the slowest step time measured on the 60-block model. Do not quote a specific crossover depth; it lies somewhere between 24 and 60 blocks.

## Dispute contract v2 (hardened)

`contracts/OptimisticInferenceV2.sol` replaces the gas-measurement mock `OptimisticInference.sol` (kept
unchanged because the v1 gas numbers in `results/gas.json` were measured with it). v2 adds full on-chain
adjudication:

- **Prefix-consistency check.** When bisection ends at step k, the contract requires
  P_k = keccak(P_{k-1} ‖ leaf_k). Without it, an asserter could commit honest values up to k, a garbage
  P_k and wrong values afterwards. Bisection then lands on a step it computed honestly, and the
  single-step proof upholds a wrong output. v1 and the original `dispute.py` did not check this.
- **Input openings.** The disputed step's input instances must be the claimed transaction input, or
  earlier leaves opened by a hash chain that recomputes to the agreed prefix P_{k-1}.
- **Output binding.** The head leaf is the digest of the public output felts and must equal the claimed
  output.
- **Game rules:**
  - strict turn order, with only the asserter revealing and only the challenger answering;
  - the midpoint is enforced and Merkle paths have fixed depth;
  - a deadline on every turn, with `timeout()` making the late party lose;
  - asserter collateral and a challenger bond, with the loser's bond paid to the winner;
  - delayed withdrawals that stay slashable;
  - independent disputes per challenger, so a colluding challenger who loses on purpose cannot shield a
    wrong claim.
- The prefix chain and Merkle tree are now **keccak in both Solidity and Python** (`chain.py`), instead of
  SHA-256 off-chain and keccak on-chain.

**Tests:**
- `forge test`: 17 tests, all passing. They cover:
  - tampering at **every one of the 63 steps**, each located exactly in ≤ 6 rounds and rejected;
  - the malformed-chain attack;
  - forged input openings, invalid proofs, a wrong transaction input;
  - all three timeouts;
  - access control and turn order;
  - collusion;
  - collateral and withdrawal rules;
  - a Python ↔ Solidity hashing cross-check.

  In these tests the step verifiers are a mock that only accepts the honest step output, i.e. a sound
  proof system; the real verifiers are exercised by `onchain_dispute.py` (v2.1 below).
- `python3 -m pytest test_chain.py`: 66 tests, all passing.

**Gas (real transactions on anvil, `gas_game_v2.py` → `results/gas_v2.json`):**

| Item | v1 (published) | v2 (hardened) |
|---|---|---|
| Commit per inference | 49,962 | 54,673 |
| Undisputed saving vs monolithic (696,984) | 13.9× | **12.8×** |
| Full dispute, typical (block step) | 1.26 M | 1.62 M |
| Full dispute, worst (projection half) | 1.67 M | 1.90 M |
| Max dispute rate for the 10× gas target | 1.18% worst / 1.6% typical | **0.79% worst / 0.93% typical** |

What the "full dispute" figures contain:
- **v2:** the measured challenge, opening, 5–6 reveal and response rounds and resolution, plus the
  EZKL step-verifier gas measured in v1 (minus the 21,000 base cost an internal call does not pay).
- **v1:** only the bisection rounds and the step verifier. It left out the challenge, opening and
  adjudication transactions.


### v2.1: the dispute game with real EZKL step verifiers on-chain (`onchain_dispute.py`)

The mock is gone from the end-to-end path. `onchain_dispute.py` plays complete games on a local chain
(anvil) against the deployed contract, with EZKL-generated Solidity verifiers and real step proofs.

**One verifier per step shape.** Each step's weights are a hashed public instance (EZKL
`param_visibility = "hashed"`), so steps of the same shape share one circuit and one verifier. The
contract stores each step's weight hash and rejects a proof whose weight-hash instance is not that
step's (`stepParamHash`, checked at the position EZKL uses: after the hashed inputs, before any public
transaction input). The 60-block model needs 4 verifiers instead of 63:

| Shape | Steps served | logrows | Deploy gas | Bytecode |
|---|---|---|---|---|
| projection half (public tx input) | 2 | 17 | 5.38 M | 24.07 KB |
| join block (two inputs) | 1 | 16 | 3.94 M | 17.60 KB |
| residual block | 59 | 16 | 3.94 M | 17.60 KB |
| head (public output) | 1 | 12 | 3.96 M | 17.66 KB |
| **total** | **63** | | **17.2 M** | |

A verifier per step (weights fixed in each circuit) would cost about 251 M gas to deploy. The price is
proving time in a dispute: a block step takes 21 s with hashed weights against 2.3 s with fixed weights
(the circuit also hashes the 1,056 weights); a projection half takes 74 s. Only one step is proved
per dispute.

**Results (input: an illicit Elliptic test-period transaction, `results/onchain_disputes_B60.json`):**

| Scenario | Located step | Bisection rounds | Step proof | Winner | Correct | Game gas (real verifier) |
|---|---|---|---|---|---|---|
| asserter cheats at a projection half | 0 (proj0) | 5 | 74 s | challenger | yes | 1.91 M |
| asserter cheats at the join step | 2 (u1) | 6 | 22 s | challenger | yes | 1.61 M |
| asserter cheats at a middle block | 30 (u29) | 6 | 21 s | challenger | yes | 1.58 M |
| asserter cheats at the head (the decision) | 62 (u61) | 6 | 1.6 s | challenger | yes | 1.58 M |
| false challenge against an honest asserter | 33 (u32) | 6 | 22 s | asserter | yes | 1.61 M |
| malformed prefix chain | 40 | 6 | none needed | challenger | yes | 0.91 M |
| a valid proof made with another block's weights | 20 (u19) | 6 | 21 s | rejected (`params instance != step weights`), then challenger | yes | 1.60 M |

The last row is the attack a shared verifier opens: the proof verifies, but it proves the wrong layer.
The weight-hash check refuses it.

The measured dispute costs (1.58 M typical, 1.91 M worst, including `resolve` with the real verifier at
0.71–1.14 M) match the estimate in the gas table above (1.62 M / 1.90 M), which added v1's separately
measured verifier gas to a mock-verifier game.

Limits:
- The projection verifier's bytecode is 24,644 bytes, just over the 24,576-byte EIP-170 limit. A
  permissioned chain can raise the limit (Besu `contractSizeLimit`), or the projection can be split in
  three instead of two.
- The input is the one test transaction stored in the H4 specification, because the Elliptic CSVs were
  not in this workspace. With `--data` (the arrays from `prepare_data.py`) the script takes a test
  transaction from the data instead.

## Layout

| File | Role |
|---|---|
| `prepare_data.py` | Kaggle CSVs → `X.npy`, `y.npy`, `ts.npy` (labelled rows only) |
| `data.py` | Temporal split and the fixed scaler |
| `train.py`, `train_suite.py` | NumPy residual-MLP trainer. Selection on validation only; 3 seeds per depth; writes `models/` |
| `onnx_builder.py` | Builds ONNX graphs from weights. `dispute_steps()` restructures the model into bisectable steps (projection split in two) |
| `quant.py`, `steps.py` | Fixed-point emulation of EZKL's arithmetic, matching it exactly; the step-level trace |
| `commit.py` | Poseidon leaves (same hash as EZKL's circuit hash) and the Merkle root |
| `prover.py` | EZKL pipeline for one circuit: settings, calibration on real data, compile, setup, prove ×N, verify |
| `bench_steps.py` | Proves single steps or the whole model (`mono`); writes `results/steps_B*.json` |
| `dispute.py` | End-to-end dispute scenarios: cheat, bisect, prove one step, verifier checks |
| `quant_accuracy.py` | Fixed-point vs float accuracy on the full test set |
| `check_quant.py` | Checks the emulator matches EZKL's witness exactly |
| `gas.py`, `gas_game.py`, `contracts/OptimisticInference.sol` | v1: deploy verifiers and the gas-measurement game on anvil; measure gas |
| `contracts/OptimisticInferenceV2.sol` | v2 dispute game with full adjudication (see above) |
| `chain.py`, `test_chain.py` | keccak prefix chain and Merkle tree shared with the contract, and their tests |
| `test/`, `foundry.toml` | Foundry tests of the v2 contract (`GameHarness.sol` holds the mock verifier and trace builders) |
| `gas_game_v2.py` | v2 gas as real transactions on anvil → `results/gas_v2.json` |
| `onchain_dispute.py` | v2.1: complete games on anvil with real EZKL step verifiers, one per step shape → `results/onchain_disputes_B60.json` |
| `make_report.py` | Builds `results/summary.json` and `results/speedup_vs_depth.png` |

## Reproduce

```bash
pip install -r requirements.txt
python3 prepare_data.py /path/to/elliptic_bitcoin_dataset
python3 train_suite.py                      # models/W32_B{1,8,24,60}.pkl (models are included already)
python3 quant_accuracy.py 9                 # accuracy criterion
python3 check_quant.py                      # emulator == EZKL, exactly
for B in 1 8 24 60; do python3 bench_steps.py $B mono; done
python3 bench_steps.py 60 proj0 proj1 u1 u2 u30 u60 u61
python3 dispute.py 60 0 1 2 3 4 5 6
# gas (needs Foundry + solc 0.8.24): FOUNDRY_BIN=~/.foundry/bin SOLC=solc
python3 gas.py 60 mono:mono_ic2 proj0:proj0_ic4 u1:u1_ic2 u2:u2_ic2 u61:u61_ic2
python3 gas_game.py
python3 make_report.py
# v2 dispute contract (Foundry; forge-std: git clone https://github.com/foundry-rs/forge-std lib/forge-std)
forge test                                  # 17 tests
python3 -m pytest -q test_chain.py          # 66 tests
forge build && python3 gas_game_v2.py       # results/gas_v2.json
python3 onchain_dispute.py --blocks 60      # real verifiers; first run sets up the 4 circuits (~3 min, ~3 GB of keys)
```

## Notes

- **Timings.** The committed files in `results/` (`steps_B*.json`, `summary.json`) come from the
  **multi-core** laptop run: 12.6× at 60 blocks, and 0.91× / 2.84× / 5.98× at 1 / 8 / 24 blocks. The
  single-CPU figures quoted in WRITEUP.md (14.9×, 7.67×, 3.88×, 1.07×) have no committed raw files.
  Always state the hardware alongside any speedup number.
- **Monolithic proof** of the 60-block model peaks around 1.3 GB RAM.
- **Solidity verifier.** `ezkl.create_evm_verifier` writes the Solidity file first, then tries to
  download solc to build the ABI. `gas.py` ignores that download error and compiles with the local solc.
- **EZKL settings used throughout:** input and parameter scale 9, `decomp_base` 256 with 4 legs. The
  smaller range-check table removes the size floor (2¹⁵ rows) that the default settings impose.
