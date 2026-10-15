//! Trace generation for one transaction (port of benchmark_v2 h1mlp::fill + compose::fill, plus the
//! SIS opening block).

use p3_baby_bear::BabyBear;
use p3_field::{Field, PrimeCharacteristicRing};
use p3_matrix::dense::RowMajorMatrix;

use crate::air::ComposedAir;
use crate::layout::*;
use crate::model::{Decision, Policy, h1, h1_forward, rule};
use crate::sis::{self, N_SIS};

/// Everything the prover knows about one transaction.
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Witness {
    /// Quantized model input (165 integers, inside the model's clipping box).
    pub x: Vec<i64>,
    pub decision: Decision,
    pub policy: Policy,
    /// Ternary commitment randomness (448 values in {-1,0,1}); used by the PQ variant.
    pub r: Vec<i64>,
}

/// Overrides used by tests to build malicious traces.
#[derive(Default, Clone)]
pub struct Tamper {
    pub risk: Option<i64>,
    pub exec: Option<i64>,
    pub viol: Option<i64>,
    /// Amount used inside the commitment opening instead of the decision's amount.
    pub committed_amount: Option<u64>,
    /// (row, column, value): overwrite one cell after filling.
    pub cell: Option<(usize, usize, i64)>,
}

fn felt(v: i64) -> BabyBear {
    BabyBear::from_i64(v)
}

fn put_bits(row: &mut [BabyBear], start: usize, n: usize, v: i64) {
    assert!(v >= 0 && (n >= 63 || v < (1i64 << n)), "value {v} out of {n}-bit range");
    for j in 0..n {
        row[start + j] = BabyBear::from_u64(((v >> j) & 1) as u64);
    }
}

/// Public values of the statement for this witness: [M, sid, slash, EXEC, VIOL, (c...)].
pub fn public_values(w: &Witness, pq: bool) -> Vec<BabyBear> {
    let (exec, viol, _) = crate::model::decide(&w.x, &w.decision, &w.policy);
    let mut pv = vec![felt(w.policy.max_compliant), felt(w.policy.sanctioned_id), felt(w.policy.slash), felt(exec), felt(viol)];
    if pq {
        let c = sis::commit(&sis::opening(w.decision.amount as u64, &w.r));
        pv.extend(c.into_iter().map(BabyBear::from_u64));
    }
    pv
}

