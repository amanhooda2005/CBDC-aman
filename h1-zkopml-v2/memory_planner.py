"""Memory-budgeted decomposition of the risk model into dispute steps.

The prover node's memory is an input of the decomposition: every step's proving circuit must be provable within
the node's memory budget M, and among the decompositions that fit, the one with the fewest steps (so the fewest
bisection rounds in a dispute) is chosen.

  1. profile   On this machine, prove one circuit of each candidate step shape in a fresh process and record its
               circuit size (logrows), proving-key size and peak resident memory (the node's hardware profile).
               Shapes: the projection split column-wise into k parts (k = 1, 2, 4, 8), the join step that
               concatenates the k parts, and groups of g consecutive residual blocks (g = 1, 2, 4, 8, 16).
  2. plan      For a budget M (read from the node: cgroup limit or available memory, or given), choose the smallest
               k and the largest g whose measured peak is at most (1 - margin)·M. If the whole model fits, a dispute
               needs no bisection. Output: the step list, its length n and the bisection rounds ceil(log2 n).
  3. validate  For each plan, prove the plan's largest steps at a different position of the model than the profile
               used (the last block group, the last projection part, the join) in fresh processes and check that the
               measured peak memory stays within M.

Usage: python3 memory_planner.py [DATA_DIR]     (needs models/W32_B60.pkl, EZKL)
Writes results/memory_profile.json and results/memory_plans.json.
"""
import json
import math
import os
import pickle
import subprocess
import sys

import numpy as np

H1 = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, H1); os.chdir(H1)
from data import load, temporal_split, apply_scaler
from onnx_builder import build, build_join_block, dispute_steps
from steps import quantize_steps, run_steps
from quant import S

WD = os.path.join(H1, "work", "memory_planner"); os.makedirs(WD, exist_ok=True)
MARGIN = 0.10
KS, GS = (1, 2, 4, 8), (1, 2, 4, 8, 16)
BUDGETS_MB = (128, 256, 512, 1024, 2048)
deq = lambda a: a / 2 ** S

PROVE = r"""
import json, resource, sys, time, ezkl
d, srs = sys.argv[1], sys.argv[2]
t = time.time(); ezkl.prove(d + "/witness.json", d + "/net.compiled", d + "/pk.key", d + "/proof_m.json", srs_path=srs)
p = time.time() - t
ok = ezkl.verify(d + "/proof_m.json", d + "/settings.json", d + "/vk.key", srs_path=srs)
print(json.dumps({"prove_s": p, "verified": bool(ok), "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}))
"""


def node_memory_budget_mb():
    """Memory this node can give the prover: the cgroup limit if there is one, else MemAvailable."""
    for f in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = open(f).read().strip()
            if v.isdigit() and int(v) < 1 << 50:
                return int(v) / 1e6
        except OSError:
            pass
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024 / 1e6
    except OSError:
        pass
    import resource
    return None


def setup_and_measure(name, onnx, inputs, calib, iv, ov, cols):
    """EZKL setup (keys) in one process, then prove + verify in a fresh process to measure its peak memory."""
    json.dump({"input_data": [deq(c).reshape(-1).tolist() for c in calib]}, open(f"{WD}/{name}_calib.json", "w"))
    json.dump([deq(a).reshape(-1).tolist() for a in inputs], open(f"{WD}/{name}_in.json", "w"))
    d = f"{WD}/{name}_d"
    r = subprocess.run([sys.executable, "prover.py", onnx, f"{WD}/{name}_in.json", d, iv, ov, "1", f"{WD}/{name}_calib.json"],
                       capture_output=True, text=True, env=dict(os.environ, ZK_INNER_COLS=str(cols), ZK_PARAM_VIS="fixed"))
    res = json.loads([l for l in r.stdout.splitlines() if l.startswith("RESULT_JSON")][0][12:])
    k = res["logrows"]
    m = json.loads(subprocess.run([sys.executable, "-c", PROVE, d, os.path.join(WD, f"srs_{k}.srs")],
                                  capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1])
    out = {"logrows": k, "pk_mb": round(os.path.getsize(f"{d}/pk.key") / 1e6, 1), "prove_s": round(m["prove_s"], 2),
           "verified": m["verified"], "peak_rss_mb": round(m["peak_rss_mb"])}
    print(name, out, flush=True)
    return out


