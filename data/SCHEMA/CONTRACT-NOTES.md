# CONTRACT-NOTES — rules JSON Schema CANNOT express (runner MUST enforce)

Companion to the eleven JSON Schemas in this directory (M0 five: manifest, config, slot-meta,
similarity-summary, index; M0.5 six: weather-snapshot, probes, similarity-pairs, stats,
fidelity-trace, send-log). JSON Schema validates **shape**; these are
the **byte-exact** rules that make `manifest_hash` reproducible across independent implementations.
Passing a schema is necessary but **not sufficient**. Every rule below is transcribed from
COMPARISON-SCHEME-v12 §1.2 / appendix A verbatim; the scheme is LAW.

> If any line here disagrees with COMPARISON-SCHEME-v12, the scheme wins — report the discrepancy,
> do not silently reconcile.

---

## 1. JCS (RFC 8785) canonical serialization

The manifest is serialized as **RFC 8785 (JCS) canonical JSON**:
- object keys sorted by UTF-16 code-unit order (RFC 8785),
- no insignificant whitespace,
- strings escaped per RFC 8785,
- numbers per RFC 8785 (ECMAScript `Number` formatting) — **but this manifest avoids JCS number
  formatting entirely for coordinates by encoding them as decimal STRINGS**, see §3.

`manifest.json` on disk **MUST** be the JCS byte stream itself (the file bytes == the hashed
bytes), because scheme §1.2 makes the manifest **the sole byte stream** that is hashed
("manifest = 批内冻结清单的唯一字节流"). Thus `index.json.manifest_hash` is re-derivable directly
from the committed file, not merely from a re-canonicalization. `manifest_hash = SHA256(JCS_bytes)`
as **lowercase hex**, written into the lock.

JSON Schema cannot express: key-ordering, whitespace, or "this document must equal its own JCS
canonical form". The runner MUST produce JCS bytes and MUST assert round-trip stability
(re-canonicalizing the file yields identical bytes).

## 2. NFC-first on every string

Every string in the manifest (city names, config_ids, date, decimal strings) is
**Unicode-normalized to NFC before** serialization/hashing. All `uniqueItems` and dedup checks
below run **after** NFC. JSON Schema's `uniqueItems` compares raw JSON values, so it only catches
already-identical bytes; NFC-equal-but-byte-different duplicates MUST be rejected by the runner.

Specifically:
- `config_ids`: reject duplicates **after NFC**. Also the persisted sort key is **NFC UTF-8 byte
  order** (§4).
- `cities`: reject duplicate **(city, date)** pairs **after NFC** (a stricter key than whole-item
  uniqueness — two entries with same city+date but different lat/lon are a rejection, not two rows).

## 3. Canonical decimal-string grammar (non-integers)

All non-integer numeric values in the manifest are written as **canonical decimal strings**, never
JSON numbers. Grammar (scheme §1.2):

- **no exponent** (`1e5` forbidden)
- **no leading `+`**
- **no leading zero** except `0.x` (`05` forbidden; `0.5` allowed)
- **no trailing decimal zero** (`1.50`, `0.0`, `52.520` forbidden)
- **no `-0`** (`-0`, `-0.0` forbidden)

Normalization algorithm: decimalize → strip trailing zeros → minimal-form string.

Reference regex (also embedded as `pattern` in `manifest.schema.json` for lat/lon):
```
^(0|-?(0\.[0-9]*[1-9]|[1-9][0-9]*(\.[0-9]*[1-9])?))$
```
Validated accept: `0 52 52.52 13.405 -13.405 -0.5`. Validated reject: `-0 05 1.50 1. 1e5 +5 .5 00 -0.0 52.520`.

