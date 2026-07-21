// ===========================================================================
// lib/patch.ts — the PATCH LAYER (v3 §2.3). A broken model card may be
// hand-fixed with an honest, visible note; the ORIGINAL is always preserved
// and served by default. A slot-level `patch.json` sits beside `card.html`:
//
//   configs/<id>/<variantDir>/slots/<k>/{patch.json, card.patched.html, patch.diff}
//
// PATCH INVARIANTS (LAW-level; verbatim in methodology):
//   1. A patch NEVER changes slot state / validCount / any coverage status.
//   2. All metrics (similarity, fidelity, shot.webp, telemetry) derive from the
//      ORIGINAL output. Card page: "metrics refer to the original output".
//   3. Arena + every voting/preference surface serve ONLY original card.html.
//   4. The category enum is closed; a patch may only make the card render,
//      never improve its content or design. patch.diff makes this auditable.
//
// Everything here is fs-backed (server/build only) and partial-data safe.
// ===========================================================================

import fs from "node:fs";

import * as P from "./paths";
import type { Variant } from "./variant";

/** Closed category enum (§2.3 — a patch only makes a card render). */
export type PatchCategory = "render-fix" | "asset-path" | "truncation-repair";

/** patch.json — the honest fix note (patch.schema.json). */
export interface PatchNote {
  version: number;
  category: PatchCategory;
  reason_en: string;
  reason_zh: string;
  patched_by: string;
  /** ISO-8601. */
  ts: string;
  diff_summary: string;
  diff_file: string;
  patched_html_file: string;
}

const PATCH_CATEGORIES: ReadonlySet<string> = new Set<PatchCategory>([
  "render-fix",
  "asset-path",
  "truncation-repair",
]);

/** Light structural guard (we do not import lib/schema.ts; the build gate
 *  validates patch.json against the frozen schema — this only keeps a
 *  malformed file from crashing a page). */
function isPatchNote(v: unknown): v is PatchNote {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  return (
    typeof o.category === "string" &&
    PATCH_CATEGORIES.has(o.category) &&
    typeof o.patched_html_file === "string"
  );
}

/**
 * Read a slot's patch note, or null when absent/malformed. Never throws — a
 * broken patch.json degrades to "no patch" (non-negotiable #4).
 */
export function loadPatch(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
): PatchNote | null {
  const p = P.slotPatchPath(batchId, configId, variant, slotIndex);
  if (!fs.existsSync(p)) return null;
  try {
    const parsed = JSON.parse(fs.readFileSync(p, "utf8"));
    return isPatchNote(parsed) ? (parsed as PatchNote) : null;
  } catch {
    return null;
  }
}

/** True iff a usable patch (note + patched html on disk) exists for this slot. */
export function hasPatch(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
): boolean {
  const note = loadPatch(batchId, configId, variant, slotIndex);
  if (!note) return false;
  return fs.existsSync(P.slotPatchedHtmlPath(batchId, configId, variant, slotIndex));
}

export interface ResolvedCard {
  html: string;
  /** true only when patched HTML was actually returned. */
  patched: boolean;
  note?: PatchNote;
}

/**
 * Resolve the card HTML for a slot. ORIGINAL-FIRST BY DEFAULT (invariant #3):
 * patched HTML is returned only when explicitly requested AND present. Falls
 * back to the original if a patched render is requested but missing.
 *
 * Throws only when the ORIGINAL card.html is itself absent (a genuine data
 * error the caller must handle) — callers on user-facing pages should guard.
 */
export function resolveCardHtml(
  batchId: string,
  configId: string,
  variant: Variant,
  slotIndex: number,
  opts?: { patched?: boolean },
): ResolvedCard {
  const wantPatched = opts?.patched === true;
  if (wantPatched) {
    const note = loadPatch(batchId, configId, variant, slotIndex);
    const patchedPath = P.slotPatchedHtmlPath(batchId, configId, variant, slotIndex);
    if (note && fs.existsSync(patchedPath)) {
      return { html: fs.readFileSync(patchedPath, "utf8"), patched: true, note };
    }
    // requested-but-absent → fall through to original.
  }
  const originalPath = P.slotCardHtmlPath(batchId, configId, variant, slotIndex);
  const html = fs.readFileSync(originalPath, "utf8");
  const note = loadPatch(batchId, configId, variant, slotIndex) ?? undefined;
  return { html, patched: false, note };
}

/** One patch discovered in the data root (for the methodology auto-list). */
export interface PatchEntry {
  batchId: string;
  configId: string;
  variant: Variant;
  slotIndex: number;
  note: PatchNote;
  /** public URL of the unified diff (mirrored by copy-assets). */
  diffUrl: string;
  /** public URLs of original + patched renderings. */
  originalUrl: string;
  patchedUrl: string;
}

/**
 * Enumerate every patch in a batch (methodology auto-lists these so the whole
 * layer is auditable on one page). Reads the on-disk slot tree; partial-safe.
 */
export function listBatchPatches(batchId: string): PatchEntry[] {
  const out: PatchEntry[] = [];
  const cfgRoot = `${P.batchDir(batchId)}/configs`;
  if (!fs.existsSync(cfgRoot)) return out;
  const VARIANT_DIRS: Array<{ dir: string; variant: Variant }> = [
    { dir: "min", variant: "P-min" },
    { dir: "q", variant: "P-q" },
  ];
  let configIds: string[] = [];
  try {
    configIds = fs
      .readdirSync(cfgRoot, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name);
  } catch {
    return out;
  }
  for (const configId of configIds) {
    for (const { dir, variant } of VARIANT_DIRS) {
      const slotsDir = `${cfgRoot}/${configId}/${dir}/slots`;
      if (!fs.existsSync(slotsDir)) continue;
      let slots: string[] = [];
      try {
        slots = fs
          .readdirSync(slotsDir, { withFileTypes: true })
          .filter((d) => d.isDirectory() && /^\d+$/.test(d.name))
          .map((d) => d.name);
      } catch {
        continue;
      }
      for (const slot of slots) {
        const k = Number(slot);
        const note = loadPatch(batchId, configId, variant, k);
        if (!note) continue;
        out.push({
          batchId,
          configId,
          variant,
          slotIndex: k,
          note,
          diffUrl: `/b/${batchId}/${configId}/${dir}/${k}/${note.diff_file}`,
          originalUrl: P.cardHtmlUrl(batchId, configId, variant, k),
          patchedUrl: P.patchedCardHtmlUrl(batchId, configId, variant, k),
        });
      }
    }
  }
  return out;
}
