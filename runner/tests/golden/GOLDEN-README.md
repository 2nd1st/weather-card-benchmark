# Golden fixtures — cross-implementation reproduction anchors (Task G, scheme §11)

These are the **regression net** for any reimplementation of the runner. Each
fixture pins a fixed input and the byte/int-exact output the *current verified*
implementation produces; the paired `test_golden_*.py` re-runs the live code and
asserts it still reproduces the frozen value. A drift in canonicalization, a
hash, a similarity formula, or the fidelity decision path trips the matching
golden immediately.

They correspond to scheme §11 上线前验证清单 item 2 ("golden fixtures（期望独立写定）
入 CI") and feed the lock (§11 item 1: manifest_hash, channel list, extractor_version,
env).

## Layout

```
runner/tests/golden/
  GOLDEN-README.md                 ← this file
  test_golden_manifest.py          ← (a) manifest / hash / seed_assign
  test_golden_fidelity.py          ← (b) fidelity trace + meta
  test_golden_channels.py          ← (c) 17 L2 channels: pair value + degenerate null
  test_golden_render.py            ← (d) render pixel-hash (ENV-TAGGED)
  fixtures/
    manifest/
      manifest.json                fixed canonical manifest (N=5, 2 config ids)
      manifest.jcs.bin             its exact RFC 8785 (JCS) byte stream
      expected.json                manifest_hash, seed_assign{cid}, assignment_table
    fidelity/
      <slug>.dom.json              exact dom-dump-v1 input (2 real devset cards)
      <slug>.expected.json         {"trace": …, "meta": …} full extractor output
      index.json                   extractor_version + slug list
    channels/
      channels.json                per-channel pair refs + exact S + degenerate kind
    render/
      card.html                    fixed deterministic card
      expected.json                main_pixel_hash + captured env descriptor
```

## (a) Manifest golden — §1.2

`manifest.json` is a fixed canonical manifest with **two** config ids and `N=5`.
`manifest.jcs.bin` is its exact RFC 8785 (JCS) canonical byte stream; the
`manifest_hash` in `expected.json` is `sha256(that stream)` in lowercase hex.
The test rebuilds the manifest from the fixture's own field values (via
`build_manifest`) and asserts the JCS bytes, the hash, the NFC-UTF-8 persisted
`config_ids` order, and the **per-config `seed_assign`** + Ω_c `rank` /
`omega_value` all reproduce exactly. `seed_assign` uses the §1.2 domain-separated
derivation `uint64_le(SHA256(b"assign\0" ‖ bytes.fromhex(manifest_hash) ‖ b"\0" ‖
NFC(config_id) ‖ b"\0" ‖ uint8(N)))[0:8]`. The assignment table is a **runtime**
artifact and is deliberately kept *outside* the manifest (asserted).

This extends the M1 manifest/seed goldens in `runner/tests/test_manifest_seed.py`
(toy N=3, one hand-built byte layout) to a realistic two-config N=5 anchor.

## (b) Fidelity golden — §3

Two real devset cards' `dom.json` are shipped verbatim next to the **full**
extractor output (`trace` + `meta`) the current R5 extractor emits against the
frozen `DEVSET_EXPECTED` snapshot (Berlin 2026-07-16). The test re-runs `extract`
and asserts byte-exact equality of the entire trace and meta (canonical-JSON
compare) plus schema validity against `fidelity-trace.schema.json` and the
`slot-meta` fidelity subschema. `EXTRACTOR_VERSION` is pinned in `index.json`;
bumping it (any change to expected values, patterns, or vocab per §11) requires
regenerating these fixtures.

The two chosen cards exercise distinct branches (one clean max/min `match`, one
`ambiguous` binding; both `ambiguous` hourly), so the max/min binding and hourly
pre-triage paths are both anchored.

## (c) Channel goldens — §4

`channels.json` has one entry per **each of the 17 channels** (15 formal + the 2
permanent diagnostics `c-ncd` / `d-tagpath`). Each entry pins:

