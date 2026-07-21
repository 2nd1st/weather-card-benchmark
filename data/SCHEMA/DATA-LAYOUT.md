# DATA-LAYOUT — the frozen batch directory contract

Status: **FROZEN DATA CONTRACT (M0 + M0.5)**. Every runner and site component depends on this
layout + the eleven JSON Schemas in this directory: the M0 five (manifest, config, slot-meta,
similarity-summary, index) and the M0.5 six (weather-snapshot, probes, similarity-pairs, stats,
fidelity-trace, send-log). Fable's scheme-silent rulings are in DECISIONS-M0.md (BINDING).
Authoritative sources: COMPARISON-SCHEME-v12
(§1.2 manifest/seed, §3 L1+fidelity, §4 L2 channels, §5 L3, §11 pre-launch lock),
IMPLEMENTATION-BRIEF-v1 §4 (interface contract), PROJECT-DESIGN-v1 §2.2 (directory layout).

> The scheme is LAW. This document does not add, optimize, or simplify any methodology rule.
> Where the scheme is silent on a JSON detail, the choice is stated and **FLAGGED**; the full
> flag list is in `CONTRACT-NOTES.md`.

---

## 1. Append-only directory tree

`data/batches/<batch_id>/` is written once and **NEVER modified** (manifest_hash anchors it;
BRIEF §4 rule 3, DESIGN §2.2). The only file in `data/` that is ever appended/rewritten is
`data/index.json`.

```
weather-card-benchmark/
  data/
    SCHEMA/                              # THIS directory — frozen schemas + notes (not batch data)
      DATA-LAYOUT.md
      manifest.schema.json
      config.schema.json
      slot-meta.schema.json
      similarity-summary.schema.json
      index.schema.json
      weather-snapshot.schema.json       # M0.5 — frozen weather + canary (scheme §1.2)
      probes.schema.json                 # M0.5 — L4 behavioral probes + network whitelist (scheme §6)
      similarity-pairs.schema.json       # M0.5 — slot-pair detail (scheme §5; DECISIONS-M0 §8)
      stats.schema.json                  # M0.5 — L3 stats + randomization test (scheme §5)
      fidelity-trace.schema.json         # M0.5 — per-slot fidelity decision trace (scheme §3; DECISIONS-M0 §7)
      send-log.schema.json               # M0.5 — per-config attempt-level send log (DECISIONS-M0 §6)
      DECISIONS-M0.md                    # Fable's rulings on scheme-silent points (BINDING)
      CONTRACT-NOTES.md
    batches/
      <batch_id>/                        # e.g. 2026-08-01--gpt-6-launch  (date + event slug)
        manifest.json                    # JCS-canonical frozen manifest (schema: manifest.schema.json)
        weather-snapshot.json            # frozen weather data + canary expected-value table (weather-snapshot.schema.json)
        probes.json                      # L4 behavioral probe results (probes.schema.json, scheme §6)
        stats.json                       # L3 descriptive stats + randomization test (stats.schema.json, scheme §5)
        similarity/
          summary--<variant>.json        # config x config x channel L3 aggregates, ONE FILE PER VARIANT (similarity-summary.schema.json; see M2c note)
          pairs/                         # slot-pair detail, lazy-loaded by /pair (one file per pair×variant-or-shard)
            <a>--<b>--<variant>.json     # per unordered config-pair × prompt variant, materialized once per variant (similarity-pairs.schema.json)
        configs/
          <config_id>/                   # config_id from manifest.config_ids (persisted NFC-byte order)
            config.json                  # per-config record (config.schema.json)
            send-log.json                # per-config attempt-level send log (send-log.schema.json; DECISIONS-M0 §6)
            slots/
              <k>/                        # slot index 0..N-1 — a slot POSITION (all 6 terminal states carry slot_index)
                card.html                # raw model output, one byte unchanged (valid/model-failed only)
                dom.json                 # OFF-GIT — frozen serialized DOM at render freeze; fidelity + L1-structure input (see M2c note)
                meta.json                # slot terminal state + L1 + fidelity TERMINAL verdict + telemetry + flags (slot-meta.schema.json)
                fidelity-trace.json      # per-slot fidelity decision TRACE, valid slots only (fidelity-trace.schema.json; DECISIONS-M0 §7)
                shot.png                 # OFF-GIT — frozen main screenshot ORIGINAL, the exact measured pixels (webp is the lossy in-repo derivative)
                shot.webp                # frozen main screenshot (q~85), the measured pixels
                thumb.webp               # ~480px thumbnail for gallery tiles
    weather-db/                          # cache-api accumulative weather store (DESIGN §4.3), append-only, batch-independent
    index.json                           # batch index — the ONLY appended/rewritten file (index.schema.json)
    lock/                                # frozen lock artifacts (see §4); pre-launch, precedes any real batch
```

