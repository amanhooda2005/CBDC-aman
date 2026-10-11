//! v3 post-quantum circuit: same statement and commitment as v2, proven with a
//! random-linear-combination check.
//!
//! Statement (identical to v2): c = G*bits + B*r mod p, with the same public uniform matrix
//! (params::g_entry / params::b_entry, n = SIS_ROWS rows), bits in {0,1}^64, r in {-1,0,1}^448,
//! plus the same workload (2x2 matmul or H1 dense layer).
//!
//! Proof: the secret vector s = (bits, r) is committed in the main trace, packed K coordinates
//! per row with exact range checks. After that commitment the verifier's coin draws gamma from
//! the 138-bit sextic extension. A single auxiliary accumulator proves
//!     sum_j gamma^j * (A_j . s)  =  sum_j gamma^j * c_j.
//! If A.s != c, the difference is a nonzero polynomial in gamma of degree < n, so a random
//! gamma satisfies the check with probability at most (n - 1) / |F_p^6| (Schwartz-Zippel).
//! This replaces v2's n accumulator columns with one extension column.

use winterfell::{
    math::{fields::f23201::BaseElement, ExtensionOf, FieldElement},
    Air, AirContext, Assertion, AuxTraceRandElements, ByteWriter, EvaluationFrame, Matrix,
    ProofOptions, Prover, Serializable, Trace, TraceInfo, TraceLayout, TransitionConstraintDegree,
};

use crate::classical::{fill_matmul, matmul_assertions, matmul_constraints, matmul_degrees, BLOCK_ASSERTIONS, BLOCK_WIDTH};
use crate::dense;
use crate::params::{b_entry, felt_from_i64, g_entry, Workload, AMOUNT_BITS, N_RAND, SIS_ROWS};

/// Coordinates packed per trace row.
pub const K: usize = 4;
pub const COORDS: usize = AMOUNT_BITS + N_RAND;
pub const ROWS: usize = COORDS / K;
const _: () = assert!(COORDS % K == 0 && AMOUNT_BITS % K == 0 && ROWS.is_power_of_two());

const fn s(t: usize) -> usize { 3 * t }
const fn u0(t: usize) -> usize { 3 * t + 1 }
const fn u1(t: usize) -> usize { 3 * t + 2 }
const SIS_MAIN_WIDTH: usize = 3 * K;
const SIS_MAIN_CONSTRAINTS: usize = 10 * K;
/// Periodic columns: sel0, is_bit, is_bit_next, then A[j][(row+1)*K + t] for each (j, t).
const PERIODIC_FIXED: usize = 3;

/// Public matrix entry A[j][i] for coordinate i of s = (bits, r): the v2 matrix [G | B].
pub fn a_entry(j: usize, i: usize) -> u64 {
    if i < AMOUNT_BITS { g_entry(j, i) } else { b_entry(j, i - AMOUNT_BITS) }
}

pub fn trace_len(w: Workload) -> usize {
    let workload_len = match w {
        Workload::Matmul => 8,
        Workload::Dense => dense::MIN_LEN,
    };
    ROWS.max(workload_len).next_power_of_two()
}

fn workload_width(w: Workload) -> usize {
    match w {
        Workload::Matmul => BLOCK_WIDTH,
        Workload::Dense => dense::WIDTH,
    }
}

pub fn main_width(w: Workload) -> usize {
    SIS_MAIN_WIDTH + workload_width(w)
}

#[cfg(test)]
pub fn num_main_constraints(w: Workload) -> usize {
    SIS_MAIN_CONSTRAINTS
        + match w {
            Workload::Matmul => crate::classical::BLOCK_CONSTRAINTS,
            Workload::Dense => dense::CONSTRAINTS,
        }
}

#[derive(Clone)]
pub struct Statement {
    pub workload: Workload,
    pub commitment: [u64; SIS_ROWS],
    pub dense_y: [u64; dense::OUT],
}

impl Serializable for Statement {
    fn write_into<W: ByteWriter>(&self, target: &mut W) {
        target.write_u8(self.workload as u8);
        let mut values: Vec<BaseElement> = self.commitment.iter().map(|&v| BaseElement::from(v)).collect();
        if self.workload == Workload::Dense {
            values.extend(self.dense_y.iter().map(|&v| BaseElement::from(v)));
        }
        target.write(&values[..]);
    }
}

pub struct RlcAir {
    context: AirContext<BaseElement>,
    statement: Statement,
    len: usize,
}

fn range<E: FieldElement + From<BaseElement>>(row: &[E], is_bit: E, t: usize, out: &mut [E]) {
    let (sv, a, b) = (row[s(t)], row[u0(t)], row[u1(t)]);
    out[0] = a * (a - E::ONE);
    out[1] = b * (b - E::ONE);
    out[2] = a * b;
    out[3] = sv + E::ONE - a - E::from(BaseElement::from(2u64)) * b;
    out[4] = is_bit * (a + b - E::ONE);
}

