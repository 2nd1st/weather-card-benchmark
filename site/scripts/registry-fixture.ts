// ===========================================================================
// scripts/registry-fixture.ts — the DEDUP FIXTURE (v3 §2.2 acceptance). Proves
// loadRegistry() resolves the seeded hard case correctly against the REAL data
// root. Run: `npx tsx scripts/registry-fixture.ts` (from site/).
//
// The hard case: `gpt-5.4-eff-high--cli--codex--dev` physically exists in the
// source batch `2026-07-17--dev-codex-gpt-5.4` (merged_into) AND in
// `2026-07-17--dev-expanded` (the non-merged merge target). The registry must:
//   - resolve the WINNING batch to `2026-07-17--dev-expanded` (non-merged →
//     its assets ship), and
//   - list BOTH readable containers in `batches[]`.
// ===========================================================================

// Keep the run quiet: exercise the real loaders without schema warn spam.
process.env.WCB_VALIDATE = process.env.WCB_VALIDATE ?? "off";

import fs from "node:fs";
import path from "node:path";
import { loadRegistry } from "../lib/registry";
import { dataRoot } from "../lib/paths";

const FIXTURE_CONFIG = "gpt-5.4-eff-high--cli--codex--dev";
const EXPECTED_WINNER = "2026-07-17--dev-expanded";
const EXPECTED_CONTAINERS = [
  "2026-07-17--dev-codex-gpt-5.4",
  "2026-07-17--dev-expanded",
];

const failures: string[] = [];
function assert(cond: boolean, msg: string): void {
  if (!cond) failures.push(msg);
}

const reg = loadRegistry();

// --- basic sanity ---
assert(reg.totals.configs > 0, "registry is empty (totals.configs == 0)");
assert(reg.entries.length === reg.byId.size, "entries/byId length mismatch");

// --- the dedup hard case ---
const entry = reg.byId.get(FIXTURE_CONFIG);
assert(entry !== undefined, `fixture config not found: ${FIXTURE_CONFIG}`);
if (entry) {
  assert(
    entry.batchId === EXPECTED_WINNER,
    `winning batch = ${entry.batchId}, expected ${EXPECTED_WINNER}`,
  );
  for (const c of EXPECTED_CONTAINERS) {
    assert(
      entry.batches.includes(c),
      `batches[] missing container ${c} (got ${JSON.stringify(entry.batches)})`,
    );
  }
  // batches[] must be sorted (byte order).
  const sorted = [...entry.batches].sort();
  assert(
    JSON.stringify(sorted) === JSON.stringify(entry.batches),
    `batches[] not sorted: ${JSON.stringify(entry.batches)}`,
  );
}

// --- every entry's winner is a real container, and its assets are shippable ---
// Winner MUST be non-merged UNLESS the config is an orphan that exists only in
// merged batches (copy-assets ships those non-duplicate configs; a merged
// winner with a non-merged sibling container would be a dedup bug).
const merged = new Set<string>();
try {
  const idx = JSON.parse(fs.readFileSync(path.join(dataRoot(), "index.json"), "utf8"));
  for (const b of idx.batches ?? []) if (b?.merged_into) merged.add(b.id);
} catch {
  /* no index → skip this invariant */
}
let orphanWinners = 0;
for (const e of reg.entries) {
  assert(e.batches.includes(e.batchId), `${e.configId}: winner ${e.batchId} not in batches[]`);
  if (merged.has(e.batchId)) {
    const hasNonMergedContainer = e.batches.some((b) => !merged.has(b));
    assert(
      !hasNonMergedContainer,
      `${e.configId}: winner is merged ${e.batchId} despite a non-merged container (dedup bug)`,
    );
    orphanWinners++;
  }
}

// --- browse order is stable/deterministic ---
const ids = reg.entries.map((e) => e.configId);
assert(new Set(ids).size === ids.length, "duplicate configId in entries");

// --- report ---
if (failures.length > 0) {
  console.error(`[registry-fixture] FAIL — ${failures.length} assertion(s):`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(
  `[registry-fixture] OK — ${reg.totals.configs} configs, ` +
    `${reg.totals.models} models, ${reg.totals.validSlots} valid slots, ` +
    `${reg.totals.batches} batches. ` +
    `${FIXTURE_CONFIG} → ${entry?.batchId} (containers: ${entry?.batches.join(", ")}).`,
);
