"""Machine resources for a dispute: whole-model proof vs one step proof (same machine, same settings).

For the circuits of bench_shared_vs_mono.py (whole 60-block model; one residual-block step and one projection-half
step, each with fixed or hashed weights) this measures what the proving machine has to hold and do:
  - circuit size (logrows: 2^logrows rows), proving-key, verifying-key and SRS (structured reference string) sizes;
  - peak resident memory of a process that only loads the key and proves (one fresh process per proof);
  - prove time, proof size and verify time.
Run bench_shared_vs_mono.py first (it builds the circuits and keys in work/shared_vs_mono/).
Usage: python3 bench_resources.py   -> results/dispute_resources.json
"""
import json
import os
import subprocess
import sys

H1 = os.path.dirname(os.path.abspath(__file__))
WD = os.path.join(H1, "work", "shared_vs_mono")
CIRCUITS = {"monolithic_60_blocks": "mono", "block_step_fixed": "blk_fixed", "block_step_hashed": "blk_hashed",
            "projection_step_fixed": "proj_fixed", "projection_step_hashed": "proj_hashed"}

PROVE = r"""
import json, resource, sys, time, ezkl
d, srs = sys.argv[1], sys.argv[2]
base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
t = time.time(); ok = ezkl.prove(d + "/witness.json", d + "/net.compiled", d + "/pk.key", d + "/proof_r.json", srs_path=srs)
prove_s = time.time() - t
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
t = time.time(); v = ezkl.verify(d + "/proof_r.json", d + "/settings.json", d + "/vk.key", srs_path=srs)
print(json.dumps({"prove_s": prove_s, "verify_s": time.time() - t, "verified": bool(v),
                  "peak_rss_mb": peak / 1024, "process_baseline_rss_mb": base / 1024}))
"""


def mb(p):
    return round(os.path.getsize(p) / 1e6, 2)


def main():
    out = {}
    for name, tag in CIRCUITS.items():
        d = os.path.join(WD, f"{tag}_d")
        s = json.load(open(os.path.join(d, "settings.json")))
        k = s["run_args"]["logrows"]
        srs = os.path.join(WD, f"srs_{k}.srs")
        r = subprocess.run([sys.executable, "-c", PROVE, d, srs], capture_output=True, text=True, check=True)
        m = json.loads(r.stdout.strip().splitlines()[-1])
        out[name] = {"logrows": k, "rows": 2 ** k, "pk_mb": mb(os.path.join(d, "pk.key")), "vk_mb": mb(os.path.join(d, "vk.key")),
                     "srs_mb": mb(srs), "proof_kb": round(os.path.getsize(os.path.join(d, "proof_r.json")) / 1024, 1),
                     "prove_s": round(m["prove_s"], 2), "verify_s": round(m["verify_s"], 3), "verified": m["verified"],
                     "peak_rss_mb": round(m["peak_rss_mb"]), "process_baseline_rss_mb": round(m["process_baseline_rss_mb"])}
        print(name, out[name], flush=True)
    mono = out["monolithic_60_blocks"]
    for name, o in out.items():
        o["vs_whole_model"] = {"rows": round(mono["rows"] / o["rows"], 1), "pk": round(mono["pk_mb"] / o["pk_mb"], 1),
                               "peak_rss": round(mono["peak_rss_mb"] / o["peak_rss_mb"], 2), "prove_time": round(mono["prove_s"] / o["prove_s"], 2)}
    out["machine"] = "2-vCPU, 8 GB cloud VM (same machine for every row); one fresh process per proof"
    json.dump(out, open(os.path.join(H1, "results", "dispute_resources.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
