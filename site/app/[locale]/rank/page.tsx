import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";

import { Link } from "@/i18n/navigation";
import { buildMeta, type MetaLocale } from "@/lib/metadata";
import { loadRegistry } from "@/lib/registry";
import type { LabelInfo } from "../arena/_components/Leaderboard";
import { RankBoard } from "./RankBoard";

// Owned here (server side) on purpose — see the note in RankBoard.tsx.
const ANGLES = ["overall", "visual", "clarity"] as const;

// Community ranking, on its own page. Everything here is UI PREFERENCE from
// blind pairwise votes — deliberately not a capability score, and the copy says
// so up front so the number is never mistaken for a benchmark result.

export const revalidate = 60;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "rank" });
  return buildMeta({
    title: t("meta.title"),
    description: t("meta.description"),
    path: "/rank",
    locale: locale as MetaLocale,
  });
}

/** configId → display label parts; the stats API returns configIds only. */
function buildLabelMap(): Record<string, LabelInfo> {
  const map: Record<string, LabelInfo> = {};
  try {
    for (const e of loadRegistry().entries) {
      map[e.configId] = {
        modelId: e.facets.modelId,
        effort: e.facets.effort,
        arm: e.facets.arm,
      };
    }
  } catch {
    /* degrade to configId labels */
  }
  return map;
}

export default async function RankPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "rank" });
  const ta = await getTranslations({ locale, namespace: "arena" });

  const labels = buildLabelMap();
  const angleLabels: Record<string, string> = {};
  for (const a of ANGLES) angleLabels[a] = ta(`angle.${a}`);

  return (
    <div className="wrap">
      <header className="pagehead">
        <h1 className="h1">{t("title")}</h1>
        <p className="lead">{t("lead")}</p>
        <p className="note">
          {t("caveat")}{" "}
          <Link href="/methodology#voting">{t("methodLink")}</Link>
        </p>
      </header>

      <RankBoard
        angles={ANGLES}
        labels={labels}
        angleLabels={angleLabels}
        angleLabel={t("angleLabel")}
        strings={{
          title: ta("leaderboard.title"),
          framing: ta("leaderboard.framing"),
          gameability: ta("leaderboard.gameability"),
          csv: ta("leaderboard.csv"),
          insufficientGroup: ta("leaderboard.insufficientGroup"),
          insufficientSummary: ta("leaderboard.insufficientSummary"),
          insufficientShow: ta("leaderboard.insufficientShow"),
          insufficientHide: ta("leaderboard.insufficientHide"),
          orderPref: ta("leaderboard.orderPref"),
          orderNeutral: ta("leaderboard.orderNeutral"),
          variantPqTip: ta("leaderboard.variantPqTip"),
          variantPminTip: ta("leaderboard.variantPminTip"),
          empty: ta("leaderboard.empty"),
          emptyMsg: ta("leaderboard.emptyMsg"),
          colVoters: ta("leaderboard.colVoters"),
          attributionExtra: ta("leaderboard.attributionExtra"),
        }}
      />
    </div>
  );
}
