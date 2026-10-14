"""Tests for chain.py (no EZKL needed):  python3 -m pytest -q test_chain.py

The same vectors are asserted in Solidity (test/OptimisticInferenceV2.t.sol::test_crossCheck_pythonVector),
so these two tests together pin the Python and on-chain hashing to each other.
"""
import pytest
from chain import (ZERO, k256, digest, prefix_chain, merkle_levels, merkle_proof, merkle_root,
                   verify_opening, first_disagreement, chain_consistent_at)

N = 63
VECTOR_ROOT = "f761df39b3e6f0841480650b1d05546d95cb001a8df4ce30a06790cb1cb71fd7"
VECTOR_P_LAST = "133d1deafc426f9f654e1873325b9a6b70b037d7ff2ae932b02e11abdf51259a"


def vector_leaves():
    # keccak256(abi.encodePacked("leaf", uint256(i)))
    return [k256(b"leaf" + i.to_bytes(32, "big")) for i in range(N)]


def test_cross_implementation_vector():
    leaves = vector_leaves()
    P = prefix_chain(leaves)
    assert P[-1].hex() == VECTOR_P_LAST
    assert merkle_root(leaves, P).hex() == VECTOR_ROOT
    assert digest([1, 2, 3]).hex() == "6e0c627900b24bd432fe7b1f713f1b0744091a646a9fe4a65a18dfed21f2949c"


def test_openings_verify_and_bind_position():
    leaves = vector_leaves()
    P = prefix_chain(leaves)
    L = merkle_levels(leaves, P)
    root = L[-1][0]
    for i in (0, 1, 31, 62):
        assert verify_opening(root, i, leaves[i], P[i], merkle_proof(L, i))
    assert not verify_opening(root, 5, leaves[6], P[6], merkle_proof(L, 6))     # wrong position
    assert not verify_opening(root, 5, leaves[5], P[4], merkle_proof(L, 5))     # wrong prefix


def _trace(corrupt_at=None, garbage_at=None):
    # a toy 63-step chain: out_k = H(k || out_{k-1}); tampering propagates like run_steps(corrupt=...)
    outs, prev = [], b"tx"
    for k in range(N):
        o = k256(b"bad" + bytes([k])) if k == corrupt_at else k256(k.to_bytes(32, "big") + prev)
        outs.append(o)
        prev = o
    P, acc = [], ZERO
    for i, l in enumerate(outs):
        acc = k256(b"garbage") if i == garbage_at else k256(acc + l)
        P.append(acc)
    return outs, P


@pytest.mark.parametrize("s", range(N))
def test_bisection_locates_every_tampered_step(s):
    H_leaves, H_P = _trace()
    A_leaves, A_P = _trace(corrupt_at=s)
    k, rounds = first_disagreement(A_P, H_P)
    assert k == s and rounds <= 6
    assert chain_consistent_at(A_leaves, A_P, k)     # a well-formed lie passes the check...
    assert A_leaves[k] != H_leaves[k]                 # ...and is caught by the single-step proof


def test_malformed_chain_attack_is_caught_only_by_consistency_check():
    m = 40
    H_leaves, H_P = _trace()
    A_leaves, A_P = _trace(corrupt_at=m + 1, garbage_at=m)
    assert A_leaves[-1] != H_leaves[-1]               # the claimed output is wrong
    k, _ = first_disagreement(A_P, H_P)
    assert k == m
    assert A_leaves[k] == H_leaves[k]                 # a proof of step k would MATCH the asserter: v1 upholds the lie
    assert not chain_consistent_at(A_leaves, A_P, k)  # v2 rejects it
