//! Small shared gadgets: bit decompositions and their constraints.

use winterfell::math::{fields::f23201::BaseElement, FieldElement};

use crate::params::felt_from_i64;

/// sum_j 2^j * row[start + j]
pub fn bits_value<E: FieldElement + From<BaseElement>>(row: &[E], start: usize, n: usize) -> E {
    let mut acc = E::ZERO;
    let mut pow = E::ONE;
    let two = E::from(BaseElement::from(2u64));
    for j in 0..n {
        acc += pow * row[start + j];
        pow *= two;
    }
    acc
}

/// b * (b - 1) = 0 for each of n bit columns; writes n constraints.
pub fn booleans<E: FieldElement + From<BaseElement>>(row: &[E], start: usize, n: usize, out: &mut [E]) {
    for j in 0..n {
        out[j] = row[start + j] * (row[start + j] - E::ONE);
    }
}

/// Writes the n-bit binary decomposition of v (0 <= v < 2^n) into columns start..start+n at `row`.
pub fn fill_bits(columns: &mut [Vec<BaseElement>], start: usize, n: usize, row: usize, v: i64) {
    assert!(v >= 0 && (n >= 63 || v < (1i64 << n)), "value {} out of {}-bit range", v, n);
    for j in 0..n {
        columns[start + j][row] = BaseElement::from(((v >> j) & 1) as u64);
    }
}

pub fn felt(v: i64) -> BaseElement {
    felt_from_i64(v)
}

pub fn c<E: From<BaseElement>>(v: i64) -> E {
    E::from(felt_from_i64(v))
}
