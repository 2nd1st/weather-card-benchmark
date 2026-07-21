import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { buildMeta, type MetaLocale } from "@/lib/metadata";
import { issueFromShare, type IssuedPair } from "@/lib/arena";
import { Section } from "@/app/components/Section";
import { EmptyState } from "@/app/components/EmptyState";
import { Link } from "@/i18n/navigation";
import { SharedMatchup, type SharedStrings } from "../../_components/SharedMatchup";
import type { ClientPair } from "../../_components/voteClient";

// Shared-matchup '/arena/m/[id]' (spec §4, §3.2). The SERVER issues a FRESH
// blind pair from the share's refs (source='share') so the recipient sees the
// same matchup BLIND, then votes → reveal → into the arena. Never gated;
// force-dynamic (each visit mints a fresh, single-use pair). OG image is the
// UNLABELED composite of both shots (blind preserved in the preview).

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}): Promise<Metadata> {
  const { locale, id } = await params;
  const t = await getTranslations({ locale, namespace: "arena" });
  return buildMeta({
    title: t("matchup.metaTitle"),
    description: t("matchup.metaDescription"),
    image: `/api/og/compare?share=${encodeURIComponent(id)}`,
    path: `/arena/m/${id}`,
    locale: locale as MetaLocale,
  });
}

export default async function SharedMatchupPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "arena" });

  let pair: IssuedPair | null = null;
  try {
    pair = await issueFromShare(id, "overall");
  } catch {
    pair = null; // share not found / bad payload / pool unavailable
  }

  if (!pair) {
    return (
      <div className="wrap">
        <Section title={t("matchup.expiredTitle")}>
          <EmptyState
            title={t("matchup.expiredTitle")}
            message={t("matchup.expiredMsg")}
            icon="◌"
            action={
              <Link className="btn btn-hero" href="/arena">
                {t("matchup.toArena")} →
              </Link>
            }
          />
        </Section>
      </div>
    );
  }

  const clientPair: ClientPair = {
    token: pair.token,
    mode: "pair",
    angle: pair.angle,
    variant: pair.variant,
    city: pair.city,
    date: pair.date,
    cards: pair.cards,
  };

  const strings: SharedStrings = {
    toArena: t("matchup.toArena"),
    compare: t("matchup.compare"),
    shareAgain: t("matchup.shareAgain"),
    copy: t("matchup.copy"),
    copied: t("matchup.copied"),
    x: t("matchup.x"),
    shareText: t("matchup.shareText"),
    attributionExtra: t("matchup.attributionExtra"),
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
  };

  return (
    <div className="wrap">
      <Section title={t("matchup.title")} subtitle={t("matchup.sub")}>
        <SharedMatchup pair={clientPair} shareId={id} strings={strings} />
      </Section>
    </div>
  );
}
