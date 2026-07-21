// Variant + channel vocabulary — the frozen tokens the data contract uses.
// Source of truth: DATA-LAYOUT.md, DECISIONS-M0.md §4 (variant dir layout),
// summary--<variant>.json `channels`, stats.json `formal_channels`.

/** Prompt-variant TOKEN as it appears in index.json / summary / pairs / stats. */
export type Variant = "P-min" | "P-q";

/** On-disk variant DIRECTORY name under configs/<id>/<variantDir>/ (DECISIONS-M0 §4). */
export type VariantDir = "min" | "q";

export const VARIANTS: readonly Variant[] = ["P-min", "P-q"] as const;

/** Token → directory: "P-min" → "min", "P-q" → "q". */
export function variantToDir(v: Variant): VariantDir {
  return v === "P-min" ? "min" : "q";
}

/** Directory → token: "min" → "P-min", "q" → "P-q". */
export function dirToVariant(d: VariantDir): Variant {
  return d === "min" ? "P-min" : "P-q";
}

/** config.json.m is keyed by directory name, not token (DECISIONS-M0 §4 / M1 §1). */
export function variantMKey(v: Variant): VariantDir {
  return variantToDir(v);
}

// ---- Similarity channels (17 total = 15 formal + 2 permanent diagnostics) ----
// Canonical order = summary--<variant>.json `channels` array.

export type Channel =
  | "v-phash" | "v-dhash" | "v-color" | "v-palette" | "v-layout" | "v-edge" | "v-ssim"
  | "c-shingle" | "c-winnow" | "c-feature" | "c-ast-js" | "c-css-prop" | "c-ncd"
  | "d-geom" | "d-text" | "d-pqgram" | "d-tagpath";

/** All 17 channels in canonical presentation order. */
export const CHANNELS: readonly Channel[] = [
  "v-phash", "v-dhash", "v-color", "v-palette", "v-layout", "v-edge", "v-ssim",
  "c-shingle", "c-winnow", "c-feature", "c-ast-js", "c-css-prop", "c-ncd",
  "d-geom", "d-text", "d-pqgram", "d-tagpath",
] as const;

/** The 2 permanent diagnostics excluded from the formal 15 (scheme §5; stats excludes them). */
export const DIAGNOSTIC_CHANNELS: readonly Channel[] = ["c-ncd", "d-tagpath"] as const;

/** The 15 formal channels (CHANNELS minus diagnostics), canonical order. */
export const FORMAL_CHANNELS: readonly Channel[] = CHANNELS.filter(
  (c) => !DIAGNOSTIC_CHANNELS.includes(c),
) as Channel[];

export type ChannelFamily = "v" | "c" | "d";

/** Channel family prefix: "v" (visual) | "c" (code) | "d" (dom-structure). */
export function channelFamily(c: Channel): ChannelFamily {
  return c.charAt(0) as ChannelFamily;
}

export function isDiagnosticChannel(c: Channel): boolean {
  return DIAGNOSTIC_CHANNELS.includes(c);
}
