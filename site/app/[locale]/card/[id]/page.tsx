import { setRequestLocale, getTranslations } from "next-intl/server";
import { codeToHtml } from "shiki";

import { buildMeta, type MetaLocale } from "@/lib/metadata";
import { loadRegistry } from "@/lib/registry";
import { configLabel } from "@/lib/label";
import {
  loadWeatherSnapshot,
  loadConfig,
  loadSendLog,
  loadSimilaritySummary,
  loadSlotMeta,
  getBatchEntry,
  slotAssets,
  listConfigIds,
  CHANNELS,
  VARIANTS,
  type Variant,
  type Channel,
  type WeatherSnapshot,
  type L1,
  type Fidelity,
  type SlotTelemetry,
} from "@/lib/data";
import { loadPatch } from "@/lib/patch";
import { isSufficient } from "@/lib/neutral";
import { exists, slotCardHtmlPath, cardHtmlUrl, patchedCardHtmlUrl } from "@/lib/paths";
import fs from "node:fs";

import { CardView } from "./CardView";
import type {
  CardSlot,
  CardViewData,
  NeighborData,
  PatchInfo,
  LiveFixture,
} from "./CardView";

// /card/[id] (spec §4). id = configId. The good parts of the old CardView —
// live controls, slots, fidelity, telemetry — reskinned into the dark-instrument
// shell, plus the patch toggle, provenance block, per-channel session-scoped
// similarity neighbors, raw config/send-log viewers, and the all-failed
// tombstone. Rendering policy §1: revalidate every 300s (new batches land
// incrementally; the registry cache is keyed on index.json mtime).

export const revalidate = 300;

async function highlight(code: string, lang: string): Promise<string> {
  return codeToHtml(code, {
    lang,
    themes: { light: "github-light", dark: "github-dark" },
    defaultColor: false,
  });
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id: rawId } = await params;
  const configId = decodeURIComponent(rawId);
  const reg = loadRegistry();
  const entry = reg.byId.get(configId);
  const t = await getTranslations({ locale, namespace: "card" });
  if (!entry) {
    return buildMeta({
      title: t("notFound.title"),
      description: t("notFound.message"),
      path: `/card/${encodeURIComponent(configId)}`,
      locale: locale as MetaLocale,
    });
  }
  const label = configLabel(entry.config);
  // OG = slot-0 P-q shot when present, else P-min, else the branded mosaic.
  let image: string | undefined;
  for (const v of VARIANTS) {
    const slot0 = entry.slots.find((s) => s.variant === v && s.index === 0 && s.state === "valid");
    if (slot0) {
      image = slotAssets(entry.batchId, configId, v, 0).shot;
      break;
    }
  }
  return buildMeta({
    title: t("meta.title", { label }),
    description: t("meta.description", { label }),
    image,
    path: `/card/${encodeURIComponent(configId)}`,
    locale: locale as MetaLocale,
  });
}

/** Session-scoped, per-channel similarity neighbors from the winning batch's
 *  similarity summaries. Cross-batch similarity is invalid (different fixtures),
 *  so this is strictly within the winning batch. Trimmed to ≤5 nearest + ≤5
 *  farthest per channel to keep the client payload small. */
function computeNeighbors(
  batchId: string,
  configId: string,
  labelOf: (id: string) => string,
): NeighborData {
  let peerCount = 0;
  try {
    peerCount = Math.max(0, listConfigIds(batchId).length - 1);
  } catch {
    peerCount = 0;
  }

  const variants: Record<string, Record<string, Array<{ configId: string; label: string; value: number }>>> = {};
  let anyData = false;

  for (const variant of VARIANTS) {
    let summary;
    try {
      summary = loadSimilaritySummary(batchId, variant);
    } catch {
      continue; // no similarity for this session/variant — degrade to note
    }
    const byChannel: Record<string, Array<{ configId: string; label: string; value: number }>> = {};
    for (const cell of summary.cells) {
      if (cell.kind !== "cross") continue;
      const isA = cell.config_a === configId;
      const isB = cell.config_b === configId;
      if (!isA && !isB) continue;
      const other = isA ? cell.config_b : cell.config_a;
      if (!isSufficient({ kind: cell.kind, n_eff: cell.n_eff, m_a: cell.m_a, m_b: cell.m_b, median: cell.median })) {
        continue;
      }
      const value = cell.median as number;
      (byChannel[cell.channel] ??= []).push({ configId: other, label: labelOf(other), value });
    }
    // sort desc + trim to head5 ∪ tail5
    for (const ch of Object.keys(byChannel)) {
      const sorted = byChannel[ch].sort((x, y) => y.value - x.value);
      if (sorted.length > 10) {
        byChannel[ch] = [...sorted.slice(0, 5), ...sorted.slice(-5)];
      } else {
        byChannel[ch] = sorted;
      }
      anyData = true;
    }
    variants[variant] = byChannel;
  }

  return { peerCount, hasSimilarity: anyData, variants: anyData ? variants : null };
}

