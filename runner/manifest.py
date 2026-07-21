"""Batch manifest builder — byte-exact per COMPARISON-SCHEME-v12 §1.2 and
data/SCHEMA/CONTRACT-NOTES.md.

The manifest is THE SOLE byte stream from which ``manifest_hash`` is derived. It
is generated BEFORE any assignment/randomization draw and excludes every runtime
field (assignments, seeds, timestamps). This module owns:

  * NFC-first normalization on every string
  * the canonical decimal-string grammar for coordinates (§3; integer coords use
    the grammar's no-fraction form per DECISIONS-M0 §2)
  * config_ids dedup-after-NFC + NFC-UTF-8 byte-order persisted sort (§4)
  * cities dedup on (city,date)-after-NFC + sort by byte order of each element's
    canonical JSON (JCS)
  * JCS (RFC 8785) serialization
  * manifest_hash = sha256(JCS bytes) lowercase hex + round-trip stability assert
  * clean-git-tree gate (dirty rejects the batch; --dev-allow-dirty override for
    DEV mode only, recorded in the output)

Rules that JSON Schema cannot express live in CONTRACT-NOTES.md; passing the
schema is necessary but NOT sufficient.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

import rfc8785

# Canonical decimal-string grammar (CONTRACT-NOTES §3, also embedded in
# manifest.schema.json for lat/lon). Accept: 0 52 52.52 13.405 -13.405 -0.5 ;
# reject: -0 05 1.50 1. 1e5 +5 .5 00 -0.0 52.520
DECIMAL_GRAMMAR = re.compile(r"^(0|-?(0\.[0-9]*[1-9]|[1-9][0-9]*(\.[0-9]*[1-9])?))$")

# Manifest keys required by manifest.schema.json (shape is validated separately
# via jsonschema; listed here so build_manifest emits exactly these).
_MANIFEST_KEYS = (
    "prompt_min_sha256",
    "prompt_q_sha256",
    "snapshot_sha256",
    "cities",
    "config_ids",
    "N",
    "env_digest",
    "pipeline_commit",
)


# --------------------------------------------------------------------------- #
# String / number normalization
# --------------------------------------------------------------------------- #
def nfc(value: str) -> str:
    """Unicode NFC-normalize a string (CONTRACT-NOTES §2 — NFC-first on every
    string BEFORE serialization/hashing and BEFORE every dedup/uniqueness check)."""
    if not isinstance(value, str):
        raise TypeError(f"nfc() expects str, got {type(value).__name__}")
    return unicodedata.normalize("NFC", value)


def canonical_decimal(value: Any) -> str:
    """Return the canonical decimal STRING for a coordinate (CONTRACT-NOTES §3).

    Grammar: no exponent, no leading ``+``, no leading zero except ``0.x``, no
    trailing decimal zero, no ``-0``. Integer-valued coordinates use the
    no-fraction form (e.g. ``52``), per DECISIONS-M0 §2 — this choice enters the
    byte stream and therefore manifest_hash and MUST be adopted uniformly.

    Normalization algorithm (§3): decimalize -> strip trailing zeros -> minimal
    form. Accepts int / str / Decimal (already-canonical or not). Rejects float
    (binary-float imprecision would make the byte stream non-reproducible; pass a
    str or Decimal instead) and bool.
    """
    if isinstance(value, bool):
        raise TypeError("bool is not a valid coordinate")
    if isinstance(value, float):
        raise TypeError(
            "float coordinate rejected: binary-float imprecision breaks byte "
            "reproducibility; pass a decimal string (e.g. '52.52') or Decimal"
        )
    if isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, Decimal):
        dec = value
    elif isinstance(value, str):
        s = nfc(value).strip()
        # Reject exponent / leading + explicitly (Decimal would accept them, but
        # the grammar forbids them as INPUT surface too).
        if "e" in s.lower() or s.startswith("+"):
            raise ValueError(f"non-canonical decimal input: {value!r}")
        try:
            dec = Decimal(s)
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal: {value!r}") from exc
    else:
        raise TypeError(f"unsupported coordinate type: {type(value).__name__}")

    if not dec.is_finite():
        raise ValueError(f"non-finite coordinate: {value!r}")

    # decimalize -> minimal form. Zero (incl. -0) collapses to "0" (no -0).
    if dec == 0:
        out = "0"
    else:
        sign = "-" if dec < 0 else ""
        # format(..., 'f') is fixed-point (never exponent), unlike Decimal.normalize().
        body = format(abs(dec), "f")
        if "." in body:
            body = body.rstrip("0").rstrip(".")
        out = sign + body

    if not DECIMAL_GRAMMAR.match(out):
        # Should be unreachable given the algorithm; guards against silent drift.
        raise AssertionError(f"canonicalization produced non-grammar string: {out!r}")
    return out


# --------------------------------------------------------------------------- #
# Manifest assembly
# --------------------------------------------------------------------------- #
def _normalize_config_ids(config_ids: Sequence[str]) -> list[str]:
    """NFC every config_id, reject duplicates AFTER NFC, sort by NFC-UTF-8 byte
    order and persist that order (CONTRACT-NOTES §4). Downstream use sites take
    this persisted order verbatim and are FORBIDDEN from re-sorting."""
    normalized: list[str] = []
    seen: set[str] = set()
    for cid in config_ids:
        n = nfc(cid)
        if n == "":
            raise ValueError("config_id must be non-empty")
        if n in seen:
            raise ValueError(f"duplicate config_id after NFC: {n!r}")
        seen.add(n)
        normalized.append(n)
    # Sort key = UTF-8 bytes (byte order), NOT any locale collation.
    normalized.sort(key=lambda c: c.encode("utf-8"))
    return normalized


def _normalize_cities(cities: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """NFC city/date, canonicalize lat/lon to decimal strings, reject duplicate
    (city,date) AFTER NFC, then sort by byte order of each element's canonical
    JSON (JCS) — CONTRACT-NOTES §2/§3 + scheme §1.2."""
    out: list[dict[str, str]] = []
    seen_city_date: set[tuple[str, str]] = set()
    for entry in cities:
        missing = {"city", "date", "lat", "lon"} - set(entry)
        if missing:
            raise ValueError(f"city entry missing keys: {sorted(missing)}")
        extra = set(entry) - {"city", "date", "lat", "lon"}
        if extra:
            raise ValueError(f"city entry has unexpected keys: {sorted(extra)}")
        city = nfc(str(entry["city"]))
        date = nfc(str(entry["date"]))
        if city == "":
            raise ValueError("city name must be non-empty")
        key = (city, date)
        if key in seen_city_date:
            raise ValueError(f"duplicate (city,date) after NFC: {key!r}")
        seen_city_date.add(key)
        out.append(
            {
                "city": city,
                "date": date,
                "lat": canonical_decimal(entry["lat"]),
                "lon": canonical_decimal(entry["lon"]),
            }
        )
    # Sort key = JCS bytes of each element (byte order of the element's canonical
    # JSON). Stable, total order over distinct (city,date) rows.
    out.sort(key=lambda el: rfc8785.dumps(el))
    return out


@dataclass(frozen=True)
class ManifestResult:
    """Result of building a manifest.

    ``jcs_bytes`` == the exact bytes to write to manifest.json on disk (the file
    bytes ARE the hashed bytes). ``manifest_hash`` = sha256(jcs_bytes) lowercase
    hex. ``dev_allow_dirty`` records whether the clean-tree gate was overridden
    (DEV only)."""

    manifest: dict[str, Any]
    jcs_bytes: bytes
    manifest_hash: str
    dev_allow_dirty: bool = False


def compute_manifest_hash(jcs_bytes: bytes) -> str:
    """manifest_hash = SHA256(JCS bytes) as lowercase hex (CONTRACT-NOTES §5)."""
    return hashlib.sha256(jcs_bytes).hexdigest()


def build_manifest(
    *,
    prompt_min_sha256: str,
    prompt_q_sha256: str,
    snapshot_sha256: str,
    cities: Sequence[Mapping[str, Any]],
    config_ids: Sequence[str],
    N: int,
    env_digest: str,
    pipeline_commit: str,
    dev_allow_dirty: bool = False,
    allow_dev_n: bool = False,
) -> ManifestResult:
    """Build the frozen manifest and derive manifest_hash.

    Pure/deterministic: NO git access, NO randomness, NO timestamps. Callers that
    need the clean-tree gate use ``freeze_manifest``. Every string is NFC'd, coords
    become canonical decimal strings, config_ids and cities are deduped-after-NFC
    and sorted per §4 / §1.2, then serialized with RFC 8785 (JCS). Round-trip
    stability is asserted (re-canonicalizing the bytes yields identical bytes).

    ``allow_dev_n`` widens the N gate to N>=2 for DEV batches (dev-matrix.yaml uses
    N=2 to keep iterations cheap). Production N stays {3,4,5}; the JSON Schemas keep
    the [3,4,5] enum, so a dev N=2 manifest is intentionally NON-publishable and
    fails manifest.schema.json validation by design (documented in the batch
    CONFORMANCE-REPORT)."""
    if isinstance(N, bool) or not isinstance(N, int):
        raise TypeError("N must be an int")
    _valid_n = (2, 3, 4, 5) if allow_dev_n else (3, 4, 5)
    if N not in _valid_n:
        raise ValueError(f"N must be one of {_valid_n} (scheme §1.1), got {N!r}")

    manifest: dict[str, Any] = {
        "prompt_min_sha256": nfc(prompt_min_sha256),
        "prompt_q_sha256": nfc(prompt_q_sha256),
        "snapshot_sha256": nfc(snapshot_sha256),
        "cities": _normalize_cities(cities),
        "config_ids": _normalize_config_ids(config_ids),
        "N": N,
        "env_digest": nfc(env_digest),
        "pipeline_commit": nfc(pipeline_commit),
    }
    # Emit exactly the required keys (no runtime fields leak in).
    assert set(manifest) == set(_MANIFEST_KEYS), "manifest key set drift"

    jcs_bytes = rfc8785.dumps(manifest)

    # Round-trip stability (CONTRACT-NOTES §1): the file must equal its own JCS
    # canonical form. Re-parse the bytes and re-canonicalize; bytes must match.
    reparsed = json.loads(jcs_bytes.decode("utf-8"))
    if rfc8785.dumps(reparsed) != jcs_bytes:
        raise AssertionError("manifest JCS round-trip instability")

    return ManifestResult(
        manifest=manifest,
        jcs_bytes=jcs_bytes,
        manifest_hash=compute_manifest_hash(jcs_bytes),
        dev_allow_dirty=dev_allow_dirty,
    )


# --------------------------------------------------------------------------- #
# Clean-git-tree gate
# --------------------------------------------------------------------------- #
def git_working_tree_dirty(repo_dir: str) -> bool:
    """True if the git working tree has uncommitted changes (tracked or
    untracked). Scheme §1.2: a dirty tree rejects the batch, no source_digest
    fallback."""
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip() != ""


def git_head_commit(repo_dir: str) -> str:
    """Current HEAD commit hash (used as pipeline_commit)."""
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def freeze_manifest(
    *,
    repo_dir: str,
    prompt_min_sha256: str,
    prompt_q_sha256: str,
    snapshot_sha256: str,
    cities: Sequence[Mapping[str, Any]],
    config_ids: Sequence[str],
    N: int,
    env_digest: str,
    pipeline_commit: str | None = None,
    dev_allow_dirty: bool = False,
    allow_dev_n: bool = False,
) -> ManifestResult:
    """Clean-tree gate + build_manifest. Rejects a dirty working tree unless
    ``dev_allow_dirty`` is set (DEV mode ONLY; the override is recorded in the
    returned ManifestResult.dev_allow_dirty). ``pipeline_commit`` defaults to the
    repo HEAD. ``allow_dev_n`` forwards the DEV N>=2 relaxation."""
    dirty = git_working_tree_dirty(repo_dir)
    if dirty and not dev_allow_dirty:
        raise RuntimeError(
            "git working tree is dirty; refusing to freeze manifest (scheme §1.2, "
            "no source_digest fallback). Pass dev_allow_dirty=True for DEV mode only."
        )
    commit = pipeline_commit if pipeline_commit is not None else git_head_commit(repo_dir)
    return build_manifest(
        prompt_min_sha256=prompt_min_sha256,
        prompt_q_sha256=prompt_q_sha256,
        snapshot_sha256=snapshot_sha256,
        cities=cities,
        config_ids=config_ids,
        N=N,
        env_digest=env_digest,
        pipeline_commit=commit,
        dev_allow_dirty=dirty and dev_allow_dirty,
        allow_dev_n=allow_dev_n,
    )
