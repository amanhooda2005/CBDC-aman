//! Full H1 model inference (W32_B1): proj 165->32 ReLU, residual block 32->32 ReLU, head 32->1.
//! Exact integer semantics from coverage/quantize_h1.py (specs/h1_spec.json).
//!
//! Streaming layout (one trace, L = 256 rows):
//!   rows 0..165    input x_i streams down column X (range-checked to [lo_i, hi_i]);
//!                  bank A (32 accumulators) accumulates W1[n][i] * x_i.
//!   rows 165..197  bank A shifts out one neuron per row into pipeline 1:
//!                  acc + b1 = 2^s1 * q + rem, h = relu(q); h feeds bank B (layer 2) and a
//!                  32-slot delay line D (for the residual).
//!   rows 197..229  bank B shifts out into pipeline 2: acc + b2 = 2^7 * q + rem,
//!                  h2 = relu(q) + h (residual from D); h2 feeds the head accumulator C.
//!   row 229        C = W3 . h2, asserted equal to the public logit minus b3.
//! Requantization windows are 2^22 and 2^20 < p, so every decomposition is unique.

use std::sync::OnceLock;

use serde_json::Value;
use winterfell::{
    math::{fields::f23201::BaseElement, FieldElement, StarkField},
    Assertion, TransitionConstraintDegree,
};

use crate::gadget::{bits_value, booleans, c, felt, fill_bits};

const F: usize = 165;
const H: usize = 32;
const SH_A_START: usize = F;
const SH_B_START: usize = F + H;
const FINAL: usize = F + 2 * H;
pub const MIN_LEN: usize = FINAL + 1;

// pipeline sizes
const XBITS: usize = 12;
const Q1: usize = 11; // offset 2^10
const R1: usize = 11; // = s1
const Q2: usize = 13; // offset 2^12
const R2: usize = 7; // = s2

// column offsets (relative to block base)
const X: usize = 0;
const XB: usize = X + 1;
const XC: usize = XB + XBITS;
const ACC_A: usize = XC + XBITS;
const ACC_B: usize = ACC_A + H;
const DL: usize = ACC_B + H;
const C_HEAD: usize = DL + H;
const P1Q: usize = C_HEAD + 1;
const P1R: usize = P1Q + Q1;
const OUT1: usize = P1R + R1;
const P2Q: usize = OUT1 + 1;
const P2R: usize = P2Q + Q2;
const OUT2: usize = P2R + R2;
pub const WIDTH: usize = OUT2 + 1;

// periodic offsets
const PE_INP: usize = 0;
const PE_LO: usize = 1;
const PE_HI: usize = 2;
const PE_W1: usize = 3;
const PE_SHA: usize = PE_W1 + H;
const PE_B1: usize = PE_SHA + 1;
const PE_W2: usize = PE_B1 + 1;
const PE_SHB: usize = PE_W2 + H;
const PE_B2: usize = PE_SHB + 1;
const PE_W3: usize = PE_B2 + 1;
const PE_DMOVE: usize = PE_W3 + 1;
const PE_DIN: usize = PE_DMOVE + 1;
pub const PERIODIC: usize = PE_DIN + 1;

#[cfg(test)]
pub const CONSTRAINTS: usize = 2 + 2 * XBITS + H + H + H + 1 + 2 + Q1 + R1 + 2 + Q2 + R2;
pub const ASSERTIONS: usize = 3 * H + 2;

pub struct Spec {
    pub s1: u32,
    pub s2: u32,
    pub w1: Vec<Vec<i64>>,
    pub b1: Vec<i64>,
    pub w2: Vec<Vec<i64>>,
    pub b2: Vec<i64>,
    pub w3: Vec<i64>,
    pub b3: i64,
    pub x_lo: Vec<i64>,
    pub x_hi: Vec<i64>,
    pub x: Vec<i64>,
    pub logit: i64,
}

fn ints(v: &Value) -> Vec<i64> {
    v.as_array().expect("array").iter().map(|e| e.as_i64().expect("int")).collect()
}

