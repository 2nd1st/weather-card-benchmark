import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { buildMeta, type MetaLocale } from "@/lib/metadata";
import { loadRegistry } from "@/lib/registry";
import { Section } from "@/app/components/Section";
import { ArenaBoard, type ArenaStrings } from "./_components/ArenaBoard";
import type { LabelInfo } from "./_components/Leaderboard";

// Arena hub '/arena' (spec §4). Never gated (deep-link/open route, §4.0).
// force-dynamic — pairs and stats are live server state.

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "arena" });
  return buildMeta({
    title: t("meta.title"),
    description: t("meta.description"),
    path: "/arena",
    locale: locale as MetaLocale,
  });
}

/** configId → display label parts, so the leaderboard can name rows the arena
 *  API returns by configId only. Built from the registry (partial-data safe). */
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

export default async function ArenaPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "arena" });
  const labels = buildLabelMap();

  const strings: ArenaStrings = {
    angleLabels: {
      overall: t("angle.overall"),
      visual: t("angle.visual"),
      clarity: t("angle.clarity"),
    },
    metaVariant: t("metaVariant"),
    metaCity: t("metaCity"),
    metaFixture: t("metaFixture"),
    metaIdentical: t("metaIdentical"),
    tally: t("tally"),
    attributionExtra: t("attributionExtra"),
    pair: {
      cardA: t("pair.cardA"),
      cardB: t("pair.cardB"),
      identityHidden: t("pair.identityHidden"),
      preferA: t("pair.preferA"),
      preferB: t("pair.preferB"),
      tie: t("pair.tie"),
      tieSub: t("pair.tieSub"),
      bothBad: t("pair.bothBad"),
      bothBadSub: t("pair.bothBadSub"),
      revealTitle: t("pair.revealTitle"),
      yourPick: t("pair.yourPick"),
      tooFast: t("pair.tooFast"),
      voteError: t("pair.voteError"),
      loading: t("pair.loading"),
      noOutput: t("pair.noOutput"),
    },
    reveal: {
      next: t("reveal.next"),
      share: t("reveal.share"),
      compare: t("reveal.compare"),
    },
    share: {
      permalinkLabel: t("share.permalinkLabel"),
      copy: t("share.copy"),
      copied: t("share.copied"),
      x: t("share.x"),
      text: t("share.text"),
    },
    unavailableTitle: t("unavailableTitle"),
    unavailableMsg: t("unavailableMsg"),
    leaderboard: {
      title: t("leaderboard.title"),
      framing: t("leaderboard.framing"),
      gameability: t("leaderboard.gameability"),
      csv: t("leaderboard.csv"),
      insufficientGroup: t("leaderboard.insufficientGroup"),
      insufficientSummary: t("leaderboard.insufficientSummary"),
      insufficientShow: t("leaderboard.insufficientShow"),
      insufficientHide: t("leaderboard.insufficientHide"),
      orderPref: t("leaderboard.orderPref"),
      orderNeutral: t("leaderboard.orderNeutral"),
      variantPqTip: t("leaderboard.variantPqTip"),
      variantPminTip: t("leaderboard.variantPminTip"),
      empty: t("leaderboard.empty"),
      emptyMsg: t("leaderboard.emptyMsg"),
      colVoters: t("leaderboard.colVoters"),
      attributionExtra: t("leaderboard.attributionExtra"),
    },
  };

  return (
    <div className="wrap">
      <Section title={t("title")} subtitle={t("sub")}>
        <ArenaBoard labels={labels} strings={strings} />
      </Section>
    </div>
  );
}
