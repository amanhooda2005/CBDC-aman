"""Gas of the v2 dispute game (contracts/OptimisticInferenceV2.sol), measured as real transactions
on a local anvil chain, the same way gas_game.py measured v1.

Step verifiers are the mock from test/GameHarness.sol (it accepts only the honest step output), so
`resolve` is measured WITHOUT the EZKL verifier; the real per-step verifier gas from results/gas.json
is added afterwards (minus the 21,000 base cost, which the internal call does not pay again).

Usage:  forge build && python3 gas_game_v2.py        (needs Foundry: anvil, cast, forge)
Writes: results/gas_v2.json
"""
import json, os, subprocess, time
from chain import k256, to_b32, digest, prefix_chain, merkle_levels, merkle_proof

BIN = os.environ.get("FOUNDRY_BIN", os.path.expanduser("~/.foundry/bin"))
PORT = os.environ.get("ANVIL_PORT", "8646")
RPC = f"http://127.0.0.1:{PORT}"
PK_A = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"   # anvil account 0: asserter
PK_C = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"   # anvil account 1: challenger
N, TXLEN = 63, 4
TX = [11, 22, 33, 44]
INPUTS = [[-1], [-1], [0, 1]] + [[k - 1] for k in range(3, N)]   # proj0, proj1, join, 59 blocks, head
ABOND, CBOND = 10**18, 5 * 10**17


def sh(*a):
    return subprocess.run(list(a), capture_output=True, text=True, check=True).stdout.strip()


def _json(out):
    """First JSON object in cast's output (cast may print extra lines after it)."""
    return json.JSONDecoder().raw_decode(out[out.index("{"):])[0]


def send(pk, to, sig, *args, value=None):
    cmd = [f"{BIN}/cast", "send", "--rpc-url", RPC, "--private-key", pk, "--json"]
    if value:
        cmd += ["--value", str(value)]
    rc = _json(sh(*cmd, to, sig, *args))
    assert rc["status"] == "0x1", rc
    return rc


def create(pk, artifact, sig=None, *args):
    code = json.load(open(artifact))["bytecode"]["object"]
    cmd = [f"{BIN}/cast", "send", "--rpc-url", RPC, "--private-key", pk, "--json", "--create", code]
    if sig:
        cmd += [sig, *args]
    rc = _json(sh(*cmd))
    assert rc["status"] == "0x1", rc
    return rc["contractAddress"]


def call(to, sig, *args):
    return sh(f"{BIN}/cast", "call", "--rpc-url", RPC, to, sig, *args)


gas = lambda rc: int(rc["gasUsed"], 16)
hx = lambda b: "0x" + b.hex()
arr = lambda xs: "[" + ",".join(xs) + "]"


# ----------------------------------------------------------------- mock model (= MockStepVerifier)
def step_out(k, ivals):
    # keccak256(abi.encode(uint256 k, bytes32[] ivals))
    enc = k.to_bytes(32, "big") + (64).to_bytes(32, "big") + len(ivals).to_bytes(32, "big") + b"".join(ivals)
    return k256(enc)


def trace(corrupt_at=None):
    in_hash = digest(TX)
    vals, leaves = [], []
    for k in range(N):
        iv = [in_hash if j == -1 else vals[j] for j in INPUTS[k]]
        v = k256(b"tampered" + k.to_bytes(32, "big")) if k == corrupt_at else step_out(k, iv)
        vals.append(v)
        leaves.append(digest([int.from_bytes(v, "big")]) if k == N - 1 else v)
    P = prefix_chain(leaves)
    L = merkle_levels(leaves, P)
    return dict(vals=vals, leaves=leaves, P=P, levels=L, root=L[-1][0], out=leaves[-1], inp=in_hash)


def proof_arg(T, i):
    return arr(hx(p) for p in merkle_proof(T["levels"], i))


def honest_proof_args(H, k):
    inst, ops = [], []
    for j in INPUTS[k]:
        if j == -1:
            inst += [str(x) for x in TX]
        else:
            inst.append(str(int.from_bytes(H["leaves"][j], "big")))
            start = bytes(32) if j == 0 else H["P"][j - 1]
            ops.append(f"({hx(start)},{arr(hx(l) for l in H['leaves'][j:k])})")
    inst.append(str(int.from_bytes(H["vals"][k], "big")))
    return arr(inst), arr(ops)


