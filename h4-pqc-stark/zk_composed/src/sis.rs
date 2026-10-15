//! Lattice (SIS) commitment to the transaction amount, over the BabyBear field itself.
//!
//! ```text
//! c = G * bits(amount) + B * r   (mod p),   p = 2^31 - 2^27 + 1 = 2,013,265,921
//! bits in {0,1}^64, r in {-1,0,1}^448, n = 48 rows.
//! ```
//!
//! Binding is statistical: two openings give a nonzero z = (z_bits, z_r) with ||z||_inf <= 2 and
//! [G|B] z = 0. There are at most 5^512 ~ 2^1188.8 candidate z and p^48 ~ 2^1483.5 syndromes, so for
//! uniform G, B the expected number of short collisions is ~ 2^-294.7 (the Winterfell v2/v3 design used
//! n = 80 over a 23-bit field: 2^-635.8). G and B are expanded by the verifier from a public seed and
//! enter the AIR as periodic columns, so the prover cannot choose them.

use p3_baby_bear::BabyBear;
use p3_field::{PrimeCharacteristicRing, PrimeField64};

pub const N_SIS: usize = 48;
pub const AMOUNT_COORDS: usize = 64;
pub const N_RAND: usize = 448;
pub const N_COORDS: usize = AMOUNT_COORDS + N_RAND; // 512 = 2 per trace row

pub const P: u64 = 2_013_265_921;

fn splitmix(mut z: u64) -> u64 {
    z = z.wrapping_add(0x9e37_79b9_7f4a_7c15);
    z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    z ^ (z >> 31)
}

/// Public matrix entry M[j][coord] = [G | B][j][coord], uniform mod p (bias < 2^-32).
pub fn entry(j: usize, coord: usize) -> u64 {
    splitmix(0x5349_535f_4242_0000 ^ ((j as u64) << 24) ^ coord as u64) % P
}

pub fn amount_coords(amount: u64) -> [i64; AMOUNT_COORDS] {
    let mut bits = [0i64; AMOUNT_COORDS];
    for (t, b) in bits.iter_mut().enumerate() {
        *b = ((amount >> t) & 1) as i64;
    }
    bits
}

/// Opening vector s = (bits(amount), r).
pub fn opening(amount: u64, r: &[i64]) -> Vec<i64> {
    assert_eq!(r.len(), N_RAND);
    let mut s = amount_coords(amount).to_vec();
    s.extend_from_slice(r);
    s
}

/// c_j = sum_i M[j][i] * s_i (mod p), computed exactly. Takes the opening as given (tests use
/// malformed openings).
pub fn commit(s: &[i64]) -> Vec<u64> {
    assert_eq!(s.len(), N_COORDS);
    (0..N_SIS)
        .map(|j| {
            let mut acc: i128 = 0;
            for (i, &v) in s.iter().enumerate() {
                acc += entry(j, i) as i128 * v as i128;
            }
            acc.rem_euclid(P as i128) as u64
        })
        .collect()
}

pub fn felt(v: i64) -> BabyBear {
    BabyBear::from_i64(v)
}

pub fn as_u64(f: BabyBear) -> u64 {
    f.as_canonical_u64()
}

/// Expected number of short collisions, log2: N_COORDS * log2(5) - N_SIS * log2(p).
pub fn log2_expected_collisions() -> f64 {
    N_COORDS as f64 * 5f64.log2() - N_SIS as f64 * (P as f64).log2()
}
