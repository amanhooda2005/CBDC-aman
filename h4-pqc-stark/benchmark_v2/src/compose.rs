//! Composed workload: one transaction through the whole stack in a single trace.
//!   1. Full trained H1 inference (h1mlp block, unchanged) on one real transaction. The logit
//!      stays private.
//!   2. Risk flag RISK = [logit > T_q], proven from the H1 head accumulator at row h1mlp::FINAL.
//!   3. The H3 decision rule for one decision, with RISK as an extra violation condition:
//!      VIOL = (A > M) OR (D == sanctioned) OR RISK. Everything else as in rule.rs.
//! Public outputs: EXEC and VIOL. In the post-quantum circuit, rlc.rs additionally binds the
//! rule's amount A to the amount inside the SIS commitment (see `rlc::LINK_*`).
//!
//! Narrow layout: the decision's values sit in "register" columns that are constant down the
//! trace. One shared 22-bit window column set is reused at different rows, each row proving
//! one comparison or range check, selected by a periodic column:
//!   row 0  gt   : A - M - 1 + 2^21 = WIN,     GT  = WIN[21]
//!   row 1  pos  : S - 1 + 2^21     = WIN,     POS = WIN[21]
//!   row 2  cge  : S - slash + 2^21 = WIN,     CGE = WIN[21]
//!   row 3  C    : CP = WIN, WIN[12..22] = 0   (C in [0, 2^12))
//!   row 4  V    : VP = WIN, WIN[13..22] = 0   (V in [0, 2^13))
//!   row 5  ex   : EXV - q*CP - 1 + 2^21 = WIN, EX = WIN[21]   (ties refuse)
//!   row 229 risk: (2*RISK - 1) * d - (1 - RISK) = WIN, with d = C_HEAD + b3 - T_q - 1.
//! The risk check is a two-sided sign proof: RISK = 1 needs d in [0, 2^22), RISK = 0 needs
//! -d - 1 in [0, 2^22). Both can hold only for d in (p - 1 - 2^22, 2^22) = (3145728, 4194304),
//! which the H1 interval bounds exclude (tested), so RISK is unique.

use std::sync::OnceLock;

use serde_json::Value;
use winterfell::{
    math::{fields::f23201::BaseElement, FieldElement},
    Assertion, TransitionConstraintDegree,
};

use crate::gadget::{bits_value, booleans, c, felt, fill_bits};
use crate::{h1mlp, rule};

/// Index of the H3 decision used (decision 1: amount 7558, not a violation under the original rule).
pub const DECISION: usize = 1;
const WB: usize = 22;
const TOP: usize = WB - 1;
const OFF: i64 = 1 << TOP;

// registers (relative to the rule part's base)
const A: usize = 0;
const D: usize = 1;
const INV: usize = 2;
const FL: usize = 3;
const CP: usize = 4;
const VP: usize = 5;
const Z: usize = 6;
const S: usize = 7;
const GT: usize = 8;
const POS: usize = 9;
const CGE: usize = 10;
const RISK: usize = 11;
const ACT: usize = 12;
const MN: usize = 13;
const ES: usize = 14;
const V0: usize = 15;
const VIOL: usize = 16;
const EXV: usize = 17;
const EX: usize = 18;
const REGS: usize = 19;
const WIN: usize = REGS;
const RULE_WIDTH: usize = REGS + WB;

pub const RULE_BASE: usize = h1mlp::WIDTH;
pub const WIDTH: usize = RULE_BASE + RULE_WIDTH;
pub const MIN_LEN: usize = h1mlp::MIN_LEN;
/// Column of the rule's amount register, relative to the composed block's base.
pub const AMOUNT_COL: usize = RULE_BASE + A;

const ROW_RISK: usize = h1mlp::FINAL;
const SELECTORS: usize = 7; // gt, pos, cge, C, V, ex, risk
#[cfg(test)]
const RULE_CONSTRAINTS: usize = REGS + 10 + WB + 2 * SELECTORS - 1;
#[cfg(test)]
pub const CONSTRAINTS: usize = h1mlp::CONSTRAINTS + RULE_CONSTRAINTS;
pub const ASSERTIONS: usize = h1mlp::ASSERTIONS - 1 + 2;

pub struct Spec {
    pub threshold: i64,
    pub b3: i64,
}

