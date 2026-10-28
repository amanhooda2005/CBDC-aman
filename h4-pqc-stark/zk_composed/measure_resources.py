"""Peak memory of the composed prover (operator node) and verifier (validator node / payment device), one fresh
process each, plus binary, proof and statement sizes.
Usage: python3 measure_resources.py [path/to/zk_composed] [runs]   -> results/resources.json"""
import json, os, subprocess, sys, tempfile
B = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "target/release/zk_composed")
N = int(sys.argv[2]) if len(sys.argv) > 2 else 5
d = tempfile.mkdtemp(); w, p, s = (os.path.join(d, n) for n in ("w.json", "p.bin", "s.json"))
subprocess.run([B, "example", "1", w], check=True)
def measure(args):
    peaks, last = [], None
    for _ in range(N):
        proc = subprocess.Popen([B] + args, stdout=subprocess.PIPE, text=True)
        _, status, ru = os.wait4(proc.pid, 0)
        assert status == 0, args
        last = json.loads(proc.stdout.read().strip().splitlines()[-1]); peaks.append(ru.ru_maxrss / 1024)
    return {"peak_rss_mb_max": round(max(peaks), 1), "runs": N, "last": last}
res = {"prove": measure(["prove", w, p, s]), "verify": measure(["verify", p, s])}
stripped = os.path.join(d, "zk"); subprocess.run(["strip", "-o", stripped, B], check=True)
res.update({"binary_mb": round(os.path.getsize(B) / 1e6, 2), "binary_stripped_mb": round(os.path.getsize(stripped) / 1e6, 2),
            "proof_bytes": os.path.getsize(p), "statement_bytes": os.path.getsize(s),
            "machine": "2-vCPU, 8 GB cloud VM (x86-64); variant pq_zk (deployed)"})
json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "resources.json"), "w"), indent=1)
print(json.dumps(res, indent=1))
