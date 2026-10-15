"""Commitments for the optimistic dispute protocol.
leaf_i = Poseidon(a_i) over the fixed-point activation after unit i, using the
same Poseidon instance EZKL uses for `hashed` inputs/outputs, so a proof's
public hash instances can be compared to committed leaves directly.
The head step's leaf is output_digest(public output felts) (see chain.py), so the
head leaf IS the claimed output digest the contract checks.
Prefix chain and Merkle tree: chain.py (keccak, identical to OptimisticInferenceV2.sol).
"""
import ezkl
from quant import S
import chain


def felts(int_vec, s=S):
    return [ezkl.float_to_felt(float(v) / 2 ** s, s) for v in int_vec]


def leaf(int_vec, s=S):
    return ezkl.poseidon_hash(felts(int_vec, s))[0]


def felt_int(f) -> int:
    """EZKL felt string -> integer, i.e. the uint256 the EVM verifier sees as a public instance."""
    return int(ezkl.felt_to_big_endian(f), 16)


def felt_b32(f) -> bytes:
    return felt_int(f).to_bytes(32, "big")


def head_leaf(int_vec, s=S) -> bytes:
    return chain.output_digest([felt_int(f) for f in felts(int_vec, s)])


def merkle_root(leaves, prefixes):
    """Merkle root over (i, leaf_i, P_i), as posted on-chain."""
    return chain.merkle_root(leaves, prefixes)
