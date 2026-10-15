//! CLI used by the operator node (prove) and by validator nodes (verify).
//!
//!   zk_composed prove  <witness.json> <proof.bin> <statement.json> [variant] [--seed N]
//!   zk_composed verify <proof.bin> <statement.json>          exit 0 = valid
//!   zk_composed bench  <runs> > results.csv                  four variants, example instance
//!   zk_composed security                                     soundness report (JSON)
//!   zk_composed info                                         model digest and shape (JSON)
//!   zk_composed example <decision_index> <witness.json>      witness for the spec example
//! variant: pq_zk (deployed, default) | pq | classical_zk | classical

use std::time::Instant;

use p3_field::PrimeCharacteristicRing;
use p3_field::coset::TwoAdicMultiplicativeCoset;
use p3_uni_stark::{AirLayout, ConjecturedSecurity, OpeningShape, ProvenSecurity, StarkSecurityParams};
use zk_composed::config::{self, Challenge, Val};
use zk_composed::layout::ROWS;
use zk_composed::*;

fn variant(name: &str) -> Variant {
    match name {
        "pq_zk" => Variant { pq: true, zk: true },
        "pq" => Variant { pq: true, zk: false },
        "classical_zk" => Variant { pq: false, zk: true },
        "classical" => Variant { pq: false, zk: false },
        _ => panic!("unknown variant {name}"),
    }
}

fn arg_after(args: &[String], flag: &str) -> Option<String> {
    args.iter().position(|a| a == flag).and_then(|i| args.get(i + 1).cloned())
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let cmd = args.get(1).map(String::as_str).unwrap_or("info");
    match cmd {
        "prove" => {
            let w: Witness = serde_json::from_str(&std::fs::read_to_string(&args[2]).expect("witness")).expect("witness json");
            let v = variant(args.get(5).filter(|a| !a.starts_with("--")).map(String::as_str).unwrap_or("pq_zk"));
            let seed = arg_after(&args, "--seed").map(|s| s.parse().unwrap());
            let t = Instant::now();
            let (proof, st) = prove(&w, v, seed, &Tamper::default());
            let ms = t.elapsed().as_secs_f64() * 1e3;
            std::fs::write(&args[3], &proof).expect("write proof");
            std::fs::write(&args[4], serde_json::to_string(&st).unwrap()).expect("write statement");
            // `risk` (the H1 flag) is private to the operator: it is printed locally, never put in the statement
            let risk = model::decide(&w.x, &w.decision, &w.policy).2;
            println!("{}", serde_json::json!({"prove_ms": ms, "proof_bytes": proof.len(), "exec": st.exec(), "viol": st.viol(), "risk": risk}));
        }
        "verify" => {
            let proof = std::fs::read(&args[2]).expect("proof");
            let st: Statement = serde_json::from_str(&std::fs::read_to_string(&args[3]).expect("statement")).expect("statement json");
            let t = Instant::now();
            let r = verify(&proof, &st);
            let ms = t.elapsed().as_secs_f64() * 1e3;
            println!("{}", serde_json::json!({"valid": r.is_ok(), "verify_ms": ms, "error": r.err()}));
            if !matches!(verify(&proof, &st), Ok(())) {
                std::process::exit(1);
            }
        }
        "example" => {
            let i: usize = args[2].parse().unwrap();
            std::fs::write(&args[3], serde_json::to_string_pretty(&example_witness(i, 7)).unwrap()).unwrap();
        }
        "bench" => {
            let runs: usize = args.get(2).map(|s| s.parse().unwrap()).unwrap_or(30);
            let w = example_witness(1, 7);
            println!("variant,run,proof_bytes,prove_ms,verify_ms");
            for v in ["classical", "pq", "classical_zk", "pq_zk"].map(variant) {
                for run in 0..runs + 3 {
                    let t = Instant::now();
                    let (proof, st) = prove(&w, v, Some(run as u64), &Tamper::default());
                    let pms = t.elapsed().as_secs_f64() * 1e3;
                    let t = Instant::now();
                    verify(&proof, &st).expect("verify");
                    let vms = t.elapsed().as_secs_f64() * 1e3;
                    if run >= 3 {
                        println!("{},{},{},{:.3},{:.3}", v.name(), run - 3, proof.len(), pms, vms);
                    }
                }
            }
        }
        "security" => {
            let mut out = serde_json::Map::new();
            for v in ["pq", "pq_zk"].map(variant) {
                let air = ComposedAir::new(v.pq);
                let perm = config::perm();
                let mmcs = config::ValMmcs::new(config::Hash::new(perm.clone()), config::Compress::new(perm), 0);
                let fri = config::fri(config::ChallengeMmcs::new(mmcs));
                let shape = if v.zk { OpeningShape::hiding(config::RANDOM_CODEWORDS) } else { OpeningShape::new() };
                let degree_bits = ROWS.trailing_zeros() as usize;
                let params = StarkSecurityParams::from_air::<Val, Challenge, _>(
                    fri.security_regime(),
                    &air,
                    AirLayout::from_air::<Val>(&air),
                    TwoAdicMultiplicativeCoset::new(Val::ONE, degree_bits).unwrap(),
                    config::EXT_DEGREE * 31,   // challenge field BabyBear^EXT_DEGREE
                    124,      // Poseidon2 digest of 8 BabyBear elements (248 bits): 124-bit collision resistance
                    2,        // the AIR reads the current and the next row
                    shape,
                    fri.grinding_sites(),
                );
                let conj = ConjecturedSecurity::compute_from_params(&params, degree_bits + v.zk as usize);
                let proven = ProvenSecurity::compute(&params, ROWS << v.zk as usize);
                out.insert(
                    v.name().into(),
                    serde_json::json!({
                        "conjectured_bits": conj.security_bits,
                        "proven_unique_decoding_bits": proven.unique_decoding_bits,
                        "proven_list_decoding_bits": proven.list_decoding_bits,
                        "num_constraints": params.num_constraints,
                        "max_constraint_degree": params.air_max_constraint_degree,
                        "num_quotient_chunks": params.num_quotient_chunks,
                        "num_batched_functions": params.num_batched_functions,
                        "fri": {"log_blowup": config::LOG_BLOWUP, "num_queries": config::NUM_QUERIES,
                                 "query_pow_bits": config::QUERY_POW_BITS},
                    }),
                );
            }
            out.insert("sis_log2_expected_collisions".into(), serde_json::json!(sis::log2_expected_collisions()));
            println!("{}", serde_json::to_string_pretty(&out).unwrap());
        }
        _ => {
            println!(
                "{}",
                serde_json::json!({
                    "model_digest": model::model_digest(),
                    "rows": ROWS,
                    "width_classical": ComposedAir::width_of(false),
                    "width_pq": ComposedAir::width_of(true),
                    "periodic_pq": ComposedAir::new(true).num_periodic_columns_pub(),
                    "sis_rows": sis::N_SIS,
                    "sis_log2_expected_collisions": sis::log2_expected_collisions(),
                })
            );
        }
    }
}
