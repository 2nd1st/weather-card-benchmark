// Serializable descriptors passed from a live server component (data load) to
// the client wall. No fs, no non-serializable values. (Moved from
// app/live/types.ts in v3 §2.5; a re-export shim remains at the old path.)

import type { SlotState, Variant } from "@/lib/data";

export interface LiveSlot {
  slotIndex: number;
  state: SlotState;
  /** public URL of the raw card.html, or null for a failed slot with no output. */
  cardUrl: string | null;
  /** public URL of the frozen thumbnail (shown as a poster before mount). */
  thumbUrl: string | null;
}

export interface LiveConfig {
  configId: string;
  family: string;
  modelId: string;
  protocol: string;
  effort: string | null;
  totalTokens: number;
  wallMs: number;
  /** decimal string, or null when unmetered/unknown. */
  costUsd: string | null;
  /** slot_index of the representative (first valid) slot, or null if none valid. */
  repSlotIndex: number | null;
  slots: LiveSlot[];
}

export interface LiveCity {
  name: string;
  /** byte-exact decimal strings (manifest §3) or plain decimals for decor cities. */
  lat: string;
  lon: string;
}

export interface LiveData {
  batchId: string;
  cities: LiveCity[];
  /** ISO yyyy-mm-dd default — the fixture date, so the first load hits the cache. */
  defaultDate: string;
  variants: Variant[];
  byVariant: Record<Variant, LiveConfig[]>;
}
