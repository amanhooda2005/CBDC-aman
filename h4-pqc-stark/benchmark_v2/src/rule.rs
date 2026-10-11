//! H3 agent decision rule for a batch of 64 real decisions (staked treatment, seed 42), exact
//! fixed-point semantics from coverage/export_h3.py (specs/h3_spec.json). One decision per row;
//! rows 64..L are padding copies of decision 0. All constraints are row-local.
//!
//! Per row (private: amount A, destination D, payoffs C and V, staked Z, stake S):
//!   F     = [D == sanctioned]        via F*(D - sid) = 0 and F = 1 - (D - sid)*INV
//!   gt    = [A > M]                  sign bit of A - M - 1 + 2^15   (16 bits)
//!   pos   = [S > 0]                  sign bit of S - 1 + 2^15       (16 bits)
//!   cge   = [S >= slash]             sign bit of S - slash + 2^15   (16 bits)
//!   VIOL  = gt OR F
//!   MN    = cge*slash + (1 - cge)*S, ACT = Z*pos, ES = ACT*MN
//!   EXV   = VIOL ? (256*V - 256*ES) : 256*C          (execute payoff, scale 2^16; p = 256)
//!   EXEC  = [EXV > 256*C]            sign bit of EXV - 256*C - 1 + 2^21  (22 bits), ties refuse
//! with C in [0, 2^12) and V in [0, 2^13) range-checked so every window stays unique.
//! Public outputs: EXEC and VIOL of each decision.

use std::sync::OnceLock;

use serde_json::Value;
use winterfell::{
    math::{fields::f23201::BaseElement, FieldElement},
    Assertion, TransitionConstraintDegree,
};

use crate::gadget::{bits_value, booleans, c, felt, fill_bits};

pub const N: usize = 64;
pub const MIN_LEN: usize = N + 1;
const W16: usize = 16;
const W22: usize = 22;
const CB: usize = 12;
const VB: usize = 13;

const A: usize = 0;
const D: usize = 1;
const INV: usize = 2;
const FL: usize = 3;
const CP: usize = 4;
const VP: usize = 5;
const Z: usize = 6;
const S: usize = 7;
const GT: usize = 8;
const POS: usize = GT + W16;
const CGE: usize = POS + W16;
const ACT: usize = CGE + W16;
const MN: usize = ACT + 1;
const ES: usize = MN + 1;
const VIOL: usize = ES + 1;
const EXV: usize = VIOL + 1;
const EX: usize = EXV + 1;
const CBITS: usize = EX + W22;
const VBITS: usize = CBITS + CB;
pub const WIDTH: usize = VBITS + VB;

#[cfg(test)]
pub const CONSTRAINTS: usize = 3 + 3 + 3 * W16 + 2 + CB + VB + 4 + 1 + 1 + W22;
pub const ASSERTIONS: usize = 2 * N;

pub struct Spec {
    pub q: i64,
    pub m: i64,
    pub slash: i64,
    pub p: i64,
    pub sid: i64,
    pub rows: Vec<[i64; 6]>, // amount, dest, compliant, violation, staked, stake
    pub exec: Vec<i64>,
    pub viol: Vec<i64>,
}

pub fn spec() -> &'static Spec {
    static SPEC: OnceLock<Spec> = OnceLock::new();
    SPEC.get_or_init(|| {
        let v: Value = serde_json::from_str(include_str!("../specs/h3_spec.json")).expect("h3 spec");
        let g = |k: &str| v[k].as_i64().unwrap();
        let dec = v["decisions"].as_array().unwrap();
        let f = |d: &Value, k: &str| d[k].as_i64().unwrap();
        let s = Spec {
            q: g("q"),
            m: g("max_compliant"),
            slash: g("slash"),
            p: g("p_detect"),
            sid: g("sanctioned_id"),
            rows: dec.iter().map(|d| [f(d, "amount"), f(d, "dest"), f(d, "compliant"), f(d, "violation"), f(d, "staked"), f(d, "stake")]).collect(),
            exec: dec.iter().map(|d| f(d, "exec")).collect(),
            viol: dec.iter().map(|d| f(d, "viol")).collect(),
        };
        assert!(s.rows.len() == N && s.q == 256 && s.p == 256, "circuit assumes 64 decisions, q = p = 2^8");
        s
    })
}

pub fn outputs() -> Vec<u64> {
    let s = spec();
    s.exec.iter().chain(s.viol.iter()).map(|&b| b as u64).collect()
}

pub fn degrees(_len: usize) -> Vec<TransitionConstraintDegree> {
    let one = || TransitionConstraintDegree::new(1);
    let two = || TransitionConstraintDegree::new(2);
    let mut d = vec![two(), two(), two()]; // Z boolean, F*(D-sid), F - 1 + (D-sid)*INV
    d.extend([one(), one(), one()]); // gt, pos, cge decompositions
    d.extend((0..3 * W16).map(|_| two()));
    d.extend([one(), one()]); // C, V range
    d.extend((0..CB + VB).map(|_| two()));
    d.extend([two(), two(), two(), two()]); // ACT, MN, ES, VIOL
    d.push(two()); // EXV
    d.push(one()); // EX decomposition
    d.extend((0..W22).map(|_| two()));
    d
}

