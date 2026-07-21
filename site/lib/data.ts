// ===========================================================================
// lib/data.ts — the typed, per-batch artifact loader. This is the low-level
// interface; the site's primary entry point is now lib/registry.ts (the
// UNIFIED, deduped registry across all batches — v3 §2.2). Use these loaders
// when you already hold a batchId (e.g. an entry's winning `batchId`).
//
// (v3 §2.1) Root resolution moved into lib/paths.ts: the data ROOT (index.json
// + every batch dir) resolves from WCB_DATA_ROOT (legacy WCB_DATA_DIR's parent
// as a fallback). The "primary batch" concept is retired — the legacy
// re-exports below survive only for pre-migration pages.
//
// Every read is schema-validated (lib/schema.ts) against the frozen data
// contract in data/SCHEMA/ (DATA-LAYOUT.md). Server/build only (Node fs);
// nothing here is client-safe. Config order within a batch is ALWAYS the
// manifest's persisted `config_ids` (NFC byte order). Use lib/neutral.ts for
// presentation ordering/gating; lib/registry.ts for the cross-batch browse order.
// ===========================================================================

import fs from "node:fs";
import path from "node:path";

import { readJsonValidated } from "./schema";
import * as P from "./paths";
import type { Variant } from "./variant";
import type { Channel } from "./variant";

// Re-export the shared vocabulary + asset scheme so builders need one import.
export type { Variant, VariantDir, Channel, ChannelFamily } from "./variant";
export {
  VARIANTS,
  CHANNELS,
  FORMAL_CHANNELS,
  DIAGNOSTIC_CHANNELS,
  channelFamily,
  isDiagnosticChannel,
  variantToDir,
  dirToVariant,
} from "./variant";
export type { AssetKind } from "./paths";
export { assetUrl, primaryBatchId, dataRoot, batchDir } from "./paths";

// ------------------------------- Types -------------------------------------

/** index.json entry (index.schema.json). */
export interface BatchIndexEntry {
  id: string;
  event: string;
  date: string;
  manifest_hash: string;
  n_configs: number;
  variants: Variant[];
  created_at: string;
  /** set when this batch's configs were physically merged into another batch
   *  (dev workflow) — merged batches are hidden from the site's switchers. */
  merged_into?: string;
}
export interface BatchIndex {
  schema_version: string;
  batches: BatchIndexEntry[];
}

/** manifest.json — the batch's frozen identity (manifest.schema.json). */
export interface City {
  city: string;
  date: string;
  /** canonical decimal STRING (byte-exact; DATA-LAYOUT §3). */
  lat: string;
  lon: string;
}
export interface Manifest {
  N: number;
  cities: City[];
  config_ids: string[];
  env_digest: string;
  pipeline_commit: string;
  prompt_min_sha256: string;
  prompt_q_sha256: string;
  snapshot_sha256: string;
}

export type Protocol = "api" | "agent" | "cli";
export type Billing = "metered" | "plan";

export interface ConfigTelemetry {
  completion_tokens: number;
  prompt_tokens: number;
  total_tokens: number;
  wall_ms: number;
  /** decimal string when known, null when unmetered/unknown (DATA-LAYOUT §3). */
  cost_usd: string | null;
}
export interface ConfigRecord {
  config_id: string;
  family: string;
  model_id: string;
  /** per-vendor opaque effort string, nullable. */
  effort: string | null;
  protocol: Protocol;
  billing: Billing;
  auth: { method: string; credential_ref: string };
  transport: string;
  served_model: string | null;
  vendor_sanction_ref: string | null;
  telemetry: ConfigTelemetry;
  /** PER-VARIANT count(valid), keyed by directory name (DECISIONS-M0 §4 / M1 §1). */
  m: { min: number; q: number };
  N: number;
}

/** Six terminal slot states (DECISIONS-M0 §6). */
export type SlotState =
  | "valid"
  | "model-failed"
  | "infra-failed"
  | "acceptance-unknown"
  | "unreachable"
  | "rate-limited-exhausted";

export type FieldState = "match" | "mismatch" | "ambiguous" | "not-found";
/** `name` is 2-state — no mismatch (scheme §3). */
export type NameState = "match" | "not-found";
export type HourlyPointState = "match" | "mismatch" | "not-shown";