def main():
    data = sys.argv[1] if len(sys.argv) > 1 else "."
    m = pickle.load(open("models/W32_B60.pkl", "rb"))
    units = m["units"]                          # proj, 60 blocks, head
    X, y, ts = load(data); _, _, (Xte, yte), _ = temporal_split(X, y, ts); Xte = apply_scaler(Xte, m["scaler"])
    rng = np.random.default_rng(0); cidx = rng.choice(len(Xte), 16, replace=False); sample = int(np.where(yte == 1)[0][0])
    unit_steps = quantize_steps(dispute_steps(units, n_proj_parts=1))   # proj, u1..u60, head: one step per unit
    cx, couts = run_steps(unit_steps, Xte[cidx]); sx, souts = run_steps(unit_steps, Xte[sample:sample + 1])
    nb = len(units) - 2                         # 60 residual blocks

    def proj_part(k, p, tag):
        st = quantize_steps(dispute_steps(units, n_proj_parts=k))[p]
        build([st["unit"]], f"{WD}/{tag}.onnx")
        return setup_and_measure(tag, f"{WD}/{tag}.onnx", [sx], [cx], "public", "hashed", 4)

    def join(k, tag):
        steps = quantize_steps(dispute_steps(units, n_proj_parts=k))
        cxk, cok = run_steps(steps, Xte[cidx]); sxk, sok = run_steps(steps, Xte[sample:sample + 1])
        st = steps[k]
        if k == 1:
            build([st["unit"]], f"{WD}/{tag}.onnx")
        else:
            build_join_block(st["unit"], [sok[j].shape[1] for j in st["inputs"]], f"{WD}/{tag}.onnx")
        return setup_and_measure(tag, f"{WD}/{tag}.onnx", [sok[j] for j in st["inputs"]], [cok[j] for j in st["inputs"]], "hashed", "hashed", 2)

    def group(first, g, tag):
        # blocks first..first+g-1 are unit_steps indices first..first+g-1 (index 0 = projection)
        build([unit_steps[i]["unit"] for i in range(first, first + g)], f"{WD}/{tag}.onnx")
        return setup_and_measure(tag, f"{WD}/{tag}.onnx", [souts[first - 1]], [couts[first - 1]], "hashed", "hashed", 2)

    # ---------------- 1. hardware profile (blocks starting at block 2; first projection part)
    prof = {"proj": {}, "join": {}, "group": {}}
    for k in KS:
        prof["proj"][k] = proj_part(k, 0, f"proj_k{k}_p0")
        prof["join"][k] = join(k, f"join_k{k}")
    for g in GS:
        prof["group"][g] = group(2, g, f"grp_g{g}_at2")
    whole = json.load(open("results/dispute_resources.json"))["monolithic_60_blocks"]
    prof["whole_model"] = {k: whole[k] for k in ("logrows", "pk_mb", "peak_rss_mb")}
    prof["machine"] = "2-vCPU, 8 GB cloud VM; fixed-weight step circuits; one fresh process per proof"
    prof["node_memory_budget_mb_detected"] = node_memory_budget_mb()
    json.dump(prof, open("results/memory_profile.json", "w"), indent=1)

    # ---------------- 2. plans
    plans = {}
    for B in BUDGETS_MB:
        cap = (1 - MARGIN) * B
        if prof["whole_model"]["peak_rss_mb"] <= cap:
            plans[B] = {"whole_model_fits": True, "steps": 1, "bisection_rounds": 0}
            continue
        k = next((k for k in KS if max(prof["proj"][k]["peak_rss_mb"], prof["join"][k]["peak_rss_mb"]) <= cap), None)
        g = max((g for g in GS if prof["group"][g]["peak_rss_mb"] <= cap), default=None)
        if k is None or g is None:
            plans[B] = {"feasible": False}
            continue
        # steps: k projection parts, the join (block 1), blocks 2..60 in groups of g, the head
        n = k + 1 + math.ceil((nb - 1) / g) + 1
        plans[B] = {"whole_model_fits": False, "proj_parts": k, "blocks_per_step": g, "steps": n,
                    "bisection_rounds": math.ceil(math.log2(n)),
                    "predicted_peak_mb": max(prof["proj"][k]["peak_rss_mb"], prof["join"][k]["peak_rss_mb"], prof["group"][g]["peak_rss_mb"])}

    # ---------------- 3. validate each distinct plan at other positions of the model
    done = {}
    for B, p in plans.items():
        if not p.get("proj_parts"):
            continue
        k, g = p["proj_parts"], p["blocks_per_step"]
        if (k, g) not in done:
            last_first = nb - g + 1                       # the last full group ends at block 60
            meas = {"last_proj_part": proj_part(k, k - 1, f"v_proj_k{k}_p{k - 1}"),
                    "last_block_group": group(last_first, g, f"v_grp_g{g}_at{last_first}")}
            done[(k, g)] = meas
        meas = done[(k, g)]
        peak = max(meas["last_proj_part"]["peak_rss_mb"], meas["last_block_group"]["peak_rss_mb"], prof["join"][k]["peak_rss_mb"])
        p["validation"] = {"measured_peak_mb_other_positions": peak, "within_budget": peak <= B,
                           "all_proofs_verified": all(x["verified"] for x in meas.values())}
    out = {"margin": MARGIN, "budgets_mb": list(BUDGETS_MB), "plans": plans,
           "unsplit_reference": {"steps_one_per_unit": 63, "bisection_rounds": 6, "whole_model_peak_mb": whole["peak_rss_mb"]}}
    json.dump(out, open("results/memory_plans.json", "w"), indent=1, default=str)
    print(json.dumps(plans, indent=1, default=str))


if __name__ == "__main__":
    main()