pub fn spec() -> &'static Spec {
    static SPEC: OnceLock<Spec> = OnceLock::new();
    SPEC.get_or_init(|| {
        let v: Value = serde_json::from_str(include_str!("../specs/h1_spec.json")).expect("h1 spec");
        Spec {
            threshold: v["threshold_q"].as_i64().unwrap(),
            b3: h1mlp::spec().b3,
        }
    })
}

/// The decision as (amount, dest, compliant, violation, staked, stake).
pub fn decision() -> [i64; 6] {
    rule::spec().rows[DECISION]
}

/// Reference semantics: H1 logit -> RISK -> H3 rule with RISK as a violation condition.
pub fn reference(x: &[i64], row: &[i64; 6]) -> (i64, i64, i64) {
    let r = rule::spec();
    let logit = h1mlp::logit(x);
    let risk = (logit > spec().threshold) as i64;
    let [a, d, cp, vp, z, st] = *row;
    let viol = (a > r.m || d == r.sid || risk == 1) as i64;
    let es = (z == 1 && st > 0) as i64 * st.min(r.slash);
    let exv = if viol == 1 { r.q * vp - r.p * es } else { r.q * cp };
    ((exv > r.q * cp) as i64, viol, risk)
}

pub fn outputs() -> Vec<u64> {
    let (exec, viol, _) = reference(&h1mlp::spec().x, &decision());
    vec![exec as u64, viol as u64]
}

pub fn degrees(len: usize) -> Vec<TransitionConstraintDegree> {
    let one = || TransitionConstraintDegree::new(1);
    let two = || TransitionConstraintDegree::new(2);
    let p1 = || TransitionConstraintDegree::with_cycles(1, vec![len]);
    let p2 = || TransitionConstraintDegree::with_cycles(2, vec![len]);
    let mut d = h1mlp::degrees(len);
    d.extend((0..REGS).map(|_| one())); // registers constant
    d.extend((0..9).map(|_| two())); // Z, RISK boolean; FL (2); ACT, MN, ES, V0, VIOL
    d.push(two()); // EXV
    d.extend((0..WB).map(|_| two())); // WIN boolean
    d.extend((0..SELECTORS - 1).map(|_| p1())); // window value checks (linear)
    d.push(p2()); // risk window check
    d.extend((0..SELECTORS - 1).map(|_| p1())); // top-bit / high-zero checks
    d
}

pub fn constraints<E: FieldElement + From<BaseElement>>(cur: &[E], next: &[E], per: &[E], base: usize, out: &mut [E]) {
    let (h1_n, h1_per) = (h1mlp::CONSTRAINTS, h1mlp::PERIODIC);
    h1mlp::constraints(cur, next, per, base, out);
    let out = &mut out[h1_n..];
    let sel = &per[h1_per..h1_per + SELECTORS];
    let r = rule::spec();
    let rb = base + RULE_BASE;
    let reg = |i: usize| cur[rb + i];
    let mut k = 0;
    for i in 0..REGS {
        out[k + i] = next[rb + i] - cur[rb + i];
    }
    k += REGS;
    let diff = reg(D) - c::<E>(r.sid);
    out[k] = reg(Z) * (reg(Z) - E::ONE);
    out[k + 1] = reg(RISK) * (reg(RISK) - E::ONE);
    out[k + 2] = reg(FL) * diff;
    out[k + 3] = reg(FL) - E::ONE + diff * reg(INV);
    out[k + 4] = reg(ACT) - reg(Z) * reg(POS);
    out[k + 5] = reg(MN) - reg(CGE) * c::<E>(r.slash) - (E::ONE - reg(CGE)) * reg(S);
    out[k + 6] = reg(ES) - reg(ACT) * reg(MN);
    out[k + 7] = reg(V0) - reg(GT) - reg(FL) + reg(GT) * reg(FL);
    out[k + 8] = reg(VIOL) - reg(V0) - reg(RISK) + reg(V0) * reg(RISK);
    k += 9;
    let (qe, pe) = (c::<E>(r.q), c::<E>(r.p));
    out[k] = reg(EXV) - reg(VIOL) * (qe * reg(VP) - pe * reg(ES) - qe * reg(CP)) - qe * reg(CP);
    k += 1;
    booleans(cur, rb + WIN, WB, &mut out[k..]);
    k += WB;
    let w = bits_value(cur, rb + WIN, WB);
    let off = c::<E>(OFF);
    let s = spec();
    let d = cur[base + h1mlp::C_HEAD] + c::<E>(s.b3 - s.threshold - 1);
    let values = [
        reg(A) - c::<E>(r.m) - E::ONE + off,
        reg(S) - E::ONE + off,
        reg(S) - c::<E>(r.slash) + off,
        reg(CP),
        reg(VP),
        reg(EXV) - qe * reg(CP) - E::ONE + off,
        (c::<E>(2) * reg(RISK) - E::ONE) * d - (E::ONE - reg(RISK)),
    ];
    for (j, v) in values.into_iter().enumerate() {
        out[k + j] = sel[j] * (v - w);
    }
    k += SELECTORS;
    let top = cur[rb + WIN + TOP];
    let high = |from: usize| (from..WB).fold(E::ZERO, |acc, j| acc + cur[rb + WIN + j]);
    out[k] = sel[0] * (reg(GT) - top);
    out[k + 1] = sel[1] * (reg(POS) - top);
    out[k + 2] = sel[2] * (reg(CGE) - top);
    out[k + 3] = sel[3] * high(rule::CB);
    out[k + 4] = sel[4] * high(rule::VB);
    out[k + 5] = sel[5] * (reg(EX) - top);
}

