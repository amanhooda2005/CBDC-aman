//! STARK configurations over BabyBear with Poseidon2 Merkle commitments (hash-based, plausibly
//! post-quantum). Two variants:
//!   - `PlainConfig`: ordinary FRI (not zero-knowledge), the baseline;
//!   - `ZkConfig`:   hiding FRI + salted (hiding) Merkle trees, so the proof reveals nothing about the
//!                   private trace beyond the public values (zero-knowledge).
//! The Poseidon2 round constants are derived from a fixed public seed, identical for prover and
//! verifier. The hiding randomness of the prover comes from the OS in production; tests may seed it.

use p3_baby_bear::{BabyBear, Poseidon2BabyBear};
use p3_challenger::DuplexChallenger;
use p3_commit::ExtensionMmcs;
use p3_dft::Radix2DitParallel;
use p3_field::Field;
use p3_field::extension::BinomialExtensionField;
use p3_fri::{FriParameters, HidingFriPcs, TwoAdicFriPcs};
use p3_merkle_tree::{MerkleTreeHidingMmcs, MerkleTreeMmcs};
use p3_symmetric::{PaddingFreeSponge, TruncatedPermutation};
use p3_uni_stark::StarkConfig;
use rand::SeedableRng;
use rand::rngs::{SmallRng, StdRng};

pub type Val = BabyBear;
pub const EXT_DEGREE: usize = 5;
pub type Challenge = BinomialExtensionField<Val, EXT_DEGREE>;
pub type Perm = Poseidon2BabyBear<16>;
pub type Hash = PaddingFreeSponge<Perm, 16, 8, 8>;
pub type Compress = TruncatedPermutation<Perm, 2, 8, 16>;
pub type Dft = Radix2DitParallel<Val>;
pub type Challenger = DuplexChallenger<Val, Perm, 16, 8>;

pub type ValMmcs = MerkleTreeMmcs<<Val as Field>::Packing, <Val as Field>::Packing, Hash, Compress, 2, 8>;
pub type ChallengeMmcs = ExtensionMmcs<Val, Challenge, ValMmcs>;
pub type Pcs = TwoAdicFriPcs<Val, Dft, ValMmcs, ChallengeMmcs>;
pub type PlainConfig = StarkConfig<Pcs, Challenge, Challenger>;

pub const SALT_ELEMS: usize = 4;
pub const RANDOM_CODEWORDS: usize = EXT_DEGREE; // hiding FRI needs at least one random codeword per extension coordinate
pub type ZkValMmcs =
    MerkleTreeHidingMmcs<<Val as Field>::Packing, <Val as Field>::Packing, Hash, Compress, StdRng, 2, 8, SALT_ELEMS>;
pub type ZkChallengeMmcs = ExtensionMmcs<Val, Challenge, ZkValMmcs>;
pub type ZkPcs = HidingFriPcs<Val, Dft, ZkValMmcs, ZkChallengeMmcs, StdRng>;
pub type ZkConfig = StarkConfig<ZkPcs, Challenge, Challenger>;

/// FRI parameters shared by both variants. Low-degree-test soundness (ethSTARK conjecture):
/// LOG_BLOWUP * NUM_QUERIES + QUERY_POW_BITS = 4 * 48 + 16 = 208 bits. End to end (Plonky3's
/// p3-security report, `zk_composed security`): 124 bits conjectured (capped by the 124-bit
/// collision resistance of the Poseidon2 digest) and 111 bits proven (list decoding). Chosen over
/// blowup 8 / 38 queries (124 conjectured, 72 proven) and blowup 32 / 40 queries (124 / 113, twice
/// the prove time); see README.md.
pub const LOG_BLOWUP: usize = 4;
pub const NUM_QUERIES: usize = 48;
pub const QUERY_POW_BITS: usize = 16;
pub const COMMIT_POW_BITS: usize = 0;
pub const BATCH_POW_BITS: usize = 0;
pub const LOG_FINAL_POLY_LEN: usize = 0;
pub const MAX_LOG_ARITY: usize = 1;

const PERM_SEED: u64 = 0x4342_4443_5f50_3332; // "CBDC_P32": public Poseidon2 constants

pub fn perm() -> Perm {
    let mut rng = SmallRng::seed_from_u64(PERM_SEED);
    Perm::new_from_rng_128(&mut rng)
}

pub fn fri<M>(mmcs: M) -> FriParameters<M> {
    FriParameters {
        log_blowup: LOG_BLOWUP,
        log_final_poly_len: LOG_FINAL_POLY_LEN,
        max_log_arity: MAX_LOG_ARITY,
        num_queries: NUM_QUERIES,
        batch_proof_of_work_bits: BATCH_POW_BITS,
        commit_proof_of_work_bits: COMMIT_POW_BITS,
        query_proof_of_work_bits: QUERY_POW_BITS,
        mmcs,
    }
}

pub fn plain_config() -> PlainConfig {
    let perm = perm();
    let val_mmcs = ValMmcs::new(Hash::new(perm.clone()), Compress::new(perm.clone()), 0);
    let challenge_mmcs = ChallengeMmcs::new(val_mmcs.clone());
    let pcs = Pcs::new(Dft::default(), val_mmcs, fri(challenge_mmcs));
    PlainConfig::new(pcs, Challenger::new(perm))
}

/// `seed = None` draws the hiding randomness from the operating system (production);
/// `Some(s)` makes it deterministic (benchmarks / tests). The verifier never uses it.
pub fn zk_config(seed: Option<u64>) -> ZkConfig {
    let perm = perm();
    let rng = |k: u64| match seed {
        Some(s) => StdRng::seed_from_u64(s ^ k),
        None => {
            let mut b = [0u8; 32];
            getrandom(&mut b);
            StdRng::from_seed(b)
        }
    };
    let val_mmcs = ZkValMmcs::new(Hash::new(perm.clone()), Compress::new(perm.clone()), 0, rng(1));
    let challenge_mmcs = ZkChallengeMmcs::new(val_mmcs.clone());
    let pcs = ZkPcs::new(Dft::default(), val_mmcs, fri(challenge_mmcs), RANDOM_CODEWORDS, rng(2));
    ZkConfig::new(pcs, Challenger::new(perm))
}

/// OS randomness via /dev/urandom (Linux/macOS); avoids an extra dependency.
fn getrandom(buf: &mut [u8]) {
    use std::io::Read;
    std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(buf))
        .expect("OS randomness for the zero-knowledge prover");
}
