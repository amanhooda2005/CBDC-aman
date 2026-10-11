mod classical;
mod dense;
mod params;
mod pq;
mod rlc;
mod sis;

use std::time::Instant;

use winterfell::{FieldExtension, HashFunction, ProofOptions, Prover, StarkProof};

use classical::{ClassicalAir, ClassicalProver};
use params::{amount_bits, commit, Workload, AMOUNT_BITS, N_RAND, SIS_ROWS};
use pq::{PqAir, PqProver};
use rlc::{RlcAir, RlcProver};

/// Identical options for both circuits: 42 queries x log2(8) = 126 bits + 16 grinding,
/// sextic extension (138-bit field), Blake3-256. Winterfell reports the resulting
/// conjectured security per proof (limited by field size minus LDE domain bits).
pub fn options() -> ProofOptions {
    ProofOptions::new(42, 8, 16, HashFunction::Blake3_256, FieldExtension::Sextic, 4, 64)
}

pub fn honest_opening(amount: u64) -> ([i64; AMOUNT_BITS], [i64; N_RAND]) {
    let bits = amount_bits(amount).map(|b| b as i64);
    let mut r = [0i64; N_RAND];
    let mut state: u64 = 0x5eed_1234;
    for v in r.iter_mut() {
        state = state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        *v = ((state >> 33) % 3) as i64 - 1;
    }
    (bits, r)
}

pub fn pq_prover(workload: Workload, amount: u64) -> PqProver {
    let (bits, r) = honest_opening(amount);
    PqProver {
        options: options(),
        workload,
        commitment: commit(&bits, &r),
        bits,
        r,
        out_override: None,
        dense_y: dense::output(&dense::input()),
    }
}

struct Measurement {
    bytes: usize,
    prove_ms: f64,
    verify_ms: f64,
    security: u32,
}

fn time_pq(prover: &PqProver) -> Measurement {
    let trace = prover.build_trace();
    let t = Instant::now();
    let proof: StarkProof = prover.prove(trace).expect("PQ proof generation failed");
    let prove_ms = t.elapsed().as_secs_f64() * 1e3;
    let (bytes, security) = (proof.to_bytes().len(), proof.security_level(true));
    let t = Instant::now();
    winterfell::verify::<PqAir>(proof, prover.statement()).expect("PQ verification failed");
    Measurement { bytes, prove_ms, verify_ms: t.elapsed().as_secs_f64() * 1e3, security }
}

fn time_classical(prover: &ClassicalProver) -> Measurement {
    let trace = prover.build_trace();
    let t = Instant::now();
    let proof: StarkProof = prover.prove(trace).expect("classical proof generation failed");
    let prove_ms = t.elapsed().as_secs_f64() * 1e3;
    let (bytes, security) = (proof.to_bytes().len(), proof.security_level(true));
    let t = Instant::now();
    winterfell::verify::<ClassicalAir>(proof, prover.claim()).expect("classical verification failed");
    Measurement { bytes, prove_ms, verify_ms: t.elapsed().as_secs_f64() * 1e3, security }
}

pub fn rlc_prover(workload: Workload, amount: u64) -> RlcProver {
    let (bits, r) = honest_opening(amount);
    RlcProver {
        options: options(),
        workload,
        commitment: commit(&bits, &r),
        bits,
        r,
        out_override: None,
        dense_y: dense::output(&dense::input()),
    }
}

fn time_rlc(prover: &RlcProver) -> Measurement {
    let trace = prover.build_trace();
    let t = Instant::now();
    let proof: StarkProof = prover.prove(trace).expect("v3 proof generation failed");
    let prove_ms = t.elapsed().as_secs_f64() * 1e3;
    let (bytes, security) = (proof.to_bytes().len(), proof.security_level(true));
    let t = Instant::now();
    winterfell::verify::<RlcAir>(proof, prover.statement()).expect("v3 verification failed");
    Measurement { bytes, prove_ms, verify_ms: t.elapsed().as_secs_f64() * 1e3, security }
}

