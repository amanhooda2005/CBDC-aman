//! The composed AIR: one execution trace proving, for ONE transaction,
//!   1. exact integer inference of the trained H1 model on the private features x
//!      (range-checked inputs, every requantization and ReLU proven, interval-bounded accumulators);
//!   2. RISK = [logit > T] by a two-sided sign window (unique for every in-range logit);
//!   3. the H3 agent decision with RISK as a violation condition:
//!      VIOL = (A > M) OR (D == sanctioned) OR RISK, EXEC = 1 if VIOL = 0, else [expected execute payoff > refuse payoff];
//!   4. (post-quantum variant) the decision's amount A equals the amount inside the SIS commitment c
//!      published as public input, and the opening (bits, r) is short (bits boolean, r ternary).
//! Public values: policy (M, sanctioned id, slash), the outputs EXEC and VIOL, and (PQ) c.
//! The logit, the features, the payoffs, the stake and the opening stay private.

use std::borrow::Cow;

use p3_air::{Air, AirBuilder, BaseAir, WindowAccess};
use p3_baby_bear::BabyBear;
use p3_field::PrimeCharacteristicRing;

use crate::layout::*;
use crate::model::{h1, rule};
use crate::sis::{self, N_SIS};

#[derive(Clone)]
pub struct ComposedAir {
    /// true: post-quantum variant with the SIS opening and the amount link.
    pub pq: bool,
    periodic: Vec<Vec<BabyBear>>,
}

impl ComposedAir {
    pub fn new(pq: bool) -> Self {
        ComposedAir { pq, periodic: periodic_columns(pq) }
    }

    pub fn width_of(pq: bool) -> usize {
        if pq { WIDTH_CLASSICAL + ACC_SIS + N_SIS } else { WIDTH_CLASSICAL }
    }

    pub fn num_periodic_columns_pub(&self) -> usize {
        self.periodic.len()
    }

    pub fn num_public_values_of(pq: bool) -> usize {
        if pq { PV_C + N_SIS } else { PV_C }
    }
}

fn f(v: i64) -> BabyBear {
    BabyBear::from_i64(v)
}

/// Verifier-computed periodic columns (period = trace length): model weights, input bounds,
/// window-row selectors and, for the PQ variant, the public SIS matrix and the amount-link weights.
pub fn periodic_columns(pq: bool) -> Vec<Vec<BabyBear>> {
    let s = h1();
    let n_cols = if pq { PE_G + 2 * N_SIS } else { CLASSICAL_PERIODIC };
    let mut cols = vec![vec![BabyBear::ZERO; ROWS]; n_cols];
    for r in 0..F_IN {
        cols[PE_INP][r] = BabyBear::ONE;
        cols[PE_LO][r] = f(s.x_lo[r]);
        cols[PE_HI][r] = f(s.x_hi[r]);
        for n in 0..H {
            cols[PE_W1 + n][r] = f(s.w1[n][r]);
        }
    }
    for k in 0..H {
        let ra = SH_A_START + k;
        cols[PE_SHA][ra] = BabyBear::ONE;
        cols[PE_B1][ra] = f(s.b1[k]);
        for m in 0..H {
            cols[PE_W2 + m][ra] = f(s.w2[m][k]);
        }
        cols[PE_DMOVE][ra] = BabyBear::ONE;
        cols[PE_DIN][ra] = BabyBear::ONE;
        let rb = SH_B_START + k;
        cols[PE_SHB][rb] = BabyBear::ONE;
        cols[PE_B2][rb] = f(s.b2[k]);
        cols[PE_W3][rb] = f(s.w3[k]);
        cols[PE_DMOVE][rb] = BabyBear::ONE;
    }
    for (j, &row) in SELECTOR_ROWS.iter().enumerate() {
        cols[PE_SEL + j][row] = BabyBear::ONE;
    }
    if pq {
        for t in 0..ROWS {
            let (ca, cb) = (2 * t, 2 * t + 1);
            if cb < sis::AMOUNT_COORDS {
                cols[PE_ISBIT][t] = BabyBear::ONE;
                if ca >= AMOUNT_BITS {
                    cols[PE_HZ][t] = BabyBear::ONE;
                } else {
                    cols[PE_WA][t] = f(1i64 << ca);
                    cols[PE_WB][t] = f(1i64 << cb);
                }
            }
            for j in 0..N_SIS {
                cols[PE_G + 2 * j][t] = BabyBear::from_u64(sis::entry(j, ca));
                cols[PE_G + 2 * j + 1][t] = BabyBear::from_u64(sis::entry(j, cb));
            }
        }
    }
    cols
}