impl Air for RlcAir {
    type BaseField = BaseElement;
    type PublicInputs = Statement;

    fn new(trace_info: TraceInfo, statement: Statement, options: ProofOptions) -> Self {
        let w = statement.workload;
        assert_eq!(trace_info.layout().main_trace_width(), main_width(w));
        let len = trace_info.length();
        let mut main = Vec::new();
        for _ in 0..K {
            for _ in 0..2 {
                main.extend(vec![TransitionConstraintDegree::new(2); 3]);
                main.push(TransitionConstraintDegree::new(1));
                main.push(TransitionConstraintDegree::with_cycles(1, vec![len]));
            }
        }
        let workload_assertions = match w {
            Workload::Matmul => {
                main.extend(matmul_degrees());
                BLOCK_ASSERTIONS
            }
            Workload::Dense => {
                main.extend(dense::degrees(len));
                dense::ASSERTIONS
            }
        };
        let aux = vec![TransitionConstraintDegree::with_cycles(1, vec![len]); 2];
        let context = AirContext::new_multi_segment(trace_info, main, aux, workload_assertions, 1, options);
        Self { context, statement, len }
    }

    fn context(&self) -> &AirContext<BaseElement> {
        &self.context
    }

    fn evaluate_transition<E: FieldElement + From<BaseElement>>(&self, frame: &EvaluationFrame<E>, periodic: &[E], result: &mut [E]) {
        let (cur, next) = (frame.current(), frame.next());
        let (bit_cur, bit_next) = (periodic[1], periodic[2]);
        for t in 0..K {
            range(cur, bit_cur, t, &mut result[10 * t..10 * t + 5]);
            range(next, bit_next, t, &mut result[10 * t + 5..10 * t + 10]);
        }
        let out = &mut result[SIS_MAIN_CONSTRAINTS..];
        match self.statement.workload {
            Workload::Matmul => matmul_constraints(cur, next, SIS_MAIN_WIDTH, out),
            Workload::Dense => dense::constraints(cur, next, &periodic[PERIODIC_FIXED + SIS_ROWS * K..], SIS_MAIN_WIDTH, out),
        }
    }

    fn evaluate_aux_transition<F, E>(
        &self,
        main_frame: &EvaluationFrame<F>,
        aux_frame: &EvaluationFrame<E>,
        periodic: &[F],
        aux_rand_elements: &AuxTraceRandElements<E>,
        result: &mut [E],
    ) where
        F: FieldElement<BaseField = BaseElement>,
        E: FieldElement<BaseField = BaseElement> + ExtensionOf<F>,
    {
        let gamma = aux_rand_elements.get_segment_elements(0)[0];
        let (cur, next) = (main_frame.current(), main_frame.next());
        let sel0 = periodic[0];
        let (mut add, mut first, mut gj) = (E::ZERO, E::ZERO, E::ONE);
        for j in 0..SIS_ROWS {
            let (mut lin_next, mut lin_cur) = (F::ZERO, F::ZERO);
            for t in 0..K {
                lin_next += periodic[PERIODIC_FIXED + j * K + t] * next[s(t)];
                lin_cur += F::from(BaseElement::from(a_entry(j, t))) * cur[s(t)];
            }
            add += gj.mul_base(lin_next);
            first += gj.mul_base(lin_cur);
            gj *= gamma;
        }
        let (acc_cur, acc_next) = (aux_frame.current()[0], aux_frame.next()[0]);
        result[0] = acc_next - acc_cur - add;
        result[1] = (acc_cur - first).mul_base(sel0);
    }

    fn get_assertions(&self) -> Vec<Assertion<BaseElement>> {
        match self.statement.workload {
            Workload::Matmul => matmul_assertions(SIS_MAIN_WIDTH),
            Workload::Dense => dense::assertions(SIS_MAIN_WIDTH, &self.statement.dense_y),
        }
    }

    fn get_aux_assertions<E: FieldElement + From<BaseElement>>(&self, aux_rand_elements: &AuxTraceRandElements<E>) -> Vec<Assertion<E>> {
        let gamma = aux_rand_elements.get_segment_elements(0)[0];
        let (mut target, mut gj) = (E::ZERO, E::ONE);
        for j in 0..SIS_ROWS {
            target += gj * E::from(BaseElement::from(self.statement.commitment[j]));
            gj *= gamma;
        }
        vec![Assertion::single(0, ROWS - 1, target)]
    }

