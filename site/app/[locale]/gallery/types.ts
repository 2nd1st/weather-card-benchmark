// Serializable payload shapes shared between the gallery server component
// (page.tsx) and its client (GalleryClient.tsx). Pure types — no runtime, no
// "use client" boundary — so both sides import them freely.

import type { Grade } from "@/app/components/Badge";

/** A representative valid slot's public asset URLs, per variant. */
export interface GallerySlotAsset {
  slotIndex: number;
  thumbUrl: string;
  /** ORIGINAL card.html URL (never patched for the gallery preview). */
  cardUrl: string;
}

/** Per-variant valid/total slot counts (variant token stays English). */
export interface GalleryCount {
  variant: string;
  valid: number;
  total: number;
}

/** One registry entry flattened for the client grid (batch nowhere in sight). */
export interface GalleryEntry {
  configId: string;
  modelId: string;
  family: string;
  effort: string | null;
  arm: string;
  armGroup: "api" | "harness";
  grade: Grade;
  /** community grade that has not passed maintainer review (§3.3 quarantine). */
  unreviewed: boolean;
  /** any slot carries a visible hand-fix (original always preserved). */
  patched: boolean;
  counts: GalleryCount[];
  /** representative valid slot per variant token ("P-min" / "P-q"); null = none. */
  rep: Record<string, GallerySlotAsset | null>;
  /** EVERY valid slot per variant token, ascending by slotIndex. Powers the slot
   *  debug filter, which pins one index instead of the representative slot. */
  allSlots: Record<string, GallerySlotAsset[]>;
  /** whether the entry has ≥1 valid slot in each variant token. */
  validByVariant: Record<string, boolean>;
}

/** Facet option lists + arm→group split, derived once server-side. */
export interface GalleryFacets {
  families: string[];
  arms: string[];
  grades: Grade[];
  /** arm tokens grouped under their arm group for the visual headers (§4). */
  armGroups: { api: string[]; harness: string[] };
  /** variant tokens present, e.g. ["P-min","P-q"]. */
  variants: string[];
  /** every valid slot index present, ascending — options for the slot debug filter. */
  slotIndices: number[];
}