fn main() {
    let runs: usize = std::env::args().nth(1).and_then(|s| s.parse().ok()).unwrap_or(30);
    let v3 = std::env::args().nth(2).as_deref() == Some("v3");
    let warmup = 3;
    eprintln!(
        "H4 benchmark v2: SIS n={} rows, {} amount bits, {} ternary r, p={}; {} warmup + {} measured runs per system",
        SIS_ROWS, AMOUNT_BITS, N_RAND, params::modulus(), warmup, runs
    );
    println!("workload,system,run,trace_rows,trace_width,proof_bytes,prove_ms,verify_ms,conjectured_security_bits");
    for workload in [Workload::Matmul, Workload::Dense] {
        let classical = ClassicalProver::new(options(), workload);
        let pq = pq_prover(workload, 123_456_789);
        let pq3 = rlc_prover(workload, 123_456_789);
        for run in 0..warmup + runs {
            // Interleave the two systems so drift in machine load affects both equally.
            let c = time_classical(&classical);
            let p = if v3 { time_rlc(&pq3) } else { time_pq(&pq) };
            if run >= warmup {
                let n = run - warmup + 1;
                println!("{},classical,{},{},{},{},{:.4},{:.4},{}", workload.name(), n, classical::trace_len(workload), classical::width(workload), c.bytes, c.prove_ms, c.verify_ms, c.security);
                let (label, rows, width) = if v3 { ("pq_sis_v3", rlc::trace_len(workload), rlc::main_width(workload) + 6) } else { ("pq_sis", pq::trace_len(workload), pq::width(workload)) };
                println!("{},{},{},{},{},{},{:.4},{:.4},{}", workload.name(), label, n, rows, width, p.bytes, p.prove_ms, p.verify_ms, p.security);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::panic::{catch_unwind, AssertUnwindSafe};
    use winterfell::{
        math::{fields::f23201::BaseElement, FieldElement},
        Air, EvaluationFrame, TraceInfo, TraceTable,
    };

    const WORKLOADS: [Workload; 2] = [Workload::Matmul, Workload::Dense];

    /// Evaluates every transition constraint on every row (with the verifier-side periodic
    /// values) and every assertion. This checks the AIR itself, independent of prover internals.
    fn air_satisfied<A: Air<BaseField = BaseElement>>(air: &A, trace: &TraceTable<BaseElement>, width: usize, len: usize, n_constraints: usize) -> bool {
        let periodic = air.get_periodic_column_values();
        let mut result = vec![BaseElement::ZERO; n_constraints];
        for row in 0..len - 1 {
            let cur: Vec<BaseElement> = (0..width).map(|c| trace.get(c, row)).collect();
            let next: Vec<BaseElement> = (0..width).map(|c| trace.get(c, row + 1)).collect();
            let values: Vec<BaseElement> = periodic.iter().map(|col| col[row % col.len()]).collect();
            air.evaluate_transition(&EvaluationFrame::from_rows(cur, next), &values, &mut result);
            if result.iter().any(|v| *v != BaseElement::ZERO) {
                return false;
            }
        }
        air.get_assertions().iter().all(|a| {
            let steps: Vec<usize> = if a.stride() == 0 { vec![a.first_step()] } else { (a.first_step()..len).step_by(a.stride()).collect() };
            steps.iter().enumerate().all(|(i, &step)| trace.get(a.column(), step) == a.values()[i.min(a.values().len() - 1)])
        })
    }

    fn pq_air_ok(p: &PqProver) -> bool {
        let (w, len) = (pq::width(p.workload), pq::trace_len(p.workload));
        let air = PqAir::new(TraceInfo::new(w, len), p.statement(), options());
        air_satisfied(&air, &p.build_trace(), w, len, pq::num_constraints(p.workload))
    }

    fn classical_air_ok(p: &ClassicalProver) -> bool {
        let (w, len) = (classical::width(p.workload), classical::trace_len(p.workload));
        let air = ClassicalAir::new(TraceInfo::new(w, len), p.claim(), options());
        air_satisfied(&air, &p.build_trace(), w, len, classical::num_constraints(p.workload))
    }

    fn malformed(workload: Workload, commit_bits: [i64; AMOUNT_BITS], commit_r: [i64; N_RAND], bits: [i64; AMOUNT_BITS], r: [i64; N_RAND]) -> PqProver {
        let mut p = pq_prover(workload, 0);
        p.commitment = commit(&commit_bits, &commit_r);
        p.bits = bits;
        p.r = r;
        p
    }

    /// True only if a proof is produced and verifies. Provers panic on traces that violate the
    /// AIR (composition-degree check); that also counts as rejection.
    fn pq_accepted(prover: PqProver) -> bool {
        let statement = prover.statement();
        catch_unwind(AssertUnwindSafe(|| match prover.prove(prover.build_trace()) {
            Ok(proof) => winterfell::verify::<PqAir>(proof, statement).is_ok(),
            Err(_) => false,
        }))
        .unwrap_or(false)
    }

    fn classical_accepted(prover: ClassicalProver) -> bool {
        let claim = prover.claim();
        catch_unwind(AssertUnwindSafe(|| match prover.prove(prover.build_trace()) {
            Ok(proof) => winterfell::verify::<ClassicalAir>(proof, claim).is_ok(),
            Err(_) => false,
        }))
        .unwrap_or(false)
    }

    #[test]
    fn air_accepts_honest_traces_for_both_workloads() {
        for w in WORKLOADS {
            assert!(pq_air_ok(&pq_prover(w, 123_456_789)), "{:?}", w);
            assert!(classical_air_ok(&ClassicalProver::new(options(), w)), "{:?}", w);
        }
    }

    #[test]
    fn air_rejects_every_lattice_forgery_for_both_workloads() {
        for w in WORKLOADS {
            let (b1000, r) = honest_opening(1000);
            let (b999, _) = honest_opening(999);
            assert!(!pq_air_ok(&malformed(w, b1000, r, b999, r)), "{:?}: different amount, same commitment", w);
            let (b, mut r5) = honest_opening(42);
            r5[0] = 5;
            assert!(!pq_air_ok(&malformed(w, b, r5, b, r5)), "{:?}: r = 5 that genuinely opens c", w);
            let (b, mut r2) = honest_opening(42);
            r2[10] = 2;
            assert!(!pq_air_ok(&malformed(w, b, r2, b, r2)), "{:?}: r = 2 that genuinely opens c", w);
            let (mut b2, r) = honest_opening(42);
            b2[3] = 2;
            assert!(!pq_air_ok(&malformed(w, b2, r, b2, r)), "{:?}: non-boolean bit that genuinely opens c", w);
        }
    }

    #[test]
    fn air_rejects_wrong_workload_results() {
        let mut p = pq_prover(Workload::Matmul, 42);
        p.out_override = Some([20, 22, 43, 50]);
        assert!(!pq_air_ok(&p), "pq: wrong matrix product");
        let mut c = ClassicalProver::new(options(), Workload::Matmul);
        c.out_override = Some([20, 22, 43, 50]);
        assert!(!classical_air_ok(&c), "classical: wrong matrix product");
        let mut p = pq_prover(Workload::Dense, 42);
        p.dense_y[0] = (p.dense_y[0] + 1) % params::modulus();
        assert!(!pq_air_ok(&p), "pq: wrong dense output");
        let mut c = ClassicalProver::new(options(), Workload::Dense);
        c.dense_y[5] = (c.dense_y[5] + 1) % params::modulus();
        assert!(!classical_air_ok(&c), "classical: wrong dense output");
    }

    #[test]
    fn end_to_end_honest_accepted_and_forgeries_rejected() {
        for w in WORKLOADS {
            assert!(pq_accepted(pq_prover(w, 123_456_789)), "{:?}", w);
            assert!(classical_accepted(ClassicalProver::new(options(), w)), "{:?}", w);
            let (b1000, r) = honest_opening(1000);
            let (b999, _) = honest_opening(999);
            assert!(!pq_accepted(malformed(w, b1000, r, b999, r)), "{:?}", w);
            let (b, mut r5) = honest_opening(42);
            r5[0] = 5;
            assert!(!pq_accepted(malformed(w, b, r5, b, r5)), "{:?}", w);
        }
        let mut c = ClassicalProver::new(options(), Workload::Dense);
        c.dense_y[0] = (c.dense_y[0] + 1) % params::modulus();
        assert!(!classical_accepted(c));
    }

    // ---------------------------------------------------------------- v3 (random linear combination)

    fn rlc_air_ok(p: &RlcProver, gamma: BaseElement) -> bool {
        use winterfell::{AuxTraceRandElements, Trace};
        let mut trace = p.build_trace();
        let len = trace.length();
        let width = rlc::main_width(p.workload);
        let aux = trace.build_aux_segment::<BaseElement>(&[], &[gamma]).expect("aux segment");
        let air = RlcAir::new(TraceInfo::new_multi_segment(trace.layout().clone(), len, vec![]), p.statement(), options());
        let mut rand = AuxTraceRandElements::new();
        rand.add_segment_elements(vec![gamma]);
        let periodic = air.get_periodic_column_values();
        let mut main_out = vec![BaseElement::ZERO; rlc::num_main_constraints(p.workload)];
        let mut aux_out = vec![BaseElement::ZERO; 2];
        let main = trace.main_segment();
        for row in 0..len - 1 {
            let cur: Vec<BaseElement> = (0..width).map(|c| main.get(c, row)).collect();
            let next: Vec<BaseElement> = (0..width).map(|c| main.get(c, row + 1)).collect();
            let values: Vec<BaseElement> = periodic.iter().map(|col| col[row % col.len()]).collect();
            let frame = EvaluationFrame::from_rows(cur, next);
            air.evaluate_transition(&frame, &values, &mut main_out);
            let aux_frame = EvaluationFrame::from_rows(vec![aux.get(0, row)], vec![aux.get(0, row + 1)]);
            air.evaluate_aux_transition(&frame, &aux_frame, &values, &rand, &mut aux_out);
            if main_out.iter().chain(aux_out.iter()).any(|v| *v != BaseElement::ZERO) {
                return false;
            }
        }
        let main_ok = air.get_assertions().iter().all(|a| main.get(a.column(), a.first_step()) == a.values()[0]);
        let aux_ok = air.get_aux_assertions(&rand).iter().all(|a| aux.get(a.column(), a.first_step()) == a.values()[0]);
        main_ok && aux_ok
    }

    fn rlc_malformed(workload: Workload, commit_bits: [i64; AMOUNT_BITS], commit_r: [i64; N_RAND], bits: [i64; AMOUNT_BITS], r: [i64; N_RAND]) -> RlcProver {
        let mut p = rlc_prover(workload, 0);
        p.commitment = commit(&commit_bits, &commit_r);
        p.bits = bits;
        p.r = r;
        p
    }

    fn rlc_accepted(prover: RlcProver) -> bool {
        let statement = prover.statement();
        catch_unwind(AssertUnwindSafe(|| match prover.prove(prover.build_trace()) {
            Ok(proof) => winterfell::verify::<RlcAir>(proof, statement).is_ok(),
            Err(_) => false,
        }))
        .unwrap_or(false)
    }

    const GAMMAS: [u64; 3] = [12_345, 987_654, 4_000_001];

    #[test]
    fn v3_commitment_is_identical_to_v2() {
        // Same statement: v3 proves the very same commitment v2 does.
        for w in WORKLOADS {
            assert_eq!(rlc_prover(w, 123_456_789).commitment, pq_prover(w, 123_456_789).commitment);
        }
    }

    #[test]
    fn v3_air_accepts_honest_traces() {
        for w in WORKLOADS {
            for g in GAMMAS {
                assert!(rlc_air_ok(&rlc_prover(w, 123_456_789), BaseElement::from(g)), "{:?} gamma {}", w, g);
            }
        }
    }

    #[test]
    fn v3_air_rejects_every_forgery() {
        for w in WORKLOADS {
            for g in GAMMAS {
                let g = BaseElement::from(g);
                let (b1000, r) = honest_opening(1000);
                let (b999, _) = honest_opening(999);
                assert!(!rlc_air_ok(&rlc_malformed(w, b1000, r, b999, r), g), "{:?}: different amount", w);
                let (b, mut r5) = honest_opening(42);
                r5[0] = 5;
                assert!(!rlc_air_ok(&rlc_malformed(w, b, r5, b, r5), g), "{:?}: r = 5", w);
                let (b, mut r2) = honest_opening(42);
                r2[10] = 2;
                assert!(!rlc_air_ok(&rlc_malformed(w, b, r2, b, r2), g), "{:?}: r = 2", w);
                let (mut b2, r) = honest_opening(42);
                b2[3] = 2;
                assert!(!rlc_air_ok(&rlc_malformed(w, b2, r, b2, r), g), "{:?}: bit = 2", w);
                let (mut bm, r) = honest_opening(42);
                bm[0] = -1;
                assert!(!rlc_air_ok(&rlc_malformed(w, bm, r, bm, r), g), "{:?}: bit = -1 (ternary in a bit slot)", w);
            }
        }
        let mut p = rlc_prover(Workload::Matmul, 42);
        p.out_override = Some([20, 22, 43, 50]);
        assert!(!rlc_air_ok(&p, BaseElement::from(12_345u64)), "wrong matrix product");
        let mut p = rlc_prover(Workload::Dense, 42);
        p.dense_y[0] = (p.dense_y[0] + 1) % params::modulus();
        assert!(!rlc_air_ok(&p, BaseElement::from(12_345u64)), "wrong dense output");
    }

    #[test]
    fn v3_end_to_end_honest_accepted_and_forgeries_rejected() {
        for w in WORKLOADS {
            assert!(rlc_accepted(rlc_prover(w, 123_456_789)), "{:?}", w);
            let (b1000, r) = honest_opening(1000);
            let (b999, _) = honest_opening(999);
            assert!(!rlc_accepted(rlc_malformed(w, b1000, r, b999, r)), "{:?}", w);
            let (b, mut r5) = honest_opening(42);
            r5[0] = 5;
            assert!(!rlc_accepted(rlc_malformed(w, b, r5, b, r5)), "{:?}", w);
        }
    }

    #[test]
    fn v3_options_reach_at_least_120_bits() {
        for w in WORKLOADS {
            let p = rlc_prover(w, 7);
            let s = p.prove(p.build_trace()).unwrap().security_level(true);
            assert!(s >= 120, "v3 {:?}: {} bits", w, s);
        }
    }

    #[test]
    fn options_reach_at_least_120_bits_for_every_circuit() {
        for w in WORKLOADS {
            let p = pq_prover(w, 7);
            let s = p.prove(p.build_trace()).unwrap().security_level(true);
            assert!(s >= 120, "pq {:?}: {} bits", w, s);
            let c = ClassicalProver::new(options(), w);
            let s = c.prove(c.build_trace()).unwrap().security_level(true);
            assert!(s >= 120, "classical {:?}: {} bits", w, s);
        }
    }
}
