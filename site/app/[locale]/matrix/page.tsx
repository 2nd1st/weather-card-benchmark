import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import {
  listBatches,
  getBatchEntry,
  loadManifest,
  listConfigIds,
  loadConfig,
  loadSlots,
  slotAssets,
  VARIANTS,
  CHANNELS,
  type Variant,
} from "@/lib/data";
import { summaryPath, exists } from "@/lib/paths";
import { sortSlugs } from "@/lib/neutral";
import { configLabel, armOf } from "@/lib/label";
import { channelGradeOf, armGroupOf } from "@/lib/channel";
import { buildMeta, type MetaLocale } from "@/lib/metadata";
import { Section } from "@/app/components/Section";
import { EmptyState } from "@/app/components/EmptyState";
import { MatrixView, type MatrixSession, type MatrixConfig } from "./MatrixView";

// /matrix (spec §4 matrix, ported from app/matrix). SERVER half: enumerates
// every MEASUREMENT SESSION — a batch that carries similarity data — and bakes
// each session's config roster (slug-ordered, with per-config grade/arm/variant
// facets + thumbs). The client MatrixView draws the canvas heatmap, the 17
// small-multiple channel switcher, the session selector, grade/arm/variant
// filters, and the cell-click pair drawer; it fetches the (large) similarity
// summary + slot-pair JSON client-side from /b/ so no giant cell payload ships
// and a session switch re-derives everything.
//
// The `primaryBatchId()` concept is dead: there is no cross-batch matrix.
// Similarity pairs are computed per batch against ONE fixture, so cross-batch
// numbers are not comparable — the session selector is the only axis that moves.

export const revalidate = 300;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "matrix" });
  return buildMeta({
    title: t("meta.title"),
    description: t("meta.description"),
    path: "/matrix",
    locale: locale as MetaLocale,
  });
}

/** Which variants of a batch have a materialized similarity summary on disk. */
function sessionVariants(batchId: string): Variant[] {
  return VARIANTS.filter((v) => {
    try {
      return exists(summaryPath(batchId, v));
    } catch {
      return false;
    }
  });
}

/** Build one session's config roster from the batch's own manifest + configs. */
function buildSession(batchId: string): MatrixSession | null {
  let variants: Variant[];
  try {
    variants = sessionVariants(batchId);
  } catch {
    variants = [];
  }
  if (variants.length === 0) return null;

  let entry;
  try {
    entry = getBatchEntry(batchId);
  } catch {
    return null;
  }

  let ids: string[];
  try {
    ids = sortSlugs(listConfigIds(batchId));
  } catch {
    ids = [];
  }

  let city = "—";
  try {
    const cities = loadManifest(batchId).cities.map((c) => c.city);
    if (cities.length) city = cities.length === 1 ? cities[0] : cities.join(" · ");
  } catch {
    /* keep placeholder */
  }

  const configs: MatrixConfig[] = [];
  for (const id of ids) {
    let c;
    try {
      c = loadConfig(batchId, id);
    } catch {
      continue; // unreadable config — skip (partial-data safe, rule #4)
    }
    const thumb: { "P-min": string | null; "P-q": string | null } = {
      "P-min": null,
      "P-q": null,
    };
    for (const v of VARIANTS) {
      let rep: { slot_index: number } | undefined;
      try {
        rep = loadSlots(batchId, id, v).find((sl) => sl.state === "valid");
      } catch {
        rep = undefined;
      }
      thumb[v] = rep ? slotAssets(batchId, id, v, rep.slot_index).thumb : null;
    }
    configs.push({
      id,
      label: configLabel({
        config_id: id,
        model_id: c.model_id,
        effort: c.effort ?? null,
        protocol: c.protocol,
      }),
      family: c.family,
      effort: c.effort ?? null,
      grade: channelGradeOf(c.transport, c.config_id),
      arm: armOf(c.config_id, c.protocol),
      // api-shaped vs harness-shaped arm (protocol "app" folds into harness). Baked
      // server-side so the client filter needs no config-record access.
      armGroup: armGroupOf(c.protocol),
      thumb,
    });
  }
  if (configs.length === 0) return null;

  const grades = [...new Set(configs.map((c) => c.grade))];
  const arms = [...new Set(configs.map((c) => c.arm))].sort();

  return {
    batchId,
    event: entry.event,
    date: entry.date,
    city,
    variants,
    configs,
    arms,
    grades,
  };
}

export default async function MatrixPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "matrix" });

  // Every readable batch that carries similarity data is a session.
  let sessions: MatrixSession[] = [];
  try {
    sessions = listBatches()
      .map((b) => buildSession(b.id))
      .filter((s): s is MatrixSession => s != null);
  } catch {
    sessions = [];
  }

  if (sessions.length === 0) {
    return (
      <div className="wrap">
        <Section etched tag={t("tag")} num={t("num")} title={t("title")} subtitle={t("subtitle")}>
          <EmptyState title={t("session.none")} />
        </Section>
      </div>
    );
  }

  // Default session (§4 matrix): pick the session with the MOST configs passing
  // the DEFAULT filter (grade = qualified) so the first paint is coherent rather
  // than colliding a dev-heavy session with a qualified-only filter. Tie-break by
  // total config count, then batch id (deterministic). When no session carries
  // qualified configs the client shows an explicit "N of M · reset" notice.
  const qualifiedCount = (s: MatrixSession) =>
    s.configs.filter((c) => c.grade === "qualified").length;
  const defaultBatchId = [...sessions].sort(
    (a, b) =>
      qualifiedCount(b) - qualifiedCount(a) ||
      b.configs.length - a.configs.length ||
      (a.batchId < b.batchId ? 1 : -1),
  )[0].batchId;

  return (
    <div className="wrap">
      <Section etched tag={t("tag")} num={t("num")} title={t("title")} subtitle={t("subtitle")}>
        <MatrixView
          sessions={sessions}
          defaultBatchId={defaultBatchId}
          channels={[...CHANNELS]}
        />
      </Section>
    </div>
  );
}
