# Running one validator on an independent machine

Goal: a person outside the team runs one of the four validators on their own computer, with a key only
they hold, their own copy of the ledger and their own build of the proof verifier. The settlement contract
is deployed so that **nothing can settle without their signature** (4 of 4). Afterwards they check, on
their own ledger, that every settled transfer was one their validator approved and that every falsified
request was refused.

Two people are involved:
- **Host** (a team member): runs the network, the operator node and three validators.
- **Reviewer** (the independent person): runs one Besu node and one validator.

This procedure was rehearsed end to end on 20 Oct 2026 with a separate Besu node, a separately generated
key and a separate verifier binary. With 40 Elliptic transfers (12 falsified), the reviewer's validator
signed the 28 honest requests and refused all 12 falsified ones, each with its reason. All 28 settlements
on the reviewer's own ledger carried its signature, and nothing settled without it.

## 0. What both machines need
- Linux or macOS (Windows through WSL2), about 8 GB RAM and 10 GB free disk.
- Git, Python 3.11+ (`pip install web3 eth-account numpy pandas`), Java 21 and **Hyperledger Besu
  25.10.0**.
- **Reviewer only:** Rust (stable) to build the verifier from source.
- **Host only:** Foundry (`forge`) and the Elliptic dataset.
- **A network path between the two machines.** The easiest is [Tailscale](https://tailscale.com) (free):
  install it on both machines, log in, and note each machine's Tailscale IP (`tailscale ip -4`).
  The reviewer must reach the host on TCP/UDP **30303** (Besu peering); the host must reach the
  reviewer on TCP **9200** (the reviewer's validator). With Tailscale both work without router changes.

## 1. Host: start the network
```bash
git clone https://github.com/maanas1234/CBDC.git && cd CBDC
cd h4-pqc-stark/zk_composed && cargo build --release && cd ../..
cd integration && forge build
BESU=/path/to/besu chain/start_chain.sh          # 4-node QBFT network, generates its own keys
```
Then send the reviewer:
1. `integration/chain/networkFiles/genesis.json` (no private keys in it);
2. the host's bootnode address:
   `enode://<contents of chain/data/node0/key.pub without 0x>@<host Tailscale IP>:30303`.

## 2. Reviewer: own node, own key, own verifier
```bash
git clone https://github.com/maanas1234/CBDC.git && cd CBDC
cd h4-pqc-stark/zk_composed && cargo build --release && cd ../..       # your own verifier build
mkdir -p ~/cbdc-review/besu && cd ~/cbdc-review
cp /path/to/received/genesis.json .
echo "0x$(openssl rand -hex 32)" > besu/nodekey                         # your Besu node's key
python3 -c "from eth_account import Account; a = Account.create(); open('my_validator.key','w').write(a.key.hex()); print(a.address)"
```
The last command prints **your validator address** and saves the key in `my_validator.key`. Send the
host **only the address**. The key never leaves your machine.

Start your Besu node and let it sync:
```bash
besu --data-path=$HOME/cbdc-review/besu/data --genesis-file=genesis.json \
     --node-private-key-file=besu/nodekey --bootnodes=<enode from the host> \
     --p2p-host=<your Tailscale IP> --p2p-port=30310 --rpc-http-enabled --rpc-http-port=8545 \
     --rpc-http-api=ETH,NET,QBFT,WEB3 --min-gas-price=0
```
Check it follows the chain: the block number from
`curl -s -X POST -H 'content-type: application/json' --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' localhost:8545`
should rise by about one per second.

## 3. Host: deploy a strict settlement contract that includes the reviewer
```bash
cd integration/pipeline
python deploy.py --attester <reviewer's validator address> --threshold 4
./start_validators.sh                 # starts the host's three validators; the fourth place is the reviewer's
```
Send the reviewer `deployment_public.json`. It holds the contract addresses, ABIs and policy, and no
private keys.

## 4. Reviewer: start your validator
```bash
cd ~/cbdc-review
python3 /path/to/CBDC/integration/pipeline/validator_node.py \
   --key-file my_validator.key --deployment deployment_public.json \
   --rpc http://127.0.0.1:8545 \
   --prover /path/to/CBDC/h4-pqc-stark/zk_composed/target/release/zk_composed \
   --host 0.0.0.0 --port 9200 --log decisions.jsonl
```
It prints your address, the contract, `listed as attester`, `threshold 4`, and the model digest computed
by **your** build. If it says `NOT an attester`, the host deployed with a different address.

Your validator reads the ledger only from your own node, verifies every proof with your own binary, and
appends every decision (signed or refused, with the reason) to `decisions.jsonl`.

## 5. Host: run the transfers
```bash
python run_pipeline.py --n 200 --source elliptic --data /path/to/elliptic_bitcoin_dataset \
   --faults 0.2 --seed 2026 --out runs/independent_200 \
   --validators http://127.0.0.1:9100,http://127.0.0.1:9101,http://127.0.0.1:9102,http://<reviewer Tailscale IP>:9200
```
About 2–3 seconds per transfer. 20 % of requests are deliberately falsified (forged outputs, swapped
amounts, stale penalty, corrupted proofs, wrong model).

## 6. Reviewer: check the result on your own ledger
```bash
python3 /path/to/CBDC/integration/pipeline/check_my_validator.py decisions.jsonl deployment_public.json --rpc http://127.0.0.1:8545
```
It reads every settlement from **your** node and reports:
- how many requests you signed and refused, and why;
- `check1`: every settlement carrying your signature is a request you logged as signed;
- `check2`: no settlement carries your signature for a request you refused;
- `check3`: with the 4-of-4 contract, nothing settled without your signature, so every settled transfer
  passed your own verifier.

All three should be `true`. In our rehearsal the result was 28 signed, 12 refused and all three checks
`true`. For a full ledger audit as well, the reviewer can also run
`python3 /path/to/CBDC/integration/pipeline/audit_chain.py` against their node (after `forge build` in
`integration/`). That needs the host's `runs/` folder for the comparison with the host's records.

Send back to the host: `decisions.jsonl`, the output of `check_my_validator.py`, and a line saying
which machine and versions you used. That is the independent confirmation.

## 7. Optional: also make the reviewer's node a block producer
So far the reviewer validates settlements and keeps a full copy of the ledger, but does not seal
blocks. To make their Besu node a fifth QBFT validator as well, the host votes it in from at least three
of the four nodes. The address is the one derived from `besu/nodekey`; the reviewer can get it with
`python3 -c "from eth_account import Account; print(Account.from_key(open('besu/nodekey').read().strip()).address)"`.
```bash
for p in 8545 8546 8547; do
  curl -s -X POST -H 'content-type: application/json' \
   --data '{"jsonrpc":"2.0","method":"qbft_proposeValidatorVote","params":["<node address>", true],"id":1}' localhost:$p
done
```
Within a few blocks, `qbft_getValidatorsByBlockNumber` lists five validators, and the reviewer's node
seals some blocks. The `miner` field of those blocks shows its address. Vote it out again the same way
with `false`, then `qbft_discardValidatorVote`, before the reviewer shuts down. Otherwise the network
keeps waiting for it. We rehearsed this too: the node sealed blocks, was voted out, and the network
continued with four.

## Notes
- The reviewer does not need to trust anything the host sends except the genesis file (which their
  node checks as it syncs) and the contract address. The model digest comes from their own build.
- With `--threshold 4`, the run stops making progress if the reviewer's validator is offline or
  refuses everything (for example, because of a different model build). That is intended for this
  check. Normal operation uses 3 of 4.
- Stop everything with `./stop_validators.sh` and `chain/stop_chain.sh` (host) and Ctrl-C (reviewer).
