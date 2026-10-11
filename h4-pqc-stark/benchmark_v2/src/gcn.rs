//! H2 GCN inference for one target node (GCNConv 166->64 ReLU, GCNConv 64->2), exact integer
//! semantics from coverage/quantize_h2.py (specs/h2_spec.json).
//!
//! Layout (L = 256 rows): feature i of every receptive-field node streams down its own input
//! column (range-checked to [lo_i, lo_i + 2^BX)). For each layer-1 node u, a bank of 64
//! accumulators adds W1[n][i] * sum_w A[u][w] x_w[i]. Rows 166..230 shift both banks out through
//! their own pipeline (acc + b1 = 2^s1 q + rem, h = relu(q)) and the two class accumulators add
//! W2[c][n] * sum_u A[v][u] h_u[n]. At row 230 they are asserted equal to the public logits minus b2.
//! Requant window 2^(qbits + s1) = 2^22 < p (unique decomposition).

use std::sync::OnceLock;

use serde_json::Value;
use winterfell::{
    math::{fields::f23201::BaseElement, FieldElement, StarkField},
    Assertion, TransitionConstraintDegree,
};

use crate::gadget::{bits_value, booleans, c, felt, fill_bits};

const F: usize = 166; // H2 keeps the time step as a feature
const HD: usize = 64;
const NS: usize = 3; // receptive-field streams
const NU: usize = 2; // layer-1 nodes
const NC: usize = 2; // classes
const SH_START: usize = F;
const FINAL: usize = F + HD;
pub const MIN_LEN: usize = FINAL + 1;
const BX: usize = 8;
const QB: usize = 14;
const RB: usize = 8;

const XS: usize = 0;
const XB: usize = XS + NS;
const ACC: usize = XB + NS * BX;
const PIPE: usize = ACC + NU * HD;
const PIPE_W: usize = QB + RB + 1;
const LOG: usize = PIPE + NU * PIPE_W;
pub const WIDTH: usize = LOG + NC;

const PE_INP: usize = 0;
const PE_LO: usize = 1;
const PE_W1: usize = 2;
const PE_SH: usize = PE_W1 + HD;
const PE_B1: usize = PE_SH + 1;
const PE_W2: usize = PE_B1 + 1;
pub const PERIODIC: usize = PE_W2 + NC;

#[cfg(test)]
pub const CONSTRAINTS: usize = NS + NS * BX + NU * HD + NU * (2 + QB + RB) + NC;
pub const ASSERTIONS: usize = NU * HD + 2 * NC;

pub struct Spec {
    pub s1: u32,
    pub w1: Vec<Vec<i64>>,
    pub b1: Vec<i64>,
    pub w2: Vec<Vec<i64>>,
    pub b2: Vec<i64>,
    pub x_lo: Vec<i64>,
    /// A[u][s]: normalized adjacency of layer-1 node u to stream s (0 if not a neighbor)
    pub a1: Vec<Vec<i64>>,
    /// A[v][u]: target row over layer-1 nodes
    pub a2: Vec<i64>,
    pub x: Vec<Vec<i64>>,
    pub logits: Vec<i64>,
}

fn ints(v: &Value) -> Vec<i64> {
    v.as_array().expect("array").iter().map(|e| e.as_i64().expect("int")).collect()
}

pub fn spec() -> &'static Spec {
    static SPEC: OnceLock<Spec> = OnceLock::new();
    SPEC.get_or_init(|| {
        let v: Value = serde_json::from_str(include_str!("../specs/h2_spec.json")).expect("h2 spec");
        let streams: Vec<String> = v["streams"].as_array().unwrap().iter().map(|e| e.as_i64().unwrap().to_string()).collect();
        let nodes: Vec<String> = v["layer1_nodes"].as_array().unwrap().iter().map(|e| e.as_i64().unwrap().to_string()).collect();
        let target = v["target"].as_i64().unwrap().to_string();
        let a = &v["A"];
        let a1 = nodes.iter().map(|u| streams.iter().map(|w| a[u].get(w).and_then(|e| e.as_i64()).unwrap_or(0)).collect()).collect();
        let a2 = nodes.iter().map(|u| a[&target].get(u).and_then(|e| e.as_i64()).unwrap_or(0)).collect();
        let s = Spec {
            s1: v["s1"].as_u64().unwrap() as u32,
            w1: v["W1"].as_array().unwrap().iter().map(ints).collect(),
            b1: ints(&v["b1"]),
            w2: v["W2"].as_array().unwrap().iter().map(ints).collect(),
            b2: ints(&v["b2"]),
            x_lo: ints(&v["x_lo"]),
            a1,
            a2,
            x: streams.iter().map(|w| ints(&v["x"][w])).collect(),
            logits: ints(&v["logits"]),
        };
        assert!(streams.len() == NS && nodes.len() == NU && nodes[0] == target);
        assert!(s.s1 as usize == RB && v["qbits"].as_u64().unwrap() as usize == QB && v["bx"].as_u64().unwrap() as usize == BX);
        assert!(s.w1.len() == HD && s.w1[0].len() == F && s.w2.len() == NC && s.w2[0].len() == HD);
        s
    })
}