pub fn spec() -> &'static Spec {
    static SPEC: OnceLock<Spec> = OnceLock::new();
    SPEC.get_or_init(|| {
        let v: Value = serde_json::from_str(include_str!("../specs/h1_spec.json")).expect("h1 spec");
        let mat = |k: &str| v[k].as_array().unwrap().iter().map(ints).collect::<Vec<_>>();
        let s = Spec {
            s1: v["s1"].as_u64().unwrap() as u32,
            s2: v["s2"].as_u64().unwrap() as u32,
            w1: mat("W1"),
            b1: ints(&v["b1"]),
            w2: mat("W2"),
            b2: ints(&v["b2"]),
            w3: mat("W3").remove(0),
            b3: ints(&v["b3"])[0],
            x_lo: ints(&v["x_lo"]),
            x_hi: ints(&v["x_hi"]),
            x: ints(&v["x"]),
            logit: v["logit"].as_i64().unwrap(),
        };
        assert!(s.s1 as usize == R1 && s.s2 as usize == R2, "pipeline widths must match the spec shifts");
        assert!(s.w1.len() == H && s.w1[0].len() == F && s.w2.len() == H && s.w3.len() == H);
        s
    })
}

/// Public output: the logit (as a field element of the signed integer).
pub fn outputs() -> Vec<u64> {
    vec![felt(spec().logit).as_int() as u64]
}

pub fn degrees(len: usize) -> Vec<TransitionConstraintDegree> {
    let p1 = || TransitionConstraintDegree::with_cycles(1, vec![len]);
    let p2 = || TransitionConstraintDegree::with_cycles(2, vec![len]);
    let two = || TransitionConstraintDegree::new(2);
    let mut d = vec![p1(), p1()];
    d.extend((0..2 * XBITS).map(|_| two()));
    d.extend((0..3 * H).map(|_| p1()));
    d.push(p1());
    d.extend([p1(), p2()]);
    d.extend((0..Q1 + R1).map(|_| two()));
    d.extend([p1(), p2()]);
    d.extend((0..Q2 + R2).map(|_| two()));
    d
}

pub fn constraints<E: FieldElement + From<BaseElement>>(cur: &[E], next: &[E], per: &[E], base: usize, out: &mut [E]) {
    let s = spec();
    let col = |row: &[E], i: usize| row[base + i];
    let (inp, lo, hi, sha, shb) = (per[PE_INP], per[PE_LO], per[PE_HI], per[PE_SHA], per[PE_SHB]);
    let x = col(cur, X);
    let mut k = 0;
    // input range: x - lo in [0, 2^12), hi - x in [0, 2^12)
    out[k] = inp * x - lo - bits_value(cur, base + XB, XBITS);
    out[k + 1] = hi - inp * x - bits_value(cur, base + XC, XBITS);
    k += 2;
    booleans(cur, base + XB, XBITS, &mut out[k..]);
    k += XBITS;
    booleans(cur, base + XC, XBITS, &mut out[k..]);
    k += XBITS;
    // bank A: accumulate during input rows, shift during SH_A rows, frozen otherwise
    let out1 = col(cur, OUT1);
    let out2 = col(cur, OUT2);
    for n in 0..H {
        let shifted = if n + 1 < H { col(cur, ACC_A + n + 1) } else { E::ZERO };
        out[k + n] = col(next, ACC_A + n) - (E::ONE - sha) * col(cur, ACC_A + n) - sha * shifted - per[PE_W1 + n] * x;
    }
    k += H;
    for m in 0..H {
        let shifted = if m + 1 < H { col(cur, ACC_B + m + 1) } else { E::ZERO };
        out[k + m] = col(next, ACC_B + m) - (E::ONE - shb) * col(cur, ACC_B + m) - shb * shifted - per[PE_W2 + m] * out1;
    }
    k += H;
    let (dmove, din) = (per[PE_DMOVE], per[PE_DIN]);
    for j in 0..H {
        let shifted = if j + 1 < H { dmove * col(cur, DL + j + 1) } else { din * out1 };
        out[k + j] = col(next, DL + j) - (E::ONE - dmove) * col(cur, DL + j) - shifted;
    }
    k += H;
    out[k] = col(next, C_HEAD) - col(cur, C_HEAD) - per[PE_W3] * out2;
    k += 1;
    // pipeline 1: acc_A0 + b1 = 2^s1 * q + rem, out1 = relu(q)
    let q1 = bits_value(cur, base + P1Q, Q1) - c::<E>(1 << (Q1 - 1));
    let rem1 = bits_value(cur, base + P1R, R1);
    out[k] = sha * (col(cur, ACC_A) - c::<E>(1i64 << s.s1) * q1 - rem1) + per[PE_B1];
    out[k + 1] = sha * (out1 - q1 * col(cur, P1Q + Q1 - 1));
    k += 2;
    booleans(cur, base + P1Q, Q1 + R1, &mut out[k..]);
    k += Q1 + R1;
    // pipeline 2: acc_B0 + b2 = 2^s2 * q + rem, out2 = relu(q) + h (residual from the delay line)
    let q2 = bits_value(cur, base + P2Q, Q2) - c::<E>(1 << (Q2 - 1));
    let rem2 = bits_value(cur, base + P2R, R2);
    out[k] = shb * (col(cur, ACC_B) - c::<E>(1i64 << s.s2) * q2 - rem2) + per[PE_B2];
    out[k + 1] = shb * (out2 - q2 * col(cur, P2Q + Q2 - 1) - col(cur, DL));
    k += 2;
    booleans(cur, base + P2Q, Q2 + R2, &mut out[k..]);
}