def dispute(game, H, k):
    Bad = trace(corrupt_at=k)
    rc = send(PK_A, game, "commit(bytes32,bytes32,bytes32)", hx(Bad["root"]), hx(Bad["out"]), hx(Bad["inp"]))
    g = {"commit": gas(rc)}
    ts = int(sh(f"{BIN}/cast", "block", "--rpc-url", RPC, str(int(rc["blockNumber"], 16)), "-f", "timestamp"))
    cid = int(call(game, "nClaims()(uint256)").split()[0]) - 1
    asserter = sh(f"{BIN}/cast", "wallet", "address", PK_A)
    claim = f"({hx(Bad['root'])},{hx(Bad['out'])},{hx(Bad['inp'])},{asserter},{ts})"
    rc = send(PK_C, game, "challenge(uint256,(bytes32,bytes32,bytes32,address,uint64))", str(cid), claim, value=CBOND)
    g["challenge"] = gas(rc)
    challenger = sh(f"{BIN}/cast", "wallet", "address", PK_C)
    did = call(game, "disputeId(uint256,address)(bytes32)", str(cid), challenger)
    CL = "(bytes32,bytes32,bytes32,address,uint64)"
    rc = send(PK_A, game, f"openFinal(bytes32,{CL},bytes32,bytes32,bytes32[],bytes32,bytes32,bytes32[])", did, claim,
              hx(Bad["leaves"][N - 1]), hx(Bad["P"][N - 1]), proof_arg(Bad, N - 1),
              hx(Bad["leaves"][N - 2]), hx(Bad["P"][N - 2]), proof_arg(Bad, N - 2))
    g["openFinal"] = gas(rc)
    g["reveal"], g["respond"] = [], []
    while True:
        phase = int(call(game, "status(bytes32)(uint8,uint256,uint256,uint64)", did).split()[0])
        mid = int(call(game, "currentMid(bytes32)(uint256)", did).split()[0])
        if phase == 2:   # AwaitReveal
            rc = send(PK_A, game, "reveal(bytes32,uint256,bytes32,bytes32,bytes32[])", did, str(mid),
                      hx(Bad["leaves"][mid]), hx(Bad["P"][mid]), proof_arg(Bad, mid))
            g["reveal"].append(gas(rc))
        elif phase == 3:  # AwaitResponse
            rc = send(PK_C, game, "respond(bytes32,bool)", did, "true" if Bad["P"][mid] == H["P"][mid] else "false")
            g["respond"].append(gas(rc))
        else:
            break
    st = call(game, "status(bytes32)(uint8,uint256,uint256,uint64)", did).split()
    assert int(st[0]) == 4 and int(st[2]) == k, st   # AwaitProof at step k
    inst, ops = honest_proof_args(H, k)
    rc = send(PK_C, game, f"resolve(bytes32,{CL},bytes,uint256[],(bytes32,bytes32[])[])", did, claim, "0x76616c6964", inst, ops)
    g["resolve_mock"] = gas(rc)
    phase = int(call(game, "status(bytes32)(uint8,uint256,uint256,uint64)", did).split()[0])
    assert phase == 6, phase   # ChallengerWon
    g["game_total"] = g["challenge"] + g["openFinal"] + sum(g["reveal"]) + sum(g["respond"]) + g["resolve_mock"]
    return g


def main():
    for d in ("out/OptimisticInferenceV2.sol/OptimisticInferenceV2.json", "out/GameHarness.sol/MockStepVerifier.json"):
        assert os.path.exists(d), "run `forge build` first"
    anvil = subprocess.Popen([f"{BIN}/anvil", "--port", PORT, "--silent"])
    time.sleep(2)
    try:
        verifiers = [create(PK_A, "out/GameHarness.sol/MockStepVerifier.json", "constructor(uint256,int32[],uint32)",
                            str(k), arr(str(j) for j in INPUTS[k]), str(TXLEN)) for k in range(N)]
        params = "(" + ",".join([str(TXLEN), arr(verifiers), "[]", arr(arr(str(j) for j in ins) for ins in INPUTS),
                                 str(ABOND), str(CBOND), "86400", "3600", "172800"]) + ")"
        game = create(PK_A, "out/OptimisticInferenceV2.sol/OptimisticInferenceV2.json",
                      "constructor((uint32,address[],bytes32[],int32[][],uint256,uint256,uint64,uint64,uint64))", params)
        send(PK_A, game, "depositBond()", value=20 * 10**18)
        H = trace()
        first = send(PK_A, game, "commit(bytes32,bytes32,bytes32)", hx(H["root"]), hx(H["out"]), hx(H["inp"]))
        res = {"commit_first": gas(first), "disputes": {}}
        for k, name in [(17, "block step (u16)"), (0, "projection half (proj0)"), (2, "join step (u1, two inputs)"), (N - 1, "head step")]:
            res["disputes"][name] = dict(step=k, **dispute(game, H, k))
            print(name, res["disputes"][name], flush=True)
        # combine with the real EZKL step-verifier gas measured in v1 (results/gas.json)
        v1 = json.load(open("results/gas.json"))
        step_verify = {"block": v1["B60_u2"]["verify_tx_gas"], "proj": v1["B60_proj0"]["verify_tx_gas"]}
        mono = v1["B60_mono"]["verify_tx_gas"]
        commit = res["disputes"]["block step (u16)"]["commit"]
        typ = res["disputes"]["block step (u16)"]["game_total"] + step_verify["block"] - 21000
        worst = res["disputes"]["projection half (proj0)"]["game_total"] + step_verify["proj"] - 21000
        res["summary"] = dict(
            monolithic_verify=mono, commit_per_inference=commit, undisputed_ratio=mono / commit,
            full_dispute_typical_est=typ, full_dispute_worst_est=worst,
            per_dispute_ratio_worst=mono / worst,
            max_dispute_rate_for_10x_worst=(mono / 10 - commit) / worst,
            max_dispute_rate_for_10x_typical=(mono / 10 - commit) / typ,
            note="full dispute = measured game txs (challenge, openFinal, reveals, responds, resolve with mock verifier) "
                 "+ v1-measured EZKL step-verifier tx gas - 21000 base",
        )
        print(json.dumps(res["summary"], indent=2))
        json.dump(res, open("results/gas_v2.json", "w"), indent=2)
    finally:
        anvil.terminate()


if __name__ == "__main__":
    main()
