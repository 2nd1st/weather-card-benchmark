# weather-card-benchmark — site

Open-source benchmark site: one weather-card prompt, every model, rendered as a
gallery of glowing exhibits. Deterministic similarity data + an arena-style
community preference layer + coverage transparency + community-contributed runs.
Built for social-media sharing. Dark-instrument ("仪表台 Dark") visual language.

Implementation contract: the top-level project README.

## Stack

- **Next.js 15** (App Router, React 19), `output: "standalone"` — SSR, not static
  export. `next-intl` locale routing (`en` default unprefixed, `/zh/*`).
- **SQLite** via `better-sqlite3` (WAL) — arena pairs/votes/shares/contribute jobs.
- **sharp** — build/server-time OG image composition (no third-party service).
- **js-yaml** — coverage plan snapshot parsing.
- Native modules (`better-sqlite3`, `sharp`) are in `serverExternalPackages`;
  rebuild them on the deploy host.

## Architecture

```
app/
  [locale]/            locale-scoped routes (next-intl as-needed prefix)
    page.tsx           /  landing + arena gate overlay          (force-dynamic)
    arena/             /arena hub + /arena/m/[id] shared matchup (force-dynamic)
    gallery/           /gallery unified filterable grid          (revalidate 300)
    compare/           /compare 2–4-up live iframes              (revalidate 300)
    card/[id]/         /card exhibit (id = configId)             (revalidate 300)
    model/[modelId]/   /model effort×arm strip                   (revalidate 300)
    matrix/            /matrix similarity heatmap + session sel.  (revalidate 300)
    progress/          /progress coverage board                  (force-dynamic)
    contribute/        /contribute tray + estimate/submit        (force-dynamic)
    findings/          /findings observation cards               (revalidate 300)
    methodology/       /methodology + patch policy + voting math (revalidate 300)
  api/                 arena / vote / stats / export / coverage / contribute / og
  components/          shared dark-instrument UI (TopNav, CardFrame, GateOverlay, …)
lib/
  registry.ts          unified deduped config registry (globalThis singleton)
  coverage.ts          plan (matrix snapshot + harness-plan) × landed → cells
  patch.ts             honest render-fix patch layer (original always served)
  channel.ts label.ts  grade (qualified/dev/community) + arm derivation
  live/                srcdoc builder + offline fixture stubFetch
  arena.ts db.ts       blind-pair sampling, BT-MM aggregation, SQLite
  ratelimit.ts singleton.ts contribute.ts github.ts metadata.ts neutral.ts …
scripts/
  copy-assets.mjs      mirror card html/thumbs/patch → public/b (skips merged dups)
  build-og-mosaic.mjs  deterministic 1200×630 OG fallback → public/og/mosaic.png
  gates/               schema + deploy gates (run in prebuild)
  registry-fixture.ts  dedup assertion   arena-selftest.mjs  vote-loop e2e
messages/{en,zh}/      per-namespace i18n strings
data/  (repo root)     batch data root + seed files (coverage/cost/harness/findings)
```

## Rendering & data policy

- Every route declares its policy (spec §1): request-time `force-dynamic` for
  vote/coverage/landing surfaces, `revalidate = 300` for the browse surfaces.
  `loadRegistry()`'s cache keys on `index.json` mtime, so newly landed batches
  appear without a restart.
- **Partial data never crashes a page** — every reader is defensive; missing
  batches / slots / seed files degrade to EmptyStates.
- **Model output is untrusted** — `card.html` renders only inside
  `<iframe sandbox="allow-scripts" srcdoc>`.

## Develop

```bash
npm run dev            # predev copies assets → public/b; needs cache-api running
npm run cache-api      # local /api/om/* fixture service on :8787 (separate shell)
npm run typecheck      # tsc --noEmit
npm run gates          # schema + deploy gates
npm run fixture:registry   # dedup assertion (npx tsx)
node scripts/arena-selftest.mjs   # arena vote-loop integrity e2e
```

`npm run build` runs the prebuild (copy-assets + OG mosaic + gates) then
`next build`. Deploy the Node standalone server behind a reverse proxy.

## i18n

`en` first-authored, `zh` natural (key screenshot surfaces natively written, not
translated). Instrument chips stay English (model ids, effort tokens, arm names,
grade text, channel names). All UI strings flow through `messages/{en,zh}/*.json`.