pub fn build(w: &Witness, pq: bool, tamper: &Tamper) -> RowMajorMatrix<BabyBear> {
    let width = ComposedAir::width_of(pq);
    let mut t = vec![BabyBear::ZERO; ROWS * width];
    let s = h1();
    let x = &w.x;
    assert_eq!(x.len(), F_IN);

    // ------------------------------------------------------------------ H1 block
    let mut acc_a = vec![0i64; H];
    let mut acc_b = vec![0i64; H];
    let mut dl = vec![0i64; H];
    let mut c_head = 0i64;
    for row in 0..ROWS {
        let r = &mut t[row * width..(row + 1) * width];
        for n in 0..H {
            r[ACC_A + n] = felt(acc_a[n]);
            r[ACC_B + n] = felt(acc_b[n]);
            r[DL + n] = felt(dl[n]);
        }
        r[C_HEAD] = felt(c_head);
        let xv = if row < F_IN { x[row] } else { 0 };
        r[X] = felt(xv);
        if row < F_IN {
            put_bits(r, XB, XBITS, (xv - s.x_lo[row]).clamp(0, (1 << XBITS) - 1));
            put_bits(r, XC, XBITS, (s.x_hi[row] - xv).clamp(0, (1 << XBITS) - 1));
        }
        let (mut out1, mut out2) = (0i64, 0i64);
        if (SH_A_START..SH_B_START).contains(&row) {
            let k = row - SH_A_START;
            let a = acc_a[0] + s.b1[k];
            let (q, rem) = (a.div_euclid(1 << s.s1), a.rem_euclid(1 << s.s1));
            put_bits(r, P1Q, Q1, (q + (1 << (Q1 - 1))).clamp(0, (1 << Q1) - 1));
            put_bits(r, P1R, R1, rem);
            out1 = q.max(0);
        } else {
            put_bits(r, P1Q, Q1, 1 << (Q1 - 1));
        }
        if (SH_B_START..FINAL).contains(&row) {
            let k = row - SH_B_START;
            let a = acc_b[0] + s.b2[k];
            let (q, rem) = (a.div_euclid(1 << s.s2), a.rem_euclid(1 << s.s2));
            put_bits(r, P2Q, Q2, (q + (1 << (Q2 - 1))).clamp(0, (1 << Q2) - 1));
            put_bits(r, P2R, R2, rem);
            out2 = q.max(0) + dl[0];
        } else {
            put_bits(r, P2Q, Q2, 1 << (Q2 - 1));
        }
        r[OUT1] = felt(out1);
        r[OUT2] = felt(out2);
        let sha = (SH_A_START..SH_B_START).contains(&row);
        let shb = (SH_B_START..FINAL).contains(&row);
        let (dmove, din) = ((SH_A_START..FINAL).contains(&row), sha);
        for n in 0..H {
            let w1 = if row < F_IN { s.w1[n][row] } else { 0 };
            acc_a[n] = if sha { if n + 1 < H { acc_a[n + 1] } else { 0 } } else { acc_a[n] } + w1 * xv;
        }
        for m in 0..H {
            let w2 = if sha { s.w2[m][row - SH_A_START] } else { 0 };
            acc_b[m] = if shb { if m + 1 < H { acc_b[m + 1] } else { 0 } } else { acc_b[m] } + w2 * out1;
        }
        dl = (0..H)
            .map(|j| if dmove { if j + 1 < H { dl[j + 1] } else if din { out1 } else { 0 } } else { dl[j] })
            .collect();
        let w3 = if shb { s.w3[row - SH_B_START] } else { 0 };
        c_head += w3 * out2;
    }

    // ------------------------------------------------------------------ rule block
    let ru = rule();
    let pol = &w.policy;
    let dcs = &w.decision;
    let logit = h1_forward(x).2;
    let risk = tamper.risk.unwrap_or((logit > s.threshold) as i64);
    let (a, d, cp, vp, z, st) = (dcs.amount, dcs.dest, dcs.compliant, dcs.violation, dcs.staked, dcs.stake);
    let fl = (d == pol.sanctioned_id) as i64;
    let gt = (a > pol.max_compliant) as i64;
    let pos = (st > 0) as i64;
    let cge = (st >= pol.slash) as i64;
    let act = z * pos;
    let mn = cge * pol.slash + (1 - cge) * st;
    let es = act * mn;
    let v0 = gt + fl - gt * fl;
    let viol = tamper.viol.unwrap_or(v0 + risk - v0 * risk);
    let exv = viol * (ru.q * vp - ru.p_detect * es - ru.q * cp) + ru.q * cp;
    let ex = tamper.exec.unwrap_or((exv > ru.q * cp) as i64);
    let inv = if fl == 1 { BabyBear::ZERO } else { felt(d - pol.sanctioned_id).inverse() };
    let mut regs = [BabyBear::ZERO; REGS];
    for (i, v) in [
        (A, a), (D, d), (FL, fl), (CP, cp), (VP, vp), (Z, z), (S, st), (GT, gt), (POS, pos), (CGE, cge),
        (RISK, risk), (ACT, act), (MN, mn), (ES, es), (V0, v0), (VIOL, viol), (EXV, exv), (EX, ex),
    ] {
        regs[i] = felt(v);
    }
    regs[INV] = inv;
    let dd = logit - s.threshold - 1;
    let clamp = |v: i64| v.clamp(0, (1i64 << WB) - 1);
    let windows = [
        (0, a - pol.max_compliant - 1 + OFF),
        (1, st - 1 + OFF),
        (2, st - pol.slash + OFF),
        (3, cp),
        (4, vp),
        (5, exv - ru.q * cp - 1 + OFF),
        (ROW_RISK, (2 * risk - 1) * dd - (1 - risk)),
    ];
    for row in 0..ROWS {
        let r = &mut t[row * width..(row + 1) * width];
        r[RB..RB + REGS].copy_from_slice(&regs);
        let v = windows.iter().find(|(rr, _)| *rr == row).map_or(0, |(_, v)| clamp(*v));
        put_bits(r, RB + WIN, WB, v);
    }

    // ------------------------------------------------------------------ SIS block
    if pq {
        let committed = tamper.committed_amount.unwrap_or(a as u64);
        let svec = sis::opening(committed, &w.r);
        let mut acc = vec![0i128; N_SIS];
        let mut link: i128 = 0;
        let p = sis::P as i128;
        for row in 0..ROWS {
            let r = &mut t[row * width..(row + 1) * width];
            let (ca, cb) = (2 * row, 2 * row + 1);
            let (va, vb) = (svec[ca], svec[cb]);
            r[SB + SA_] = felt(va);
            r[SB + SB_] = felt(vb);
            let tern = |v: i64| -> (u64, u64) {
                let u = (v + 1).clamp(0, 3);
                ((u & 1) as u64, (u >> 1) as u64)
            };
            let (u0a, u1a) = tern(va);
            let (u0b, u1b) = tern(vb);
            r[SB + U0A] = BabyBear::from_u64(u0a);
            r[SB + U1A] = BabyBear::from_u64(u1a);
            r[SB + U0B] = BabyBear::from_u64(u0b);
            r[SB + U1B] = BabyBear::from_u64(u1b);
            r[SB + LINK] = felt((link % p) as i64);
            for j in 0..N_SIS {
                r[SB + ACC_SIS + j] = BabyBear::from_u64(acc[j].rem_euclid(p) as u64);
                acc[j] += sis::entry(j, ca) as i128 * va as i128 + sis::entry(j, cb) as i128 * vb as i128;
            }
            if cb < AMOUNT_BITS {
                link += (1i128 << ca) * va as i128 + (1i128 << cb) * vb as i128;
            }
        }
    }

    if let Some((row, col, v)) = tamper.cell {
        t[row * width + col] = felt(v);
    }
    RowMajorMatrix::new(t, width)
}