Notes:
- **PNG measurement originals + `dom.json`** (golden evidence + fidelity input) live on the measurement host
  local disk + backup, **not in git** (DESIGN §2.1). Only the webp derivatives ship in the repo.
  `shot.png` is the exact measured pixels the similarity/L1-visual algorithms read; `dom.json` is the
  frozen serialized DOM the fidelity extractor and L1-structure read. Both are genuinely produced
  per valid slot by the render pipeline (`runner.slot_pipeline` / `runner.rerender_offline`) — they
  are off-git for size, not absent. This layout marks them **OFF-GIT** rather than omitting them.
- `similarity/pairs/` sharding (one file per pair vs. grouped) is a runner choice; the `/pair` page
  loads it lazily (DESIGN §5). Pair detail is now schema'd (`similarity-pairs.schema.json`); each
  UNORDERED config-pair is materialized **exactly once** (DECISIONS-M0 §8).
- **M2c (2026-07-17) — `summary--<variant>.json` naming ADOPTED (was FLAGGED).** The L2 similarity
  summary is written **one file per variant** with a `--<variant>` suffix (`summary--P-min.json`,
  `summary--P-q.json`), NOT a singular `summary.json`. Driver: `similarity-summary.schema.json`
  carries a single required `variant` field and DECISIONS-M0 §4 keys the summary by variant, so a
  singular file cannot hold both variants. The pairs files already carried the `--<variant>` token;
  the summary now matches. **`fidelity-trace.json` and `send-log.json` are now REAL produced
  artifacts** (R5 fidelity extractor + R2/R3 send log), no longer pending — validated schema-valid
  end-to-end on the M1 dev batch (CONFORMANCE-REPORT: 8/8 fidelity-trace PASS, N=2 the only residual
  deviation).
- **M0.5 freeze (this pass):** the six remaining artifacts named in BRIEF §4 now have schema files —
  `weather-snapshot.schema.json`, `probes.schema.json`, `similarity-pairs.schema.json`,
  `stats.schema.json`, plus the two artifacts split out by Fable's rulings
  (`fidelity-trace.schema.json` — DECISIONS-M0 §7; `send-log.schema.json` — DECISIONS-M0 §6). The
  schema-file freeze is now **eleven schemas** (five M0 + six M0.5). `snapshot_sha256` in the
  manifest anchors the `weather-snapshot.json` bytes; that artifact follows the same JCS/NFC/
  decimal-string discipline as the manifest (it is a directly-hashed byte stream — CONTRACT-NOTES).

---

## 2. Artifact-by-artifact purpose

### `manifest.json` — the batch's frozen identity (scheme §1.2)
The **sole byte stream** from which `manifest_hash` is computed. Generated **before any
assignment/randomization is drawn**, and excludes ALL runtime fields (assignment results, seeds,
timestamps). Fields (schema-enforced shape): `prompt_min_sha256`, `prompt_q_sha256`,
`snapshot_sha256`, `cities[{city,date,lat,lon}]`, `config_ids`, `N`, `env_digest`,
`pipeline_commit`. The byte-exact serialization (JCS/NFC/decimal-string) is runner-enforced —
see CONTRACT-NOTES. A dirty git working tree **rejects the whole batch** (no `source_digest`
fallback).

