"""Same-machine comparison: monolithic 60-block proof vs one dispute step, fixed vs hashed (shared) weights.
Usage: python3 bench_shared_vs_mono.py [DATA_DIR]   -> results/same_machine_step_vs_mono.json (2 trials each)"""
import json, os, pickle, subprocess, sys
import numpy as np
H1 = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, H1); os.chdir(H1)
from data import load, temporal_split, apply_scaler
from onnx_builder import build, dispute_steps
from steps import quantize_steps, run_steps
from quant import S
WD = os.path.join(H1, "work", "shared_vs_mono"); os.makedirs(WD, exist_ok=True)
DATA = sys.argv[1] if len(sys.argv) > 1 else "."   # folder with X.npy, y.npy, ts.npy (prepare_data.py)
m = pickle.load(open("models/W32_B60.pkl", "rb"))
X, y, ts = load(DATA); _, _, (Xte, yte), _ = temporal_split(X, y, ts); Xte = apply_scaler(Xte, m["scaler"])
steps = quantize_steps(dispute_steps(m["units"]))
rng = np.random.default_rng(0); cidx = rng.choice(len(Xte), 16, replace=False); sample = int(np.where(yte == 1)[0][0])
cx, couts = run_steps(steps, Xte[cidx]); sx, souts = run_steps(steps, Xte[sample:sample + 1])
deq = lambda a: a / 2 ** S
def prove(onnx, inputs, calib, name, iv, ov, pv, ic):
    json.dump({"input_data": [deq(c).reshape(-1).tolist() for c in calib]}, open(f"{WD}/{name}_calib.json", "w"))
    json.dump([deq(a).reshape(-1).tolist() for a in inputs], open(f"{WD}/{name}_in.json", "w"))
    r = subprocess.run(["python3", "prover.py", onnx, f"{WD}/{name}_in.json", f"{WD}/{name}_d", iv, ov, "2", f"{WD}/{name}_calib.json"],
                       capture_output=True, text=True, env=dict(os.environ, ZK_INNER_COLS=str(ic), ZK_PARAM_VIS=pv))
    line = [l for l in r.stdout.splitlines() if l.startswith("RESULT_JSON")]
    res = json.loads(line[0][12:]); print(name, pv, "logrows", res["logrows"], "prove_s", [round(t, 2) for t in res["prove_times"]], "ok", res["verified"], flush=True)
    return {"weights": pv, "logrows": res["logrows"], "prove_times_s": res["prove_times"], "prove_mean_s": res["prove_mean_s"], "verified": res["verified"]}
out = {}
build(m["units"], f"{WD}/mono.onnx"); out["monolithic_60_blocks"] = prove(f"{WD}/mono.onnx", [sx], [cx], "mono", "public", "public", "fixed", 2)
k = 31; build([steps[k]["unit"]], f"{WD}/blk.onnx")   # u30, a residual block
for pv in ("fixed", "hashed"):
    out[f"block_step_{pv}"] = prove(f"{WD}/blk.onnx", [souts[k - 1]], [couts[k - 1]], f"blk_{pv}", "hashed", "hashed", pv, 2)
build([steps[0]["unit"]], f"{WD}/proj.onnx")
for pv in ("fixed", "hashed"):
    out[f"projection_step_{pv}"] = prove(f"{WD}/proj.onnx", [sx], [cx], f"proj_{pv}", "public", "hashed", pv, 4)
out["machine"] = "2-vCPU, 8 GB cloud VM (same machine for every row)"
json.dump(out, open("results/same_machine_step_vs_mono.json", "w"), indent=1)
