"""Assignment seeding + Ω_c randomization space — byte-exact per
COMPARISON-SCHEME-v12 §1.2 / appendix A and data/SCHEMA/CONTRACT-NOTES §6.

Owns:
  * ``seed_assign(config)`` — the exact byte layout that keys each config's
    independent PRNG stream.
  * Ω_c enumeration + rank encoding — legal P-min/P-q pairing bit strings with
    b[0]=MSB ascending rank.
  * the single ``integers(0, |Ω_c|)`` draw per config via
    ``numpy.random.Generator(PCG64(seed_assign))``.
  * the assignment table, which is persisted OUTSIDE the manifest (runtime field;
    it never enters manifest_hash).

Ω_c encoding (§1.2 / CONTRACT-NOTES §6):
  N time blocks (index 0..N-1); each block runs one P-min and one P-q.
  bit value 0 = min->q, bit value 1 = q->min (assignment = in-block exec order).
  n0 = #{bit=0}, n1 = #{bit=1}; a legal assignment has |n0 - n1| <= 1.
  Ω_c = all legal bit strings b in {0,1}^N; rank = interpret b as an N-bit
  unsigned integer with b[0] as the MSB, ascending, 0..|Ω_c|-1.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from numpy.random import Generator, PCG64

from .manifest import nfc

_HEX64 = 64  # manifest_hash is 32 bytes == 64 lowercase-hex chars


# --------------------------------------------------------------------------- #
# seed_assign (CONTRACT-NOTES §6, verbatim)
# --------------------------------------------------------------------------- #
def seed_assign(manifest_hash: str, config_id: str, N: int) -> int:
    """seed_assign(config) = uint64_le(SHA256(
           b"assign\\0" || bytes.fromhex(manifest_hash) || b"\\0"
           || NFC(config_id).encode("utf-8") || b"\\0" || uint8(N)
       )[0:8])

    ``\\0`` separators give every field a boundary (no concatenation collision).
    uint64_le = little-endian interpretation of the first 8 digest bytes.
    """
    if isinstance(N, bool) or not isinstance(N, int):
        raise TypeError("N must be an int")
    if not (0 <= N <= 255):
        raise ValueError("N must fit in uint8")
    if len(manifest_hash) != _HEX64 or any(c not in "0123456789abcdef" for c in manifest_hash):
        raise ValueError("manifest_hash must be 64 lowercase-hex chars")

    payload = (
        b"assign\x00"
        + bytes.fromhex(manifest_hash)
        + b"\x00"
        + nfc(config_id).encode("utf-8")
        + b"\x00"
        + bytes([N])  # uint8(N)
    )
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[0:8], byteorder="little", signed=False)


# --------------------------------------------------------------------------- #
# Ω_c enumeration + rank encoding
# --------------------------------------------------------------------------- #
def enumerate_omega(N: int) -> list[int]:
    """Return Ω_c as the ascending list of legal bit-string integer values.

    Each element is the integer whose N-bit big-endian representation (b[0]=MSB)
    is a legal assignment: n1 = popcount, n0 = N - popcount, legal iff
    |n0 - n1| <= 1. The list is sorted ascending by integer value (== rank
    order), so ``enumerate_omega(N)[rank]`` maps a rank back to its bit string,
    and ``len(...)`` == |Ω_c|.
    """
    if isinstance(N, bool) or not isinstance(N, int):
        raise TypeError("N must be an int")
    if N < 1:
        raise ValueError("N must be >= 1")
    omega: list[int] = []
    for value in range(1 << N):  # ascending integer order == rank order
        n1 = value.bit_count()
        n0 = N - n1
        if abs(n0 - n1) <= 1:
            omega.append(value)
    return omega


def omega_size(N: int) -> int:
    """|Ω_c| for a given N."""
    return len(enumerate_omega(N))


def bits_from_value(value: int, N: int) -> list[int]:
    """Expand an Ω_c integer to its bit list with b[0]=MSB (index 0..N-1 ==
    block 0..N-1). b[k]=0 -> block k runs min->q; b[k]=1 -> q->min."""
    return [(value >> (N - 1 - k)) & 1 for k in range(N)]


# --------------------------------------------------------------------------- #
# The single per-config assignment draw
# --------------------------------------------------------------------------- #
def draw_assignment_rank(manifest_hash: str, config_id: str, N: int) -> int:
    """Draw the assignment rank for one config: an INDEPENDENT
    Generator(PCG64(seed_assign)) calling integers(0, |Ω_c|) EXACTLY ONCE
    (CONTRACT-NOTES §6). Returns a rank in [0, |Ω_c|)."""
    seed = seed_assign(manifest_hash, config_id, N)
    k = omega_size(N)
    gen = Generator(PCG64(seed))
    rank = int(gen.integers(0, k))  # half-open [0, k), one draw only
    return rank


@dataclass(frozen=True)
class ConfigAssignment:
    """One config's frozen assignment (a runtime record, persisted OUTSIDE the
    manifest)."""

    config_id: str
    N: int
    seed_assign: int
    omega_size: int
    rank: int
    omega_value: int  # the chosen Ω_c bit-string integer (b[0]=MSB)
    bits: list[int]  # per-block bit: 0 = min->q, 1 = q->min (index == block)


def assign_config(manifest_hash: str, config_id: str, N: int) -> ConfigAssignment:
    """Full assignment record for one config."""
    seed = seed_assign(manifest_hash, config_id, N)
    omega = enumerate_omega(N)
    k = len(omega)
    gen = Generator(PCG64(seed))
    rank = int(gen.integers(0, k))
    value = omega[rank]
    return ConfigAssignment(
        config_id=nfc(config_id),
        N=N,
        seed_assign=seed,
        omega_size=k,
        rank=rank,
        omega_value=value,
        bits=bits_from_value(value, N),
    )


def build_assignment_table(
    manifest_hash: str, config_ids: Sequence[str], N: int
) -> list[dict]:
    """Build the full assignment table.

    Traversal order = config_ids PERSISTED array order (§4) — the caller MUST pass
    ``config_ids`` already in the manifest's persisted order and this function does
    NOT re-sort. Each config draws exactly once. The returned table is a runtime
    artifact persisted OUTSIDE the manifest (never enters manifest_hash); block
    ordering within a config is block index ascending (bits[0..N-1])."""
    table: list[dict] = []
    for cid in config_ids:
        a = assign_config(manifest_hash, cid, N)
        table.append(
            {
                "config_id": a.config_id,
                "N": a.N,
                "seed_assign": a.seed_assign,
                "omega_size": a.omega_size,
                "rank": a.rank,
                "omega_value": a.omega_value,
                # blocks in ascending index order; bit 0 = min->q, 1 = q->min
                "bits": a.bits,
            }
        )
    return table
