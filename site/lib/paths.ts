// Data-directory resolution + the public asset URL scheme.
//
// (v3 §2.1) Root resolution reworked. The "primary batch" concept is retired:
// the site now loads a UNIFIED REGISTRY across every batch (lib/registry.ts).
// The data ROOT (which holds index.json and every batch dir) is resolved once
// here; all batch-scoped path helpers below are UNCHANGED and keep taking an
// explicit batchId.
//
//   WCB_DATA_ROOT  → the data root directly (default <repo>/data/batches-dev).
//   WCB_DATA_DIR   → LEGACY: a single BATCH directory. When WCB_DATA_ROOT is
//                    unset and this is set, the root is its dirname (parent).
//
// The legacy `primaryBatchDir`/`primaryBatchId` helpers survive as thin shims
// so pre-migration pages keep compiling until their owning agents port them;
// nothing new should call them (use loadRegistry()).

import path from "node:path";
import fs from "node:fs";
import type { Variant, VariantDir } from "./variant";
import { variantToDir } from "./variant";

/**
 * Repo-relative default data root (dev only; prod MUST set WCB_DATA_ROOT — §3.6).
 * The private repo ships the full measured set under data/batches-dev; the public
 * OSS repo ships a flagship subset under data/batches. Prefer whichever exists so
 * one codebase works in both. cwd is the `site/` dir under Next.js, so the repo
 * root is one level up.
 */
function defaultDataRoot(): string {
  const repo = path.resolve(process.cwd(), "..");
  const dev = path.join(repo, "data", "batches-dev");
  const pub = path.join(repo, "data", "batches");
  if (fs.existsSync(path.join(dev, "index.json"))) return dev;
  if (fs.existsSync(path.join(pub, "index.json"))) return pub;
  return dev;
}
const DEV_DEFAULT_ROOT = defaultDataRoot();

/** Legacy default BATCH dir (only reachable through the legacy shims below). */
const DEV_DEFAULT_BATCH_DIR = path.join(
  DEV_DEFAULT_ROOT,
  "2026-07-16--m1-dev-e2e-smoke",
);

/**
 * Absolute path of the data root that holds index.json and every batch dir.
 * WCB_DATA_ROOT wins; else legacy WCB_DATA_DIR's parent; else the dev default.
 */
export function dataRoot(): string {
  const root = process.env.WCB_DATA_ROOT;
  if (root && root.length > 0) return path.resolve(root);
  const legacy = process.env.WCB_DATA_DIR;
  if (legacy && legacy.length > 0) return path.dirname(path.resolve(legacy));
  return DEV_DEFAULT_ROOT;
}

// --------------------------------------------------------------------------
// LEGACY primary-batch shims (deprecated; retained for pre-migration pages).
// --------------------------------------------------------------------------

/** @deprecated The primary-batch concept is retired (§2.1). Use loadRegistry(). */
export function primaryBatchDir(): string {
  const env = process.env.WCB_DATA_DIR;
  if (env && env.length > 0) return path.resolve(env);
  // Fall back to the first batch under the resolved root, else the dev default.
  return DEV_DEFAULT_BATCH_DIR;
}

/** @deprecated Use loadRegistry() / an explicit batchId. */
export function primaryBatchId(): string {
  return path.basename(primaryBatchDir());
}

/** Absolute directory of a batch by id. */
export function batchDir(batchId: string): string {
  return path.join(dataRoot(), batchId);
}

export function manifestPath(batchId: string): string {
  return path.join(batchDir(batchId), "manifest.json");
}
export function weatherSnapshotPath(batchId: string): string {
  return path.join(batchDir(batchId), "weather-snapshot.json");
}
export function statsPath(batchId: string): string {
  return path.join(batchDir(batchId), "stats.json");
}
export function probesPath(batchId: string): string {
  return path.join(batchDir(batchId), "probes.json");
}
export function indexPath(): string {
  return path.join(dataRoot(), "index.json");
}

export function configDir(batchId: string, configId: string): string {
  return path.join(batchDir(batchId), "configs", configId);
}
export function configPath(batchId: string, configId: string): string {
  return path.join(configDir(batchId, configId), "config.json");
}
export function sendLogPath(batchId: string, configId: string): string {
  return path.join(configDir(batchId, configId), "send-log.json");
}

