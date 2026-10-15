//! Zero-knowledge, post-quantum-bound proof of one CBDC transaction decision (Plonky3).
//! See air.rs for the statement and README.md for parameters and results.

pub mod air;
pub mod config;
pub mod layout;
pub mod model;
pub mod sis;
pub mod trace;

use p3_baby_bear::BabyBear;
use p3_field::{PrimeCharacteristicRing, PrimeField64};
use serde::{Deserialize, Serialize};

pub use air::ComposedAir;
pub use trace::{Tamper, Witness};

/// Which statement / proof system: `pq` adds the SIS opening and the amount link; `zk` uses the
/// hiding PCS. The deployed configuration is pq = true, zk = true.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Variant {
    pub pq: bool,
    pub zk: bool,
}

impl Variant {
    pub const DEPLOYED: Variant = Variant { pq: true, zk: true };
    pub fn name(self) -> &'static str {
        match (self.pq, self.zk) {
            (false, false) => "classical",
            (true, false) => "pq",
            (false, true) => "classical_zk",
            (true, true) => "pq_zk",
        }
    }
}

/// What a verifier needs besides the proof bytes.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Statement {
    pub variant: Variant,
    pub model: String,
    /// [M, sanctioned id, slash, EXEC, VIOL, c_0 .. c_47]
    pub public: Vec<u64>,
}

impl Statement {
    pub fn exec(&self) -> u64 {
        self.public[layout::PV_EXEC]
    }
    pub fn viol(&self) -> u64 {
        self.public[layout::PV_VIOL]
    }
    pub fn commitment(&self) -> &[u64] {
        &self.public[layout::PV_C..]
    }
}

fn to_felts(v: &[u64]) -> Vec<BabyBear> {
    v.iter().map(|&x| BabyBear::from_u64(x)).collect()
}

/// Proves the witness; `hiding_seed = None` uses OS randomness. Returns (proof bytes, statement).
pub fn prove(w: &Witness, variant: Variant, hiding_seed: Option<u64>, tamper: &Tamper) -> (Vec<u8>, Statement) {
    let air = ComposedAir::new(variant.pq);
    let pv = trace::public_values(w, variant.pq);
    let tr = trace::build(w, variant.pq, tamper);
    let bytes = if variant.zk {
        let cfg = config::zk_config(hiding_seed);
        postcard::to_allocvec(&p3_uni_stark::prove(&cfg, &air, tr, &pv).map_err(|e| format!("{e:?}")).expect("prove")).expect("serialize")
    } else {
        let cfg = config::plain_config();
        postcard::to_allocvec(&p3_uni_stark::prove(&cfg, &air, tr, &pv).map_err(|e| format!("{e:?}")).expect("prove")).expect("serialize")
    };
    let st = Statement { variant, model: model::model_digest(), public: pv.iter().map(|f| f.as_canonical_u64()).collect() };
    (bytes, st)
}

pub fn verify(proof: &[u8], st: &Statement) -> Result<(), String> {
    if st.model != model::model_digest() {
        return Err("proof is about a different model".into());
    }
    let air = ComposedAir::new(st.variant.pq);
    if st.public.len() != ComposedAir::num_public_values_of(st.variant.pq) {
        return Err("wrong number of public values".into());
    }
    let pv = to_felts(&st.public);
    if st.variant.zk {
        let cfg = config::zk_config(Some(0));
        let p: p3_uni_stark::Proof<config::ZkConfig> = postcard::from_bytes(proof).map_err(|e| e.to_string())?;
        p3_uni_stark::verify(&cfg, &air, &p, &pv).map_err(|e| format!("{e:?}"))
    } else {
        let cfg = config::plain_config();
        let p: p3_uni_stark::Proof<config::PlainConfig> = postcard::from_bytes(proof).map_err(|e| e.to_string())?;
        p3_uni_stark::verify(&cfg, &air, &p, &pv).map_err(|e| format!("{e:?}"))
    }
}

/// Witness for the composed instance of benchmark_v2 (decision 1 of the H3 spec, the flagged H1
/// example transaction), with deterministic ternary randomness.
pub fn example_witness(decision_index: usize, r_seed: u64) -> Witness {
    let rule = model::rule();
    Witness {
        x: model::h1().example_x.clone(),
        decision: model::Decision::from_row(&rule.example_rows[decision_index]),
        policy: model::Policy::default_h3(),
        r: ternary(r_seed),
    }
}

/// Deterministic ternary vector (tests / benchmarks); production draws r from OS randomness.
pub fn ternary(seed: u64) -> Vec<i64> {
    let mut z = seed ^ 0x7465_726e_6172_7921;
    (0..sis::N_RAND)
        .map(|_| {
            z = z.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            ((z >> 33) % 3) as i64 - 1
        })
        .collect()
}

#[cfg(test)]
mod tests;