export default async function CardPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id: rawId } = await params;
  setRequestLocale(locale);
  const configId = decodeURIComponent(rawId);
  const t = await getTranslations({ locale, namespace: "card" });

  const reg = loadRegistry();
  const entry = reg.byId.get(configId);

  if (!entry) {
    return (
      <div className="wrap" style={{ paddingBlock: "48px" }}>
        <h1 className="mono" style={{ fontSize: 22, marginBottom: 10 }}>{t("notFound.title")}</h1>
        <p className="dim">{t("notFound.message")}</p>
      </div>
    );
  }

  const { batchId, config, facets } = entry;
  const label = configLabel(config);

  // ---- provenance: captured ts from the winning batch ----
  let capturedAt: string | null = null;
  try {
    capturedAt = getBatchEntry(batchId).created_at;
  } catch {
    capturedAt = null;
  }

  // ---- weather snapshot (offline live) + fixture display params ----
  let snapshot: WeatherSnapshot | null = null;
  try {
    snapshot = loadWeatherSnapshot(batchId);
  } catch {
    snapshot = null;
  }
  const loc = snapshot?.locations?.find((l) => l.endpoint === "forecast") ?? snapshot?.locations?.[0] ?? null;
  const fixture: LiveFixture | null = loc
    ? { lat: String(loc.lat), lon: String(loc.lon), name: loc.city, date: loc.date }
    : null;

  // ---- slots ----
  const slots: CardSlot[] = [];
  for (const ref of entry.slots) {
    const { variant, index, state } = ref;
    const hasHtml = exists(slotCardHtmlPath(batchId, configId, variant, index));
    let highlighted: string | null = null;
    let sourceLines: number | null = null;
    let sourceBytes: number | null = null;
    if (hasHtml) {
      try {
        const raw = fs.readFileSync(slotCardHtmlPath(batchId, configId, variant, index), "utf8");
        highlighted = await highlight(raw, "html");
        sourceLines = raw.split("\n").length;
        sourceBytes = Buffer.byteLength(raw, "utf8");
      } catch {
        highlighted = null;
      }
    }

    // patch info (per-slot). Metrics still refer to the original (invariant #2).
    let patch: PatchInfo | null = null;
    const note = loadPatch(batchId, configId, variant, index);
    if (note && ref.hasPatch) {
      const vdir = variant === "P-min" ? "min" : "q";
      patch = {
        category: note.category,
        reasonEn: note.reason_en,
        reasonZh: note.reason_zh,
        patchedBy: note.patched_by,
        ts: note.ts,
        patchedUrl: patchedCardHtmlUrl(batchId, configId, variant, index),
        diffUrl: `/b/${batchId}/${configId}/${vdir}/${index}/${note.diff_file}`,
      };
    }

    const shots = state === "valid" ? slotAssets(batchId, configId, variant, index) : null;
    slots.push({
      variant,
      index,
      state,
      shotUrl: shots?.shot ?? null,
      thumbUrl: shots?.thumb ?? null,
      cardUrl: hasHtml ? cardHtmlUrl(batchId, configId, variant, index) : null,
      highlightedSource: highlighted,
      sourceLines,
      sourceBytes,
      l1: ref.state === "valid" ? slotL1(batchId, configId, variant, index) : null,
      fidelity: ref.state === "valid" ? slotFidelity(batchId, configId, variant, index) : null,
      telemetry: slotTelemetry(batchId, configId, variant, index),
      patch,
    });
  }

  const validTotal = entry.validCount.min + entry.validCount.q;
  const isAllFailed = validTotal === 0;

  // ---- neighbors (session-scoped) ----
  const labelOf = (id: string): string => {
    const peer = reg.byId.get(id);
    if (peer) return configLabel(peer.config);
    try {
      return configLabel(loadConfig(batchId, id));
    } catch {
      return id;
    }
  };
  const neighbors = computeNeighbors(batchId, configId, labelOf);

  // ---- raw config.json + send-log (collapsible viewers) ----
  let rawConfigHtml: string | null = null;
  let rawConfigBytes: number | null = null;
  try {
    const cfgJson = JSON.stringify(config, null, 2);
    rawConfigBytes = Buffer.byteLength(cfgJson, "utf8");
    rawConfigHtml = await highlight(cfgJson, "json");
  } catch {
    rawConfigHtml = null;
  }
  let sendLogHtml: string | null = null;
  let sendLogBytes: number | null = null;
  try {
    const sl = loadSendLog(batchId, configId);
    const slJson = JSON.stringify(sl, null, 2);
    sendLogBytes = Buffer.byteLength(slJson, "utf8");
    sendLogHtml = await highlight(slJson, "json");
  } catch {
    sendLogHtml = null;
  }

  const data: CardViewData = {
    configId,
    label,
    family: facets.family,
    modelId: facets.modelId,
    effort: facets.effort,
    arm: facets.arm,
    grade: facets.grade,
    protocol: facets.protocol,
    transport: facets.transport,
    billing: facets.billing,
    servedModel: config.served_model,
    batchId,
    batches: entry.batches,
    capturedAt,
    N: config.N,
    slots,
    snapshot,
    fixture,
    neighbors,
    channels: CHANNELS as unknown as Channel[],
    rawConfigHtml,
    rawConfigBytes,
    sendLogHtml,
    sendLogBytes,
    isAllFailed,
  };

  return <CardView data={data} />;
}

// --- narrow slot-meta reads (registry already validated slot states) ---
function slotL1(b: string, c: string, v: Variant, k: number): L1 | null {
  try {
    return loadSlotMeta(b, c, v, k).l1;
  } catch {
    return null;
  }
}
function slotFidelity(b: string, c: string, v: Variant, k: number): Fidelity | null {
  try {
    return loadSlotMeta(b, c, v, k).fidelity;
  } catch {
    return null;
  }
}
function slotTelemetry(b: string, c: string, v: Variant, k: number): SlotTelemetry {
  try {
    return loadSlotMeta(b, c, v, k).telemetry;
  } catch {
    return {
      completion_tokens: 0,
      prompt_tokens: 0,
      total_tokens: 0,
      wall_ms: 0,
      cost_usd: null,
      request_id: null,
    };
  }
}