export interface LabColor {
  L: number;
  a: number;
  b: number;
}
export interface L1 {
  bytes: { css: number; html: number; js: number; total: number };
  palette_top8: Array<{ bin_index: number; lab: LabColor; weight: number }>;
  structure: {
    css_rules: number;
    dom_depth: number;
    dom_nodes: number;
    js_bytes_present: boolean;
  };
  visual: {
    brightness: number;
    colorfulness: number;
    contrast: number;
    frame_change: { max: number; median: number };
    whitespace_ratio: number;
  };
}

export interface FieldVerdict {
  state: FieldState;
  reason: string | null;
}
export interface TempFieldVerdict extends FieldVerdict {
  value: string | null;
  display_decimals: number | null;
}
export interface HourlyVerdict {
  state: FieldState;
  coverage: string;
  points: Array<{ hour: number; state: HourlyPointState }>;
  points_match: number;
  points_mismatch: number;
  points_not_shown: number;
  reason: string | null;
}
/** Per-field TERMINAL fidelity verdict (meta.json; deep trace lives separately). */
export interface Fidelity {
  extractor_version: string;
  name: { state: NameState; reason: string | null };
  condition: FieldVerdict;
  date: FieldVerdict;
  max: TempFieldVerdict;
  min: TempFieldVerdict;
  hourly: HourlyVerdict;
  request_side: { status: string; violations: unknown[] };
}

export interface SlotTelemetry {
  completion_tokens: number;
  prompt_tokens: number;
  total_tokens: number;
  wall_ms: number;
  cost_usd: string | null;
  request_id: string | null;
}
/** meta.json — a slot POSITION's full record. l1/fidelity non-null iff state=valid. */
export interface SlotMeta {
  slot_index: number;
  state: SlotState;
  flags: string[];
  telemetry: SlotTelemetry;
  l1: L1 | null;
  fidelity: Fidelity | null;
}

export interface WeatherLocation {
  city: string;
  date: string;
  lat: string;
  lon: string;
  endpoint: string;
  canary: unknown;
  response: unknown;
}
export interface WeatherSnapshot {
  batch_id: string;
  extractor_version: string;
  fetched_at: string;
  source: string;
  locations: WeatherLocation[];
}

export type CellKind = "self" | "cross";
export interface IQR {
  p25: number;
  p75: number;
}
/** One config×config×channel aggregate cell. null aggregates → "insufficient data". */
export interface SummaryCell {
  config_a: string;
  config_b: string;
  channel: Channel;
  kind: CellKind;
  median: number | null;
  iqr: IQR | null;
  n_eff: number;
  m_a: number;
  m_b: number;
}
export interface SimilaritySummary {
  batch_id: string;
  variant: Variant;
  channels: Channel[];
  configs: string[];
  cells: SummaryCell[];
}

export interface PairItem {
  slot_a: number;
  slot_b: number;
  s: Partial<Record<Channel, number | null>>;
  diagnostics?: {
    v_ssim_raw?: number;
    c_ncd_ncd_sym?: number;
    [k: string]: number | null | undefined;
  };
}
export interface PairDetail {
  batch_id: string;
  variant: Variant;
  config_a: string;
  config_b: string;
  kind: CellKind;
  channels: Channel[];
  pairs: PairItem[];
}

export interface SendAttempt {
  attempt_index: number;
  backoff_ms: number | null;
  charged: boolean;
  started_at: string;
  ended_at: string;
  http_status: number | null;
  outcome: string;
  reached_model: boolean;
  reason: string;
  request_id: string | null;
}
export interface SendPosition {
  attempts: SendAttempt[];
  block_index: number;
  model_reaching_attempt_index: number | null;
  slot_index: number;
  terminal_state: SlotState;
  variant: Variant;
}
export interface SendLog {
  N: number;
  batch_id: string;
  config_id: string;
  positions: SendPosition[];
}

/** stats.json — L3 descriptive stats + the single randomization test (stats.schema.json).
 * Top-level shape is typed; the descriptive/test blocks are deep and left broad so
 * builders read them structurally against stats.schema.json. */
export interface Stats {
  batch_id: string;
  configs: string[];
  formal_channels: Channel[];
  descriptive: Array<{ variant: Variant; [k: string]: unknown }>;
  [k: string]: unknown;
}

/** probes.json — L4 behavioral probes (probes.schema.json). Broad top-level. */
export interface Probes {
  batch_id?: string;
  [k: string]: unknown;
}

