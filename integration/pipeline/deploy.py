"""Deploys the lab CBDC stack onto the local Besu QBFT network and writes deployment.json.

  - LiabilityRegistry (H3): s = 15, S = 100, B = 14 = B* (units of 1e15 wei), regulator = account 0
  - SettlementGate: the 4 QBFT validators (their consensus keys) attest, threshold 3
  - operator (account 1) is credentialed, registers N agents (paying bond B each) and stakes
  - customers (accounts 2..5) receive CBDC from the central bank and mandate the agents

Usage: python deploy.py [n_agents] [prover_bin]   (chain must be running: chain/start_chain.sh)
"""
import json
import subprocess
import sys
from pathlib import Path

from eth_account import Account
from web3 import Web3

HERE = Path(__file__).parent
ROOT = HERE.parent
RPC = [f"http://127.0.0.1:{8545 + i}" for i in range(4)]
# anvil / hardhat well-known development keys (funded in the genesis; lab only)
DEV_KEYS = [
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
    "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
    "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba",
    "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e",
    "0x4bbbf85ce3377467afe5d46f804f221813b2bb87f24d81f60f1fcdbf7cbf4356",
    "0xdbda1821b80551c9d65939329250298aa3472ba22feea921c0cf5d620ea67b97",
    "0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6",
]
UNIT = 10**15
POLICY = {"max_compliant": 12800, "sanctioned_id": 1000}   # H3 rule units (x256): limit 50, sanctioned id


def artifact(name):
    a = json.load(open(ROOT / "out" / f"{name}.sol" / f"{name}.json"))
    return a["abi"], a["bytecode"]["object"]


class Chain:
    def __init__(self, rpc=RPC[0]):
        self.w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 60}))
        self.nonces = {}

    def send(self, key, tx_fn=None, *, deploy=None, value=0):
        acct = Account.from_key(key)
        nonce = self.nonces.get(acct.address, self.w3.eth.get_transaction_count(acct.address, "pending"))
        base = {"from": acct.address, "nonce": nonce, "gasPrice": 0, "chainId": self.w3.eth.chain_id, "value": value}
        if deploy is not None:
            abi, code, args = deploy
            tx = self.w3.eth.contract(abi=abi, bytecode=code).constructor(*args).build_transaction({**base, "gas": 12_000_000})
        else:
            tx = tx_fn.build_transaction({**base, "gas": 3_000_000})
        signed = acct.sign_transaction(tx)
        h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        self.nonces[acct.address] = nonce + 1
        return h

    def wait(self, h):
        rc = self.w3.eth.wait_for_transaction_receipt(h, timeout=120, poll_latency=0.2)
        assert rc.status == 1, rc
        return rc


def main(n_agents=4, prover_bin=None):
    prover_bin = prover_bin or str(ROOT.parent / "h4-pqc-stark/zk_composed/target/release/zk_composed")
    ch = Chain()
    w3 = ch.w3
    reg_abi, reg_code = artifact("LiabilityRegistry")
    gate_abi, gate_code = artifact("SettlementGate")
    regulator, operator = Account.from_key(DEV_KEYS[0]), Account.from_key(DEV_KEYS[1])
    treasury = Account.from_key(DEV_KEYS[9]).address
    keys_dir = ROOT / "chain/networkFiles/keys"
    vkeys = [{"address": Account.from_key((d / "key").read_text().strip()).address, "key": (d / "key").read_text().strip()}
             for d in sorted(keys_dir.iterdir())]
    qbft = [Web3.to_checksum_address(a) for a in w3.provider.make_request("qbft_getValidatorsByBlockNumber", ["latest"])["result"]]
    assert sorted(v["address"] for v in vkeys) == sorted(qbft), "attestation keys must be the QBFT validators"

    params = (regulator.address, treasury, 15 * UNIT, 100 * UNIT, 14 * UNIT, 2 * 86400, 30 * UNIT, 1 * UNIT, 10000)
    rc = ch.wait(ch.send(DEV_KEYS[0], deploy=(reg_abi, reg_code, [params])))
    registry = w3.eth.contract(address=rc.contractAddress, abi=reg_abi)
    rc = ch.wait(ch.send(DEV_KEYS[0], deploy=(gate_abi, gate_code, [registry.address, regulator.address, [v["address"] for v in vkeys], 3])))
    gate = w3.eth.contract(address=rc.contractAddress, abi=gate_abi)
    hs = [ch.send(DEV_KEYS[0], registry.functions.setSlasher(gate.address)),
          ch.send(DEV_KEYS[0], registry.functions.issueCredential(operator.address))]
    for h in hs:
        ch.wait(h)
    agents = [Web3.keccak(text=f"agent-{i}").hex() for i in range(n_agents)]
    hs = [ch.send(DEV_KEYS[1], registry.functions.registerAgent(Web3.to_bytes(hexstr=a)), value=14 * UNIT) for a in agents]
    hs.append(ch.send(DEV_KEYS[1], registry.functions.topUp(), value=100 * UNIT))
    customers = [Account.from_key(k) for k in DEV_KEYS[2:6]]
    for c in customers:
        hs.append(ch.send(DEV_KEYS[0], gate.functions.mint(c.address, 10**12)))
    for h in hs:
        ch.wait(h)
    hs = [ch.send(c.key.hex(), gate.functions.setMandate(Web3.to_bytes(hexstr=a), True)) for c in customers for a in agents]
    for h in hs:
        ch.wait(h)
    digest = json.loads(subprocess.run([prover_bin, "info"], capture_output=True, text=True, check=True).stdout)["model_digest"]
    dep = {
        "rpc": RPC, "registry": registry.address, "gate": gate.address,
        "regulator": regulator.address, "operator": operator.address, "operator_key": DEV_KEYS[1], "treasury": treasury,
        "customers": [{"address": c.address, "key": c.key.hex()} for c in customers],
        "agents": agents, "validators": vkeys, "threshold": 3,
        "registry_unit": UNIT, "policy": POLICY, "prover_bin": prover_bin, "model_digest": digest,
        "abi": {"registry": reg_abi, "gate": gate_abi},
    }
    json.dump(dep, open(HERE / "deployment.json", "w"), indent=1)
    print(json.dumps({k: dep[k] for k in ("registry", "gate", "operator", "agents", "model_digest")}, indent=1))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 4, sys.argv[2] if len(sys.argv) > 2 else None)
