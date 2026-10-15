//! The trained, quantized H1 model (specs/h1_spec.json, from coverage/quantize_h1.py) and the H3
//! decision-rule constants (specs/h3_spec.json, from coverage/export_h3.py). The model is part of the
//! AIR (weights are periodic columns), so a proof is a proof about THIS model; `model_digest` names it.

use std::sync::OnceLock;

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::layout::{F_IN, H, R1, R2};

pub const H1_SPEC: &str = include_str!("../specs/h1_spec.json");
pub const H3_SPEC: &str = include_str!("../specs/h3_spec.json");

pub struct H1 {
    pub s1: u32,
    pub s2: u32,
    pub ax: u32,
    pub w1: Vec<Vec<i64>>, // [H][F_IN]
    pub b1: Vec<i64>,
    pub w2: Vec<Vec<i64>>, // [H][H]
    pub b2: Vec<i64>,
    pub w3: Vec<i64>,
    pub b3: i64,
    pub x_lo: Vec<i64>,
    pub x_hi: Vec<i64>,
    pub threshold: i64,
    pub example_x: Vec<i64>,
}

pub struct Rule {
    pub q: i64,       // payoff scale (256)
    pub p_detect: i64, // detection probability scaled by q (256 = certain)
    pub example_rows: Vec<[i64; 6]>, // amount, dest, compliant, violation, staked, stake
    pub max_compliant: i64,
    pub slash: i64,
    pub sanctioned_id: i64,
}

fn ints(v: &Value) -> Vec<i64> {
    v.as_array().expect("array").iter().map(|e| e.as_i64().expect("int")).collect()
}

pub fn h1() -> &'static H1 {
    static M: OnceLock<H1> = OnceLock::new();
    M.get_or_init(|| {
        let v: Value = serde_json::from_str(H1_SPEC).expect("h1 spec");
        let mat = |k: &str| v[k].as_array().unwrap().iter().map(ints).collect::<Vec<_>>();
        let m = H1 {
            s1: v["s1"].as_u64().unwrap() as u32,
            s2: v["s2"].as_u64().unwrap() as u32,
            ax: v["ax"].as_u64().unwrap() as u32,
            w1: mat("W1"),
            b1: ints(&v["b1"]),
            w2: mat("W2"),
            b2: ints(&v["b2"]),
            w3: mat("W3").remove(0),
            b3: ints(&v["b3"])[0],
            x_lo: ints(&v["x_lo"]),
            x_hi: ints(&v["x_hi"]),
            threshold: v["threshold_q"].as_i64().unwrap(),
            example_x: ints(&v["x"]),
        };
        assert!(m.s1 as usize == R1 && m.s2 as usize == R2, "pipeline widths must match the spec shifts");
        assert!(m.w1.len() == H && m.w1[0].len() == F_IN && m.w2.len() == H && m.w3.len() == H);
        m
    })
}

pub fn rule() -> &'static Rule {
    static R: OnceLock<Rule> = OnceLock::new();
    R.get_or_init(|| {
        let v: Value = serde_json::from_str(H3_SPEC).expect("h3 spec");
        let g = |k: &str| v[k].as_i64().unwrap();
        let rows = v["decisions"]
            .as_array()
            .unwrap()
            .iter()
            .map(|d| {
                let f = |k: &str| d[k].as_i64().unwrap();
                [f("amount"), f("dest"), f("compliant"), f("violation"), f("staked"), f("stake")]
            })
            .collect();
        Rule {
            q: g("q"),
            p_detect: g("p_detect"),
            example_rows: rows,
            max_compliant: g("max_compliant"),
            slash: g("slash"),
            sanctioned_id: g("sanctioned_id"),
        }
    })
}

/// SHA-256 over both specs: identifies the model and rule a proof is about.
pub fn model_digest() -> String {
    let mut h = Sha256::new();
    h.update(H1_SPEC.as_bytes());
    h.update(H3_SPEC.as_bytes());
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// Integer forward pass, exactly the semantics the circuit enforces. Returns (h, h2, logit).
pub fn h1_forward(x: &[i64]) -> (Vec<i64>, Vec<i64>, i64) {
    let s = h1();
    let h: Vec<i64> = (0..H)
        .map(|n| {
            let a: i64 = (0..F_IN).map(|i| s.w1[n][i] * x[i]).sum::<i64>() + s.b1[n];
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

/// Decision of one transaction: (EXEC, VIOL, RISK) under policy (M, sid, slash).
pub fn decide(x: &[i64], d: &Decision, policy: &Policy) -> (i64, i64, i64) {
    let r = rule();
    let logit = h1_forward(x).2;
    let risk = (logit > h1().threshold) as i64;
    let viol = (d.amount > policy.max_compliant || d.dest == policy.sanctioned_id || risk == 1) as i64;
    let es = (d.staked == 1 && d.stake > 0) as i64 * d.stake.min(policy.slash);
    let exv = if viol == 1 { r.q * d.violation - r.p_detect * es } else { r.q * d.compliant };
    // EXEC: a compliant transfer is executed (it is the agent's job; H3's refuse-on-tie only matters for
    // violating offers); a violating one only if its expected payoff beats complying (the H3 rule)
    let exec = if viol == 1 { (exv > r.q * d.compliant) as i64 } else { 1 };
    (exec, viol, risk)
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Decision {
    pub amount: i64,
    pub dest: i64,
    pub compliant: i64,
    pub violation: i64,
    pub staked: i64,
    pub stake: i64,
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Policy {
    pub max_compliant: i64,
    pub sanctioned_id: i64,
    pub slash: i64,
}

impl Policy {
    pub fn default_h3() -> Self {
        let r = rule();
        Policy { max_compliant: r.max_compliant, sanctioned_id: r.sanctioned_id, slash: r.slash }
    }
}

impl Decision {
    pub fn from_row(row: &[i64; 6]) -> Self {
        let [amount, dest, compliant, violation, staked, stake] = *row;
        Decision { amount, dest, compliant, violation, staked, stake }
    }
}
