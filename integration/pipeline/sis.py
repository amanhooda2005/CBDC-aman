"""SIS commitment, identical to h4-pqc-stark/zk_composed/src/sis.rs (checked against the Rust prover).

c = G*bits(amount) + B*r (mod p), p = 2^31 - 2^27 + 1, 48 rows, 64 amount bits + 448 ternary r.
Validators use it to check that a statement's commitment opens to the amount being settled.
"""
import secrets

P = 2_013_265_921
N_SIS, AMOUNT_COORDS, N_RAND = 48, 64, 448
M64 = (1 << 64) - 1


def _splitmix(z: int) -> int:
    z = (z + 0x9E3779B97F4A7C15) & M64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
    return z ^ (z >> 31)


_ENTRY = [[_splitmix(0x5349535F42420000 ^ (j << 24) ^ i) % P for i in range(AMOUNT_COORDS + N_RAND)] for j in range(N_SIS)]


def opening(amount: int, r):
    assert 0 <= amount < (1 << 20) and len(r) == N_RAND and all(v in (-1, 0, 1) for v in r)
    return [(amount >> t) & 1 for t in range(AMOUNT_COORDS)] + list(r)


def commit(amount: int, r):
    s = opening(amount, r)
    return [sum(row[i] * s[i] for i in range(len(s))) % P for row in _ENTRY]


def random_ternary():
    return [secrets.randbelow(3) - 1 for _ in range(N_RAND)]
