"""Summarize results/bench_zk_composed.csv (30 measured runs per variant after 3 warm-ups)."""
import collections, csv, json, statistics as st, sys
rows = list(csv.DictReader(open(sys.argv[1] if len(sys.argv) > 1 else "results/bench_zk_composed.csv")))
g = collections.defaultdict(list)
for r in rows:
    g[r["variant"]].append(r)
summ = {}
for v, rs in g.items():
    p = [float(r["prove_ms"]) for r in rs]; q = [float(r["verify_ms"]) for r in rs]
    summ[v] = dict(proof_bytes=int(rs[0]["proof_bytes"]), runs=len(rs), prove_ms_mean=st.mean(p), prove_ms_sd=st.stdev(p),
                   prove_ms_median=st.median(p), verify_ms_mean=st.mean(q), verify_ms_sd=st.stdev(q), verify_ms_median=st.median(q))
def ratio(a, b):
    return {m: round(summ[a][k] / summ[b][k], 2) for m, k in (("size", "proof_bytes"), ("prove", "prove_ms_mean"), ("verify", "verify_ms_mean"))}
out = dict(variants=summ, ratios={"pq/classical": ratio("pq", "classical"), "pq_zk/classical_zk": ratio("pq_zk", "classical_zk"),
                                  "classical_zk/classical": ratio("classical_zk", "classical"), "pq_zk/classical": ratio("pq_zk", "classical")})
print(json.dumps(out, indent=1))