/** fidelity-trace.json — deep per-slot audit trace (fidelity-trace.schema.json). */
export interface FidelityTrace {
  [k: string]: unknown;
}

// ----------------------------- Loaders -------------------------------------

/** Read + validate data/index.json. */
export function loadIndex(): BatchIndex {
  return readJsonValidated<BatchIndex>(P.indexPath(), "index.schema.json");
}

/** All batches in presentation order (by id — date-prefixed, never a ranking). */
/** A batch with a readable manifest is enumerable — including PARTIAL batches
 *  (2026-07-18: 成本约束下没法一次跑齐,半批也要上墙). Safety against
 *  racing an in-flight runner lives in listConfigIds, which only returns
 *  configs whose config.json is actually on disk. */
export function isBatchReadable(batchId: string): boolean {
  try {
    loadManifest(batchId);
    return true;
  } catch {
    return false; // no/invalid manifest yet
  }
}

export function listBatches(): BatchIndexEntry[] {
  // Merged-away batches stay on disk (append-only) but are not enumerated —
  // their configs already appear inside the batch they were merged into.
  return [...loadIndex().batches]
    .filter((b) => !b.merged_into && isBatchReadable(b.id))
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

export function getBatchEntry(batchId: string): BatchIndexEntry {
  const entry = loadIndex().batches.find((b) => b.id === batchId);
  if (!entry) throw new Error(`batch not in index.json: ${batchId}`);
  return entry;
}

export function loadManifest(batchId: string): Manifest {
  return readJsonValidated<Manifest>(P.manifestPath(batchId), "manifest.schema.json");
}

export function loadWeatherSnapshot(batchId: string): WeatherSnapshot {
  return readJsonValidated<WeatherSnapshot>(
    P.weatherSnapshotPath(batchId),
    "weather-snapshot.schema.json",
  );
}

export function loadStats(batchId: string): Stats {
  return readJsonValidated<Stats>(P.statsPath(batchId), "stats.schema.json");
}

/** probes.json is optional in early batches; returns null when absent. */
export function loadProbes(batchId: string): Probes | null {
  const p = P.probesPath(batchId);
  if (!P.exists(p)) return null;
  return readJsonValidated<Probes>(p, "probes.schema.json");
}

/** Config ids in authoritative manifest order (NFC byte order), restricted to
 *  configs whose config.json exists — a PARTIAL/in-flight batch shows what it
 *  has instead of crashing the build (existence filter = the race guard). */
export function listConfigIds(batchId: string): string[] {
  return loadManifest(batchId).config_ids.filter((id) =>
    fs.existsSync(P.configPath(batchId, id)),
  );
}

export function loadConfig(batchId: string, configId: string): ConfigRecord {
  return readJsonValidated<ConfigRecord>(
    P.configPath(batchId, configId),
    "config.schema.json",
  );
}

/** All config records in authoritative manifest order. */
export function loadConfigs(batchId: string): ConfigRecord[] {
  return listConfigIds(batchId).map((id) => loadConfig(batchId, id));
}

export function loadSendLog(batchId: string, configId: string): SendLog {
  return readJsonValidated<SendLog>(
    P.sendLogPath(batchId, configId),
    "send-log.schema.json",
  );
}

/** Slot POSITION indices present on disk for a (config, variant), numeric-sorted. */
export function listSlotIndices(
  batchId: string,
  configId: string,
  variant: Variant,
): number[] {
  const dir = P.slotsRootDir(batchId, configId, variant);
  if (!P.exists(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((d) => d.isDirectory() && /^\d+$/.test(d.name))
    .map((d) => Number(d.name))
    .sort((a, b) => a - b);
}

export function loadSlotMeta(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
): SlotMeta {
  return readJsonValidated<SlotMeta>(
    P.slotMetaPath(batchId, configId, variant, slotIndex),
    "slot-meta.schema.json",
  );
}

/** All slot metas for a (config, variant) in slot-index order. */
export function loadSlots(
  batchId: string,
  configId: string,
  variant: Variant,
): SlotMeta[] {
  return listSlotIndices(batchId, configId, variant).map((k) =>
    loadSlotMeta(batchId, configId, variant, k),
  );
}

/** Raw model output HTML, one byte unchanged (card.html). Valid/model-failed slots only. */
export function loadCardHtml(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
): string {
  return fs.readFileSync(P.slotCardHtmlPath(batchId, configId, variant, slotIndex), "utf8");
}

/** Recorded slot screenshot bytes (shot.webp); null when absent/unreadable. Used
 *  to inline a BLIND static shot into a gate pair — a data: URI carries no
 *  identity path, so blindness (§3.2) is preserved. */
export function loadShot(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
): Buffer | null {
  const p = P.slotShotPath(batchId, configId, variant, slotIndex);
  if (!P.exists(p)) return null;
  try {
    return fs.readFileSync(p);
  } catch {
    return null;
  }
}

/** Deep per-slot fidelity trace (valid slots only); null when absent. */
export function loadFidelityTrace(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
): FidelityTrace | null {
  const p = P.slotFidelityTracePath(batchId, configId, variant, slotIndex);
  if (!P.exists(p)) return null;
  return readJsonValidated<FidelityTrace>(p, "fidelity-trace.schema.json");
}

const _summaryCache = new Map<string, SimilaritySummary>();
export function loadSimilaritySummary(
  batchId: string,
  variant: Variant,
): SimilaritySummary {
  const _ck = `${batchId}::${variant}`;
  const _hit = _summaryCache.get(_ck);
  if (_hit) return _hit;
  const _out = readJsonValidated<SimilaritySummary>(
    P.summaryPath(batchId, variant),
    "similarity-summary.schema.json",
  );
  _summaryCache.set(_ck, _out);
  return _out;
}

/**
 * Slot-pair detail for an UNORDERED config-pair × variant. Materialized exactly
 * once per unordered pair (DECISIONS-M0 §8) with the filename in canonical
 * (byte-order) config order; this resolver tries the canonical order first then
 * the swapped order, so callers may pass (a,b) in any order.
 */
export function loadPair(
  batchId: string,
  configA: string,
  configB: string,
  variant: Variant,
): PairDetail {
  const [lo, hi] = configA <= configB ? [configA, configB] : [configB, configA];
  const primary = P.pairPath(batchId, lo, hi, variant);
  if (P.exists(primary)) {
    return readJsonValidated<PairDetail>(primary, "similarity-pairs.schema.json");
  }
  const swapped = P.pairPath(batchId, hi, lo, variant);
  if (P.exists(swapped)) {
    return readJsonValidated<PairDetail>(swapped, "similarity-pairs.schema.json");
  }
  throw new Error(
    `pair detail not found for ${configA}--${configB}--${variant} in ${batchId}`,
  );
}

/** Which unordered config-pairs have a materialized detail file, per variant. */
export function listPairFiles(
  batchId: string,
  variant: Variant,
): Array<{ config_a: string; config_b: string }> {
  const dir = P.pairsDir(batchId);
  if (!P.exists(dir)) return [];
  const suffix = `--${variant}.json`;
  const out: Array<{ config_a: string; config_b: string }> = [];
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(suffix)) continue;
    const stem = name.slice(0, -suffix.length);
    const parts = stem.split("--");
    // config ids themselves contain "--"; a pair file is "<a>--<b>". Recover the
    // split by matching against known config ids from the manifest.
    const ids = listConfigIds(batchId);
    const match = findPairSplit(stem, ids);
    if (match) out.push(match);
    else if (parts.length >= 2) {
      // fallback: even split (both ids share the same "--" arity)
      const half = parts.length / 2;
      out.push({
        config_a: parts.slice(0, half).join("--"),
        config_b: parts.slice(half).join("--"),
      });
    }
  }
  return out;
}

function findPairSplit(
  stem: string,
  ids: string[],
): { config_a: string; config_b: string } | null {
  for (const a of ids) {
    if (stem === `${a}--${a}`) return { config_a: a, config_b: a };
    if (stem.startsWith(`${a}--`)) {
      const rest = stem.slice(a.length + 2);
      if (ids.includes(rest)) return { config_a: a, config_b: rest };
    }
  }
  return null;
}

/** Convenience: public URLs for a slot's screenshot assets. */
export function slotAssets(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
): { shot: string; thumb: string } {
  return {
    shot: P.assetUrl(batchId, configId, variant, slotIndex, "shot"),
    thumb: P.assetUrl(batchId, configId, variant, slotIndex, "thumb"),
  };
}