pub fn outputs() -> Vec<u64> {
    spec().logits.iter().map(|&l| felt(l).as_int() as u64).collect()
}

fn pq(u: usize) -> usize {
    PIPE + u * PIPE_W
}

pub fn degrees(len: usize) -> Vec<TransitionConstraintDegree> {
    let p1 = || TransitionConstraintDegree::with_cycles(1, vec![len]);
    let p2 = || TransitionConstraintDegree::with_cycles(2, vec![len]);
    let two = || TransitionConstraintDegree::new(2);
    let mut d: Vec<_> = (0..NS).map(|_| p1()).collect();
    d.extend((0..NS * BX).map(|_| two()));
    d.extend((0..NU * HD).map(|_| p1()));
    for _ in 0..NU {
        d.extend([p1(), p2()]);
        d.extend((0..QB + RB).map(|_| two()));
    }
    d.extend((0..NC).map(|_| p1()));
    d
}

pub fn constraints<E: FieldElement + From<BaseElement>>(cur: &[E], next: &[E], per: &[E], base: usize, out: &mut [E]) {
    let s = spec();
    let (inp, lo, sh) = (per[PE_INP], per[PE_LO], per[PE_SH]);
    let mut k = 0;
    for st in 0..NS {
        out[k] = inp * cur[base + XS + st] - lo - bits_value(cur, base + XB + st * BX, BX);
        k += 1;
    }
    for st in 0..NS {
        booleans(cur, base + XB + st * BX, BX, &mut out[k..]);
        k += BX;
    }
    for u in 0..NU {
        let mut agg = E::ZERO;
        for st in 0..NS {
            agg += c::<E>(s.a1[u][st]) * cur[base + XS + st];
        }
        for n in 0..HD {
            let a = base + ACC + u * HD + n;
            let shifted = if n + 1 < HD { cur[a + 1] } else { E::ZERO };
            out[k] = next[a] - (E::ONE - sh) * cur[a] - sh * shifted - per[PE_W1 + n] * agg;
            k += 1;
        }
    }
    let mut hagg = E::ZERO;
    for u in 0..NU {
        let b = base + pq(u);
        let q = bits_value(cur, b, QB) - c::<E>(1 << (QB - 1));
        let rem = bits_value(cur, b + QB, RB);
        let outu = cur[b + QB + RB];
        out[k] = sh * (cur[base + ACC + u * HD] - c::<E>(1i64 << s.s1) * q - rem) + per[PE_B1];
        out[k + 1] = sh * (outu - q * cur[b + QB - 1]);
        k += 2;
        booleans(cur, b, QB + RB, &mut out[k..]);
        k += QB + RB;
        hagg += c::<E>(s.a2[u]) * outu;
    }
    for cl in 0..NC {
        out[k + cl] = next[base + LOG + cl] - cur[base + LOG + cl] - per[PE_W2 + cl] * hagg;
    }
}

pub fn assertions(base: usize, outputs: &[u64]) -> Vec<Assertion<BaseElement>> {
    let mut v = Vec::with_capacity(ASSERTIONS);
    for i in 0..NU * HD {
        v.push(Assertion::single(base + ACC + i, 0, BaseElement::ZERO));
    }
    for cl in 0..NC {
        v.push(Assertion::single(base + LOG + cl, 0, BaseElement::ZERO));
        v.push(Assertion::single(base + LOG + cl, FINAL, BaseElement::from(outputs[cl]) - felt(spec().b2[cl])));
    }
    v
}

