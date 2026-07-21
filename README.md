# weather-card-benchmark

**One identical task, every frontier model — how differently do they build it?**

Every model gets the *exact same* prompt and the *exact same* frozen weather
data, and is asked to produce a self-contained HTML "weather card." Each card is
rendered headlessly under identical conditions and screenshotted, then every
pair of results is scored on a set of visual and structural similarity channels.
The result is a map of where today's top models converge and where they diverge
on a single, well-specified, one-shot front-end task — including how the *same*
model shifts across different coding harnesses/CLIs and reasoning-effort levels.

**Live site:** https://weathercard.secondfirst.ai

## The task

Two prompt variants, one frozen data fixture:

- **P-min** — a minimal prompt (just the essentials).
- **P-q** — a qualified prompt (fuller spec).

Both are given the same frozen weather snapshot (so nothing depends on when or
where the benchmark is run). The verbatim prompts live in [`prompts/`](prompts/).

Each model runs the task `N` times per variant; a card must render to
non-trivial, on-spec content to count as a valid slot.

## The comparison

Every configuration is a point on three axes:

- **model** — the frontier model (Claude, GPT, Gemini, Qwen, GLM, Kimi,
  DeepSeek, Grok, Doubao, MiMo, MiniMax, …).
- **harness** — how it was driven: an official API, or a coding CLI/harness
  (Claude Code, Codex, Qoder, opencode, Kiro, grok-cli, …).
- **effort** — the reasoning-effort / thinking level, where the model exposes
  one.

Pairwise similarity across all configurations produces the similarity **matrix**
(and per-pair detail, a gallery, and a side-by-side compare view) on the site.

## Data in this repo

The full measured set is large (190+ configurations × multiple slots × screenshots).
To keep the repository lean, it ships a **flagship subset** — one canonical
configuration per frontier lab — under [`data/batches/`](data/batches/). The
site renders this subset out of the box.

The **full measured set** is browsable live at the site above, and downloadable
as a single pack:

- **Full dataset** — <https://weathercard.secondfirst.ai/downloads/wcb-full-dataset-2026-07-19.tar.gz>
  (~800 MB). Extracts to `2026-07-19--unified/` + `index.json`; point
  `WCB_DATA_ROOT` at the extracted directory to serve the whole set locally.

## Layout

```
weather-card-benchmark/
├── runner/              # Python pipeline: sample → render → similarity
│   ├── adapters/        #   per-vendor API/CLI protocol adapters
│   ├── render/          #   deterministic headless render + screenshot
│   ├── similarity/      #   L1–L3 similarity channels
│   ├── configs/         #   config matrices (production-matrix.yaml + dev)
│   └── tests/           #   pytest suite
├── site/                # Next.js site: gallery / matrix / compare / methodology
├── data/
│   ├── SCHEMA/          #   frozen JSON Schemas + data-layout docs
│   └── batches/         #   flagship data subset (rendered here)
└── prompts/             # the two verbatim task prompts
```

## Quickstart

### Runner (Python 3.11+)

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install requests playwright pytest pyyaml jsonschema rfc8785
.venv/bin/python -m playwright install chromium

cp runner/.env.example runner/.env   # then fill in keys + WCB_API_BASE
cd runner && ../.venv/bin/python -m pytest -q
```

`runner/.env` holds your keys and is gitignored — it must never be committed.
`credential_ref` fields in the config YAMLs are env-var *names*, never secret
values. For reproducible public data, point each arm at its vendor's official
API base URL and official key.

### Site (Node)

```bash
cd site
npm install
npm run dev        # http://localhost:3000
```

The site resolves its data root from `WCB_DATA_ROOT` (defaulting to the
in-repo `data/batches`). Set `NEXT_PUBLIC_SITE_URL` to your own domain when
deploying a fork.

## License

MIT — see [LICENSE](LICENSE).