**FLAGGED judgment call — integer-valued coordinates.** The scheme mandates decimal strings for
**non-integers** and is **silent on integer-valued coordinates**. A strict byte reading would let
an integer coordinate serialize as a JSON number, forcing a fragile integer/non-integer type
branch in the manifest. This contract instead requires **all coordinates to be canonical decimal
strings** (an integer coordinate uses the grammar's no-fraction form, e.g. `"52"`), for type
stability and to keep the manifest free of JCS number formatting. **This choice affects the byte
stream and therefore `manifest_hash`** — it must be adopted uniformly by every implementation or
they will not reproduce each other. If the scheme owner intends integer coordinates as JSON
numbers, this is the single line to change (and re-freeze).

## 4. config order = persisted array order (scheme §1.2 v12)

`config_ids` (and every pooled `mask_h`) is sorted **at build time by NFC UTF-8 byte order** and
**persisted**. Thereafter **every** use site — config/block traversal, pooled PRNG sampling map,
mask iteration — takes the persisted array order **verbatim** and is **FORBIDDEN** from re-sorting
by any local "lexicographic" collation. JSON Schema cannot express "consumers must not re-sort".

`mask_h` list hash = `SHA256` of that `config_id` array's **canonical JSON (JCS)**, emitted as
**lowercase hex** — same case discipline as `manifest_hash` (scheme §1.2 states `manifest_hash` is
小写 hex and that `mask_h` is computed "同样"/likewise; the scheme does not spell out mask_h's case,
so this contract fixes lowercase hex for cross-impl reproduction — FLAGGED as a scheme-silent
choice). Missing/failed configs stay in the mask under strict null propagation — never removed post
hoc.

## 5. `manifest_hash` computation

```
nfc_all_strings(manifest)                    # NFC every string
coords -> canonical decimal strings          # §3
config_ids sorted by NFC-UTF8 byte order     # §4, persisted
cities sorted by byte order of each element's canonical JSON (JCS)   # scheme §1.2
bytes = JCS(manifest)                         # RFC 8785
manifest_hash = sha256(bytes).hexdigest()     # lowercase hex
# written into lock and data/index.json; anchors the whole batch (append-only)
```
Generated **before any assignment draw**. Excludes all runtime fields (assignments, seeds,
timestamps). Requires a **clean git working tree** — a dirty tree rejects the batch (no
`source_digest` fallback).

## 6. `seed_assign` formula (scheme §1.2, verbatim)

```
seed_assign(config) =
    uint64_le(
        SHA256(
            b"assign\0"
            ‖ bytes.fromhex(manifest_hash)
            ‖ b"\0"
            ‖ NFC(config_id).encode("utf-8")
            ‖ b"\0"
            ‖ uint8(N)
        )[0:8]
    )
```
- `‖` = byte concatenation; the `\0` separators give every field a boundary (no concatenation
  collision).
- `uint64_le(x)` = little-endian interpretation of the first 8 bytes of the digest.
- Each config uses an **independent** `Generator(PCG64(seed_assign))` and calls
  `integers(0, |Ω_c|)` **exactly once** to pick the assignment rank.
- Traversal order = **config_ids persisted array order (§4) × block index ascending**, frozen to
  disk. Assignment table / order / timestamps are persisted **outside** the manifest (runtime
  fields), so they never enter `manifest_hash`.

### Ω_c encoding (for the assignment rank; scheme §1.2 / appendix A)
- Per base config: N time blocks (index 0..N-1); each block runs one P-min and one P-q.
- `bit 0 = min→q`, `bit 1 = q→min`; assignment = in-block execution order.
- Legal assignment: `|n0 − n1| ≤ 1` (odd N → {⌊N/2⌋, ⌈N/2⌉}).
- `Ω_c` = all legal bit strings `b ∈ {0,1}^N`; **rank** = interpret `b` as an N-bit unsigned integer
  with `b[0]` as MSB, ascending, `0..|Ω_c|−1`. Observed assignment `ω_obs` uses the same encoding.
- On re-randomization, the **outcome sticks to (block, time-position)**; only the min/q labeling is
  re-interpreted per the new bit string.

### Related stats-layer seed (appendix A — noted here for completeness, NOT a manifest field)
```
seed_h = uint64_le(SHA256(b"perm\0" ‖ NFC(hypothesis_id).encode("utf-8"))[0:8]) XOR 20260716
```
Per-hypothesis independent PRNG stream for the pooled permutation test; other random processes use
independent domains (`b"boot\0"` …). Zero stream sharing. `B_perm=10000`, `p=(b+1)/(B_perm+1)`.
Belongs to `stats.json` (not in the M0 five-schema freeze) — listed so the seed-domain discipline is
visible in one place.

## 7. Schema file SHA256 + `schema_version` into the lock (scheme §11)

For each schema file in this directory the lock stores `(sha256(file bytes) lowercase-hex,
schema_version)`. Current `schema_version` = `1.0.0` for all five. A schema edit ⇒ version bump ⇒
new lock entry; the manifest schema in particular must be re-frozen before any batch. This is the
audit mechanism behind "byte-exact cross-implementation reproduction": schema drift is detectable
as a hash change. (See DATA-LAYOUT §4 for the loop.)

## 8. Deterministic number serialization for rehashed files (FLAGGED)

Scheme §8 verify step re-runs metrics/stats and **compares hashes**. The scheme mandates the
canonical decimal-string grammar **only for the manifest**. To make the rehash hold, every file
that enters a verify hash (`scalars.json`, `matrices.json`, and the numeric content flowing into
`summary.json` / `meta.json`) MUST use a **single fixed canonical float serialization**.

Contract requirement (FLAGGED, scheme silent on the exact form for non-manifest files):
- The schemas type these as JSON numbers for readability, but the runner MUST fix ONE canonical
  serialization for all rehashed outputs. **Recommendation: reuse the §3 decimal-string grammar.**
- Displayed temperatures already ARE decimal strings in `meta.json` (`fidelity.max/min.value`) to
  preserve displayed precision for the ROUND_HALF_UP quantize comparison (scheme §3 数值匹配) —
  never bare-`round`; JS side MUST quantize on decimal strings.

## 9. Enum / vocabulary version-locking (schema encodes the set; lock encodes the version)

The schemas hard-code these closed sets; the **version** is what enters the lock:
- slot terminal states (6): valid, model-failed, infra-failed, acceptance-unknown, unreachable,
  rate-limited-exhausted (scheme §1.1 + v12.1). `rate-limited-exhausted` requires
  **deterministic uncharged evidence** (explicit 429/quota/503) — ambiguous cases fall to
  `acceptance-unknown` (conservative, may have billed).
- fidelity field states — the general enum is 4: match, mismatch, ambiguous, not-found (used by
  `date` and `condition`). **Per-field restrictions from scheme §3:**
  - `name`: **only {match, not-found}** — scheme §3 says name has no mismatch state ("无 mismatch
    态") and no ambiguous. Enforced via the 2-state `nameVerdict` `$def`, NOT the shared
    `fieldVerdict`.
  - `condition`: scheme §3 names only WMO-text-match and icon-only→not-found; it never authorizes a
    mismatch/ambiguous terminal but does not prohibit one either. **RULED (DECISIONS-M0 §1): stays on
    the general 4-state `fieldVerdict`** — NOT restricted to {match, not-found}. Fable's rationale:
    `name` is 2-state only because scheme §3 explicitly says name has no mismatch state; `condition`
    carries no such restriction, and a displayed condition contradicting the WMO code (e.g. code=61
    shown as "Sunny") is a real mismatch signal that must be kept. Flag resolved.
- hourly point states (3): match, mismatch, not-shown.
- flags (closed enum, FLAGGED): fence, content-not-ready, unstable-after-freeze,
  non-deterministic-render, icon-only, network-violation. The scheme lists flags with a trailing
  "…"; this contract **freezes** the set and treats any addition as a `schema_version` bump (keeps
  the lock stable). If a needed flag is missing, bump the version — do not smuggle an out-of-enum
  string.
- 17 channels (scheme §4): the 15 formal channels + the 2 permanent diagnostics **c-ncd** and
  **d-tagpath** (displayed, never in greedy pruning / statistics / hypothesis families).
- READY predicates, extractor_version, all vocabularies (WMO condition, error-state, max/min
  labels, hourly-label templates, date locale templates, visible-text selectors) — versioned and
  locked (scheme §11); referenced from `meta.json.fidelity.extractor_version`.

## 10. Cross-file referential invariants (runner-enforced; no single schema sees two files)

- `config.json.config_id` ∈ `manifest.config_ids`, and the directory name matches it.
- `config.json.N` == `manifest.N`. `slot-meta.json.slot_index` ∈ `0..N-1` **for ALL SIX terminal
  states** (RULED DECISIONS-M0 §6). Ruling model: every config × variant has N slot POSITIONS
  k=0..N-1; a slot is a position, its terminal state records what happened there. The four
  model-reaching states (valid, model-failed, infra-failed, acceptance-unknown) FILL the position;
  `unreachable` (provably-not-sent) and `rate-limited-exhausted` (provably-uncharged pre-model
  refusal) are the same positions left **UNFILLED** after retries exhausted. "不占 slot" (scheme §1.1)
  means those retries do NOT consume cost / are not samples (cost bound = model-reaching requests
  ≤ N) — it does NOT mean "no slot record". So `slot_index` is required for every state; L1/fidelity
  are null for the two unfilled (and every non-valid) state. **Attempt-level** retry detail (multiple
  429 / not-sent tries before a position's terminal state) lives in the per-config `send-log.json`
  (DECISIONS-M0 §6), keyed by `(variant, slot_index)`.
- `config.json.m` == count of slots with `state=valid` (scheme §1.1: `m = count(valid)`).
- **`summary.json.cells` composite-key uniqueness (runner-enforced):** each `(config_a, config_b,
  channel)` triple appears **at most once** in `cells`. JSON Schema `uniqueItems` is whole-object
  only, so two contradictory cells for the same triple with different `median` both validate — the
  runner MUST reject them (same JSON-Schema limitation as `cities (city,date)` and `config_ids`;
  scheme §5 defines one aggregate per pair × channel).
- **Calendar-date validity (runner-enforced):** `manifest.cities[].date` and
  `index.batches[].date` pass a `^[0-9]{4}-[0-9]{2}-[0-9]{2}$` pattern, but Draft 2020-12
  `format:"date"` is annotation-only (not asserted), so an impossible date like `2026-13-45`
  validates. Scheme §1.2 requires a real **UTC calendar date**; the runner MUST additionally parse
  and reject invalid calendar dates (or run a format-assertion validator).
- **Coordinate range (runner / request-side):** `manifest.cities[].lat/lon` carry the canonical
  decimal-string grammar but **no numeric range** — `"999.9"` passes the schema. Scheme is silent on
  schema-layer range; coordinates are range-checked request-side (endpoint lat/lon ±0.01, scheme §3
  请求侧硬校验), not at the schema layer. Noted so the absence of `[-90,90]`/`[-180,180]` bounds in
  the schema is intentional, not an oversight.
- `summary.json.configs` == `manifest.config_ids` **in persisted order** (§4); matrix row/col order
  is exactly this.
- `summary.json.variant` ∈ `index.json.batches[].variants`.
- `index.json.batches[].manifest_hash` == the batch's computed `manifest_hash` (§5).
- Similarity pairs are computed **only between valid slots**; `n_eff` / `m_a` / `m_b` follow
  scheme §5 eligibility (n_eff≥4; cross also needs m_a≥2 ∧ m_b≥2).
- **`weather-snapshot.json` ↔ manifest (runner-enforced):** `snapshot_sha256` == `SHA256` of the
  weather-snapshot bytes (which are JCS/NFC/decimal-string canonical — it is a directly-hashed byte
  stream like the manifest); its `locations[]` cover exactly `manifest.cities` with matching
  (city,date,lat,lon) after NFC; each location's daily/hourly array lengths are index-aligned
  (daily ≥1, hourly == 24); `canary` is derivable from `response` but persisted separately so drift is
  detectable. The top-level required `extractor_version` == the locked extractor_version ==
  `meta.json.fidelity.extractor_version` / `fidelity-trace.json.extractor_version` for the batch
  (scheme §1.2 '期望值表随 extractor 版本落盘'), so a re-versioned extractor drifting from a stale
  canary is detectable from the bytes; it is FROZEN and enters `snapshot_sha256`.
- **`similarity/pairs/<a>--<b>--<variant>.json` composite uniqueness + single materialization
  (runner-enforced):** each `(slot_a, slot_b)` appears at most once per file; each UNORDERED
  config-pair is materialized **exactly once per prompt variant** (DECISIONS-M0 §8; similarity is
  computed per variant, so the `variant` token is part of the path — see the resolved layout note in
  "Still FLAGGED") with `config_a` preceding `config_b` in persisted order (cross) or
  `config_a==config_b` (self); the matrix page renders the symmetric expansion. The per-slot-pair S
  values feed `summary.json` and enter the §8 rehash → one canonical float serialization (§8 below).
  **Diagnostic coexistence ("并存", appendix A c-ncd / v-ssim):** whenever a pair's `s["c-ncd"]`
  (resp. `s["v-ssim"]`) is non-null, the corresponding `diagnostics.c_ncd_ncd_sym` (resp.
  `v_ssim_raw`) MUST be present — schema-enforced via the pair-item `allOf` conditional and
  runner-verified. The unclamped `c_ncd_ncd_sym` is intentionally unbounded on both sides (it may be
  < 0 or > 1; that is the whole reason the pre-clamp diagnostic exists).
- **`stats.json` (runner-enforced):** only the 15 formal channels appear (c-ncd / d-tagpath excluded
  from all statistics, scheme §4); `holm_family` + `hypothesis_id`s match the lock and are frozen
  BEFORE any p is emitted; `family_size_k` == `|holm_family|` and stays the Holm denominator even when
  some hypotheses are p=null (null假设 excluded from ranking, k unchanged); `seed_h` matches the §6
  formula; `p=(b+1)/(B_perm+1)` with `B_perm=10000`; strict null propagation → p=null. No inferential
  CLAIM fields (banned by scheme §0/§2 + lint).
- **`fidelity-trace.json` ↔ meta.json (runner-enforced):** present iff `meta.json.state=valid`;
  `extractor_version` and `slot_index` match `meta.json`; the trace's terminal `field_paths[*].state`
  agree with `meta.json.fidelity` (`name` 2-state, others 4-state); occurrence-ID components < 2³¹−1.
  NOTE (scheme-silent, trace-convenience): the occurrence `namespace` enum extends past §3's
  occurrence pools (temperature / max-label / min-label / hourly-label) with `name` / `date` /
  `condition` / `other`. Those extra values are TRACE-CONVENIENCE pools only — §3 matches `name`
  (visible-text substring), `date` (locale-template parse) and `condition` (WMO text match) by
  DIFFERENT mechanisms, NOT the occurrence matcher — so the runner MUST NOT route those non-occurrence
  fields through the §3 occurrence pipeline (leftmost-longest / distance 二元组 / canonical) merely
  because they can appear in the occurrences array.
- **`probes.json` ↔ config (runner-enforced):** one record per config in persisted order;
  `served_model.verdict=mismatch` ⇔ `served_model != requested_model` (a recorded confound, never
  corrected, scheme §10); any `network.violations[]` entry ⇒ the slot's `network-violation` flag;
  `effort_reached_api.verdict=n-a` ⇔ `config.json.effort` is null.
- **`send-log.json` ↔ meta.json (runner-enforced):** one position per `(variant, slot_index)`;
  `positions[].terminal_state` == the matching slot's `meta.json.state`;
  `model_reaching_attempt_index` non-null iff terminal_state ∈ the four model-reaching states;
  `reached_model` false ⇒ outcome ∈ {unreachable, rate-limited}; `charged=false` requires
  deterministic uncharged evidence (429/quota/503 or provably-not-sent) — else `acceptance-unknown`
  with `charged=null` (scheme §1.1 conservative). NOTE the pre-existing layout gap: `slots/<k>/` does
  not encode the P-min/P-q variant, so `(variant, slot_index)` is the true position key — FLAGGED.

---

## M0.5 freeze status (this pass) + remaining follow-ups

**FROZEN this pass (six new schemas + slot-meta corrections):** the artifacts previously deferred are
now schema'd — `weather-snapshot.schema.json`, `probes.schema.json`, `similarity-pairs.schema.json`,
`stats.schema.json`, and the two artifacts split out by Fable's rulings
(`fidelity-trace.schema.json` — DECISIONS-M0 §7; `send-log.schema.json` — DECISIONS-M0 §6). The
schema-file freeze is now **eleven schemas**. `slot-meta.schema.json` was corrected per DECISIONS-M0
§1 (condition stays 4-state), §6 (slot_index required for all six states), §7 (fidelity trace moves to
`fidelity-trace.json`). Resolved flags: the fidelity-trace shape (was OPEN → structure (b), a separate
per-slot artifact, per DECISIONS-M0 §7); condition field-state (was OPEN → 4-state, DECISIONS-M0 §1);
slot occupancy of the two unfilled states (was OPEN → slot_index required, DECISIONS-M0 §6); pair
single-materialization (DECISIONS-M0 §8). All six new schemas meta-validate under Draft 2020-12.

**Still FLAGGED (scheme-silent, non-blocking):**
- **Slot ↔ variant layout gap (pre-existing, PARTIALLY resolved this pass):** the frozen `slots/<k>/`
  directory + `meta.json` do NOT encode the P-min/P-q variant, yet each config × variant has its own N
  positions (scheme §1.1/§1.2). `send-log.json` and `stats.json`/`similarity-pairs` all treat
  `(variant, slot_index)` as the true key. **RESOLVED for `similarity/pairs`:** the pair filename now
  carries the variant token (`<a>--<b>--<variant>.json`, DATA-LAYOUT §1/§2) — the schema's `variant`
  is a single required enum (one file = one variant), and `<a>--<b>` alone would collide the two
  variants onto one path. **STILL OPEN:** the physical directory home of the two variants' SLOTS is
  under-specified in DATA-LAYOUT and needs an owner decision (separate `slots/<variant>/<k>/` vs. a
  `variant` field in `meta.json`).
- **weather-snapshot Open-Meteo wire shape vs. hash discipline:** the frozen artifact encodes numerics
  as canonical decimal strings for `snapshot_sha256` reproducibility; the cache-api re-materializes
  JSON-number Open-Meteo shape when serving. Adopt uniformly or the hash won't reproduce.
- **weather-snapshot `generationtime_ms`:** included as a frozen served value that enters the hash; if
  the owner prefers it excluded (it is upstream-non-deterministic), drop it and re-freeze.
- **stats L1-scalar hypothesis vocabulary:** the exact locked set of L1 scalars in the Holm family is
  referenced as version-locked strings; the concrete list is fixed in the lock, not the schema.
- `env_digest` exact input bytes and `pipeline_commit` hash width (SHA-1 40-hex assumed; SHA-256
  git would need 64-hex) — see the corresponding `manifest.schema.json` field descriptions.