impl BaseAir<BabyBear> for ComposedAir {
    fn width(&self) -> usize {
        Self::width_of(self.pq)
    }

    fn num_periodic_columns(&self) -> usize {
        self.periodic.len()
    }

    fn periodic_columns(&self) -> Cow<'_, [Vec<BabyBear>]> {
        Cow::Borrowed(&self.periodic)
    }

    fn num_public_values(&self) -> usize {
        Self::num_public_values_of(self.pq)
    }
}

fn bits_value<AB: AirBuilder>(row: &[AB::Var], start: usize, n: usize) -> AB::Expr {
    let mut acc = AB::Expr::ZERO;
    for j in 0..n {
        acc += row[start + j].into() * AB::Expr::from_i64(1i64 << j);
    }
    acc
}

impl<AB: AirBuilder<F = BabyBear>> Air<AB> for ComposedAir {
    fn eval(&self, builder: &mut AB) {
        let main = builder.main();
        let cur: Vec<AB::Var> = main.current_slice().to_vec();
        let nxt: Vec<AB::Var> = main.next_slice().to_vec();
        let per: Vec<AB::Expr> = builder.periodic_values().iter().map(|&v| v.into()).collect();
        let pv: Vec<AB::Expr> = builder.public_values().iter().map(|&v| v.into()).collect();
        let c = |v: i64| AB::Expr::from_i64(v);
        let e = |v: AB::Var| -> AB::Expr { v.into() };
        let s = h1();
        let r = rule();

        // =========================================================== H1 inference block
        let (inp, lo, hi) = (per[PE_INP].clone(), per[PE_LO].clone(), per[PE_HI].clone());
        let (sha, shb) = (per[PE_SHA].clone(), per[PE_SHB].clone());
        let x = e(cur[X]);
        // input range: x - lo in [0, 2^12), hi - x in [0, 2^12) on input rows
        builder.assert_zero(inp.clone() * x.clone() - lo - bits_value::<AB>(&cur, XB, XBITS));
        builder.assert_zero(hi - inp * x.clone() - bits_value::<AB>(&cur, XC, XBITS));
        for j in 0..XBITS {
            builder.assert_bool(cur[XB + j]);
            builder.assert_bool(cur[XC + j]);
        }
        let out1 = e(cur[OUT1]);
        let out2 = e(cur[OUT2]);
        {
            let mut t = builder.when_transition();
            // bank A: accumulate W1 x on input rows, shift out during SH_A rows
            for n in 0..H {
                let shifted = if n + 1 < H { e(cur[ACC_A + n + 1]) } else { AB::Expr::ZERO };
                t.assert_zero(
                    e(nxt[ACC_A + n]) - (AB::Expr::ONE - sha.clone()) * e(cur[ACC_A + n]) - sha.clone() * shifted
                        - per[PE_W1 + n].clone() * x.clone(),
                );
            }
            // bank B: accumulate W2 h, shift out during SH_B rows
            for m in 0..H {
                let shifted = if m + 1 < H { e(cur[ACC_B + m + 1]) } else { AB::Expr::ZERO };
                t.assert_zero(
                    e(nxt[ACC_B + m]) - (AB::Expr::ONE - shb.clone()) * e(cur[ACC_B + m]) - shb.clone() * shifted
                        - per[PE_W2 + m].clone() * out1.clone(),
                );
            }
            // delay line carrying h for the residual connection
            let (dmove, din) = (per[PE_DMOVE].clone(), per[PE_DIN].clone());
            for j in 0..H {
                let shifted = if j + 1 < H { dmove.clone() * e(cur[DL + j + 1]) } else { din.clone() * out1.clone() };
                t.assert_zero(e(nxt[DL + j]) - (AB::Expr::ONE - dmove.clone()) * e(cur[DL + j]) - shifted);
            }
            // head accumulator
            t.assert_zero(e(nxt[C_HEAD]) - e(cur[C_HEAD]) - per[PE_W3].clone() * out2.clone());
        }
        // pipeline 1: acc_A0 + b1 = 2^s1 q + rem, out1 = relu(q)
        let q1 = bits_value::<AB>(&cur, P1Q, Q1) - c(1 << (Q1 - 1));
        let rem1 = bits_value::<AB>(&cur, P1R, R1);
        builder.assert_zero(sha.clone() * (e(cur[ACC_A]) - c(1i64 << s.s1) * q1.clone() - rem1) + per[PE_B1].clone());
        builder.assert_zero(sha * (out1 - q1 * e(cur[P1Q + Q1 - 1])));
        for j in 0..Q1 + R1 {
            builder.assert_bool(cur[P1Q + j]);
        }
        // pipeline 2: acc_B0 + b2 = 2^s2 q + rem, out2 = relu(q) + h (residual from the delay line)
        let q2 = bits_value::<AB>(&cur, P2Q, Q2) - c(1 << (Q2 - 1));
        let rem2 = bits_value::<AB>(&cur, P2R, R2);
        builder.assert_zero(shb.clone() * (e(cur[ACC_B]) - c(1i64 << s.s2) * q2.clone() - rem2) + per[PE_B2].clone());
        builder.assert_zero(shb * (out2 - q2 * e(cur[P2Q + Q2 - 1]) - e(cur[DL])));
        for j in 0..Q2 + R2 {
            builder.assert_bool(cur[P2Q + j]);
        }
        {
            let mut first = builder.when_first_row();
            for n in 0..H {
                first.assert_zero(cur[ACC_A + n]);
                first.assert_zero(cur[ACC_B + n]);
                first.assert_zero(cur[DL + n]);
            }
            first.assert_zero(cur[C_HEAD]);
        }

        // =========================================================== H3 rule block
        let reg = |i: usize| -> AB::Expr { cur[RB + i].into() };
        {
            let mut t = builder.when_transition();
            for i in 0..REGS {
                t.assert_zero(e(nxt[RB + i]) - e(cur[RB + i]));
            }
        }
        let (m_pub, sid_pub, slash_pub) = (pv[PV_M].clone(), pv[PV_SID].clone(), pv[PV_SLASH].clone());
        let diff = reg(D) - sid_pub;
        builder.assert_bool(cur[RB + Z]);
        builder.assert_bool(cur[RB + RISK]);
        builder.assert_zero(reg(FL) * diff.clone());
        builder.assert_zero(reg(FL) - AB::Expr::ONE + diff * reg(INV));
        builder.assert_zero(reg(ACT) - reg(Z) * reg(POS));
        builder.assert_zero(reg(MN) - reg(CGE) * slash_pub.clone() - (AB::Expr::ONE - reg(CGE)) * reg(S));
        builder.assert_zero(reg(ES) - reg(ACT) * reg(MN));
        builder.assert_zero(reg(V0) - reg(GT) - reg(FL) + reg(GT) * reg(FL));
        builder.assert_zero(reg(VIOL) - reg(V0) - reg(RISK) + reg(V0) * reg(RISK));
        let (qe, pe) = (c(r.q), c(r.p_detect));
        builder.assert_zero(
            reg(EXV) - reg(VIOL) * (qe.clone() * reg(VP) - pe * reg(ES) - qe.clone() * reg(CP)) - qe.clone() * reg(CP),
        );
        for j in 0..WB {
            builder.assert_bool(cur[RB + WIN + j]);
        }
        let w = bits_value::<AB>(&cur, RB + WIN, WB);
        let off = c(OFF);
        let d = e(cur[C_HEAD]) + c(s.b3 - s.threshold - 1);
        let values = [
            reg(A) - m_pub - AB::Expr::ONE + off.clone(),
            reg(S) - AB::Expr::ONE + off.clone(),
            reg(S) - slash_pub + off.clone(),
            reg(CP),
            reg(VP),
            reg(EXV) - qe * reg(CP) - AB::Expr::ONE + off,
            (c(2) * reg(RISK) - AB::Expr::ONE) * d - (AB::Expr::ONE - reg(RISK)),
        ];
        for (j, v) in values.into_iter().enumerate() {
            builder.assert_zero(per[PE_SEL + j].clone() * (v - w.clone()));
        }
        let top = e(cur[RB + WIN + TOP]);
        let high = |from: usize| (from..WB).fold(AB::Expr::ZERO, |acc, j| acc + e(cur[RB + WIN + j]));
        builder.assert_zero(per[PE_SEL].clone() * (reg(GT) - top.clone()));
        builder.assert_zero(per[PE_SEL + 1].clone() * (reg(POS) - top.clone()));
        builder.assert_zero(per[PE_SEL + 2].clone() * (reg(CGE) - top.clone()));
        builder.assert_zero(per[PE_SEL + 3].clone() * high(CB));
        builder.assert_zero(per[PE_SEL + 4].clone() * high(VB));
        // EXEC = 1 - VIOL + VIOL * [EXV > q*CP]: compliant transfers execute; violating ones follow the H3 rule
        builder.assert_zero(per[PE_SEL + 5].clone() * (reg(EX) - AB::Expr::ONE + reg(VIOL) - reg(VIOL) * top));
        {
            let mut first = builder.when_first_row();
            first.assert_eq(reg(EX), pv[PV_EXEC].clone());
            first.assert_eq(reg(VIOL), pv[PV_VIOL].clone());
        }

        if !self.pq {
            return;
        }
        // =========================================================== SIS opening + amount link
        let sc = |i: usize| -> AB::Expr { cur[SB + i].into() };
        let (sa, sb) = (sc(SA_), sc(SB_));
        for u in [U0A, U1A, U0B, U1B] {
            builder.assert_bool(cur[SB + u]);
        }
        // every coordinate is ternary: s + 1 = u0 + 2 u1 with u0 u1 = 0
        builder.assert_zero(sc(U0A) * sc(U1A));
        builder.assert_zero(sc(U0B) * sc(U1B));
        builder.assert_zero(sa.clone() + AB::Expr::ONE - sc(U0A) - c(2) * sc(U1A));
        builder.assert_zero(sb.clone() + AB::Expr::ONE - sc(U0B) - c(2) * sc(U1B));
        // amount coordinates are bits; those at or above AMOUNT_BITS are zero
        let isbit = per[PE_ISBIT].clone();
        builder.assert_zero(isbit.clone() * sa.clone() * (sa.clone() - AB::Expr::ONE));
        builder.assert_zero(isbit * sb.clone() * (sb.clone() - AB::Expr::ONE));
        builder.assert_zero(per[PE_HZ].clone() * sa.clone());
        builder.assert_zero(per[PE_HZ].clone() * sb.clone());
        // accumulators: ACC_j += M[j][2t] s_2t + M[j][2t+1] s_2t+1 ; LINK += 2^i bit_i
        let step = |j: usize| per[PE_G + 2 * j].clone() * sa.clone() + per[PE_G + 2 * j + 1].clone() * sb.clone();
        let link_step = per[PE_WA].clone() * sa.clone() + per[PE_WB].clone() * sb.clone();
        {
            let mut t = builder.when_transition();
            for j in 0..N_SIS {
                t.assert_zero(e(nxt[SB + ACC_SIS + j]) - sc(ACC_SIS + j) - step(j));
            }
            t.assert_zero(e(nxt[SB + LINK]) - sc(LINK) - link_step.clone());
        }
        {
            let mut first = builder.when_first_row();
            for j in 0..N_SIS {
                first.assert_zero(cur[SB + ACC_SIS + j]);
            }
            first.assert_zero(cur[SB + LINK]);
        }
        {
            let mut last = builder.when_last_row();
            for j in 0..N_SIS {
                last.assert_eq(sc(ACC_SIS + j) + step(j), pv[PV_C + j].clone());
            }
            // the decision's amount register equals the committed amount
            last.assert_eq(sc(LINK) + link_step, reg(A));
        }
    }
}
