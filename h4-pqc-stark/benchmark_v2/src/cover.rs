//! Dispatch for the H1-H3 model-class coverage workloads (h1mlp, gcn, rule).

use winterfell::{
    math::{fields::f23201::BaseElement, FieldElement},
    Assertion, TransitionConstraintDegree,
};

use crate::params::Workload;
use crate::{gcn, h1mlp, rule};

pub fn min_len(w: Workload) -> usize {
    match w {
        Workload::H1Mlp => h1mlp::MIN_LEN,
        Workload::H2Gcn => gcn::MIN_LEN,
        Workload::H3Rule => rule::MIN_LEN,
        _ => unreachable!("not a coverage workload"),
    }
}

pub fn width(w: Workload) -> usize {
    match w {
        Workload::H1Mlp => h1mlp::WIDTH,
        Workload::H2Gcn => gcn::WIDTH,
        Workload::H3Rule => rule::WIDTH,
        _ => unreachable!(),
    }
}

#[cfg(test)]
pub fn num_constraints(w: Workload) -> usize {
    match w {
        Workload::H1Mlp => h1mlp::CONSTRAINTS,
        Workload::H2Gcn => gcn::CONSTRAINTS,
        Workload::H3Rule => rule::CONSTRAINTS,
        _ => unreachable!(),
    }
}

pub fn num_assertions(w: Workload) -> usize {
    match w {
        Workload::H1Mlp => h1mlp::ASSERTIONS,
        Workload::H2Gcn => gcn::ASSERTIONS,
        Workload::H3Rule => rule::ASSERTIONS,
        _ => unreachable!(),
    }
}

pub fn degrees(w: Workload, len: usize) -> Vec<TransitionConstraintDegree> {
    match w {
        Workload::H1Mlp => h1mlp::degrees(len),
        Workload::H2Gcn => gcn::degrees(len),
        Workload::H3Rule => rule::degrees(len),
        _ => unreachable!(),
    }
}

pub fn constraints<E: FieldElement + From<BaseElement>>(w: Workload, cur: &[E], next: &[E], per: &[E], base: usize, out: &mut [E]) {
    match w {
        Workload::H1Mlp => h1mlp::constraints(cur, next, per, base, out),
        Workload::H2Gcn => gcn::constraints(cur, next, per, base, out),
        Workload::H3Rule => rule::constraints(cur, next, per, base, out),
        _ => unreachable!(),
    }
}

pub fn assertions(w: Workload, base: usize, outputs: &[u64]) -> Vec<Assertion<BaseElement>> {
    match w {
        Workload::H1Mlp => h1mlp::assertions(base, outputs),
        Workload::H2Gcn => gcn::assertions(base, outputs),
        Workload::H3Rule => rule::assertions(base, outputs),
        _ => unreachable!(),
    }
}

pub fn periodic_columns(w: Workload, len: usize) -> Vec<Vec<BaseElement>> {
    match w {
        Workload::H1Mlp => h1mlp::periodic_columns(len),
        Workload::H2Gcn => gcn::periodic_columns(len),
        Workload::H3Rule => rule::periodic_columns(len),
        _ => unreachable!(),
    }
}

/// Public outputs of the honest instance.
pub fn outputs(w: Workload) -> Vec<u64> {
    match w {
        Workload::H1Mlp => h1mlp::outputs(),
        Workload::H2Gcn => gcn::outputs(),
        Workload::H3Rule => rule::outputs(),
        _ => unreachable!(),
    }
}

/// Private inputs of a coverage workload (tests may tamper with them).
#[derive(Clone)]
pub enum Inputs {
    H1(Vec<i64>),
    H2(Vec<Vec<i64>>),
    H3(Vec<[i64; 6]>),
}

pub fn honest_inputs(w: Workload) -> Inputs {
    match w {
        Workload::H1Mlp => Inputs::H1(h1mlp::spec().x.clone()),
        Workload::H2Gcn => Inputs::H2(gcn::spec().x.clone()),
        Workload::H3Rule => Inputs::H3(rule::spec().rows.clone()),
        _ => unreachable!(),
    }
}

pub fn fill(inputs: &Inputs, columns: &mut [Vec<BaseElement>], base: usize, len: usize) {
    match inputs {
        Inputs::H1(x) => h1mlp::fill(columns, base, len, x),
        Inputs::H2(x) => gcn::fill(columns, base, len, x),
        Inputs::H3(rows) => rule::fill(columns, base, len, rows),
    }
}
