#!/usr/bin/env python3
"""Incremental-cache identity, canonical serialization, and atomicity primitives
for the L2 similarity runner (INCREMENTAL-PLAN.md verdicts 1/2/5).

This module owns everything the runner needs to decide *whether an existing pair
document is still valid* and to *write outputs atomically and deterministically*:

  * component / slot / config-variant inventory digests (verdict 1);
  * pair-engine and summary-engine fingerprints, kept SEPARATE so a summary-only
    change never invalidates pair computations (verdict 1);
  * the pair identity key and the per-pair SIDECAR read/write/validate (verdict 2 —
    the frozen pairs schema is ``additionalProperties:false`` so identity cannot be
    embedded in the pair JSON; the sidecar carries the pair file's SHA-256 and is
    written AFTER the pair, exactly the fallback Sol blessed);
  * canonical JSON serialization: ``sort_keys``, ``allow_nan=False``, normalized
    ``-0.0``, fixed whitespace (verdict 5);
  * atomic same-directory write (tmp + fsync + os.replace), skip-if-identical,
    the batch advisory lock, and the INCOMPLETE marker / completion manifest
    (verdict 2).

Identity structures carry only strings / ints / nested containers (never floats),
so their canonical bytes are unambiguous; the float-normalization path is used only
for the emitted pair / summary documents.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# version boundaries (verdict 1: explicit, reviewable compatibility boundaries)
# ---------------------------------------------------------------------------
# Bump PAIR_ENGINE_VERSION when the *pair* computation changes in a way NOT
# captured by a channel-module source hash — i.e. artifact loading
# (``_read_slot_bytes`` / ``_artifacts_from_bytes``), the ``_compute_pair`` channel
# loop, the diagnostics rules, or the per-pair JSON shape.
PAIR_ENGINE_VERSION = "pair-engine/1"
# Bump SUMMARY_ENGINE_VERSION when aggregation (``_aggregate_cell`` /
# ``_percentile_linear`` / eligibility / summary JSON shape) changes. Kept separate
# so summary edits do not invalidate pair caches.
SUMMARY_ENGINE_VERSION = "summary-engine/1"
# Canonical serializer identity: bump if the emitted-file byte form changes so that
# stale-format files are recomputed rather than mixed.
SERIALIZER_VERSION = "canon-json/1"
# Pair / summary JSON schema shape this runner targets (mirrors the frozen schemas).
PAIR_SCHEMA_VERSION = "similarity-pairs/1.0.0"
SUMMARY_SCHEMA_VERSION = "similarity-summary/1.0.0"

CACHE_DIRNAME = ".incremental"          # under <batch>/similarity/
INCOMPLETE_MARKER_NAME = "INCOMPLETE"   # under <batch>/similarity/
COMPLETION_MANIFEST_NAME = "completion.json"  # under .incremental/
LOCK_NAME = ".run.lock"                 # under .incremental/
SIDECAR_SUFFIX = ".key"                 # sidecar = <pairfile>.key (never *.json)

ABSENT = "ABSENT"


# ---------------------------------------------------------------------------
# hashing
# ---------------------------------------------------------------------------
def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _identity_bytes(obj: Any) -> bytes:
    """Compact, sorted, unambiguous bytes for HASHING identity structures.

    Identity structures never contain floats, so no float normalization is needed
    here; the whitespace-free ``separators`` + ``sort_keys`` make the encoding
    canonical.
    """
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def identity_digest(obj: Any) -> str:
    return sha256_hex(_identity_bytes(obj))


# ---------------------------------------------------------------------------
# canonical document serialization (verdict 5) — for EMITTED pair / summary files
# ---------------------------------------------------------------------------
def _normalize_floats(obj: Any) -> Any:
    """Recursively normalize ``-0.0`` → ``0.0`` (and pass everything else through).

    ``json.dumps`` already emits floats via Python's shortest round-tripping repr
    (deterministic across processes of the same CPython); the only non-canonical
    float form it would preserve is signed zero, which this collapses. ``nan`` /
    ``inf`` are left for ``allow_nan=False`` to reject loudly (a nan S is a bug, not
    a cacheable value — verdict 7).
    """
    if isinstance(obj, float):
        return 0.0 if obj == 0.0 else obj
    if isinstance(obj, dict):
        return {k: _normalize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_floats(v) for v in obj]
    return obj


def dump_doc(doc: dict) -> bytes:
    """Canonical UTF-8 bytes for a pair / summary document.

    ``sort_keys`` fixes object key order (arrays keep their semantic order — the
    runner emits channels / configs / pairs / cells already ordered); ``indent=1``
    keeps parity with the historical, human-readable form; ``allow_nan=False``
    rejects non-finite S. Trailing newline for POSIX / git friendliness.
    """
    text = json.dumps(
        _normalize_floats(doc),
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        indent=1,
    )
    return (text + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# slot component digests (verdict 1)
# ---------------------------------------------------------------------------
def _shot_selection(slot_dir: Path) -> tuple[str, bytes] | None:
    """Prefer shot.png (frozen original); fall back to shot.webp (in-repo derivative).

    Returns ``(kind, bytes)`` or ``None``. Mirrors ``runner._shot_path`` selection.
    """
    for name, kind in (("shot.png", "png"), ("shot.webp", "webp")):
        p = slot_dir / name
        if p.is_file() and p.stat().st_size > 0:
            return kind, p.read_bytes()
    return None


def read_slot_bytes(slot_dir: Path) -> dict[str, Any]:
    """Read the raw artifact bytes of one slot exactly ONCE.

    Returns ``{"html": bytes|None, "shot_kind": str|None, "shot": bytes|None,
    "dom": bytes|None}``. The caller decides validity from meta.json; this only
    reads the three artifacts that feed the channels.
    """
    html_p = slot_dir / "card.html"
    dom_p = slot_dir / "dom.json"
    shot = _shot_selection(slot_dir)
    return {
        "html": html_p.read_bytes() if html_p.is_file() else None,
        "shot_kind": shot[0] if shot is not None else None,
        "shot": shot[1] if shot is not None else None,
        "dom": dom_p.read_bytes() if dom_p.is_file() else None,
    }


def component_digests(sb: dict[str, Any]) -> list:
    """Per-component digest tuple with explicit ABSENT markers and typed framing
    (verdict 1: 分组件哈希非裸拼接 — hash each component separately, mark absence).

    The shot component includes its container KIND (png vs webp) because the two
    decode to different pixels; a png→webp swap must invalidate the visual channels.
    """
    html = ["html", sha256_hex(sb["html"])] if sb["html"] is not None else ["html", ABSENT]
    if sb["shot"] is not None:
        shot = ["shot", sb["shot_kind"], sha256_hex(sb["shot"])]
    else:
        shot = ["shot", ABSENT]
    dom = ["dom", sha256_hex(sb["dom"])] if sb["dom"] is not None else ["dom", ABSENT]
    return [html, shot, dom]


def slot_digest(sb: dict[str, Any]) -> str:
    return identity_digest(component_digests(sb))


def inventory_digest(ordered_slot_entries: list[tuple[int, str]]) -> str:
    """Config-variant inventory digest over ordered ``(slot_index, slot_digest)``
    entries (verdict 1 — catches added / removed / validity-changed / edited slots).
    """
    return identity_digest([[idx, dig] for idx, dig in ordered_slot_entries])


# ---------------------------------------------------------------------------
# engine fingerprints (verdict 1)
# ---------------------------------------------------------------------------
def _node_version() -> str:
    try:
        out = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=30
        )
        return out.stdout.strip() if out.returncode == 0 else f"ERR:{out.returncode}"
    except Exception as exc:  # noqa: BLE001 — absence is itself a fingerprint value
        return f"ABSENT:{type(exc).__name__}"


def _node_helper_digests(helper_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in ("package.json", "package-lock.json"):
        p = helper_dir / name
        out[name] = sha256_file(p) if p.is_file() else ABSENT
    for script in sorted(helper_dir.glob("*.mjs")):
        out[script.name] = sha256_file(script)
    return out


def _runtime_versions() -> dict[str, str]:
    import zlib

    versions = {"python": sys.version.split()[0], "zlib": zlib.ZLIB_VERSION}
    for mod_name, key in (("PIL", "pillow"), ("numpy", "numpy"), ("skimage", "skimage")):
        try:
            mod = __import__(mod_name)
            versions[key] = getattr(mod, "__version__", "UNKNOWN")
        except Exception:  # noqa: BLE001
            versions[key] = ABSENT
    return versions


def build_engine_fingerprint(
    channel_modules: list[tuple[str, Any]],
    channel_names: list[str],
    n_eff_min: int,
    node_helper_dirs: dict[str, Path],
) -> dict[str, Any]:
    """Full engine fingerprint. Returns a dict carrying both the raw material and
    the two derived digests (``pair_engine_digest`` / ``summary_engine_digest``).

    Pair-engine coverage: per-channel source hashes, node helper + node version,
    Pillow / numpy / skimage / zlib / Python versions, artifact/serializer/schema
    version boundaries. Summary-engine wraps the pair-engine digest (a pair change
    is also a summary change) plus its own version / eligibility params.
    """
    channel_sources = {
        name: sha256_file(Path(mod.__file__)) for name, mod in channel_modules
    }
    node_helpers = {
        label: _node_helper_digests(d) for label, d in sorted(node_helper_dirs.items())
    }
    pair_engine = {
        "pair_engine_version": PAIR_ENGINE_VERSION,
        "pair_schema_version": PAIR_SCHEMA_VERSION,
        "serializer_version": SERIALIZER_VERSION,
        "channel_names": list(channel_names),
        "channel_sources": channel_sources,
        "node_version": _node_version(),
        "node_helpers": node_helpers,
        "runtime": _runtime_versions(),
    }
    pair_engine_digest = identity_digest(pair_engine)

    summary_engine = {
        "summary_engine_version": SUMMARY_ENGINE_VERSION,
        "summary_schema_version": SUMMARY_SCHEMA_VERSION,
        "serializer_version": SERIALIZER_VERSION,
        "n_eff_min": n_eff_min,
        "channel_names": list(channel_names),
        "pair_engine_digest": pair_engine_digest,
    }
    summary_engine_digest = identity_digest(summary_engine)

    return {
        "pair_engine": pair_engine,
        "pair_engine_digest": pair_engine_digest,
        "summary_engine": summary_engine,
        "summary_engine_digest": summary_engine_digest,
    }


# ---------------------------------------------------------------------------
# pair / summary identity keys (verdict 1)
# ---------------------------------------------------------------------------
def pair_key(
    pair_engine_digest: str,
    variant: str,
    kind: str,
    config_ids_ordered: list[str],
    inventory_digests_ordered: list[str],
) -> str:
    """Pair identity = (variant, ordered config ids, self/cross kind, pair schema,
    engine fingerprint, involved inventory digests)."""
    return identity_digest(
        {
            "pair_engine_digest": pair_engine_digest,
            "variant": variant,
            "kind": kind,
            "configs": config_ids_ordered,
            "inventories": inventory_digests_ordered,
        }
    )


def summary_key(
    summary_engine_digest: str,
    variant: str,
    config_ids: list[str],
    inventory_by_config: dict[str, str],
) -> str:
    """Summary identity = (summary engine, variant, config order, every involved
    inventory digest). Rebuilds exactly when any pair could have changed."""
    return identity_digest(
        {
            "summary_engine_digest": summary_engine_digest,
            "variant": variant,
            "configs": config_ids,
            "inventories": [[cid, inventory_by_config[cid]] for cid in config_ids],
        }
    )


# ---------------------------------------------------------------------------
# atomic IO (verdict 2)
# ---------------------------------------------------------------------------
def _fsync_dir(dir_path: Path) -> None:
    try:
        fd = os.open(str(dir_path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass  # best effort (some filesystems disallow directory fsync)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Same-directory tmp + flush + fsync + os.replace (verdict 2)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_if_changed(path: Path, data: bytes) -> bool:
    """Write only when the on-disk bytes differ; returns True iff written.

    Keeps warm runs mtime-stable (verdict / verification: a re-run that changes
    nothing must touch nothing).
    """
    if path.is_file():
        try:
            if path.read_bytes() == data:
                return False
        except OSError:
            pass
    atomic_write_bytes(path, data)
    return True


# ---------------------------------------------------------------------------
# per-pair sidecar (verdict 2 — schema forbids embedded identity)
# ---------------------------------------------------------------------------
def sidecar_path(cache_pairs_dir: Path, pair_filename: str) -> Path:
    return cache_pairs_dir / (pair_filename + SIDECAR_SUFFIX)


def write_sidecar(
    cache_pairs_dir: Path, pair_filename: str, pair_path: Path, record: dict[str, Any]
) -> None:
    """Write the sidecar AFTER the pair file, binding it to the pair's SHA-256."""
    full = dict(record)
    full["pair_sha256"] = sha256_file(pair_path)
    atomic_write_bytes(
        sidecar_path(cache_pairs_dir, pair_filename), _identity_bytes(full) + b"\n"
    )


