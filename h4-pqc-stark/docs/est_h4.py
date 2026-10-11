"""Lattice-estimator run for the H4 SIS parameters.

Reproduce (writes h4_lattice_estimator.json next to this file):
    git clone https://github.com/malb/lattice-estimator   # tested at commit 53da598
    docker run --rm -v "$PWD:/work" sagemath/sagemath:latest sage -python /work/est_h4.py
with this script and the lattice-estimator checkout both in the mounted directory.
"""
import json
import sys

sys.path.insert(0, "/work/lattice-estimator")
from sage.all import oo, sqrt, log  # noqa: E402
from estimator import SIS, RC  # noqa: E402
from estimator.reduction import ADPS16  # noqa: E402

CASES = {
    "v2_inf": dict(n=80, q=7340033, m=512, length_bound=2, norm=oo),
    "v2_l2": dict(n=80, q=7340033, m=512, length_bound=float(2 * sqrt(512)), norm=2),
    "v1_inf": dict(n=128, q=8380417, m=256 + 4, length_bound=2 * 131071, norm=oo),
}
MODELS = {
    "MATZOV (default)": RC.MATZOV,
    "ADPS16 classical": RC.ADPS16,
    "ADPS16 quantum": ADPS16(mode="quantum"),
    "ChaLoy21 quantum": RC.ChaLoy21,
}

out = {}
for name, kw in CASES.items():
    params = SIS.Parameters(**kw)
    out[name] = {"params": {k: str(v) for k, v in kw.items()}}
    for label, model in MODELS.items():
        try:
            res = SIS.estimate(params, red_cost_model=model, quiet=True)
            best = min(res.items(), key=lambda kv: kv[1]["rop"])
            out[name][label] = {
                "best_attack": best[0],
                "log2_rop": float(log(best[1]["rop"], 2)),
                "beta": int(best[1].get("beta", -1)) if best[1].get("beta") is not None else None,
                "all": {a: float(log(c["rop"], 2)) for a, c in res.items()},
            }
        except Exception as exc:  # report, never hide
            out[name][label] = {"error": repr(exc)}
        print(name, label, out[name][label], flush=True)

with open("/work/h4_lattice_estimator.json", "w") as handle:
    json.dump(out, handle, indent=2)
print("done")
