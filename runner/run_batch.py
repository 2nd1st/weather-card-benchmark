"""run_batch — M1 DEV end-to-end batch runner wiring R1→R4.

Flow (scheme §1.1/§1.2, §2; DATA-LAYOUT + DECISIONS-M0 §4 variant layout):

    freeze weather snapshot  →  build manifest (dev-allow-dirty)
      →  seed/assignment (Ω draw, both P-min/P-q per block)
      →  slot engine × adapter (sub2api dev gateway)
      →  render each VALID card (frozen shot + double-render)
      →  write data/batches-dev/<batch-id>/ per the frozen layout
      →  validate every written JSON against its schema (jsonschema)
      →  index.json + CONFORMANCE-REPORT.md + machine summary

DEV ONLY. Output goes to data/batches-dev/ (gitignored), N=2 (a dev relaxation of
the published-only [3,4,5] schema enum — the resulting manifest/config/send-log
intentionally FAIL their schema on N and this is documented in the batch
CONFORMANCE-REPORT). R5 (L1/fidelity extractor), R6 (L2 similarity), R7 (L3 stats),
R8 (probes) are NOT implemented yet: this runner writes only the artifacts those
milestones do not own, and reports the pending ones.

Usage:
    python -m runner.run_batch --config runner/configs/dev-matrix.yaml --dev \
        [--batch-id <id>] [--only gpt-5.6-sol--api--raw--dev] [--no-render]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import requests
import rfc8785
import yaml
from jsonschema import Draft202012Validator

from . import manifest as R1
from . import seed as R2seed
from . import slots as R2
from .adapters import openai_compat as R3
from .adapters import claude_cli as R3CLI
from .adapters import codex_cli as R3CODEX
from .adapters import grok_cli as R3GROK
from .adapters import kiro_cli as R3KIRO
from .adapters import qoder_cli as R3QODER
from .adapters import opencode_cli as R3OC
from .fidelity import EXTRACTOR_BASE, expected_from_snapshot, extractor_version_for
from .render.env_digest import env_digest
from .render.local_server import LocalCardServer
from .slot_pipeline import produce_slot_artifacts

# --------------------------------------------------------------------------- #
# Fixed DEV constants
# --------------------------------------------------------------------------- #
REPO = Path(__file__).resolve().parent.parent
TRIAL = REPO / "trial-20260715"
SCHEMA_DIR = REPO / "data" / "SCHEMA"
BATCHES_DEV = REPO / "data" / "batches-dev"
API_CACHE = TRIAL / "api-cache"
PROMPT_MIN = TRIAL / "prompt-v5-min.txt"
PROMPT_Q = TRIAL / "prompt-v5-q.txt"

# Measurement fixture: Berlin + a FIXED past date present in the trial cache
# (archive data for 2026-07-15 verified reachable; render Date is frozen to noon
# of the following day so the card routes to /api/om/archive).
CITY = "Berlin"
LAT = "52.52"
LON = "13.405"
FIXTURE_DATE = "2026-07-15"
FETCHED_AT = "2026-07-16T12:00:00Z"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# extractor_version = the fidelity-extractor revision + the frozen expected-value
# table (derived from this batch's snapshot: city+date), stamped into the snapshot
# and every fidelity verdict / trace (scheme §1.2 期望值表随 extractor 版本落盘 / §11).
EXTRACTOR_VERSION = f"{EXTRACTOR_BASE}+{CITY}-{FIXTURE_DATE}"

VARIANT_DATA = {"min": "P-min", "q": "P-q"}  # dir token -> data/schema token

SCHEMA_FILES = {
    "manifest": "manifest.schema.json",
    "config": "config.schema.json",
    "slot-meta": "slot-meta.schema.json",
    "send-log": "send-log.schema.json",
    "weather-snapshot": "weather-snapshot.schema.json",
    "index": "index.schema.json",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env(env_path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def cdec(value: Any) -> str:
    """Canonical decimal string, accepting Decimal/int/str (never bare float)."""
    return R1.canonical_decimal(value)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assert_calendar_date(s: str) -> None:
    datetime.strptime(s, "%Y-%m-%d")  # rejects impossible dates (CONTRACT-NOTES §10)


# --------------------------------------------------------------------------- #
# weather snapshot (R1 companion — directly-hashed byte stream)
# --------------------------------------------------------------------------- #
def fetch_archive(session: requests.Session) -> dict:
    params = {
        "latitude": LAT,
        "longitude": LON,
        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        "hourly": "temperature_2m",
        "timezone": "UTC",
        "temperature_unit": "celsius",
        "start_date": FIXTURE_DATE,
        "end_date": FIXTURE_DATE,
    }
    r = session.get(ARCHIVE_URL, params=params, timeout=30)
    r.raise_for_status()
    # Parse numbers as Decimal so the canonical decimal-string encoding is exact.
    return json.loads(r.text, parse_float=Decimal)


def build_snapshot(batch_id: str, raw: dict) -> tuple[dict, bytes, str]:
    """Project the upstream archive response down to the pinned contract fields,
    encode every non-integer numeric as a canonical decimal string, and freeze the
    whole weather-snapshot object as a JCS byte stream (snapshot_sha256 anchor)."""
    du = raw["daily_units"]
    d = raw["daily"]
    hu = raw["hourly_units"]
    h = raw["hourly"]

    def dstr_list(xs: list) -> list[str]:
        return [cdec(x) for x in xs]

    response = {
        "latitude": cdec(raw["latitude"]),
        "longitude": cdec(raw["longitude"]),
        "generationtime_ms": cdec(raw["generationtime_ms"]),
        "utc_offset_seconds": int(raw["utc_offset_seconds"]),
        "timezone": R1.nfc(str(raw["timezone"])),
        "timezone_abbreviation": R1.nfc(str(raw["timezone_abbreviation"])),
        "elevation": cdec(raw["elevation"]),
        "daily_units": {
            "time": R1.nfc(str(du["time"])),
            "temperature_2m_max": R1.nfc(str(du["temperature_2m_max"])),
            "temperature_2m_min": R1.nfc(str(du["temperature_2m_min"])),
            "weather_code": R1.nfc(str(du["weather_code"])),
        },
        "daily": {
            "time": [R1.nfc(str(t)) for t in d["time"]],
            "temperature_2m_max": dstr_list(d["temperature_2m_max"]),
            "temperature_2m_min": dstr_list(d["temperature_2m_min"]),
            "weather_code": [int(c) for c in d["weather_code"]],
        },
        "hourly_units": {
            "time": R1.nfc(str(hu["time"])),
            "temperature_2m": R1.nfc(str(hu["temperature_2m"])),
        },
        "hourly": {
            "time": [R1.nfc(str(t)) for t in h["time"]],
            "temperature_2m": dstr_list(h["temperature_2m"]),
        },
    }
    canary = {
        "daily": {
            "temperature_2m_max": response["daily"]["temperature_2m_max"][0],
            "temperature_2m_min": response["daily"]["temperature_2m_min"][0],
            "weather_code": response["daily"]["weather_code"][0],
        },
        "hourly_temperature_2m": list(response["hourly"]["temperature_2m"]),
    }
    snapshot = {
        "batch_id": batch_id,
        "source": "open-meteo",
        "extractor_version": EXTRACTOR_VERSION,
        "fetched_at": FETCHED_AT,
        "locations": [
            {
                "city": R1.nfc(CITY),
                "date": FIXTURE_DATE,
                "lat": LAT,
                "lon": LON,
                "endpoint": "archive",
                "response": response,
                "canary": canary,
            }
        ],
    }
    jcs = rfc8785.dumps(snapshot)
    return snapshot, jcs, sha256_hex(jcs)


# --------------------------------------------------------------------------- #
# adapter wrapper: openai_compat.call_model -> R2 slot-engine AdapterResult
# --------------------------------------------------------------------------- #
_OPENAI_TO_SLOT_STATUS = {
    "valid": "ok",
    "model_error": "model_error",
    "infra_error": "infra_error",
    "acceptance_unknown": "ambiguous",
    "unsent": "unsent",
    "rate_limited": "rate_limited",
}


@dataclass
class CodexCliAdapter:
    """gpt-family cli arm: codex exec, isolated CODEX_HOME (stock harness).

    oauth=True → ChatGPT-plan direct login (zero proxy, formal-grade channel);
    oauth=False → sub2 provider via api_key (dev-only channel).
    """
    api_key: str = ""
    oauth: bool = False
    calls: list = field(default_factory=list)

    def call(self, prompt: str, config: dict) -> R2.AdapterResult:
        r = R3CODEX.call_codex_cli(config, prompt, api_key=self.api_key, oauth=self.oauth)
        self.calls.append(r)
        if r.status != "valid":
            print(f"[codex-cli] {r.status} wall={r.wall_ms}ms err={r.error!r}", flush=True)
        status = {"valid": "ok", "model_error": "model_error",
                  "infra_error": "infra_error", "rate_limited": "rate_limited"}[r.status]
        return R2.AdapterResult(
            status=status,
            html=r.html,
            telemetry={
                "prompt_tokens": r.tokens_in,
                "completion_tokens": r.tokens_out,
                "wall_ms": r.wall_ms,
                "ttft_ms": None,
            },
            served_model=r.served_model,
            raw_error=r.error,
            reason=("http-429" if status == "rate_limited" else None),
            http_status=None,
            request_id=None,
        )


@dataclass
class OpencodeCliAdapter:
    """cli arm via opencode CLI, isolated XDG home, go-subscription provider."""
    calls: list = field(default_factory=list)

    def call(self, prompt: str, config: dict) -> R2.AdapterResult:
        r = R3OC.call_opencode_cli(config, prompt)
        self.calls.append(r)
        if r.status != "valid":
            print(f"[opencode-cli] {r.status} wall={r.wall_ms}ms err={r.error!r}", flush=True)
        status = {"valid": "ok", "model_error": "model_error",
                  "infra_error": "infra_error", "rate_limited": "rate_limited"}[r.status]
        return R2.AdapterResult(
            status=status,
            html=r.html,
            telemetry={
                "prompt_tokens": r.tokens_in,
                "completion_tokens": r.tokens_out,
                "wall_ms": r.wall_ms,
                "ttft_ms": None,
            },
            served_model=r.served_model,
            raw_error=r.error,
            reason=("http-429" if status == "rate_limited" else None),
            http_status=None,
            request_id=None,
        )


@dataclass
class GrokCliAdapter:
    """grok-family cli arm: official grok binary, isolated fake $HOME (stock harness)."""
    calls: list = field(default_factory=list)

    def call(self, prompt: str, config: dict) -> R2.AdapterResult:
        r = R3GROK.call_grok_cli(config, prompt)
        self.calls.append(r)
        if r.status != "valid":
            print(f"[grok-cli] {r.status} wall={r.wall_ms}ms err={r.error!r}", flush=True)
        status = {"valid": "ok", "model_error": "model_error",
                  "infra_error": "infra_error", "rate_limited": "rate_limited"}[r.status]
        return R2.AdapterResult(
            status=status,
            html=r.html,
            telemetry={
                "prompt_tokens": r.tokens_in,
                "completion_tokens": r.tokens_out,
                "wall_ms": r.wall_ms,
                "ttft_ms": None,
            },
            served_model=r.served_model,
            raw_error=r.error,
            reason=("http-429" if status == "rate_limited" else None),
            http_status=None,
            request_id=None,
        )


@dataclass
class QoderCliAdapter:
    """qwen-family cli arm: qodercli headless, stock login (clean global config)."""
    calls: list = field(default_factory=list)

    def call(self, prompt: str, config: dict) -> R2.AdapterResult:
        r = R3QODER.call_qoder_cli(config, prompt)
        self.calls.append(r)
        if r.status != "valid":
            print(f"[qoder-cli] {r.status} wall={r.wall_ms}ms err={r.error!r}", flush=True)
        status = {"valid": "ok", "model_error": "model_error",
                  "infra_error": "infra_error", "rate_limited": "rate_limited"}[r.status]
        return R2.AdapterResult(
            status=status,
            html=r.html,
            telemetry={
                "prompt_tokens": r.tokens_in,
                "completion_tokens": r.tokens_out,
                "wall_ms": r.wall_ms,
                "ttft_ms": None,
            },
            served_model=r.served_model,
            raw_error=r.error,
            reason=("http-429" if status == "rate_limited" else None),
            http_status=None,
            request_id=None,
        )


@dataclass
class KiroCliAdapter:
    """multi-family cli arm: kiro-cli headless, isolated fake $HOME (stock harness)."""
    calls: list = field(default_factory=list)

    def call(self, prompt: str, config: dict) -> R2.AdapterResult:
        r = R3KIRO.call_kiro_cli(config, prompt)
        self.calls.append(r)
        if r.status != "valid":
            print(f"[kiro-cli] {r.status} wall={r.wall_ms}ms err={r.error!r}", flush=True)
        status = {"valid": "ok", "model_error": "model_error",
                  "infra_error": "infra_error", "rate_limited": "rate_limited"}[r.status]
        return R2.AdapterResult(
            status=status,
            html=r.html,
            telemetry={
                "prompt_tokens": r.tokens_in,
                "completion_tokens": r.tokens_out,
                "wall_ms": r.wall_ms,
                "ttft_ms": None,
            },
            served_model=r.served_model,
            raw_error=r.error,
            reason=("http-429" if status == "rate_limited" else None),
            http_status=None,
            request_id=None,
        )


@dataclass
class ClaudeCliAdapter:
    """protocol=cli harness arm: local Claude Code headless (plan, zero proxy)."""
    calls: list = field(default_factory=list)

    def call(self, prompt: str, config: dict) -> R2.AdapterResult:
        r = R3CLI.call_claude_cli(config, prompt)
        self.calls.append(r)
        if r.status != "valid":
            print(f"[claude-cli] {r.status} wall={r.wall_ms}ms err={r.error!r}", flush=True)
        status = {"valid": "ok", "model_error": "model_error",
                  "infra_error": "infra_error", "rate_limited": "rate_limited"}[r.status]
        return R2.AdapterResult(
            status=status,
            html=r.html,
            telemetry={
                "prompt_tokens": r.tokens_in,
                "completion_tokens": r.tokens_out,
                "wall_ms": r.wall_ms,
                "ttft_ms": None,
            },
            served_model=r.served_model,
            raw_error=r.error,
            reason=("quota-exceeded" if status == "rate_limited" else None),
            http_status=None,
            request_id=None,
        )


@dataclass
class Sub2ApiAdapter:
    api_key: str
    base_url: str
    # transport.background: true -> /responses background+poll (official OpenAI
    # SSE drops long-reasoning streams mid-flight; see openai_compat notes).
    background: bool = False
    calls: list = field(default_factory=list)

    def call(self, prompt: str, config: dict) -> R2.AdapterResult:
        if self.background and R3.endpoint_for(config.get("family"), config) == "responses":
            r = R3.call_model_background(config, prompt, api_key=self.api_key, base_url=self.base_url)
        else:
            r = R3.call_model(config, prompt, api_key=self.api_key, base_url=self.base_url)
        self.calls.append(r)
        status = _OPENAI_TO_SLOT_STATUS[r.status]
        reason = None
        if status == "rate_limited":
            reason = {429: "http-429", 503: "http-503-overloaded", 402: "quota-exceeded"}.get(
                r.http_status, "http-429"
            )
        return R2.AdapterResult(
            status=status,
            html=r.html,
            telemetry={
                "prompt_tokens": r.tokens_in,
                "completion_tokens": r.tokens_out,
                "wall_ms": r.wall_ms,
                "ttft_ms": r.ttft_ms,
            },
            served_model=r.served_model,
            raw_error=r.error,
            reason=reason,
            http_status=(r.http_status if r.http_status is not None else None),
            request_id=None,
        )


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
_VALIDATORS: dict[str, Draft202012Validator] = {}


def validator_for(kind: str) -> Draft202012Validator:
    if kind not in _VALIDATORS:
        schema = json.loads((SCHEMA_DIR / SCHEMA_FILES[kind]).read_text())
        _VALIDATORS[kind] = Draft202012Validator(schema)
    return _VALIDATORS[kind]


def validate(kind: str, instance: Any, rel_path: str) -> dict:
    errs = sorted(validator_for(kind).iter_errors(instance), key=lambda e: list(e.path))
    return {
        "file": rel_path,
        "schema": SCHEMA_FILES[kind],
        "valid": not errs,
        "errors": [
            {"path": "/".join(str(p) for p in e.path) or "<root>", "message": e.message}
            for e in errs
        ],
    }


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="M1 DEV end-to-end batch runner")
    ap.add_argument("--config", required=True, help="path to dev matrix yaml")
    ap.add_argument("--dev", action="store_true", help="DEV mode (required)")
    ap.add_argument("--batch-id", default=None)
    ap.add_argument("--only", default=None, help="run only this config_id")
    ap.add_argument("--skip-completed", action="store_true",
                    help="resume: skip configs whose config.json already exists in the batch dir")
    ap.add_argument("--no-render", action="store_true", help="skip Playwright render")
    ap.add_argument("--no-retain-png", dest="retain_png", action="store_false",
                    help="do not keep the frozen shot.png (default: retain — PNG stays "
                         "out of git via the data/batches-dev gitignore, per DESIGN)")
    ap.set_defaults(retain_png=True)
    args = ap.parse_args(argv)
    if not args.dev:
        ap.error("this runner only supports --dev (DEV mode) in M1")

    cfg = yaml.safe_load(Path(args.config).read_text())
    N = int(cfg["N"])
    transport = cfg["transport"]
    configs = cfg["configs"]
    if args.only:
        configs = [c for c in configs if c["config_id"] == args.only]
        if not configs:
            ap.error(f"--only {args.only!r} matched no config")

    env = load_env(REPO / "runner" / ".env")
    # The yaml's transport.base_url is authoritative — WCB_API_BASE is only a
    # fallback for yamls that don't pin one. (An unconditional env override once
    # sent the opencode-go key to the sub2 gateway → 64×401.)
    base_url = transport.get("base_url") or env.get("WCB_API_BASE", "")

    batch_id = args.batch_id or f"{FETCHED_AT[:10]}--m1-dev-e2e-smoke"
    batch_dir = BATCHES_DEV / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = batch_dir / "_runtime"
    runtime_dir.mkdir(exist_ok=True)

    assert_calendar_date(FIXTURE_DATE)
    log = lambda *a: print(*a, file=sys.stderr, flush=True)  # noqa: E731

    # ---- prompts (hash raw file bytes, scheme §1.2 / §9) -------------------
    min_bytes = PROMPT_MIN.read_bytes()
    q_bytes = PROMPT_Q.read_bytes()
    prompt_min_sha256 = sha256_hex(min_bytes)
    prompt_q_sha256 = sha256_hex(q_bytes)
    prompt_text = {"min": min_bytes.decode("utf-8"), "q": q_bytes.decode("utf-8")}

    # ---- launch browser (for env_digest + render) -------------------------
    playwright = None
    browser = None
    chromium_version = "unavailable"
    if not args.no_render:
        from playwright.sync_api import sync_playwright  # local import (heavy)

        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-gpu",
                "--font-render-hinting=none",
                "--force-color-profile=srgb",
                "--hide-scrollbars",
            ],
        )
        chromium_version = browser.version
    digest = env_digest(chromium_version, dev=True)

    session = requests.Session()

    validations: list[dict] = []
    slot_outcomes: list[dict] = []
    render_reports: list[dict] = []
    config_records: list[dict] = []

    try:
        # ---- weather snapshot -------------------------------------------- #
        log(f"[snapshot] fetching archive {CITY} {FIXTURE_DATE} ...")
        raw = fetch_archive(session)
        snapshot, snap_jcs, snapshot_sha256 = build_snapshot(batch_id, raw)
        (batch_dir / "weather-snapshot.json").write_bytes(snap_jcs)
        validations.append(validate("weather-snapshot", snapshot, "weather-snapshot.json"))
        log(f"[snapshot] snapshot_sha256={snapshot_sha256}")

        # ---- expected-value table (derived from THIS batch's snapshot) ----- #
        expected = expected_from_snapshot(snapshot)
        extractor_version = extractor_version_for(snapshot)
        render_snapshot = {"fetched_at": snapshot["fetched_at"]}
        render_params = {"lat": LAT, "lon": LON, "name": CITY, "date": FIXTURE_DATE}
        log(f"[fidelity] extractor_version={extractor_version} "
            f"expected max={expected.temp_max} min={expected.temp_min} "
            f"code={expected.weather_code} hourly={len(expected.hourly)}")

        # ---- manifest (R1) ----------------------------------------------- #
        config_ids = [c["config_id"] for c in configs]
        mres = R1.freeze_manifest(
            repo_dir=str(REPO),
            prompt_min_sha256=prompt_min_sha256,
            prompt_q_sha256=prompt_q_sha256,
            snapshot_sha256=snapshot_sha256,
            cities=[{"city": CITY, "date": FIXTURE_DATE, "lat": LAT, "lon": LON}],
            config_ids=config_ids,
            N=N,
            env_digest=digest,
            dev_allow_dirty=True,
            allow_dev_n=True,
        )
        (batch_dir / "manifest.json").write_bytes(mres.jcs_bytes)
        validations.append(validate("manifest", mres.manifest, "manifest.json"))
        manifest_hash = mres.manifest_hash
        persisted_ids = mres.manifest["config_ids"]  # persisted order authority
        log(f"[manifest] manifest_hash={manifest_hash}  config_ids={persisted_ids}")

        # ---- assignment (R2 seed) — Ω draw per config -------------------- #
        assignment_table = R2seed.build_assignment_table(manifest_hash, persisted_ids, N)
        write_json(runtime_dir / "assignment.json", {
            "manifest_hash": manifest_hash,
            "N": N,
            "omega_size": R2seed.omega_size(N),
            "configs": assignment_table,
        })
        assign_by_id = {a["config_id"]: a for a in assignment_table}

        # ---- render server ----------------------------------------------- #
        server = None
        if not args.no_render:
            server = LocalCardServer(
                cache_dirs=[API_CACHE],
                overlay_dir=runtime_dir / "cache-overlay",
                allow_upstream=True,
            )
            server.__enter__()
            log(f"[render] card server {server.base_url}")

        policy = R2.RetryPolicy(rate_limit_max_attempts=4, rate_limit_max_total_ms=60000)
        cfg_by_id = {c["config_id"]: c for c in configs}

        # ---- per config -------------------------------------------------- #
        for cid in persisted_ids:
            if args.skip_completed and (batch_dir / "configs" / cid / "config.json").exists():
                log(f"[skip] {cid} — config.json already on disk (resume)")
                continue
            ycfg = cfg_by_id[cid]
            bits = assign_by_id[cid]["bits"]  # bit k: 0 => min->q, 1 => q->min
            if ycfg.get("protocol") == "cli":
                # explicit yaml `harness:` wins; fall back to family inference
                # (opencode carries Chinese-family models, so family can't route it).
                harness = ycfg.get("harness")
                if harness == "opencode":
                    adapter = OpencodeCliAdapter()
                elif harness == "codex" or (harness is None and ycfg.get("family") == "gpt"):
                    # auth.method oauth-login → ChatGPT-plan direct (no key);
                    # api-key → sub2 provider (dev-only).
                    if ycfg.get("auth", {}).get("method") == "oauth-login":
                        adapter = CodexCliAdapter(oauth=True)
                    else:
                        adapter = CodexCliAdapter(api_key=env.get(ycfg["auth"]["credential_ref"], ""))
                elif harness == "grok" or (harness is None and ycfg.get("family") == "grok"):
                    adapter = GrokCliAdapter()
                elif harness == "kiro":
                    adapter = KiroCliAdapter()
                elif harness == "qoder":
                    adapter = QoderCliAdapter()
                else:
                    adapter = ClaudeCliAdapter()
            else:
                api_key = env.get(ycfg["auth"]["credential_ref"], "")
                adapter = Sub2ApiAdapter(api_key=api_key, base_url=base_url,
                                         background=bool(transport.get("background")))
            call_config = {
                "family": ycfg["family"],
                "model_id": ycfg["model_id"],
                "effort": ycfg.get("effort"),
                "effort_param": ycfg.get("effort_param"),
                "provider_model_id": ycfg.get("provider_model_id"),
            }
            positions: list[R2.SlotPosition] = []
            served_models: set[str] = set()
            agg = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "wall_ms": 0}
            m_valid = 0

            for k in range(N):
                order = ["min", "q"] if bits[k] == 0 else ["q", "min"]
                for dvar in order:
                    data_variant = VARIANT_DATA[dvar]
                    log(f"[slot] {cid} block={k} variant={data_variant} calling model ...")
                    start = len(adapter.calls)
                    pos = R2.run_position(
                        adapter,
                        prompt_text[dvar],
                        call_config,
                        variant=data_variant,
                        slot_index=k,
                        block_index=k,
                        policy=policy,
                    )
                    positions.append(pos)
                    pos_calls = adapter.calls[start:]
                    last = pos_calls[-1] if pos_calls else None
                    log(f"[slot]   -> {pos.terminal_state}"
                        + (f" served={last.served_model} tin={last.tokens_in} tout={last.tokens_out}"
                           if last else ""))

                    slot_dir = batch_dir / "configs" / cid / dvar / "slots" / str(k)
                    slot_dir.mkdir(parents=True, exist_ok=True)
                    flags: list[str] = []
                    l1 = None
                    fidelity = None

                    if last is not None and pos.terminal_state in ("valid", "model-failed"):
                        # valid → store the fence-STRIPPED renderable document (the
                        # same bytes slot_pipeline renders + the offline re-render
                        # replays); model-failed → store the raw output for audit.
                        # raw_text may be None on adapters that can't capture it.
                        card_bytes = (
                            last.html if pos.terminal_state == "valid" else last.raw_text
                        )
                        if card_bytes is not None:
                            (slot_dir / "card.html").write_text(card_bytes, encoding="utf-8")
                        # 'preamble' is not a slot-flag enum value; keep only 'fence'.
                        if "fence" in (last.flags or []):
                            flags.append("fence")

                    if pos.terminal_state == "valid":
                        m_valid += 1
                        if last.served_model:
                            served_models.add(last.served_model)
                        if last.tokens_in is not None:
                            agg["prompt_tokens"] += last.tokens_in
                        if last.tokens_out is not None:
                            agg["completion_tokens"] += last.tokens_out
                        if last.tokens_in is not None and last.tokens_out is not None:
                            agg["total_tokens"] += last.tokens_in + last.tokens_out
                        agg["wall_ms"] += last.wall_ms or 0

                        if not args.no_render and server is not None:
                            # ONE shared code path (slot_pipeline): frozen render +
                            # DOM dump + shot.png/webp + full L1 + fidelity verdict +
                            # fidelity-trace.json (task W).
                            slug = f"{cid}--{data_variant}--{k}"
                            card_written = slot_dir / "card.html"
                            art = produce_slot_artifacts(
                                browser=browser, base_url=server.base_url, server=server,
                                slot_dir=slot_dir, card_html_path=card_written,
                                render_params=render_params, render_snapshot=render_snapshot,
                                expected=expected, extractor_version=extractor_version,
                                slot_index=k, slug=slug, retain_png=args.retain_png,
                            )
                            l1 = art.l1
                            fidelity = art.fidelity
                            for f in art.render_flags:
                                if f not in flags:
                                    flags.append(f)
                            render_reports.append({
                                "config_id": cid, "variant": data_variant,
                                **art.render_report,
                            })

                    telemetry = {
                        "prompt_tokens": last.tokens_in if last else None,
                        "completion_tokens": last.tokens_out if last else None,
                        "total_tokens": (
                            (last.tokens_in + last.tokens_out)
                            if last and last.tokens_in is not None and last.tokens_out is not None
                            else None
                        ),
                        "wall_ms": last.wall_ms if last else None,
                        "cost_usd": None,
                        "request_id": None,
                    }
                    meta = {
                        "slot_index": k,
                        "state": pos.terminal_state,
                        "flags": flags,
                        "telemetry": telemetry,
                        "l1": l1,          # full L1 (bytes+structure+visual+palette_top8) or null
                        "fidelity": fidelity,  # terminal fidelity verdict or null
                    }
                    if "fence" in flags:
                        meta["fence"] = True
                    write_json(slot_dir / "meta.json", meta)
                    rel = f"configs/{cid}/{dvar}/slots/{k}/meta.json"
                    validations.append(validate("slot-meta", meta, rel))
                    slot_outcomes.append({
                        "config_id": cid, "variant": data_variant, "slot_index": k,
                        "block_index": k, "state": pos.terminal_state, "flags": flags,
                    })

            # ---- send-log (R2) ------------------------------------------- #
            send_log = R2.build_send_log(
                batch_id=batch_id, config_id=cid, N=N, policy=policy,
                positions=positions, allow_dev_n=True,
            )
            write_json(batch_dir / "configs" / cid / "send-log.json", send_log)
            validations.append(validate("send-log", send_log, f"configs/{cid}/send-log.json"))

            # ---- config.json --------------------------------------------- #
            served = None
            if len(served_models) == 1:
                served = next(iter(served_models))
            elif len(served_models) > 1:
                served = sorted(served_models)[0]
            config_record = {
                "config_id": cid,
                "family": ycfg["family"],
                "model_id": ycfg["model_id"],
                "effort": ycfg.get("effort"),
                "protocol": ycfg["protocol"],
                "billing": ycfg["billing"],
                "auth": {
                    "method": ycfg["auth"]["method"],
                    "credential_ref": ycfg["auth"]["credential_ref"],
                },
                "transport": transport.get("descriptor", "sub2api-dev-gateway"),
                "served_model": served,
                "vendor_sanction_ref": None,
                "telemetry": {
                    "prompt_tokens": agg["prompt_tokens"] or None,
                    "completion_tokens": agg["completion_tokens"] or None,
                    "total_tokens": agg["total_tokens"] or None,
                    "wall_ms": agg["wall_ms"] or None,
                    "cost_usd": None,
                },
                "m": m_valid,
                "N": N,
            }
            write_json(batch_dir / "configs" / cid / "config.json", config_record)
            validations.append(validate("config", config_record, f"configs/{cid}/config.json"))
            config_records.append(config_record)

        if server is not None:
            server.__exit__()

        # ---- index.json (append) ----------------------------------------- #
        # DEV mode writes a DEV-SCOPED index under data/batches-dev/ (gitignored),
        # NOT the public data/index.json — a dev batch must never leave a dangling
        # entry in the public index (its batch dir is gitignored).
        index_path = BATCHES_DEV / "index.json"
        # concurrent dev batches: serialize read-modify-write with an fcntl lock
        import fcntl
        lock_path = BATCHES_DEV / ".index.lock"
        lock_fh = open(lock_path, "w")
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        if index_path.exists():
            index = json.loads(index_path.read_text())
        else:
            index = {"schema_version": "1.0.0", "batches": []}
        index["batches"] = [b for b in index["batches"] if b["id"] != batch_id]
        index["batches"].append({
            "id": batch_id,
            "event": "m1-dev-e2e-smoke",
            "date": FETCHED_AT[:10],
            "manifest_hash": manifest_hash,
            "n_configs": len(persisted_ids),
            "variants": ["P-min", "P-q"],
            "created_at": _now_iso(),
        })
        index["batches"].sort(key=lambda b: b["id"])
        write_json(index_path, index)
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()
        validations.append(validate("index", index, "../index.json (dev-scoped)"))

    finally:
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
        session.close()

    # ---- machine summary + conformance report ---------------------------- #
    summary = {
        "batch_id": batch_id,
        "batch_dir": str(batch_dir),
        "manifest_hash": manifest_hash,
        "snapshot_sha256": snapshot_sha256,
        "env_digest": digest,
        "chromium_version": chromium_version,
        "N": N,
        "config_ids": persisted_ids,
        "slot_outcomes": slot_outcomes,
        "render_reports": render_reports,
        "configs": [
            {"config_id": c["config_id"], "m": c["m"], "N": c["N"],
             "served_model": c["served_model"], "telemetry": c["telemetry"]}
            for c in config_records
        ],
        "validation": validations,
    }
    write_json(runtime_dir / "run-summary.json", summary)
    write_conformance_report(batch_dir, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def write_conformance_report(batch_dir: Path, summary: dict) -> None:
    lines: list[str] = []
    A = lines.append
    A(f"# CONFORMANCE-REPORT — {summary['batch_id']}")
    A("")
    A("DEV batch (M1 end-to-end smoke). Wires R1 (manifest/seed) → R2 (slot engine) "
      "→ R3 (sub2api adapter) → R4 (deterministic render). R5 (L1/fidelity), R6 (L2 "
      "similarity), R7 (L3 stats), R8 (probes) are NOT implemented; artifacts they own "
      "are intentionally absent (listed below).")
    A("")
    A(f"- manifest_hash: `{summary['manifest_hash']}`")
    A(f"- snapshot_sha256: `{summary['snapshot_sha256']}`")
    A(f"- env_digest: `{summary['env_digest']}` (dev, chromium {summary['chromium_version']})")
    A(f"- N = {summary['N']} (DEV relaxation; production schema enum is [3,4,5])")
    A("")
    A("## Schema validation table")
    A("")
    A("| file | schema | valid | notes |")
    A("|---|---|:---:|---|")
    for v in summary["validation"]:
        note = ""
        if not v["valid"]:
            note = "; ".join(f"{e['path']}: {e['message']}" for e in v["errors"])
            note = note.replace("|", "\\|")
            if len(note) > 240:
                note = note[:237] + "..."
        A(f"| `{v['file']}` | {v['schema']} | {'PASS' if v['valid'] else 'FAIL'} | {note} |")
    A("")
    A("## Known / expected deviations")
    A("")
    A("1. **request_side is a placeholder (network-log wiring pending).** Every valid "
      "`meta.json.fidelity.request_side` is `{status: match, violations: []}`. The §3 "
      "请求侧硬校验 needs the per-request network log (endpoint / lat·lon±0.01 / "
      "start·end_date / field sets / timezone=UTC / temperature_unit=celsius), which is "
      "NOT in `dom.json` — it is the harness / §6 white-list's output. The dom-only "
      "fidelity extractor emits the schema-valid placeholder; the real check wires where "
      "the network log lives. FLAGGED (unchanged from the R5 trace.py note).")
    A("2. **L1 `bytes` html/css/js split is a dev approximation.** `l1.bytes` splits by "
      "text inside `<style>`/`<script>`, not the frozen appendix-A definition; "
      "`structure.css_rules` counts `{`. `visual` (colorfulness/brightness/contrast/"
      "whitespace/frame_change) and `palette_top8` now follow the appendix-A formulas "
      "(reusing the v-color / v-palette front-end). frame_change is captured from the "
      "§2.3 non-paused frame-change render (t_v+{700,1300,2100,3400}).")
    A("3. **N=2 dev relaxation.** `manifest.json`, every `config.json`, and every "
      "`send-log.json` carry `N: 2`; all three schemas enum `N` to `[3,4,5]` → "
      "validation error `N: 2 is not one of [3, 4, 5]`. dev-matrix.yaml pre-acknowledges "
      "this: N=2 keeps DEV iterations cheap and dev batches are gitignored / never "
      "published. Production batches use N∈{3,4,5} and pass. (config.json passes on "
      "everything else — it fails ONLY on N.)")
    A("")
    A("## Open semantic question surfaced (needs an owner ruling, non-blocking)")
    A("")
    A("**`config.json.m` with two variants.** Each config runs BOTH variants, N slot "
      "positions each (2N model-reaching slots total; here 2×2=4, all valid). "
      "CONTRACT-NOTES §10 says `config.json.m == count of slots with state=valid` → m=4, "
      "which is what this runner writes (and the schema permits: `m` has `minimum:0`, no "
      "maximum). BUT config.schema.json's prose says `m<=N`, and scheme §5 similarity is "
      "computed PER VARIANT (self = C(m,2) per variant), which needs a PER-VARIANT m — "
      "so `m/N` as a per-variant badge would read m=2,N=2. config.json carries a single "
      "`m`/`N` pair, so the two-variant reconciliation (total-valid vs per-variant, or a "
      "per-variant m_min/m_q split) is scheme-silent. This runner followed the literal "
      "CONTRACT-NOTES §10 rule (total = 4); flag for R6/M2 when similarity eligibility "
      "makes the per-variant count load-bearing.")
    A("")
    A("## Fields / artifacts pending later milestones")
    A("")
    A("| artifact / field | owner | status |")
    A("|---|---|---|")
    A("| `meta.json.fidelity.request_side` | network-log wiring | placeholder (match/[]) |")
    A("| `similarity/summary.json`, `similarity/pairs/` | R6 | separate orchestrator (runner.similarity.runner) |")
    A("| `stats.json` | R7 | not implemented (absent) |")
    A("| `probes.json` (served_model verify, effort-reached-API, network whitelist) | R8 | not implemented (absent) |")
    A("| `config.json.telemetry.cost_usd`, `meta.json.telemetry.cost_usd` | pricing wiring | null (no committed pricing) |")
    A("")
    A("## Artifacts written this batch")
    A("")
    A("- `manifest.json` (JCS byte stream; manifest_hash anchor)")
    A("- `weather-snapshot.json` (JCS byte stream; snapshot_sha256 anchor; canary table)")
    A("- `configs/<id>/config.json`, `configs/<id>/send-log.json`")
    A("- `configs/<id>/<variant>/slots/<k>/{card.html, meta.json, dom.json, shot.png, "
      "shot.webp, thumb.webp, fidelity-trace.json}` (variant ∈ {min,q}, DECISIONS-M0 §4; "
      "shot.png retained only with --retain-png, gitignored under data/batches-dev)")
    A("- `_runtime/` (assignment.json, run-summary.json, cache-overlay/): runtime artifacts, "
      "no schema (out of the D2 eleven-schema contract).")
    A("")
    (batch_dir / "CONFORMANCE-REPORT.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
