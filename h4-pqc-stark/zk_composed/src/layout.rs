//! Column and periodic-column layout of the composed trace (256 rows).
//!
//! Main trace = [ H1 inference block | H3 rule block | (PQ only) SIS opening block ].
//! The H1 and rule blocks follow benchmark_v2/src/h1mlp.rs and compose.rs exactly (same streaming
//! layout and the same integer semantics); the SIS block is new: the lattice opening is checked
//! row by row against verifier-computed periodic columns (no post-commitment challenge needed).

pub const ROWS: usize = 256;

// ---------------------------------------------------------------- H1 model shape
pub const F_IN: usize = 165;
pub const H: usize = 32;
pub const SH_A_START: usize = F_IN; // rows 165..197 shift bank A through pipeline 1
pub const SH_B_START: usize = F_IN + H; // rows 197..229 shift bank B through pipeline 2
pub const FINAL: usize = F_IN + 2 * H; // row 229: head accumulator complete

pub const XBITS: usize = 12;
pub const Q1: usize = 11; // offset 2^10
pub const R1: usize = 11; // = s1
pub const Q2: usize = 13; // offset 2^12
pub const R2: usize = 7; // = s2

// H1 block columns
pub const X: usize = 0;
pub const XB: usize = X + 1;
pub const XC: usize = XB + XBITS;
pub const ACC_A: usize = XC + XBITS;
pub const ACC_B: usize = ACC_A + H;
pub const DL: usize = ACC_B + H;
pub const C_HEAD: usize = DL + H;
pub const P1Q: usize = C_HEAD + 1;
pub const P1R: usize = P1Q + Q1;
pub const OUT1: usize = P1R + R1;
pub const P2Q: usize = OUT1 + 1;
pub const P2R: usize = P2Q + Q2;
pub const OUT2: usize = P2R + R2;
pub const H1_WIDTH: usize = OUT2 + 1;

// ---------------------------------------------------------------- H3 rule block (registers + one shared window)
pub const RB: usize = H1_WIDTH;
pub const A: usize = 0; // amount
pub const D: usize = 1; // destination id
pub const INV: usize = 2; // (D - sid)^-1 or 0
pub const FL: usize = 3; // [D == sid]
pub const CP: usize = 4; // compliant payoff
pub const VP: usize = 5; // violation payoff
pub const Z: usize = 6; // staked
pub const S: usize = 7; // stake
pub const GT: usize = 8; // [A > M]
pub const POS: usize = 9; // [S > 0]
pub const CGE: usize = 10; // [S >= slash]
pub const RISK: usize = 11; // [logit > T]
pub const ACT: usize = 12;
pub const MN: usize = 13;
pub const ES: usize = 14; // expected slash
pub const V0: usize = 15;
pub const VIOL: usize = 16;
pub const EXV: usize = 17; // execute payoff
pub const EX: usize = 18; // EXEC
pub const REGS: usize = 19;
pub const WIN: usize = REGS;
/// Shared comparison window: 30 bits, signed values in [-2^29, 2^29). It must hold EXV - q*CP - 1,
/// whose magnitude reaches q*VP + p_detect*min(S, slash) + q*CP; with slash < 2^POLICY_BITS and
/// q = p_detect = 256 that is < 2^29. (22 bits only covered the unescalated slash.) Every window value
/// stays below p - 2^30 in magnitude, so its sign decomposition is unique in BabyBear.
pub const WB: usize = 30;
pub const TOP: usize = WB - 1;
pub const OFF: i64 = 1 << TOP;
/// Public policy values (limit M, slash) and the private stake are in [0, 2^POLICY_BITS); the verifier
/// rejects statements outside this domain and the prover refuses witnesses outside it.
pub const POLICY_BITS: usize = 20;
pub const CB: usize = 12; // compliant payoff in [0, 2^12)
pub const VB: usize = 13; // violation payoff in [0, 2^13)
pub const RULE_WIDTH: usize = REGS + WB;
pub const ROW_RISK: usize = FINAL;
pub const SELECTOR_ROWS: [usize; 7] = [0, 1, 2, 3, 4, 5, ROW_RISK]; // gt, pos, cge, C, V, ex, risk

pub const WIDTH_CLASSICAL: usize = RB + RULE_WIDTH;

// ---------------------------------------------------------------- SIS opening block (post-quantum variant)
pub const SB: usize = WIDTH_CLASSICAL;
pub const SA_: usize = 0; // coordinate 2t
pub const SB_: usize = 1; // coordinate 2t + 1
pub const U0A: usize = 2;
pub const U1A: usize = 3;
pub const U0B: usize = 4;
pub const U1B: usize = 5;
pub const LINK: usize = 6;
pub const ACC_SIS: usize = 7;

// ---------------------------------------------------------------- periodic columns
pub const PE_INP: usize = 0;
pub const PE_LO: usize = 1;
pub const PE_HI: usize = 2;
pub const PE_W1: usize = 3;
pub const PE_SHA: usize = PE_W1 + H;
pub const PE_B1: usize = PE_SHA + 1;
pub const PE_W2: usize = PE_B1 + 1;
pub const PE_SHB: usize = PE_W2 + H;
pub const PE_B2: usize = PE_SHB + 1;
pub const PE_W3: usize = PE_B2 + 1;
pub const PE_DMOVE: usize = PE_W3 + 1;
pub const PE_DIN: usize = PE_DMOVE + 1;
pub const H1_PERIODIC: usize = PE_DIN + 1;
pub const PE_SEL: usize = H1_PERIODIC; // 7 window-row selectors
pub const CLASSICAL_PERIODIC: usize = PE_SEL + SELECTOR_ROWS.len();
pub const PE_ISBIT: usize = CLASSICAL_PERIODIC; // rows holding the 64 amount bits
pub const PE_HZ: usize = PE_ISBIT + 1; // rows whose amount bits must be zero (>= AMOUNT_BITS)
pub const PE_WA: usize = PE_HZ + 1; // 2^(2t) for amount bit 2t, else 0
pub const PE_WB: usize = PE_WA + 1; // 2^(2t+1)
pub const PE_G: usize = PE_WB + 1; // per SIS row j: matrix entry for coordinate 2t, then 2t+1

/// Amount range bound to the commitment: bits 0..AMOUNT_BITS may be set, bits AMOUNT_BITS..64 are zero.
pub const AMOUNT_BITS: usize = 20;

/// Public values: [M, sanctioned id, slash, EXEC, VIOL] then (PQ) the n SIS commitment coordinates.
pub const PV_M: usize = 0;
pub const PV_SID: usize = 1;
pub const PV_SLASH: usize = 2;
pub const PV_EXEC: usize = 3;
pub const PV_VIOL: usize = 4;
pub const PV_C: usize = 5;
