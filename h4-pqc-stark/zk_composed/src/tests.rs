//! Soundness tests: the honest trace satisfies every constraint and proves/verifies in all four
//! variants; each forgery violates a constraint (checked with p3_air::check_constraints, which is
//! what the prover's debug check runs) or fails verification.

use std::panic::{AssertUnwindSafe, catch_unwind};

use p3_baby_bear::BabyBear;
use p3_field::PrimeCharacteristicRing;

use super::*;
use crate::layout::*;

fn constraints_hold(w: &Witness, pq: bool, tamper: &Tamper, pv: Option<Vec<BabyBear>>) -> bool {
    let air = ComposedAir::new(pq);
    let tr = trace::build(w, pq, tamper);
    let pv = pv.unwrap_or_else(|| trace::public_values(w, pq));
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(|_| {}));
    let ok = catch_unwind(AssertUnwindSafe(|| p3_air::check_constraints(&air, &tr, &pv))).is_ok();
    std::panic::set_hook(prev);
    ok
}

fn honest() -> Witness {
    example_witness(1, 7)
}

#[test]
fn reference_matches_benchmark_v2_instance() {
    // benchmark_v2 composed instance: flagged H1 transaction + decision 1 -> RISK = 1, VIOL = 1
    let w = honest();
    let (exec, viol, risk) = model::decide(&w.x, &w.decision, &w.policy);
    assert_eq!((risk, viol), (1, 1));
    assert_eq!(model::h1_forward(&w.x).2, 11040);   // logit recorded in specs/h1_spec.json
    assert_eq!(exec, 0);
}

#[test]
fn honest_trace_satisfies_all_constraints() {
    for pq in [false, true] {
        for i in 0..8 {
            let w = example_witness(i, i as u64);
            assert!(constraints_hold(&w, pq, &Tamper::default(), None), "decision {i}, pq={pq}");
        }
    }
}

#[test]
fn prove_verify_all_variants() {
    let w = honest();
    for v in [
        Variant { pq: false, zk: false },
        Variant { pq: true, zk: false },
        Variant { pq: false, zk: true },
        Variant { pq: true, zk: true },
    ] {
        let (proof, st) = prove(&w, v, Some(1), &Tamper::default());
        verify(&proof, &st).unwrap_or_else(|e| panic!("{}: {e}", v.name()));
    }
}

#[test]
fn zk_proofs_are_randomized() {
    // same statement, different hiding randomness -> different proofs, both valid
    let w = honest();
    let (p1, st1) = prove(&w, Variant::DEPLOYED, Some(1), &Tamper::default());
    let (p2, st2) = prove(&w, Variant::DEPLOYED, Some(2), &Tamper::default());
    assert_eq!(st1, st2);
    assert_ne!(p1, p2);
    verify(&p1, &st1).unwrap();
    verify(&p2, &st2).unwrap();
}

#[test]
fn wrong_public_outputs_rejected() {
    let w = honest();
    let (proof, mut st) = prove(&w, Variant::DEPLOYED, Some(3), &Tamper::default());
    st.public[PV_VIOL] ^= 1;
    assert!(verify(&proof, &st).is_err(), "flipped VIOL accepted");
    st.public[PV_VIOL] ^= 1;
    st.public[PV_EXEC] ^= 1;
    assert!(verify(&proof, &st).is_err(), "flipped EXEC accepted");
    st.public[PV_EXEC] ^= 1;
    st.public[PV_SLASH] += 1;
    assert!(verify(&proof, &st).is_err(), "different policy accepted");
    st.public[PV_SLASH] -= 1;
    st.public[PV_C] = (st.public[PV_C] + 1) % sis::P;
    assert!(verify(&proof, &st).is_err(), "different commitment accepted");
    st.public[PV_C] = (st.public[PV_C] + sis::P - 1) % sis::P;
    verify(&proof, &st).unwrap();
    let mut other = st.clone();
    other.model = "0".repeat(64);
    assert!(verify(&proof, &other).is_err(), "different model accepted");
}

#[test]
fn forged_risk_flag_rejected() {
    let w = honest();
    // RISK = 0 for a flagged transaction (and the outputs that would follow from it)
    let t = Tamper { risk: Some(0), viol: Some(0), ..Default::default() };
    let mut pv = trace::public_values(&w, true);
    pv[PV_VIOL] = BabyBear::ZERO;
    assert!(!constraints_hold(&w, true, &t, Some(pv)));
}

#[test]
fn forged_exec_or_viol_rejected() {
    let w = honest();
    for t in [Tamper { exec: Some(1), ..Default::default() }, Tamper { viol: Some(0), ..Default::default() }] {
        let mut pv = trace::public_values(&w, true);
        if let Some(e) = t.exec {
            pv[PV_EXEC] = BabyBear::from_i64(e);
        }
        if let Some(v) = t.viol {
            pv[PV_VIOL] = BabyBear::from_i64(v);
        }
        assert!(!constraints_hold(&w, true, &t, Some(pv)));
    }
}

#[test]
fn decision_on_other_amount_than_committed_rejected_only_with_commitment() {
    // the decision uses amount A, the commitment opens to A + 1
    let w = honest();
    let t = Tamper { committed_amount: Some(w.decision.amount as u64 + 1), ..Default::default() };
    let mut pv = trace::public_values(&w, true);
    let c = sis::commit(&sis::opening(w.decision.amount as u64 + 1, &w.r));
    for (j, cj) in c.into_iter().enumerate() {
        pv[PV_C + j] = BabyBear::from_u64(cj);
    }
    assert!(!constraints_hold(&w, true, &t, Some(pv)), "PQ circuit must reject");
    // the classical circuit has no commitment, so nothing ties the amount to anything published
    assert!(constraints_hold(&w, false, &Tamper::default(), None));
}

#[test]
fn committed_high_bits_rejected() {
    let w = honest();
    // set amount bit 40 inside the opening (row 20 holds coordinates 40, 41)
    let t = Tamper { cell: Some((20, SB + SA_, 1)), ..Default::default() };
    assert!(!constraints_hold(&w, true, &t, None));
}

#[test]
fn non_ternary_randomness_rejected() {
    let w = honest();
    let t = Tamper { cell: Some((100, SB + SB_, 2)), ..Default::default() };
    assert!(!constraints_hold(&w, true, &t, None));
}

#[test]
fn register_changed_mid_trace_rejected() {
    let w = honest();
    let t = Tamper { cell: Some((120, RB + A, w.decision.amount + 1)), ..Default::default() };
    assert!(!constraints_hold(&w, true, &t, None));
}

#[test]
fn tampered_inference_rejected() {
    let w = honest();
    for (row, col) in [(10, ACC_A + 3), (170, OUT1), (200, OUT2), (229, C_HEAD), (50, X)] {
        let t = Tamper { cell: Some((row, col, 12345)), ..Default::default() };
        assert!(!constraints_hold(&w, true, &t, None), "cell ({row},{col})");
    }
}

#[test]
fn out_of_range_input_rejected() {
    let mut w = honest();
    w.x[0] = model::h1().x_hi[0] + 1;
    assert!(!constraints_hold(&w, true, &Tamper::default(), None));
}

#[test]
fn sis_parameters_are_statistically_binding() {
    assert!(sis::log2_expected_collisions() < -128.0, "{}", sis::log2_expected_collisions());
}
