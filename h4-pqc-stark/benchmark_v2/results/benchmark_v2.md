| Workload | System | Trace (rows x cols) | Security (bits) | Proof size (B) | Prove ms (mean ± sd) | Verify ms (mean ± sd) | Runs |
|---|---|---|---|---|---|---|---|
| dense_165x32 | classical | 256 x 33 | 126 | 33,710 | 22.390 ± 1.553 | 5.152 ± 0.491 | 30 |
| dense_165x32 | pq_sis | 512 x 180 | 125 | 63,539 | 221.749 ± 5.286 | 22.535 ± 1.151 | 30 |
| matmul_2x2 | classical | 8 x 17 | 128 | 6,683 | 2.586 ± 0.195 | 1.367 ± 0.163 | 30 |
| matmul_2x2 | pq_sis | 512 x 164 | 125 | 60,844 | 184.861 ± 5.612 | 19.282 ± 0.752 | 30 |

| Workload | Metric | Classical mean | PQ mean | Ratio (PQ/classical) | Bound | Result |
|---|---|---|---|---|---|---|
| dense_165x32 | proof_bytes | 33,710.000 | 63,539.000 | 1.88x | <= 20x | PASS |
| dense_165x32 | prove_ms | 22.390 | 221.749 | 9.90x | <= 200x | PASS |
| dense_165x32 | verify_ms | 5.152 | 22.535 | 4.37x | <= 10x | PASS |
| dense_165x32 | **all three (pq_sis)** | | | | | **PASS** |
| matmul_2x2 | proof_bytes | 6,683.000 | 60,844.000 | 9.10x | <= 20x | PASS |
| matmul_2x2 | prove_ms | 2.586 | 184.861 | 71.50x | <= 200x | PASS |
| matmul_2x2 | verify_ms | 1.367 | 19.282 | 14.10x | <= 10x | FAIL |
| matmul_2x2 | **all three (pq_sis)** | | | | | **FAIL** |