    fn get_periodic_column_values(&self) -> Vec<Vec<BaseElement>> {
        let len = self.len;
        let bit_rows = AMOUNT_BITS / K;
        let mut sel0 = vec![BaseElement::ZERO; len];
        sel0[0] = BaseElement::ONE;
        let is_bit = (0..len).map(|r| if r < bit_rows { BaseElement::ONE } else { BaseElement::ZERO }).collect();
        let is_bit_next = (0..len).map(|r| if r + 1 < bit_rows { BaseElement::ONE } else { BaseElement::ZERO }).collect();
        let mut columns = vec![sel0, is_bit, is_bit_next];
        for j in 0..SIS_ROWS {
            for t in 0..K {
                columns.push((0..len).map(|r| if r + 1 < ROWS { BaseElement::from(a_entry(j, (r + 1) * K + t)) } else { BaseElement::ZERO }).collect());
            }
        }
        if self.statement.workload == Workload::Dense {
            columns.extend(dense::periodic_columns(len));
        }
        columns
    }
}

/// Main trace plus one auxiliary extension-field accumulator built from gamma.
pub struct RlcTrace {
    layout: TraceLayout,
    main: Matrix<BaseElement>,
    coords: Vec<i64>,
}

impl Trace for RlcTrace {
    type BaseField = BaseElement;

    fn layout(&self) -> &TraceLayout {
        &self.layout
    }

    fn length(&self) -> usize {
        self.main.num_rows()
    }

    fn meta(&self) -> &[u8] {
        &[]
    }

    fn main_segment(&self) -> &Matrix<BaseElement> {
        &self.main
    }

    fn build_aux_segment<E: FieldElement<BaseField = BaseElement>>(&mut self, aux_segments: &[Matrix<E>], rand_elements: &[E]) -> Option<Matrix<E>> {
        if !aux_segments.is_empty() {
            return None;
        }
        let gamma = rand_elements[0];
        let len = self.length();
        // Combined row vector a_gamma[i] = sum_j gamma^j A[j][i].
        let mut a_gamma = vec![E::ZERO; COORDS];
        let mut gj = E::ONE;
        for j in 0..SIS_ROWS {
            for (i, entry) in a_gamma.iter_mut().enumerate() {
                *entry += gj * E::from(BaseElement::from(a_entry(j, i)));
            }
            gj *= gamma;
        }
        let mut acc = vec![E::ZERO; len];
        let mut running = E::ZERO;
        for (row, slot) in acc.iter_mut().enumerate() {
            if row < ROWS {
                for t in 0..K {
                    running += a_gamma[row * K + t] * E::from(felt_from_i64(self.coords[row * K + t]));
                }
            }
            *slot = running;
        }
        Some(Matrix::new(vec![acc]))
    }

    fn read_main_frame(&self, row_idx: usize, frame: &mut EvaluationFrame<BaseElement>) {
        let next = (row_idx + 1) % self.length();
        self.main.read_row_into(row_idx, frame.current_mut());
        self.main.read_row_into(next, frame.next_mut());
    }
}

/// Prover. `bits` and `r` are deliberately unchecked so tests can attempt malformed openings.
pub struct RlcProver {
    pub options: ProofOptions,
    pub workload: Workload,
    pub commitment: [u64; SIS_ROWS],
    pub bits: [i64; AMOUNT_BITS],
    pub r: [i64; N_RAND],
    pub out_override: Option<[u64; 4]>,
    pub dense_y: [u64; dense::OUT],
}

impl RlcProver {
    pub fn statement(&self) -> Statement {
        Statement { workload: self.workload, commitment: self.commitment, dense_y: self.dense_y }
    }

    pub fn build_trace(&self) -> RlcTrace {
        let len = trace_len(self.workload);
        let width = main_width(self.workload);
        let coords: Vec<i64> = self.bits.iter().chain(self.r.iter()).copied().collect();
        let mut columns = vec![vec![BaseElement::ZERO; len]; width];
        for row in 0..len {
            for t in 0..K {
                let v = if row < ROWS { coords[row * K + t] } else { 0 };
                columns[s(t)][row] = felt_from_i64(v);
                columns[u0(t)][row] = felt_from_i64((v + 1).rem_euclid(2));
                columns[u1(t)][row] = felt_from_i64((v + 1).div_euclid(2));
            }
        }
        match self.workload {
            Workload::Matmul => fill_matmul(&mut columns, SIS_MAIN_WIDTH, len, self.out_override),
            Workload::Dense => dense::fill(&mut columns, SIS_MAIN_WIDTH, len, &dense::input()),
        }
        RlcTrace { layout: TraceLayout::new(width, [1], [1]), main: Matrix::new(columns), coords }
    }
}

impl Prover for RlcProver {
    type BaseField = BaseElement;
    type Air = RlcAir;
    type Trace = RlcTrace;

    fn get_pub_inputs(&self, _trace: &Self::Trace) -> Statement {
        self.statement()
    }

    fn options(&self) -> &ProofOptions {
        &self.options
    }
}