def validate_pair_cache(
    cache_pairs_dir: Path,
    pairs_dir: Path,
    pair_filename: str,
    expected_pair_key: str,
) -> bool:
    """A pair document is a valid cache hit iff: its sidecar exists and parses, its
    recorded pair_key equals the expected key, the pair file exists, and the pair
    file's live SHA-256 matches the sidecar (guards corruption / partial writes /
    a hash-vs-write race — verdict 2 'never trust a central index alone': the
    sidecar is bound to the actual bytes, not a bare index)."""
    sc = sidecar_path(cache_pairs_dir, pair_filename)
    if not sc.is_file():
        return False
    try:
        rec = json.loads(sc.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if rec.get("pair_key") != expected_pair_key:
        return False
    pf = pairs_dir / pair_filename
    if not pf.is_file():
        return False
    try:
        return sha256_file(pf) == rec.get("pair_sha256")
    except OSError:
        return False


# ---------------------------------------------------------------------------
# batch lock / incomplete marker / completion manifest (verdict 2)
# ---------------------------------------------------------------------------
class BatchLock:
    """Exclusive advisory lock (fcntl.flock) held by the single run() process for
    the whole batch. Workers never take it."""

    def __init__(self, cache_dir: Path):
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._path = cache_dir / LOCK_NAME
        self._fd: int | None = None

    def __enter__(self) -> "BatchLock":
        self._fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            raise RuntimeError(
                f"similarity: batch already locked by another run ({self._path})"
            ) from exc
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def write_incomplete_marker(sim_dir: Path, info: dict[str, Any]) -> None:
    atomic_write_bytes(sim_dir / INCOMPLETE_MARKER_NAME, _identity_bytes(info) + b"\n")


def remove_incomplete_marker(sim_dir: Path) -> None:
    p = sim_dir / INCOMPLETE_MARKER_NAME
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass
    _fsync_dir(sim_dir)


def incomplete_marker_present(sim_dir: Path) -> bool:
    return (sim_dir / INCOMPLETE_MARKER_NAME).is_file()


def write_completion_manifest(cache_dir: Path, record: dict[str, Any]) -> bool:
    """Deterministic completion manifest (no wall-clock → warm-run stable).
    Written LAST, just before the INCOMPLETE marker is removed."""
    return atomic_write_if_changed(
        cache_dir / COMPLETION_MANIFEST_NAME, _identity_bytes(record) + b"\n"
    )