pub fn assertions(base: usize, outputs: &[u64]) -> Vec<Assertion<BaseElement>> {
    let mut v = Vec::with_capacity(ASSERTIONS);
    for n in 0..H {
        v.push(Assertion::single(base + ACC_A + n, 0, BaseElement::ZERO));
        v.push(Assertion::single(base + ACC_B + n, 0, BaseElement::ZERO));
        v.push(Assertion::single(base + DL + n, 0, BaseElement::ZERO));
    }
    v.push(Assertion::single(base + C_HEAD, 0, BaseElement::ZERO));
    // public logit minus the public bias
    let logit = BaseElement::from(outputs[0]);
    v.push(Assertion::single(base + C_HEAD, FINAL, logit - felt(spec().b3)));
    v
}

pub fn periodic_columns(len: usize) -> Vec<Vec<BaseElement>> {
    let s = spec();
    let z = || vec![BaseElement::ZERO; len];
    let mut cols = vec![z(); PERIODIC];
    for r in 0..F {
        cols[PE_INP][r] = BaseElement::ONE;
        cols[PE_LO][r] = felt(s.x_lo[r]);
        cols[PE_HI][r] = felt(s.x_hi[r]);
        for n in 0..H {
            cols[PE_W1 + n][r] = felt(s.w1[n][r]);
        }
    }
    for k in 0..H {
        let ra = SH_A_START + k;
        cols[PE_SHA][ra] = BaseElement::ONE;
        cols[PE_B1][ra] = felt(s.b1[k]);
        for m in 0..H {
            cols[PE_W2 + m][ra] = felt(s.w2[m][k]);
        }
        cols[PE_DMOVE][ra] = BaseElement::ONE;
        cols[PE_DIN][ra] = BaseElement::ONE;
        let rb = SH_B_START + k;
        cols[PE_SHB][rb] = BaseElement::ONE;
        cols[PE_B2][rb] = felt(s.b2[k]);
        cols[PE_W3][rb] = felt(s.w3[k]);
        cols[PE_DMOVE][rb] = BaseElement::ONE;
    }
    cols
}

#[cfg(test)]
/// Integer reference forward pass (same semantics the circuit enforces).
pub fn reference(x: &[i64]) -> (Vec<i64>, Vec<i64>, i64) {
    let s = spec();
    let h: Vec<i64> = (0..H)
        .map(|n| {
            let a: i64 = (0..F).map(|i| s.w1[n][i] * x[i]).sum::<i64>() + s.b1[n];
            a.div_euclid(1 << s.s1).max(0)
        })
        .collect();
    let h2: Vec<i64> = (0..H)
        .map(|m| {
            let a: i64 = (0..H).map(|k| s.w2[m][k] * h[k]).sum::<i64>() + s.b2[m];
            h[m] + a.div_euclid(1 << s.s2).max(0)
        })
        .collect();
    let logit = (0..H).map(|n| s.w3[n] * h2[n]).sum::<i64>() + s.b3;
    (h, h2, logit)
}