pub fn constraints<E: FieldElement + From<BaseElement>>(cur: &[E], _next: &[E], _per: &[E], base: usize, out: &mut [E]) {
    let s = spec();
    let col = |i: usize| cur[base + i];
    let diff = col(D) - c::<E>(s.sid);
    let mut k = 0;
    out[k] = col(Z) * (col(Z) - E::ONE);
    out[k + 1] = col(FL) * diff;
    out[k + 2] = col(FL) - E::ONE + diff * col(INV);
    k += 3;
    let two15 = c::<E>(1 << 15);
    out[k] = col(A) - c::<E>(s.m) - E::ONE + two15 - bits_value(cur, base + GT, W16);
    out[k + 1] = col(S) - E::ONE + two15 - bits_value(cur, base + POS, W16);
    out[k + 2] = col(S) - c::<E>(s.slash) + two15 - bits_value(cur, base + CGE, W16);
    k += 3;
    booleans(cur, base + GT, 3 * W16, &mut out[k..]);
    k += 3 * W16;
    out[k] = col(CP) - bits_value(cur, base + CBITS, CB);
    out[k + 1] = col(VP) - bits_value(cur, base + VBITS, VB);
    k += 2;
    booleans(cur, base + CBITS, CB + VB, &mut out[k..]);
    k += CB + VB;
    let (gt, pos, cge) = (col(GT + W16 - 1), col(POS + W16 - 1), col(CGE + W16 - 1));
    out[k] = col(ACT) - col(Z) * pos;
    out[k + 1] = col(MN) - cge * c::<E>(s.slash) - (E::ONE - cge) * col(S);
    out[k + 2] = col(ES) - col(ACT) * col(MN);
    out[k + 3] = col(VIOL) - gt - col(FL) + gt * col(FL);
    k += 4;
    // execute = VIOL ? (q*V - p*ES) : q*C ; refuse = q*C
    let qe = c::<E>(s.q);
    let pe = c::<E>(s.p);
    out[k] = col(EXV) - col(VIOL) * (qe * col(VP) - pe * col(ES) - qe * col(CP)) - qe * col(CP);
    k += 1;
    out[k] = col(EXV) - qe * col(CP) - E::ONE + c::<E>(1 << 21) - bits_value(cur, base + EX, W22);
    k += 1;
    booleans(cur, base + EX, W22, &mut out[k..]);
}

pub fn assertions(base: usize, outputs: &[u64]) -> Vec<Assertion<BaseElement>> {
    let mut v = Vec::with_capacity(ASSERTIONS);
    for r in 0..N {
        v.push(Assertion::single(base + EX + W22 - 1, r, BaseElement::from(outputs[r])));
        v.push(Assertion::single(base + VIOL, r, BaseElement::from(outputs[N + r])));
    }
    v
}

pub fn periodic_columns(_len: usize) -> Vec<Vec<BaseElement>> {
    Vec::new()
}

#[cfg(test)]
/// Reference decision (same fixed-point semantics).
pub fn reference(row: &[i64; 6]) -> (i64, i64) {
    let s = spec();
    let [a, d, cp, vp, z, st] = *row;
    let viol = (a > s.m || d == s.sid) as i64;
    let act = (z == 1 && st > 0) as i64;
    let es = act * st.min(s.slash);
    let exv = if viol == 1 { s.q * vp - s.p * es } else { s.q * cp };
    ((exv > s.q * cp) as i64, viol)
}

pub fn fill(columns: &mut [Vec<BaseElement>], base: usize, len: usize, rows: &[[i64; 6]]) {
    let s = spec();
    for row in 0..len {
        let [a, d, cp, vp, z, st] = rows[if row < N { row } else { 0 }];
        let col = |i: usize| base + i;
        let diff = felt(d - s.sid);
        let fl = (d == s.sid) as i64;
        columns[col(A)][row] = felt(a);
        columns[col(D)][row] = felt(d);
        columns[col(INV)][row] = if fl == 1 { BaseElement::ZERO } else { diff.inv() };
        columns[col(FL)][row] = felt(fl);
        columns[col(CP)][row] = felt(cp);
        columns[col(VP)][row] = felt(vp);
        columns[col(Z)][row] = felt(z);
        columns[col(S)][row] = felt(st);
        let w = |v: i64, n: usize| v.clamp(0, (1i64 << n) - 1);
        fill_bits(columns, col(GT), W16, row, w(a - s.m - 1 + (1 << 15), W16));
        fill_bits(columns, col(POS), W16, row, w(st - 1 + (1 << 15), W16));
        fill_bits(columns, col(CGE), W16, row, w(st - s.slash + (1 << 15), W16));
        fill_bits(columns, col(CBITS), CB, row, w(cp, CB));
        fill_bits(columns, col(VBITS), VB, row, w(vp, VB));
        let gt = (a > s.m) as i64;
        let pos = (st > 0) as i64;
        let cge = (st >= s.slash) as i64;
        let act = z * pos;
        let mn = cge * s.slash + (1 - cge) * st;
        let es = act * mn;
        let viol = gt + fl - gt * fl;
        let exv = viol * (s.q * vp - s.p * es - s.q * cp) + s.q * cp;
        columns[col(ACT)][row] = felt(act);
        columns[col(MN)][row] = felt(mn);
        columns[col(ES)][row] = felt(es);
        columns[col(VIOL)][row] = felt(viol);
        columns[col(EXV)][row] = felt(exv);
        fill_bits(columns, col(EX), W22, row, w(exv - s.q * cp - 1 + (1 << 21), W22));
    }
}