### `weather-snapshot.json` — frozen upstream weather + canary (weather-snapshot.schema.json)
The exact Open-Meteo-compatible responses the cache-api served during the batch, one per
(city,date) in `manifest.cities`, plus the canary expected-value table (e.g. max 27.4° / min 13.9° /
code 61 + hourly `temperature_2m` ×24) versioned with the extractor. `snapshot_sha256` in the
manifest is `SHA256` of this artifact's bytes, so — like the manifest — it is a **directly-hashed
byte stream** and follows the SAME JCS/NFC/decimal-string discipline (every non-integer numeric is a
canonical decimal string; `fetched_at` is the frozen `Date` value and DOES enter the hash). Feeds the
cache-api store (`weather-db/`, first-writer-wins). FLAG: Open-Meteo wire format uses JSON numbers —
the frozen artifact uses decimal strings for hash reproducibility, the cache-api re-materializes the
number wire shape when serving (CONTRACT-NOTES).

### `configs/<config_id>/config.json` — per-config identity + provenance (BRIEF §4)
`family`, `model_id`, `effort` (per-vendor opaque string, nullable), `protocol` (api|agent|cli),
`billing` (metered|plan), `auth` (method + credential **reference**, never a secret), `transport`,
`served_model` (verified; mismatch is a recorded confound, not corrected), `vendor_sanction_ref`
(required iff plan), `telemetry` summary (tokens/wall/cost), `m` = count(valid), `N`. Config
**order authority** is the manifest's persisted `config_ids` array — never re-sort at use sites.

### `configs/<config_id>/send-log.json` — per-config attempt-level send log (send-log.schema.json)
The attempt history behind each slot POSITION (DECISIONS-M0 §6): every send attempt with its
`outcome`, whether it `reached_model` (the cost/sample boundary), `charged` evidence, `reason`
(unreachable / rate-limited / 429 / quota / 503 / …), `backoff_ms`, and `request_id`. One entry per
`(variant, slot_index)` position, keyed explicitly because the `slots/<k>/` directory does not encode
the P-min/P-q variant (FLAG — see below). This is the detail behind both successful slot fills and the
`rate-limited-exhausted` / `unreachable` **unfilled** positions; `meta.json` keeps only the terminal
state. Observational timestamps are excluded from verify hashes (scheme §0).

### `configs/<config_id>/slots/<k>/card.html` — the model output, verbatim
Raw HTML, one byte unchanged (published as-is, DESIGN §8). If it arrived code-fence-wrapped and
stripped to a valid document, the slot stays `valid` and carries the `fence` flag — the stored
`card.html` is the model's literal bytes.

### `configs/<config_id>/slots/<k>/meta.json` — the slot's full record (scheme §1.1, §3)
Slot **terminal state** (six-state enum, `slot_index` REQUIRED for all six — DECISIONS-M0 §6), and —
**only when `state=valid`** — the L1 scalars (§3 + appendix A), the data-fidelity **terminal**
verdict (per-field field-state enum `{match,mismatch,ambiguous,not-found}` — except `name`, which is
**2-state** `{match,not-found}` per scheme §3 "无 mismatch 态"; hourly point-wise
`{match,mismatch,not-shown}`), telemetry, and the version-locked flag set. For non-valid states `l1`
and `fidelity` are `null` (schema-enforced conditional).

