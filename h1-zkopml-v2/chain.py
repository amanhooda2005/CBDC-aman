"""Prefix chain and Merkle tree for the zk-OPML dispute game.

Byte-for-byte identical to contracts/OptimisticInferenceV2.sol (checked by
test/OptimisticInferenceV2.t.sol::test_crossCheck_pythonVector and test_chain.py):

    P_{-1} = 0x00..00
    P_i    = keccak256(P_{i-1} || leaf_i)
    node_i = keccak256(uint256(i) || leaf_i || P_i)          (Merkle leaf)
    tree   = binary keccak tree over node_0..node_{n-1}, padded to a power of two by repeating
             the last node; parent = keccak256(left || right)

Leaves are 32-byte values: the Poseidon hash EZKL computes for inner steps, and
output_digest(output felts) for the head step, so the head leaf is the claimed output digest.
"""
from Crypto.Hash import keccak

ZERO = bytes(32)


def k256(b: bytes) -> bytes:
    return keccak.new(digest_bits=256, data=b).digest()


def to_b32(x) -> bytes:
    """int, 0x-hex string, EZKL felt hex string or 32 bytes -> 32-byte big-endian word."""
    if isinstance(x, (bytes, bytearray)):
        assert len(x) == 32
        return bytes(x)
    if isinstance(x, str):
        x = int(x, 16)
    return int(x).to_bytes(32, "big")


def digest(felts) -> bytes:
    """keccak256 over consecutive 32-byte words: the contract's _hashSlice / abi.encodePacked(uint256[])."""
    return k256(b"".join(to_b32(f) for f in felts))


output_digest = digest   # head-step leaf = claimed output digest
input_hash = digest      # hash of the transaction's public input felts


def prefix_chain(leaves):
    out, acc = [], ZERO
    for leaf in leaves:
        acc = k256(acc + to_b32(leaf))
        out.append(acc)
    return out


def merkle_levels(leaves, prefixes):
    nodes = [k256(i.to_bytes(32, "big") + to_b32(l) + p) for i, (l, p) in enumerate(zip(leaves, prefixes))]
    size = 1
    while size < len(nodes):
        size *= 2
    nodes += [nodes[-1]] * (size - len(nodes))
    levels = [nodes]
    while len(levels[-1]) > 1:
        t = levels[-1]
        levels.append([k256(t[i] + t[i + 1]) for i in range(0, len(t), 2)])
    return levels


def merkle_root(leaves, prefixes) -> bytes:
    return merkle_levels(leaves, prefixes)[-1][0]


def merkle_proof(levels, i):
    proof = []
    for lvl in levels[:-1]:
        proof.append(lvl[i ^ 1])
        i >>= 1
    return proof


def verify_opening(root, i, leaf, prefix, proof) -> bool:
    h = k256(i.to_bytes(32, "big") + to_b32(leaf) + prefix)
    for sib in proof:
        h = k256(h + sib) if i & 1 == 0 else k256(sib + h)
        i >>= 1
    return h == root


def first_disagreement(asserter_prefixes, challenger_prefixes):
    """Reference bisection (same midpoints as the contract): returns (k, rounds) with P_{k-1} agreed, P_k not."""
    n = len(asserter_prefixes)
    assert asserter_prefixes[n - 1] != challenger_prefixes[n - 1]
    lo, hi, rounds = -1, n - 1, 0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        rounds += 1
        if asserter_prefixes[mid] == challenger_prefixes[mid]:
            lo = mid
        else:
            hi = mid
    return hi, rounds


def chain_consistent_at(leaves, prefixes, k) -> bool:
    prev = ZERO if k == 0 else prefixes[k - 1]
    return prefixes[k] == k256(prev + to_b32(leaves[k]))
