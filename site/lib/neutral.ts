// ===========================================================================
// lib/neutral.ts — the measurement-presentation primitives.
//
// The colormap is a fixed neutral SINGLE-HUE ramp on the fixed domain [0,1]
// (never auto-stretched, never red/green). Cells failing the n_eff / m gate
// render a grey "insufficient data" cell and NEVER a number. This module is the
// single source of those rules.
//
// ---------------------------------------------------------------------------
// ORDERING DOCTRINE (v3 §2.2 — reconciles registry browse order vs this module).
// The site uses exactly THREE deterministic orders; no ad-hoc fourth is allowed:
//
//   (a) BROWSE order — family → modelId → effort → arm. Produced by
//       lib/registry.ts (`Registry.entries`); used by the gallery grid and the
//       matrix row layout. Human-scannable grouping of the config set.
//
//   (b) NEUTRAL order — `compareSlug` (NFC UTF-8 byte order) over the config
//       slug / compareSlug key. Used by API + CSV EXPORTS (arena stats, votes
//       export) so external consumers get a byte-reproducible ordering
//       independent of display state.
//
//   (c) PREFERENCE order — community UI-preference data (e.g. most-preferred
//       first). Used ONLY on preference surfaces (arena leaderboard) and always
//       LABELLED as community UI preference (§0.1(b)), never as a measurement.
//
// Deterministic orders only. `compareSlug`/`sortSlugs`/`sortBySlug` below are
// the canonical implementation of (b).
// ---------------------------------------------------------------------------
// NOTE (positioning, §0.1): the old "ranks nothing" NEUTRALITY_SENTENCE that
// used to live here has been REMOVED. §0.1 drops the self-righteous neutrality
// framing — there is no forbidden-language lint, and copy now lives in the i18n
// messages files with natural vocabulary. This module is pure ordering doctrine.
// ===========================================================================

// --------------------------- Slug byte-order sort --------------------------
//
// The default order everywhere is the config slug in NFC-normalized UTF-8 BYTE
// order (DATA-LAYOUT: config_ids are "persisted NFC-byte order"; scheme §7
// "slug 字典序默认"). JS string comparison is UTF-16 code-unit order, which
// diverges from UTF-8 byte order above U+FFFF and for some multibyte sequences —
// so we normalize to NFC and compare the actual UTF-8 bytes.

const _enc = new TextEncoder();

/** Compare two slugs by NFC UTF-8 byte order. Returns -1 | 0 | 1. */
export function compareSlug(a: string, b: string): number {
  const ba = _enc.encode(a.normalize("NFC"));
  const bb = _enc.encode(b.normalize("NFC"));
  const n = Math.min(ba.length, bb.length);
  for (let i = 0; i < n; i++) {
    if (ba[i] !== bb[i]) return ba[i] < bb[i] ? -1 : 1;
  }
  return ba.length === bb.length ? 0 : ba.length < bb.length ? -1 : 1;
}

/** Sort a copy of `slugs` into canonical NFC UTF-8 byte order. */
export function sortSlugs(slugs: readonly string[]): string[] {
  return [...slugs].sort(compareSlug);
}

/** Sort a copy of `items` by a slug-valued key in canonical byte order. */
export function sortBySlug<T>(items: readonly T[], key: (item: T) => string): T[] {
  return [...items].sort((x, y) => compareSlug(key(x), key(y)));
}

// ----------------------------- n_eff / m gate ------------------------------
//
// A cell is eligible only with enough effective independent comparisons, and a
// cross cell needs ≥2 valid slots on BOTH sides (scheme §5). Ineligible → grey
// "insufficient data", never a number (scheme §7, PROJECT-DESIGN §4.4).

/** Minimum effective sample size for any eligible cell. */
export const N_EFF_MIN = 4;
/** Minimum valid slots per side for a cross cell. */
export const M_CROSS_MIN = 2;
/** Grey fill for an ineligible cell (theme-neutral mid grey). */
export const INSUFFICIENT_COLOR = "#3a3f4b";
export const INSUFFICIENT_LABEL = "insufficient data";

