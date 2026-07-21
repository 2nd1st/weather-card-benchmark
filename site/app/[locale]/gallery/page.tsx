import { Suspense } from "react";
import type { Metadata } from "next";
import { setRequestLocale, getTranslations } from "next-intl/server";

import { buildMeta, type MetaLocale } from "@/lib/metadata";
import { loadRegistry, type RegistryEntry } from "@/lib/registry";
import { VARIANTS, variantToDir } from "@/lib/variant";
import type { Variant } from "@/lib/variant";
import { cardHtmlUrl, assetUrl } from "@/lib/paths";
import type { Grade } from "@/app/components/Badge";

import { GalleryClient } from "./GalleryClient";
import { GalleryGate } from "./GalleryGate";
import type { GalleryEntry, GalleryFacets, GallerySlotAsset } from "./types";

// /gallery (spec §4) — the unified grid over loadRegistry(): ONE filterable set
// of configs, batch nowhere in the primary UI. De-faceted FilterBar (family /
// model-search / arm / channel + live / valid-only / has-patch toggles). URL
// round-trips every facet. The arena gate is an OVERLAY on BARE /gallery only
// (§4.0) — any query string opens the grid with zero interstitials.
//
// revalidate 300 (§1 rendering table): the fs-reading server component is
// re-derived at most every 5 min; loadRegistry()'s own cache keys on index.json
// mtime so a newly landed batch still appears. Partial data never crashes here —
// loadRegistry is guarded and every downstream read is defensive (non-neg #4).

export const revalidate = 300;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "gallery" });
  return buildMeta({
    title: t("meta.title"),
    description: t("meta.description"),
    path: "/gallery",
    locale: locale as MetaLocale,
    image: "/og/mosaic.png",
  });
}

/** EVERY valid slot in a variant → its public asset URLs, ascending by index.
 *  The gallery normally shows only the first (see repFor), but the slot debug
 *  filter pins a specific index, which needs the whole list. */
function slotsFor(entry: RegistryEntry, variant: Variant): GallerySlotAsset[] {
  return entry.slots
    .filter((s) => s.variant === variant && s.state === "valid")
    .sort((a, b) => a.index - b.index)
    .map((s) => ({
      slotIndex: s.index,
      thumbUrl: assetUrl(entry.batchId, entry.configId, variant, s.index, "thumb"),
      cardUrl: cardHtmlUrl(entry.batchId, entry.configId, variant, s.index),
    }));
}

/** Lowest-index valid slot in a variant → its public asset URLs, else null. */
function repFor(entry: RegistryEntry, variant: Variant): GallerySlotAsset | null {
  return slotsFor(entry, variant)[0] ?? null;
}

function toGalleryEntry(entry: RegistryEntry): GalleryEntry {
  const rep: Record<string, GallerySlotAsset | null> = {};
  const allSlots: Record<string, GallerySlotAsset[]> = {};
  const validByVariant: Record<string, boolean> = {};
  const counts = VARIANTS.map((variant) => {
    const dir = variantToDir(variant);
    const total = entry.slots.filter((s) => s.variant === variant).length;
    const valid = entry.validCount[dir] ?? 0;
    const list = slotsFor(entry, variant);
    allSlots[variant] = list;
    rep[variant] = list[0] ?? null;
    validByVariant[variant] = valid > 0;
    return { variant, valid, total };
  });
  return {
    configId: entry.configId,
    modelId: entry.facets.modelId,
    family: entry.facets.family,
    effort: entry.facets.effort,
    arm: entry.facets.arm,
    armGroup: entry.facets.armGroup,
    grade: entry.facets.grade as Grade,
    // registry surfaces no per-batch `reviewed` flag; treat every community run
    // as unreviewed in the gallery (safe default, §3.3 quarantine).
    unreviewed: entry.facets.grade === "community",
    patched: entry.slots.some((s) => s.hasPatch),
    counts,
    rep,
    allSlots,
    validByVariant,
  };
}

/** Registry → serializable payload; any read failure degrades to an empty grid. */
function loadGalleryData(): { entries: GalleryEntry[]; facets: GalleryFacets; total: number } {
  try {
    const registry = loadRegistry();
    const entries = registry.entries.map(toGalleryEntry);

    // arm → group split for the visual api/harness headers (§4).
    const api = new Set<string>();
    const harness = new Set<string>();
    for (const e of entries) {
      (e.armGroup === "api" ? api : harness).add(e.arm);
    }
    const bySlug = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

    // every valid slot index present anywhere — the slot debug filter's options
    const slotIdx = new Set<number>();
    for (const e of entries) {
      for (const list of Object.values(e.allSlots)) {
        for (const s of list) slotIdx.add(s.slotIndex);
      }
    }

    return {
      entries,
      total: registry.totals.configs,
      facets: {
        families: registry.facets.families,
        arms: registry.facets.arms,
        grades: registry.facets.grades as Grade[],
        armGroups: {
          api: [...api].sort(bySlug),
          harness: [...harness].sort(bySlug),
        },
        variants: [...VARIANTS],
        slotIndices: [...slotIdx].sort((a, b) => a - b),
      },
    };
  } catch {
    return {
      entries: [],
      total: 0,
      facets: {
        families: [],
        arms: [],
        grades: [],
        armGroups: { api: [], harness: [] },
        variants: [...VARIANTS],
        slotIndices: [],
      },
    };
  }
}

export default async function GalleryPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const sp = await searchParams;

  const { entries, facets, total } = loadGalleryData();

  // Gate applies ONLY to bare /gallery (no query string) — §4.0. With any query
  // the grid is open, no interstitial. The overlay itself only paints when the
  // pre-hydration stamp marked <html data-gate="pending"> (fresh visitor).
  const bare = Object.keys(sp).length === 0;

  return (
    <div className="wrap gallery-page">
      <Suspense fallback={null}>
        <GalleryClient entries={entries} facets={facets} total={total} />
      </Suspense>
      {bare ? <GalleryGate /> : null}
    </div>
  );
}