/** Directory of one slot POSITION: configs/<id>/<variantDir>/slots/<k>/ (DECISIONS-M0 §4). */
export function slotDir(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
): string {
  return path.join(
    configDir(batchId, configId),
    variantToDir(variant),
    "slots",
    String(slotIndex),
  );
}
export function slotsRootDir(batchId: string, configId: string, variant: Variant): string {
  return path.join(configDir(batchId, configId), variantToDir(variant), "slots");
}
export function slotMetaPath(b: string, c: string, v: Variant, k: number): string {
  return path.join(slotDir(b, c, v, k), "meta.json");
}
export function slotCardHtmlPath(b: string, c: string, v: Variant, k: number): string {
  return path.join(slotDir(b, c, v, k), "card.html");
}
export function slotShotPath(b: string, c: string, v: Variant, k: number): string {
  return path.join(slotDir(b, c, v, k), "shot.webp");
}
export function slotFidelityTracePath(b: string, c: string, v: Variant, k: number): string {
  return path.join(slotDir(b, c, v, k), "fidelity-trace.json");
}

// --------------------------------------------------------------------------
// Patch layer (v3 §2.3): slot-level hand-fix files sitting beside card.html.
// --------------------------------------------------------------------------

/** patch.json — the honest, visible fix note for a slot (schema'd). */
export function slotPatchPath(b: string, c: string, v: Variant, k: number): string {
  return path.join(slotDir(b, c, v, k), "patch.json");
}
/** card.patched.html — the repaired rendering (original is always preserved). */
export function slotPatchedHtmlPath(b: string, c: string, v: Variant, k: number): string {
  return path.join(slotDir(b, c, v, k), "card.patched.html");
}
/** patch.diff — machine-auditable unified diff original→patched. */
export function slotPatchDiffPath(b: string, c: string, v: Variant, k: number): string {
  return path.join(slotDir(b, c, v, k), "patch.diff");
}

export function similarityDir(batchId: string): string {
  return path.join(batchDir(batchId), "similarity");
}
export function summaryPath(batchId: string, variant: Variant): string {
  return path.join(similarityDir(batchId), `summary--${variant}.json`);
}
export function pairsDir(batchId: string): string {
  return path.join(similarityDir(batchId), "pairs");
}
/** Unordered config-pair detail file: <a>--<b>--<variant>.json (DECISIONS-M0 §8). */
export function pairPath(
  batchId: string,
  configA: string,
  configB: string,
  variant: Variant,
): string {
  return path.join(pairsDir(batchId), `${configA}--${configB}--${variant}.json`);
}

export function exists(p: string): boolean {
  return fs.existsSync(p);
}

// ---------------------------------------------------------------------------
// Public asset URL scheme.
//
// scripts/copy-assets.mjs mirrors shot.webp / thumb.webp / card.html (+ patch
// files) into public/b/<batchId>/<configId>/<variantDir>/<slotIndex>/..., which
// the standalone server serves at /b/... . KEEP THIS IN SYNC with
// scripts/copy-assets.mjs (that file cannot import this TS module).
// ---------------------------------------------------------------------------

export type AssetKind = "shot" | "thumb";

/** Public URL of a slot's raw (ORIGINAL) card.html (mirrored by copy-assets). */
export function cardHtmlUrl(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
): string {
  const vdir: VariantDir = variantToDir(variant);
  return `/b/${batchId}/${configId}/${vdir}/${slotIndex}/card.html`;
}

/** Public URL of a slot's PATCHED card.html (present only for patched slots). */
export function patchedCardHtmlUrl(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
): string {
  const vdir: VariantDir = variantToDir(variant);
  return `/b/${batchId}/${configId}/${vdir}/${slotIndex}/card.patched.html`;
}

/** Public URL of a batch's per-variant similarity summary (mirrored by copy-assets). */
export function summaryUrl(batchId: string, variant: Variant): string {
  return `/b/${batchId}/similarity/summary--${variant}.json`;
}

/**
 * Public URL of the COMPACT matrix summary (index-encoded numeric tuples). The
 * client matrix fetches this instead of the verbose summary; copy-assets emits
 * it beside the verbose file. See writeCompactSummary in scripts/copy-assets.mjs.
 */
export function summaryCompactUrl(batchId: string, variant: Variant): string {
  return `/b/${batchId}/similarity/summary-compact--${variant}.json`;
}

/** Public URL of an unordered config-pair × variant detail file (canonical order). */
export function pairUrl(
  batchId: string,
  configA: string,
  configB: string,
  variant: Variant,
): string {
  return `/b/${batchId}/similarity/pairs/${configA}--${configB}--${variant}.json`;
}

/** Public URL (served by Caddy / next server) for a slot screenshot asset. */
export function assetUrl(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
  kind: AssetKind,
): string {
  const vdir: VariantDir = variantToDir(variant);
  return `/b/${batchId}/${configId}/${vdir}/${slotIndex}/${kind}.webp`;
}