/// Fills the block. `x` is the private input (tests may pass an out-of-range one).
pub fn fill(columns: &mut [Vec<BaseElement>], base: usize, len: usize, x: &[i64]) {
    let s = spec();
    let col = |i: usize| base + i;
    // accumulator banks as integers, row by row
    let mut acc_a = vec![0i64; H];
    let mut acc_b = vec![0i64; H];
    let mut dl = vec![0i64; H];
    let mut c_head = 0i64;
    for row in 0..len {
        // write current state
        for n in 0..H {
            columns[col(ACC_A + n)][row] = felt(acc_a[n]);
            columns[col(ACC_B + n)][row] = felt(acc_b[n]);
            columns[col(DL + n)][row] = felt(dl[n]);
        }
        columns[col(C_HEAD)][row] = felt(c_head);
        let xv = if row < F { x[row] } else { 0 };
        columns[col(X)][row] = felt(xv);
        if row < F {
            fill_bits(columns, col(XB), XBITS, row, (xv - s.x_lo[row]).clamp(0, (1 << XBITS) - 1));
            fill_bits(columns, col(XC), XBITS, row, (s.x_hi[row] - xv).clamp(0, (1 << XBITS) - 1));
        } else {
            fill_bits(columns, col(XB), XBITS, row, 0);
            fill_bits(columns, col(XC), XBITS, row, 0);
        }
        // pipeline values for this row
        let (mut out1, mut out2) = (0i64, 0i64);
        if (SH_A_START..SH_B_START).contains(&row) {
            let k = row - SH_A_START;
            let a = acc_a[0] + s.b1[k];
            let (q, rem) = (a.div_euclid(1 << s.s1), a.rem_euclid(1 << s.s1));
            fill_bits(columns, col(P1Q), Q1, row, (q + (1 << (Q1 - 1))).clamp(0, (1 << Q1) - 1));
            fill_bits(columns, col(P1R), R1, row, rem);
            out1 = q.max(0);
        } else {
            fill_bits(columns, col(P1Q), Q1, row, 1 << (Q1 - 1));
            fill_bits(columns, col(P1R), R1, row, 0);
        }
        if (SH_B_START..FINAL).contains(&row) {
            let k = row - SH_B_START;
            let a = acc_b[0] + s.b2[k];
            let (q, rem) = (a.div_euclid(1 << s.s2), a.rem_euclid(1 << s.s2));
            fill_bits(columns, col(P2Q), Q2, row, (q + (1 << (Q2 - 1))).clamp(0, (1 << Q2) - 1));
            fill_bits(columns, col(P2R), R2, row, rem);
            out2 = q.max(0) + dl[0];
        } else {
            fill_bits(columns, col(P2Q), Q2, row, 1 << (Q2 - 1));
            fill_bits(columns, col(P2R), R2, row, 0);
        }
        columns[col(OUT1)][row] = felt(out1);
        columns[col(OUT2)][row] = felt(out2);
        // transition to the next row
        let sha = (SH_A_START..SH_B_START).contains(&row);
        let shb = (SH_B_START..FINAL).contains(&row);
        let (dmove, din) = ((SH_A_START..FINAL).contains(&row), sha);
        for n in 0..H {
            let w1 = if row < F { s.w1[n][row] } else { 0 };
            acc_a[n] = if sha { if n + 1 < H { acc_a[n + 1] } else { 0 } } else { acc_a[n] } + w1 * xv;
        }
        for m in 0..H {
            let w2 = if sha { s.w2[m][row - SH_A_START] } else { 0 };
            acc_b[m] = if shb { if m + 1 < H { acc_b[m + 1] } else { 0 } } else { acc_b[m] } + w2 * out1;
        }
        let new_dl: Vec<i64> = (0..H)
            .map(|j| if dmove { if j + 1 < H { dl[j + 1] } else if din { out1 } else { 0 } } else { dl[j] })
            .collect();
        dl = new_dl;
        let w3 = if shb { s.w3[row - SH_B_START] } else { 0 };
        c_head += w3 * out2;
    }
}
