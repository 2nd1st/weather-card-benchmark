// ===========================================================================
// lib/registry.ts — the UNIFIED REGISTRY (v3 §2.2). The batch-centric model is
// dead: the site shows ONE filterable set of configs. This module scans every
// batch in the data root, DEDUPES configs that appear in more than one batch,
// and exposes a single ordered registry the whole site reads.
//
// Provenance is preserved: each entry records its WINNING batch (`batchId`) and
// every readable batch that contains it (`batches[]`).
//
// DEDUP DOCTRINE (v3 §2.2 + the copy-assets constraint):
//   - `batches[]` lists EVERY readable batch containing the configId (merged
//     and non-merged alike) — full provenance.
//   - The WINNING batch is chosen among NON-MERGED containers only. A batch
//     with `merged_into` set has its identity-bearing assets skipped by
//     copy-assets (their duplicate HTML must not ship — §10 A1 note), so a
//     merged batch can never be the asset source; if a configId is somehow
//     only in merged batches, we fall back to those so the entry still exists.
//   - Winner comparator among candidates: more valid slots first; tie → newer
//     created_at; final tie → higher batch id (fully deterministic).
//   The seeded hard case: `gpt-5.4-eff-high--cli--codex--dev` lives in the
//   source `2026-07-17--dev-codex-gpt-5.4` (merged) AND `2026-07-17--dev-
//   expanded` (non-merged) → winner = dev-expanded, batches[] = both.
//
// ORDERING DOCTRINE: entries are in BROWSE order (family → modelId → effort →
// arm) per §2.2. See lib/neutral.ts for the three-order reconciliation.
//
// Server/build only (fs). Cached on globalThis, keyed on index.json mtimeMs, so
// a newly landed batch appears without a process restart (§1 cache policy).
// ===========================================================================

import fs from "node:fs";
import crypto from "node:crypto";

import * as P from "./paths";
import { compareSlug } from "./neutral";
import { VARIANTS, variantToDir } from "./variant";
import type { Variant, VariantDir } from "./variant";
import { armOf } from "./label";
import { channelGradeOf, armGroupOf } from "./channel";
import type { ChannelGrade, ArmGroup } from "./channel";
import { hasPatch } from "./patch";
import {
  loadIndex,
  loadManifest,
  listConfigIds,
  loadConfig,
  loadSlots,
} from "./data";
import type { ConfigRecord, SlotState, BatchIndexEntry } from "./data";

export type Grade = ChannelGrade; // "qualified" | "dev" | "community"

export interface EntryFacets {
  family: string;
  modelId: string;
  effort: string | null;
  armGroup: ArmGroup;
  arm: string; // "api" | "CC" | "codex" | "codex-oauth" | "grok-cli" | "go" | ...
  grade: Grade;
  protocol: string;
  transport: string;
  billing: string;
}

export interface SlotRef {
  variant: Variant;
  index: number;
  state: SlotState;
  hasPatch: boolean;
  /** sha256 of the ORIGINAL card.html for valid slots (vote provenance); null otherwise. */
  sha256: string | null;
}

export interface RegistryEntry {
  configId: string;
  batchId: string; // winning batch (asset source)
  batches: string[]; // every readable batch containing this configId
  config: ConfigRecord;
  facets: EntryFacets;
  slots: SlotRef[];
  validCount: Record<VariantDir, number>;
}

export interface Registry {
  entries: RegistryEntry[]; // browse order: family, modelId, effort, arm
  byId: Map<string, RegistryEntry>;
  facets: {
    families: string[];
    arms: string[];
    efforts: string[];
    grades: Grade[];
    models: string[];
  };
  totals: { configs: number; models: number; validSlots: number; batches: number };
}

const GRADE_ORDER: Grade[] = ["qualified", "dev", "community"];

// ------------------------------ internal scan ------------------------------

interface Container {
  batchId: string;
  createdAt: string;
  merged: boolean; // this batch is merged_into another
  config: ConfigRecord;
  slots: SlotRef[];
  validCount: Record<VariantDir, number>;
  validTotal: number;
}

function sha256OfCard(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
): string | null {
  try {
    const buf = fs.readFileSync(P.slotCardHtmlPath(batchId, configId, variant, slotIndex));
    return crypto.createHash("sha256").update(buf).digest("hex");
  } catch {
    return null;
  }
}

function scanConfig(
  batchId: string,
  createdAt: string,
  merged: boolean,
  configId: string,
): Container | null {
  let config: ConfigRecord;
  try {
    config = loadConfig(batchId, configId);
  } catch {
    return null; // unreadable config.json — skip (partial-data safe)
  }
  const validCount: Record<VariantDir, number> = { min: 0, q: 0 };
  const slots: SlotRef[] = [];
  let validTotal = 0;
  for (const variant of VARIANTS) {
    const vdir = variantToDir(variant);
    let metas: Array<{ slot_index: number; state: SlotState }> = [];
    try {
      metas = loadSlots(batchId, configId, variant);
    } catch {
      metas = [];
    }
    for (const meta of metas) {
      const valid = meta.state === "valid";
      if (valid) {
        validCount[vdir] += 1;
        validTotal += 1;
      }
      slots.push({
        variant,
        index: meta.slot_index,
        state: meta.state,
        hasPatch: hasPatch(batchId, configId, variant, meta.slot_index),
        sha256: valid ? sha256OfCard(batchId, configId, variant, meta.slot_index) : null,
      });
    }
  }
  return { batchId, createdAt, merged, config, slots, validCount, validTotal };
}

