| Workload | System | Trace (rows x cols) | Security (bits) | Proof size (B) | Prove ms (mean ± sd) | Verify ms (mean ± sd) | Runs |
|---|---|---|---|---|---|---|---|
| composed_h1_h3 | classical | 256 x 207 | 126 | 61,659 | 111.005 ± 7.951 | 19.581 ± 1.667 | 30 |
| composed_h1_h3 | pq_sis_v3 | 256 x 226 | 126 | 71,191 | 142.170 ± 13.591 | 34.753 ± 2.649 | 30 |

| Workload | Metric | Classical mean | PQ mean | Ratio (PQ/classical) | Bound | Result |
|---|---|---|---|---|---|---|
| composed_h1_h3 | proof_bytes | 61,659.000 | 71,191.000 | 1.15x | <= 20x | PASS |
| composed_h1_h3 | prove_ms | 111.005 | 142.170 | 1.28x | <= 200x | PASS |
| composed_h1_h3 | verify_ms | 19.581 | 34.753 | 1.77x | <= 10x | PASS |
| composed_h1_h3 | **all three (pq_sis_v3)** | | | | | **PASS** |
