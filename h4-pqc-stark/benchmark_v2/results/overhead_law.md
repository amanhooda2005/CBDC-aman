# Post-quantum overhead law (post-hoc, no new runs)

Law: `pq = classical + c`, so `ratio = 1 + c / classical`. `c` is the mean added cost over the 5 workloads; the two-parameter fit `pq = a*classical + b` checks whether the overhead is additive (a near 1) or multiplicative.

| metric | c (mean added cost) | sd across workloads | a | b | R^2 of 1-param law |
|---|---|---|---|---|---|
| verify_ms | 12.0 | 3.2 | 1.276 | 9.3 | 0.8806 |
| prove_ms | 28.5 | 9.4 | 0.943 | 31.1 | 0.9365 |
| proof_bytes | 12056.4 | 8140.8 | 0.690 | 24220.8 | 0.7251 |

| metric | workload | classical | pq_sis_v3 | added | ratio observed | ratio by law |
|---|---|---|---|---|---|---|
| verify_ms | dense_165x32 | 5.8 | 20.1 | 14.3 | 3.45 | 3.06 |
| verify_ms | h1_mlp_full | 16.3 | 31.4 | 15.1 | 1.92 | 1.74 |
| verify_ms | h2_gcn_node | 17.9 | 31.6 | 13.7 | 1.76 | 1.67 |
| verify_ms | h3_rule_x64 | 8.4 | 16.4 | 8.0 | 1.96 | 2.43 |
| verify_ms | matmul_2x2 | 1.4 | 10.6 | 9.1 | 7.40 | 9.44 |
| prove_ms | dense_165x32 | 24.5 | 63.3 | 38.8 | 2.58 | 2.17 |
| prove_ms | h1_mlp_full | 86.0 | 109.8 | 23.9 | 1.28 | 1.33 |
| prove_ms | h2_gcn_node | 86.7 | 117.6 | 30.9 | 1.36 | 1.33 |
| prove_ms | h3_rule_x64 | 29.4 | 44.2 | 14.8 | 1.51 | 1.97 |
| prove_ms | matmul_2x2 | 2.6 | 36.9 | 34.3 | 14.20 | 11.98 |
| proof_bytes | dense_165x32 | 33710.0 | 41460.0 | 7750.0 | 1.23 | 1.36 |
| proof_bytes | h1_mlp_full | 54998.0 | 65149.0 | 10151.0 | 1.18 | 1.22 |
| proof_bytes | h2_gcn_node | 61549.0 | 69535.0 | 7986.0 | 1.13 | 1.20 |
| proof_bytes | h3_rule_x64 | 39493.0 | 47377.0 | 7884.0 | 1.20 | 1.31 |
| proof_bytes | matmul_2x2 | 6683.0 | 33194.0 | 26511.0 | 4.97 | 2.80 |

## Note on proof size
Workloads whose PQ trace is longer than their classical trace (matmul_2x2) pay a trace-length floor: the SIS block needs its own rows, so a tiny statement is padded up. Excluding them, the added size is 7750-10151 bytes (mean 8443), i.e. a ratio of 1.13-1.23.