pub fn periodic_columns(len: usize) -> Vec<Vec<BaseElement>> {
    let s = spec();
    let mut cols = vec![vec![BaseElement::ZERO; len]; PERIODIC];
    for r in 0..F {
        cols[PE_INP][r] = BaseElement::ONE;
        cols[PE_LO][r] = felt(s.x_lo[r]);
        for n in 0..HD {
            cols[PE_W1 + n][r] = felt(s.w1[n][r]);
        }
    }
    for n in 0..HD {
        let r = SH_START + n;
        cols[PE_SH][r] = BaseElement::ONE;
        cols[PE_B1][r] = felt(s.b1[n]);
        for cl in 0..NC {
            cols[PE_W2 + cl][r] = felt(s.w2[cl][n]);
        }
    }
    cols
}

#[cfg(test)]
/// Integer reference (same schedule as the circuit).
pub fn reference(x: &[Vec<i64>]) -> Vec<i64> {
    let s = spec();
    let h: Vec<Vec<i64>> = (0..NU)
        .map(|u| {
            (0..HD)
                .map(|n| {
                    let a: i64 = (0..F).map(|i| s.w1[n][i] * (0..NS).map(|st| s.a1[u][st] * x[st][i]).sum::<i64>()).sum::<i64>() + s.b1[n];
                    a.div_euclid(1 << s.s1).max(0)
                })
                .collect()
        })
        .collect();
    (0..NC).map(|cl| (0..HD).map(|n| s.w2[cl][n] * (0..NU).map(|u| s.a2[u] * h[u][n]).sum::<i64>()).sum::<i64>() + s.b2[cl]).collect()
}

pub fn fill(columns: &mut [Vec<BaseElement>], base: usize, len: usize, x: &[Vec<i64>]) {
    let s = spec();
    let mut acc = vec![vec![0i64; HD]; NU];
    let mut logits = vec![0i64; NC];
    for row in 0..len {
        for u in 0..NU {
            for n in 0..HD {
                columns[base + ACC + u * HD + n][row] = felt(acc[u][n]);
            }
        }
        for cl in 0..NC {
            columns[base + LOG + cl][row] = felt(logits[cl]);
        }
        let xv: Vec<i64> = (0..NS).map(|st| if row < F { x[st][row] } else { 0 }).collect();
        for st in 0..NS {
            columns[base + XS + st][row] = felt(xv[st]);
            let d = if row < F { (xv[st] - s.x_lo[row]).clamp(0, (1 << BX) - 1) } else { 0 };
            fill_bits(columns, base + XB + st * BX, BX, row, d);
        }
        let sh = (SH_START..FINAL).contains(&row);
        let mut outs = vec![0i64; NU];
        for u in 0..NU {
            let b = base + pq(u);
            if sh {
                let a = acc[u][0] + s.b1[row - SH_START];
                let (q, rem) = (a.div_euclid(1 << s.s1), a.rem_euclid(1 << s.s1));
                fill_bits(columns, b, QB, row, (q + (1 << (QB - 1))).clamp(0, (1 << QB) - 1));
                fill_bits(columns, b + QB, RB, row, rem);
                outs[u] = q.max(0);
            } else {
                fill_bits(columns, b, QB, row, 1 << (QB - 1));
                fill_bits(columns, b + QB, RB, row, 0);
            }
            columns[b + QB + RB][row] = felt(outs[u]);
        }
        // transition
        for u in 0..NU {
            let agg: i64 = (0..NS).map(|st| s.a1[u][st] * xv[st]).sum();
            let next: Vec<i64> = (0..HD)
                .map(|n| {
                    let w1 = if row < F { s.w1[n][row] } else { 0 };
                    let kept = if sh { if n + 1 < HD { acc[u][n + 1] } else { 0 } } else { acc[u][n] };
                    kept + w1 * agg
                })
                .collect();
            acc[u] = next;
        }
        if sh {
            let hagg: i64 = (0..NU).map(|u| s.a2[u] * outs[u]).sum();
            for cl in 0..NC {
                logits[cl] += s.w2[cl][row - SH_START] * hagg;
            }
        }
    }
}