export interface GateInput {
  kind: "self" | "cross";
  n_eff: number;
  m_a: number;
  m_b: number;
  /** the aggregate value; null means the pipeline already marked it ineligible. */
  median: number | null;
}

/** True iff this cell has sufficient data to display a value (scheme §5/§7). */
export function isSufficient(cell: GateInput): boolean {
  if (cell.median == null) return false;
  if (cell.n_eff < N_EFF_MIN) return false;
  if (cell.kind === "cross" && (cell.m_a < M_CROSS_MIN || cell.m_b < M_CROSS_MIN)) {
    return false;
  }
  return true;
}

// ------------------------- Fixed neutral colormap --------------------------
//
// A single-hue slate ramp on the FIXED domain [0,1]. t is CLAMPED to [0,1] and
// the domain is never rescaled to the data (no "stretch to make differences pop"
// — scheme §7 "固定 [0,1] 中性单调色域"). Interpolation is exact, per-channel
// LINEAR in sRGB (gamma-encoded 8-bit space) between the two documented
// mapping below — deterministic and trivially reproducible.
//
// COLORMAP (v2, 2026-07-17): viridis — perceptually-uniform, colorblind-safe,
// sequential (luminance-monotone). Chosen because real similarity values cluster
// in ~[0.4, 0.9]; the previous single-hue slate ramp had too little perceptual
// dynamic range there to see structure. Still LAW-compliant: FIXED [0,1] domain,
// linear, never data-stretched, batch-independent, and carries no good/bad
// semantics (no diverging red/green). The 17-stop LUT below is the standard
// matplotlib viridis sampled uniformly on [0,1]; cells lerp linearly in sRGB
// between adjacent stops.

/** Standard viridis, 17 uniform stops on [0,1], sRGB 8-bit. */
const VIRIDIS: ReadonlyArray<readonly [number, number, number]> = [
  [68, 1, 84], [72, 26, 108], [71, 47, 125], [65, 68, 135],
  [57, 86, 140], [49, 104, 142], [42, 120, 142], [35, 136, 142],
  [31, 152, 139], [34, 168, 132], [53, 183, 121], [84, 197, 104],
  [122, 209, 81], [165, 219, 54], [210, 226, 27], [253, 231, 37],
  [253, 231, 37],
] as const;

/** kept for back-compat with any importer; equal to the LUT endpoints. */
export const NEUTRAL_LOW = { r: 68, g: 1, b: 84 } as const;
export const NEUTRAL_HIGH = { r: 253, g: 231, b: 37 } as const;

function clamp01(t: number): number {
  return t < 0 ? 0 : t > 1 ? 1 : t;
}

/** Map t∈[0,1] (clamped, linear, fixed domain) to CSS `rgb(...)` on viridis. */
export function neutralColor(t: number): string {
  const c = clamp01(t) * 15; // 16 intervals over stops 0..16
  const i = Math.min(15, Math.floor(c));
  const f = c - i;
  const [r0, g0, b0] = VIRIDIS[i];
  const [r1, g1, b1] = VIRIDIS[i + 1];
  const r = Math.round(r0 + (r1 - r0) * f);
  const g = Math.round(g0 + (g1 - g0) * f);
  const b = Math.round(b0 + (b1 - b0) * f);
  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * Fill color for a matrix/heatmap cell: the neutral ramp at its median when the
 * data is sufficient, otherwise the grey insufficient-data fill. This is the ONLY
 * function pages should call to color a cell — it fuses the gate and the colormap
 * so a number can never leak through an ineligible cell.
 */
export function cellColor(cell: GateInput): string {
  return isSufficient(cell) ? neutralColor(cell.median as number) : INSUFFICIENT_COLOR;
}
