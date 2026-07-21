import { getTranslations } from "next-intl/server";
import type { Metadata } from "next";
import { loadRegistry, type RegistryEntry } from "@/lib/registry";
import { cardHtmlUrl, assetUrl } from "@/lib/paths";
import { VARIANTS, type Variant } from "@/lib/variant";
import { configLabel } from "@/lib/label";
import { buildMeta, type MetaLocale } from "@/lib/metadata";
import {
  CompareClient,
  type CompareEntry,
  type CompareInitial,
} from "./CompareClient";

// /compare (spec §4, owner B3). Deep-linkable, never gated (§4.0). Renders the
// real page server-side; the interactive picker/frames live in CompareClient.
// Rendering policy per §1 table.
export const revalidate = 300;

type SearchParams = Record<string, string | string[] | undefined>;

function asLocale(locale: string): MetaLocale {
  return locale === "zh" ? "zh" : "en";
}

function firstParam(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

function parseIds(v: string | string[] | undefined): string[] {
  const raw = firstParam(v) ?? "";
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const id = part.trim();
    if (id && !out.includes(id)) out.push(id);
    if (out.length >= 12) break; // matches MAX_PICKS in CompareClient (2026-07-20)
  }
  return out;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Map a registry entry to the serializable shape the client renders. */
function toCompareEntry(e: RegistryEntry): CompareEntry {
  const slots: Record<Variant, CompareEntry["slots"][Variant]> = {
    "P-min": [],
    "P-q": [],
  };
  for (const s of e.slots) {
    const valid = s.state === "valid";
    slots[s.variant].push({
      index: s.index,
      state: s.state,
      cardUrl: valid ? cardHtmlUrl(e.batchId, e.configId, s.variant, s.index) : null,
      thumbUrl: valid ? assetUrl(e.batchId, e.configId, s.variant, s.index, "thumb") : null,
    });
  }
  return {
    configId: e.configId,
    family: e.facets.family,
    modelId: e.facets.modelId,
    effort: e.facets.effort,
    arm: e.facets.arm,
    grade: e.facets.grade,
    label: configLabel({
      config_id: e.configId,
      model_id: e.facets.modelId,
      effort: e.facets.effort,
      protocol: e.facets.protocol,
    }),
    hasPatch: e.slots.some((s) => s.hasPatch),
    validCount: { "P-min": e.validCount.min, "P-q": e.validCount.q },
    slots,
  };
}

export async function generateMetadata({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<SearchParams>;
}): Promise<Metadata> {
  const { locale } = await params;
  const sp = await searchParams;
  const ids = parseIds(sp.ids);
  const t = await getTranslations({ locale, namespace: "compare" });
  // 2+ ids → composite OG via /api/og/compare; else the branded mosaic fallback.
  const image =
    ids.length >= 2
      ? `/api/og/compare?ids=${ids.map((id) => encodeURIComponent(id)).join(",")}`
      : undefined;
  return buildMeta({
    title: t("meta.title"),
    description: t("meta.description"),
    path: "/compare",
    locale: asLocale(locale),
    image,
  });
}

export default async function ComparePage({
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;

  // Partial-data safe: loadRegistry() degrades to an empty registry on any error.
  let entries: CompareEntry[] = [];
  try {
    entries = loadRegistry().entries.map(toCompareEntry);
  } catch {
    entries = [];
  }

  const rawVariant = firstParam(sp.variant);
  const variant: Variant = VARIANTS.includes(rawVariant as Variant)
    ? (rawVariant as Variant)
    : "P-min";
  const rawDelay = Number(firstParam(sp.delay));
  const initial: CompareInitial = {
    ids: parseIds(sp.ids),
    city: firstParam(sp.city) ?? "London",
    date: firstParam(sp.date) ?? todayIso(),
    variant,
    delay: Number.isFinite(rawDelay) && rawDelay > 0 ? rawDelay : 0,
  };

  return <CompareClient entries={entries} initial={initial} />;
}