/** Winner comparator: more valid slots, then newer created_at, then higher id. */
function cmpWinner(a: Container, b: Container): number {
  if (a.validTotal !== b.validTotal) return b.validTotal - a.validTotal;
  if (a.createdAt !== b.createdAt) return a.createdAt < b.createdAt ? 1 : -1;
  return a.batchId < b.batchId ? 1 : a.batchId > b.batchId ? -1 : 0;
}

function facetsOf(config: ConfigRecord): EntryFacets {
  return {
    family: config.family,
    modelId: config.model_id,
    effort: config.effort,
    armGroup: armGroupOf(config.protocol),
    arm: armOf(config.config_id, config.protocol),
    grade: channelGradeOf(config.transport, config.config_id),
    protocol: config.protocol,
    transport: config.transport,
    billing: config.billing,
  };
}

/** Browse order key comparison: family → modelId → effort → arm (deterministic). */
function cmpBrowse(a: RegistryEntry, b: RegistryEntry): number {
  const f = a.facets;
  const g = b.facets;
  return (
    cmpStr(f.family, g.family) ||
    cmpStr(f.modelId, g.modelId) ||
    cmpStr(f.effort ?? "", g.effort ?? "") ||
    cmpStr(f.arm, g.arm) ||
    cmpStr(a.configId, b.configId)
  );
}
function cmpStr(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function uniqSorted(values: Iterable<string>): string[] {
  return [...new Set(values)].sort(compareSlug);
}

function buildRegistry(): Registry {
  let indexBatches: BatchIndexEntry[] = [];
  try {
    indexBatches = loadIndex().batches;
  } catch {
    indexBatches = [];
  }

  // configId → every readable container.
  const byConfig = new Map<string, Container[]>();
  const nonMergedReadable = new Set<string>();

  for (const b of indexBatches) {
    // readable = manifest loads.
    try {
      loadManifest(b.id);
    } catch {
      continue;
    }
    const merged = !!b.merged_into;
    if (!merged) nonMergedReadable.add(b.id);
    let configIds: string[] = [];
    try {
      configIds = listConfigIds(b.id);
    } catch {
      configIds = [];
    }
    for (const configId of configIds) {
      const container = scanConfig(b.id, b.created_at, merged, configId);
      if (!container) continue;
      const arr = byConfig.get(configId);
      if (arr) arr.push(container);
      else byConfig.set(configId, [container]);
    }
  }

  const entries: RegistryEntry[] = [];
  for (const [configId, containers] of byConfig) {
    const shippable = containers.filter((c) => !c.merged);
    const pool = shippable.length > 0 ? shippable : containers;
    const winner = [...pool].sort(cmpWinner)[0];
    const batches = [...new Set(containers.map((c) => c.batchId))].sort(compareSlug);
    entries.push({
      configId,
      batchId: winner.batchId,
      batches,
      config: winner.config,
      facets: facetsOf(winner.config),
      slots: winner.slots,
      validCount: winner.validCount,
    });
  }

  entries.sort(cmpBrowse);

  const byId = new Map<string, RegistryEntry>();
  for (const e of entries) byId.set(e.configId, e);

  const presentGrades = new Set(entries.map((e) => e.facets.grade));
  const validSlots = entries.reduce(
    (sum, e) => sum + e.validCount.min + e.validCount.q,
    0,
  );
  const winnerBatches = new Set(entries.map((e) => e.batchId));

  return {
    entries,
    byId,
    facets: {
      families: uniqSorted(entries.map((e) => e.facets.family)),
      arms: uniqSorted(entries.map((e) => e.facets.arm)),
      efforts: uniqSorted(
        entries.map((e) => e.facets.effort).filter((x): x is string => x != null),
      ),
      grades: GRADE_ORDER.filter((g) => presentGrades.has(g)),
      models: uniqSorted(entries.map((e) => e.facets.modelId)),
    },
    totals: {
      configs: entries.length,
      models: new Set(entries.map((e) => e.facets.modelId)).size,
      validSlots,
      batches: winnerBatches.size,
    },
  };
}

// -------------------------------- caching ----------------------------------
//
// globalThis singleton keyed on index.json mtimeMs (§2.2 / §1 cache policy).
// Self-contained on purpose: lib/singleton.ts (A3) may not exist during
// parallel build-out; integration may later route this through it.

interface RegistryCache {
  mtime: number;
  registry: Registry;
  builtAt: number;
}
const G = globalThis as unknown as { __wcb_registry?: RegistryCache };

function indexMtime(): number {
  try {
    return fs.statSync(P.indexPath()).mtimeMs;
  } catch {
    return 0;
  }
}

/** The unified registry (singleton; rebuilds when index.json changes). */
export function loadRegistry(): Registry {
  const mtime = indexMtime();
  const cache = G.__wcb_registry;
  if (cache && cache.mtime === mtime) return cache.registry;
  const registry = buildRegistry();
  G.__wcb_registry = { mtime, registry, builtAt: Date.now() };
  return registry;
}

/** Epoch millis when the current registry cache was built (for coverageAsOf). */
export function registryBuiltAt(): number {
  return G.__wcb_registry?.builtAt ?? Date.now();
}
