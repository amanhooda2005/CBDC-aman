| Workload | System | Trace (rows x cols) | Security (bits) | Proof size (B) | Prove ms (mean ± sd) | Verify ms (mean ± sd) | Runs |
|---|---|---|---|---|---|---|---|
| dense_165x32 | classical | 256 x 33 | 126 | 33,710 | 24.495 ± 2.135 | 5.838 ± 0.969 | 30 |
| dense_165x32 | pq_sis_v3 | 256 x 51 | 126 | 41,460 | 63.289 ± 3.279 | 20.135 ± 1.118 | 30 |
| matmul_2x2 | classical | 8 x 17 | 128 | 6,683 | 2.599 ± 0.169 | 1.427 ± 0.286 | 30 |
| matmul_2x2 | pq_sis_v3 | 128 x 35 | 127 | 33,194 | 36.907 ± 3.061 | 10.566 ± 0.779 | 30 |

| Workload | Metric | Classical mean | PQ mean | Ratio (PQ/classical) | Bound | Result |
|---|---|---|---|---|---|---|
| dense_165x32 | proof_bytes | 33,710.000 | 41,460.000 | 1.23x | <= 20x | PASS |
| dense_165x32 | prove_ms | 24.495 | 63.289 | 2.58x | <= 200x | PASS |
| dense_165x32 | verify_ms | 5.838 | 20.135 | 3.45x | <= 10x | PASS |
| dense_165x32 | **all three (pq_sis_v3)** | | | | | **PASS** |
| matmul_2x2 | proof_bytes | 6,683.000 | 33,194.000 | 4.97x | <= 20x | PASS |
| matmul_2x2 | prove_ms | 2.599 | 36.907 | 14.20x | <= 200x | PASS |
| matmul_2x2 | verify_ms | 1.427 | 10.566 | 7.40x | <= 10x | PASS |
| matmul_2x2 | **all three (pq_sis_v3)** | | | | | **PASS** |