pub fn assertions(base: usize, outputs: &[u64]) -> Vec<Assertion<BaseElement>> {
    let mut v = h1mlp::assertions(base, &[0]);
    v.pop(); // the logit stays private
    let rb = base + RULE_BASE;
    v.push(Assertion::single(rb + EX, 0, BaseElement::from(outputs[0])));
    v.push(Assertion::single(rb + VIOL, 0, BaseElement::from(outputs[1])));
    v
}

pub fn periodic_columns(len: usize) -> Vec<Vec<BaseElement>> {
    let mut cols = h1mlp::periodic_columns(len);
    for row in [0, 1, 2, 3, 4, 5, ROW_RISK] {
        let mut s = vec![BaseElement::ZERO; len];
        s[row] = BaseElement::ONE;
        cols.push(s);
    }
    cols
}

/// Fills the block. `x` is the H1 input, `row` the decision; `risk` overrides the flag (tests).
pub fn fill(columns: &mut [Vec<BaseElement>], base: usize, len: usize, x: &[i64], row: &[i64; 6], risk: Option<i64>) {
    h1mlp::fill(columns, base, len, x);
    let r = rule::spec();
    let s = spec();
    let logit = h1mlp::logit(x);
    let risk = risk.unwrap_or((logit > s.threshold) as i64);
    let [a, d, cp, vp, z, st] = *row;
    let fl = (d == r.sid) as i64;
    let gt = (a > r.m) as i64;
    let pos = (st > 0) as i64;
    let cge = (st >= r.slash) as i64;
    let act = z * pos;
    let mn = cge * r.slash + (1 - cge) * st;
    let es = act * mn;
    let v0 = gt + fl - gt * fl;
    let viol = v0 + risk - v0 * risk;
    let exv = viol * (r.q * vp - r.p * es - r.q * cp) + r.q * cp;
    let ex = (exv > r.q * cp) as i64;
    let inv = if fl == 1 { BaseElement::ZERO } else { felt(d - r.sid).inv() };
    let mut regs = [BaseElement::ZERO; REGS];
    for (i, v) in [(A, a), (D, d), (FL, fl), (CP, cp), (VP, vp), (Z, z), (S, st), (GT, gt), (POS, pos), (CGE, cge), (RISK, risk), (ACT, act), (MN, mn), (ES, es), (V0, v0), (VIOL, viol), (EXV, exv), (EX, ex)] {
        regs[i] = felt(v);
    }
    regs[INV] = inv;
    let rb = base + RULE_BASE;
    let dd = logit - s.threshold - 1;
    let clamp = |v: i64| v.clamp(0, (1i64 << WB) - 1);
    let windows = [
        (0, a - r.m - 1 + OFF),
        (1, st - 1 + OFF),
        (2, st - r.slash + OFF),
        (3, cp),
        (4, vp),
        (5, exv - r.q * cp - 1 + OFF),
        (ROW_RISK, (2 * risk - 1) * dd - (1 - risk)),
    ];
    for t in 0..len {
        for (i, v) in regs.iter().enumerate() {
            columns[rb + i][t] = *v;
        }
        let v = windows.iter().find(|(row, _)| *row == t).map_or(0, |(_, v)| clamp(*v));
        fill_bits(columns, rb + WIN, WB, t, v);
    }
}
