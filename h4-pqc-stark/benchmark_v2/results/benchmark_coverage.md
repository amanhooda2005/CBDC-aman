| Workload | System | Trace (rows x cols) | Security (bits) | Proof size (B) | Prove ms (mean ± sd) | Verify ms (mean ± sd) | Runs |
|---|---|---|---|---|---|---|---|
| h1_mlp_full | classical | 256 x 166 | 126 | 54,998 | 85.953 ± 6.367 | 16.345 ± 1.228 | 30 |
| h1_mlp_full | pq_sis_v3 | 256 x 184 | 126 | 65,149 | 109.835 ± 8.575 | 31.401 ± 2.205 | 30 |
| h2_gcn_node | classical | 256 x 203 | 126 | 61,549 | 86.713 ± 3.394 | 17.930 ± 1.833 | 30 |
| h2_gcn_node | pq_sis_v3 | 256 x 221 | 126 | 69,535 | 117.621 ± 4.292 | 31.630 ± 1.623 | 30 |
| h3_rule_x64 | classical | 128 x 108 | 127 | 39,493 | 29.365 ± 1.657 | 8.402 ± 0.695 | 30 |
| h3_rule_x64 | pq_sis_v3 | 128 x 126 | 127 | 47,377 | 44.203 ± 3.252 | 16.446 ± 1.109 | 30 |

| Workload | Metric | Classical mean | PQ mean | Ratio (PQ/classical) | Bound | Result |
|---|---|---|---|---|---|---|
| h1_mlp_full | proof_bytes | 54,998.000 | 65,149.000 | 1.18x | <= 20x | PASS |
| h1_mlp_full | prove_ms | 85.953 | 109.835 | 1.28x | <= 200x | PASS |
| h1_mlp_full | verify_ms | 16.345 | 31.401 | 1.92x | <= 10x | PASS |
| h1_mlp_full | **all three (pq_sis_v3)** | | | | | **PASS** |
| h2_gcn_node | proof_bytes | 61,549.000 | 69,535.000 | 1.13x | <= 20x | PASS |
| h2_gcn_node | prove_ms | 86.713 | 117.621 | 1.36x | <= 200x | PASS |
| h2_gcn_node | verify_ms | 17.930 | 31.630 | 1.76x | <= 10x | PASS |
| h2_gcn_node | **all three (pq_sis_v3)** | | | | | **PASS** |
| h3_rule_x64 | proof_bytes | 39,493.000 | 47,377.000 | 1.20x | <= 20x | PASS |
| h3_rule_x64 | prove_ms | 29.365 | 44.203 | 1.51x | <= 200x | PASS |
| h3_rule_x64 | verify_ms | 8.402 | 16.446 | 1.96x | <= 10x | PASS |
| h3_rule_x64 | **all three (pq_sis_v3)** | | | | | **PASS** |