- **RULED (DECISIONS-M0 §7):** the full binding trace scheme §3/§8 require ("候选、显著度、绑定决策、
  判定路径全部落盘") lives in the **separate** per-slot `fidelity-trace.json` (fidelity-trace.schema.json),
  NOT inline in `meta.json`. `meta.json.fidelity` keeps the terminal verdict + scalar `reason` only,
  so the card page stays light; the trace is deep-audit data. Frozen this pass.
- **RULED (DECISIONS-M0 §6):** all six terminal states are outcomes of a slot POSITION `k`, so
  `slot_index` is REQUIRED for every one (reverting the earlier scoping). `rate-limited-exhausted`
  and `unreachable` are the same positions left **unfilled** (retries never reached the model);
  "不占 slot" means those retries do not consume cost / are not samples (cost bound = model-reaching
  requests ≤ N), NOT "no slot record". Per-attempt retry detail lives in `send-log.json`.

### `configs/<config_id>/slots/<k>/fidelity-trace.json` — per-slot fidelity decision trace
Valid slots only. The load-bearing "全部落盘" artifact scheme §3/§8 mandate, split out of `meta.json`
per DECISIONS-M0 §7 (fidelity-trace.schema.json): the candidate **occurrence** set (ID = (node
preorder, start, end) + `pattern_id`, per-namespace, same-span merge + category summary + label
voiding), the leftmost-longest arbitration log, the salience set E (`f_max` + per-candidate
`(f_max−f)/f_max`), the max/min binding branch + distance **二元组** (DOM-edge count via LCA,
|text-coordinate diff|) + assignment + canonical publication, the hourly pre-triage / label-parse /
max-cardinality injective binding + fixed-length signature, and the decision path to every field
verdict. Must be frozen before any fidelity verdict is published (LAW).

### `configs/<config_id>/slots/<k>/{shot.webp,thumb.webp}` — measured pixels
`shot.webp` is the frozen main screenshot (§2.2 freeze channel) — **the exact pixels the
similarity algorithms measure** (DESIGN §4.1 "展示即证据"). `thumb.webp` is the gallery tile.

### `similarity/summary--<variant>.json` — the matrix data source (scheme §5, DESIGN §5)
**One file per variant** (`summary--P-min.json`, `summary--P-q.json`; M2c adoption — see Notes).
config × config × channel cells with `median`, `iqr` (p25/p75),
`n_eff`, `m_a`, `m_b`. Ineligible cells (n_eff<4, or cross with m<2 either side, or all-null)
carry `null` aggregates → the matrix renders a gray "insufficient data" cell. All 17 channels
(15 formal + 2 permanent diagnostics c-ncd / d-tagpath).

### `similarity/pairs/<a>--<b>--<variant>.json` — slot-pair detail (similarity-pairs.schema.json)
Lazy-loaded by `/pair`. Per UNORDERED config-pair **× prompt variant** (each variant materialized
once — DECISIONS-M0 §8; similarity is computed per variant, so the variant token is REQUIRED in the
filename — the schema's `variant` is a single required enum, one file = one variant, and `<a>--<b>`
alone would collide the two variants onto one path), the per-channel S across all 17 channels for each
valid slot-pair (self = C(m,2); cross = A×B). This is the run-level raw material `summary.json`
aggregates, so it enters the §8 verify rehash → runner fixes one canonical float serialization
(CONTRACT-NOTES §8). Per-pair diagnostics carry the unclamped c-ncd NCD_sym and raw v-ssim and MUST
coexist whenever the corresponding channel S is non-null (appendix A "并存"; schema-enforced via the
pair-item `allOf` conditional).

### `probes.json` — L4 behavioral probes (probes.schema.json, scheme §6)
Per config: contract-IN probes (pass/indeterminate/fail — error-state + parameter-correctness),
network-whitelist violations (blocked), contract-OUT neutral observations (observed/not-observed/n-a),
plus the **effort-parameter-reached-API** probe (MODEL-MATRIX §3 warning) and **served_model**
verification. Observational — excluded from every verify hash (scheme §0).

### `similarity/` sits beside `stats.json` — L3 statistics (stats.schema.json, scheme §5)
Descriptive summaries over the self/cross pair sets (mean/median/IQR/n_eff, self-consistency,
separation, distinctiveness) + LORO sensitivity, and the SINGLE test = the P-min/P-q randomization
test (per-config exploratory with granularity 1/|Ω_c|; pooled main with B_perm=10000,
p=(b+1)/(B_perm+1), per-hypothesis `seed_h` streams, strict null→p=null, Holm excluding null假设 with
full family-size k denominator). Descriptive-only — no inferential CLAIM fields. Only the 15 formal
channels (diagnostics excluded). Enters the §8 verify rehash. Physical home is the batch root next to
`similarity/` (pipeline §8 `stats → stats.json`).

### `index.json` — the batch index (DESIGN §2.2)
The **only** appended/rewritten file in `data/`. One entry per batch: `id`, `event`, `date`,
`manifest_hash`, `n_configs`, `variants`, `created_at`. Presentation order is by id (date-prefixed),
never a ranking.

---

## 3. Where numbers live and how they are typed

| Artifact | Numeric values | Representation |
|---|---|---|
| `manifest.json` | coordinates | **canonical decimal STRINGS** (scheme §1.2, byte-exact). `N` is a JSON integer (bounded enum). |
| `weather-snapshot.json` | temperatures / coords / elevation / gen-time | **canonical decimal STRINGS** (directly-hashed via `snapshot_sha256`, same discipline as manifest); `weather_code` / `utc_offset_seconds` integers |
| `config.json` | tokens / wall_ms | JSON integers; `cost_usd` decimal string |
| `meta.json` (L1) | colorfulness, brightness, S-values… | JSON numbers (see caveat below) + decimal strings for displayed temperatures |
| `summary.json` / `similarity/pairs` / `stats.json` | median / IQR / S / T / p | JSON numbers (rehash caveat below) |
| `fidelity-trace.json` | font sizes / salience ratios | JSON numbers (rehash caveat); displayed temperatures = decimal strings; IDs/distances integers |
| `send-log.json` | attempt indices / backoff_ms / status | JSON integers; timestamps observational (excluded from hashes) |

**Determinism caveat (FLAGGED):** the scheme mandates canonical decimal strings only for the
manifest. Any file that enters a **verify rehash** (`scalars.json`, `matrices.json`, and by
extension the numeric content that flows into `summary.json` / `meta.json`) must serialize
floats **deterministically** for the §8 "metrics/stats 重跑 hash" to hold. This contract keeps
those as JSON numbers in the schema for readability but **requires** the runner to fix a single
canonical float serialization for all rehashed outputs (recommendation: reuse the manifest
decimal-string grammar). See CONTRACT-NOTES § "Deterministic number serialization".

---

## 4. How lock / schema-hash works (scheme §11)

Before **any** real batch runs, the pre-launch lock (`data/lock/…`) freezes:

1. Channel list + full-order greedy-retention decision (after the 42-card dev-set redundancy audit).
2. golden fixtures (expected values written independently).
3. Holm family + hypothesis IDs.
4. `manifest_hash` of the (dev/first) batch where applicable.
5. **The SHA256 of every schema file in this directory + its `schema_version`.** This is the
   mechanism that makes "byte-exact cross-implementation reproduction" auditable: a schema change
   is a hash change is a lock change. (scheme §11 explicitly adds "manifest schema file SHA256 +
   schema_version" and "each pooled hypothesis mask_h (config_id list + hash)".)
6. Each pooled hypothesis `mask_h` = the frozen complete `config_id` list (build-time NFC UTF-8
   byte order + list hash; missing/failed configs stay in the mask under strict null propagation).
7. All vocabularies (WMO condition, error-state, max/min labels, hourly-label templates, date
   locale templates, visible-text selectors), the READY predicates, and `extractor_version`.

**Schema-hash procedure (this directory):**

```
for f in manifest.schema.json config.schema.json slot-meta.schema.json \
         similarity-summary.schema.json index.schema.json \
         weather-snapshot.schema.json probes.schema.json similarity-pairs.schema.json \
         stats.schema.json fidelity-trace.schema.json send-log.schema.json:
    lock[f] = { "sha256": sha256(bytes(f)).hexdigest(),   # lowercase hex, raw file bytes
                "schema_version": <version from the file's $comment/schema_version> }
```

All **eleven** schemas (five M0 + six M0.5) declare `schema_version` (currently `1.0.0`, in the
file's `$comment`; `index.json` also carries it as a data field). The lock stores
`(file SHA256, schema_version)` pairs. Runner and site both validate every artifact against the
**locked** schema bytes; a schema edit requires a version bump **and** a new lock entry, and (for the
manifest / weather-snapshot) re-freezing before any batch. NOTE: `slot-meta.schema.json` was edited
this pass (DECISIONS-M0 §1/§6/§7 corrections) — if it was previously locked at 1.0.0 it needs a
version bump + re-lock; the corrections are descriptive/constraint-tightening within the frozen shape.

**Data immutability chain:** `manifest.json` bytes → `manifest_hash` (in lock + `index.json`) →
every artifact under `<batch_id>/` is anchored to that hash. `card.html` / `shot.webp` /
`meta.json` are never edited after write; a correction is a **new batch**, never an in-place edit.