* `card_a` / `card_b` — a real devset card pair (input refs), and `s` — the
  **exact** S value written at full float precision (Python `repr` round-trips
  through the JSON). The test rebuilds the artifacts from those cards through the
  production `_build_artifacts` resolver and asserts `compute()["s"]` is
  bit-identical (`float(s) == fixture` and `repr` match). Two values were
  cross-checked against the independently-computed devset matrices (`v-color`,
  `c-shingle`) at generation time.
* `degenerate` — a §4 zero-vector/empty/absent input kind that must yield
  `S=null`:
  * `empty-html`  (all `c-*`): `card_html=""` → empty shingle/feature set.
  * `no-shot`     (v-phash, v-dhash, v-color, v-palette, v-ssim): absent
    screenshot → the only null these produce (an all-black image is a valid
    non-zero histogram/hash for them, per each module's flag).
  * `black-image` (v-layout, v-edge): all-black 1280×800 → genuine §4 **zero
    vector** (flat grayscale / no edge energy).
  * `empty-dom`   (all `d-*`): a dom with zero nodes → empty bag / all-zero
    occupancy grid.

Input source is `data/batches-dev/devset-42/cards/`; the golden references cards
by slug rather than copying the ~0.5 MB shot.png/dom.json per pair.

## (d) Render golden — §2.2 (ENV-TAGGED, read this)

`card.html` is a fixed, animation-free card. It fires exactly one same-origin
`/api/om/forecast` GET whose query resolves **200 from the frozen trial
api-cache** (so the §2.1 ready state-machine observes a finished target request),
drains the body, and renders four static text lines. No animation, no
data-driven layout → deterministic pixels.

`expected.json` stores `main_pixel_hash` **together with the `env_digest` it was
captured under** and the human-readable env descriptor (chromium build,
playwright version, OS/arch). The pixel hash is an environment-dependent artifact:
font rasterization, the Chromium build, image libraries, and platform all feed
it. Therefore:

* `test_render_golden_pixel_hash` asserts the **env-independent** invariants on
  every platform — target request finished, `READY_NORMAL` reached, intra-render
  stability, no `content-not-ready` / `unstable-after-freeze` flag — and then,
  **only when the live `env_digest` equals the golden's**, asserts the strict
  pixel-hash equality. On a different env it `pytest.skip`s the strict check with
  a message naming both env_digests.
* `test_render_golden_is_deterministic` asserts double-render pixel equality
  (§2.4) — this holds env-independently.

**Captured env (this golden was frozen under):**

| field | value |
|---|---|
| chromium | `149.0.7827.55` |
| env_digest | `6a2e650101c58961d763c8f38cca138e8f2bbad3d5dcc89afc87ffe41bff4cde` |
| main_pixel_hash | `03385915f86bbc8bb6ff546f0bf7d9769d3569a2d3b862ae7ad4650655136041` |

`env_digest` here is the **provisional** DEV descriptor from
`runner/render/env_digest.py` (appendix-A follow-up pending, DECISIONS-M0);
when the final env byte layout is pinned, regenerate this fixture. To
re-freeze after an intentional env change:
`PYTHONPATH=. .venv/bin/python scratchpad/gen_render.py` (regenerator), then
review the new hash before committing.

## Regenerating (only on an intentional, reviewed change)

The generators live in `scratchpad/` and were used to write these fixtures:

```
PYTHONPATH=. .venv/bin/python scratchpad/gen_golden.py    # manifest + fidelity + channels
PYTHONPATH=. .venv/bin/python scratchpad/gen_render.py    # render (needs a browser)
```

Regenerating is a lock-affecting act: a changed manifest_hash / channel value /
extractor trace / pixel hash means the frozen contract moved. Treat a golden
failure as a signal to investigate, not to blindly re-freeze.

## Running

```
.venv/bin/python -m pytest runner/tests/golden -q          # goldens only
.venv/bin/python -m pytest runner -q                       # whole suite
```

The render goldens need Playwright's Chromium; the rest are pure-Python and fast.
