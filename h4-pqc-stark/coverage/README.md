# Generating the coverage specs

These scripts produce `benchmark_v2/specs/*.json`, the exact integer models and instances the circuits prove. Elliptic CSVs come from the PyG mirror (`https://data.pyg.org/datasets/elliptic/<name>.csv.zip`), or Kaggle. Do not commit them.

## H1 — `quantize_h1.py`

Run in a working directory that contains:
- `h1_data.py` (a copy of `h1-zkopml-v2/data.py`)
- `W32_B1.pkl` (from `h1-zkopml-v2/models/`)
- `X.npy`, `y.npy`, `ts.npy` (from `python h1-zkopml-v2/prepare_data.py <elliptic csv dir>`)

Then run `python quantize_h1.py`. It writes `h1_spec.json` and prints float vs quantized test metrics.

## H2 — `quantize_h2.py`

1. In a copy of `h2-federated-aml/`, place the CSVs in `data/elliptic1/`.
2. Run `python src/main.py prepare-data`.
3. Run `python src/main.py centralized --seed 42`. This trains the GCN and writes `results/centralized_gcn.pt`.
4. From that copy, run `python ../quantize_h2.py 36333 <clip>` for clip ∈ {0.1, 0.5, 1.0}. Each writes `../h2_spec_clip<clip>.json`.
5. Keep the file with the highest `val_metrics` (validation PR-AUC, then F1) as `h2_spec.json`. That is clip 0.1.

## H3 — `export_h3.py`

Run `python export_h3.py <h3-abt/src> <h3-abt/configs/experiment.yaml>`. It writes `h3_spec.json` and prints the agreement of the fixed-point rule with H3's float decisions over all 1,000 treatment decisions.

## Check

Run `cd benchmark_v2 && cargo test --release`. The coverage tests confirm the Rust integer reference reproduces each spec exactly, and that the AIR accepts the honest instance and rejects forgeries.
