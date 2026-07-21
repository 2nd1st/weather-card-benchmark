import fs from "node:fs";
import path from "node:path";

import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import { buildMeta, type MetaLocale } from "@/lib/metadata";
import { loadRegistry, type RegistryEntry } from "@/lib/registry";
import { assetUrl, batchDir } from "@/lib/paths";
import { getDb } from "@/lib/db";
import { StatsStrip } from "@/app/components/StatsStrip";
import { Section } from "@/app/components/Section";
import { CardTile, type VariantCount } from "@/app/components/CardTile";
import { HeroExhibit } from "@/app/components/HeroExhibit";
import { PromptPanel } from "@/app/components/PromptPanel";
import { readPrompts } from "@/lib/prompt";
import { GateFlow, type GateStrings } from "./arena/_components/GateFlow";
import styles from "./arena/arena.module.css";

// Landing '/' (spec §4 '/', gate per §4.0). Real SSR landing; the arena gate is
// an OVERLAY (GateFlow) above it — never a redirect, so crawlers/OG unfurlers
// and deep links see real content. force-dynamic: newly landed batches + live
// vote counts appear without a rebuild (§1 rendering policy).

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "home" });
  return buildMeta({
    title: t("meta.title"),
    description: t("meta.description"),
    path: "/",
    locale: locale as MetaLocale,
  });
}

// ---- community vote count (partial-data safe: DB may be empty/absent) ----
function communityVoteCount(): number {
  try {
    const db = getDb();
    const r = db.prepare("SELECT COUNT(*) AS n FROM votes").get() as { n: number };
    return r?.n ?? 0;
  } catch {
    return 0;
  }
}

function hasValid(e: RegistryEntry): boolean {
  return e.validCount.min + e.validCount.q > 0;
}

const STRIP_N = 6;
/** Below this many votes the community ranking is noise, so the homepage keeps
 *  showing "newest" instead of a leaderboard built on a handful of clicks. */
const RANK_MIN_VOTES = 200;

/**
 * When each config entered the benchmark, read from the `first-seen.json`
 * sidecar that runner/tools/write_first_seen.py freezes into the unified batch.
 *
 * It must be a sidecar, not something derived here: the date comes from the
 * DATED source batch ids, and production ships ONLY the unified batch (source
 * batches are not publishable). Deriving it at read time worked on a dev
 * machine and silently produced "" for every config in prod — the strip then
 * ordered arbitrarily while still calling itself "newest".
 */
function loadFirstSeen(): Map<string, string> {
  const map = new Map<string, string>();
  try {
    const raw = fs.readFileSync(
      path.join(batchDir("2026-07-19--unified"), "first-seen.json"),
      "utf8",
    );
    for (const [k, v] of Object.entries(JSON.parse(raw) as Record<string, string>)) {
      map.set(k, v);
    }
  } catch {
    /* sidecar absent → every config dates to "", order falls back to catalog */
  }
  return map;
}

/** model_id → public RELEASE date (YYYY-MM-DD). The "newest" strip is meant to
 *  surface the newest MODELS in the field, not the last thing we happened to
 *  measure — so ordering is by when the model shipped, not our first-seen date
 *  (2026-07-20). Sidecar `model-release-dates.json`; a model absent from it
 *  has no known release date and is dropped from the strip (see pickLatest). */
function loadReleaseDates(): Map<string, string> {
  const map = new Map<string, string>();
  try {
    const raw = fs.readFileSync(
      path.join(batchDir("2026-07-19--unified"), "model-release-dates.json"),
      "utf8",
    );
    for (const [k, v] of Object.entries(JSON.parse(raw) as Record<string, string>)) {
      if (typeof v === "string" && v) map.set(k, v);
    }
  } catch {
    /* sidecar absent → strip falls back to first-seen ordering */
  }
  return map;
}

/** Newest models first BY RELEASE DATE, ONE entry per model (a model that landed
 *  with five effort tiers should not eat the whole strip). The representative
 *  config is the one we have measured longest; the SORT key is the model's
 *  release date. A model with NO known release date sorts last (empty key) and
 *  drops out of the strip rather than jumping the queue on its measurement date —
 *  the strip is strictly "newest RELEASED", so an undated model is not "new"
 *  here (2026-07-20). Keep the release-dates sidecar current as models ship. */
function pickLatest(
  entries: RegistryEntry[],
  seen: Map<string, string>,
  release: Map<string, string>,
  n: number,
): RegistryEntry[] {
  const pool = entries.filter(hasValid);
  const byModel = new Map<string, { e: RegistryEntry; seenDay: string }>();
  for (const e of pool) {
    const seenDay = seen.get(e.configId) ?? "";
    const cur = byModel.get(e.facets.modelId);
    // representative = the config we've had measured the longest
    if (!cur || (seenDay !== "" && (cur.seenDay === "" || seenDay < cur.seenDay))) {
      byModel.set(e.facets.modelId, { e, seenDay });
    }
  }
  return [...byModel.entries()]
    .map(([modelId, x]) => ({ e: x.e, day: release.get(modelId) ?? "" }))
    .filter((x) => x.day !== "")
    .sort((a, b) => (a.day < b.day ? 1 : a.day > b.day ? -1 : 0))
    .slice(0, n)
    .map((x) => x.e);
}

/** Community-ranked strip: strongest Bradley–Terry first, one per model, and
 *  only entries with enough votes to be worth showing. */
function pickTopVoted(
  entries: RegistryEntry[],
  strength: Map<string, { strength: number | null; insufficient: boolean }>,
  n: number,
): RegistryEntry[] {
  const seen = new Set<string>();
  return entries
    .filter((e) => {
      if (!hasValid(e)) return false;
      const s = strength.get(e.configId);
      if (!s || s.insufficient || s.strength === null) return false;
      if (seen.has(e.facets.modelId)) return false;
      seen.add(e.facets.modelId);
      return true;
    })
    .sort(
      (a, b) =>
        (strength.get(b.configId)?.strength ?? 0) - (strength.get(a.configId)?.strength ?? 0),
    )
    .slice(0, n);
}

function thumbFor(e: RegistryEntry): string | null {
  const valid = e.slots.find((s) => s.state === "valid");
  if (!valid) return null;
  return assetUrl(e.batchId, e.configId, valid.variant, valid.index, "thumb");
}

function countsFor(e: RegistryEntry): VariantCount[] {
  const totals: Record<string, { valid: number; total: number }> = {
    "P-min": { valid: e.validCount.min, total: 0 },
    "P-q": { valid: e.validCount.q, total: 0 },
  };
  for (const s of e.slots) totals[s.variant].total += 1;
  return (["P-min", "P-q"] as const)
    .filter((v) => totals[v].total > 0)
    .map((v) => ({ variant: v, valid: totals[v].valid, total: totals[v].total }));
}

export default async function LandingPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "home" });

  const registry = loadRegistry();
  const votes = communityVoteCount();

  // The strip used to be a deterministic daily shuffle. It now answers a real
  // question instead: what landed most recently — and, once the community has
  // voted enough for a ranking to mean anything, what they actually preferred.
  const latest = pickLatest(registry.entries, loadFirstSeen(), loadReleaseDates(), STRIP_N);
  let ranked: RegistryEntry[] = [];
  if (votes >= RANK_MIN_VOTES) {
    try {
      const { stats: arenaStats } = await import("@/lib/arena");
      const s = arenaStats("overall", "P-min");
      const byId = new Map(
        s.items.map((it) => [
          it.configId,
          { strength: it.strength, insufficient: it.insufficient },
        ]),
      );
      ranked = pickTopVoted(registry.entries, byId, STRIP_N);
    } catch {
      /* vote db unavailable → the newest strip alone still carries the page */
    }
  }

  // hero exhibit: up to 3 shots from whatever the lead strip is showing
  const heroShots = (ranked.length > 0 ? ranked : latest)
    .slice(0, 3)
    .map(thumbFor)
    .filter((u): u is string => Boolean(u));

  const prompts = readPrompts();

  const stats = [
    { value: registry.totals.models, label: t("stats.models") },
    { value: registry.totals.configs, label: t("stats.configs") },
    { value: registry.totals.validSlots, label: t("stats.validCards") },
    { value: votes, label: t("stats.votes") },
  ];

  const gateStrings: GateStrings = {
    title: t("gate.title"),
    sub: t("gate.sub"),
    skip: t("gate.skip"),
    metaAngle: t("gate.metaAngle"),
    metaVariant: t("gate.metaVariant"),
    metaCity: t("gate.metaCity"),
    metaFixture: t("gate.metaFixture"),
    nextPick: t("gate.nextPick"),
    continueLabel: t("gate.continueLabel"),
    pair: {
      cardA: t("gate.pair.cardA"),
      cardB: t("gate.pair.cardB"),
      identityHidden: t("gate.pair.identityHidden"),
      preferA: t("gate.pair.preferA"),
      preferB: t("gate.pair.preferB"),
      tie: t("gate.pair.tie"),
      tieSub: t("gate.pair.tieSub"),
      bothBad: t("gate.pair.bothBad"),
      bothBadSub: t("gate.pair.bothBadSub"),
      revealTitle: t("gate.pair.revealTitle"),
      yourPick: t("gate.pair.yourPick"),
      tooFast: t("gate.pair.tooFast"),
      voteError: t("gate.pair.voteError"),
      loading: t("gate.pair.loading"),
      noOutput: t("gate.pair.noOutput"),
    },
    unlock: {
      title: t("gate.unlock.title"),
      body: t("gate.unlock.body"),
      enter: t("gate.unlock.enter"),
      shareLabel: t("gate.unlock.shareLabel"),
      copy: t("gate.unlock.copy"),
      copied: t("gate.unlock.copied"),
      x: t("gate.unlock.x"),
      text: t("gate.unlock.text"),
    },
  };

  return (
    <div className="wrap">
      <section className={`hero${heroShots.length > 0 ? " hero-split" : ""}`}>
        <div className="hero-lede">
          <div className="kicker">{t("hero.kicker")}</div>
          <h1 className="hero-title">
            {t("hero.title1")}
            <br />
            {t("hero.title2")}
            <br />
            <span className="glow">{t("hero.title3")}</span>
          </h1>
          <p className="hero-sub">{t("hero.sub")}</p>
          <div className="hero-cta">
            <Link className="btn btn-hero" href="/arena">
              {t("hero.ctaArena")} →
            </Link>
            <Link className="btn btn-ghost" href="/methodology">
              {t("hero.ctaMethodology")}
            </Link>
          </div>
        </div>

        <HeroExhibit shots={heroShots} />

        <StatsStrip
          stats={stats}
          ariaLabel={t("stats.aria")}
          scopeNote={t("stats.scopeNote")}
        />
      </section>

      {/* the task itself, up top — the reader sees WHAT every model was asked
          before seeing how they answered (2026-07-20). */}
      {prompts.length > 0 ? (
        <Section title={t("prompt.title")} subtitle={t("prompt.caption")}>
          <PromptPanel
            docs={prompts}
            compact
            labels={{
              heading: t("prompt.heading"),
              note: "",
              download: t("prompt.download"),
            }}
          />
        </Section>
      ) : null}

      {/* community ranking leads once it is statistically worth leading with */}
      {ranked.length > 0 ? (
        <Section title={t("ranked.title")} subtitle={t("ranked.caption")}>
          <div className={styles.featuredScroll}>
            {ranked.map((e) => (
              <CardTile
                key={e.configId}
                configId={e.configId}
                modelId={e.facets.modelId}
                effort={e.facets.effort}
                arm={e.facets.arm}
                grade={e.facets.grade}
                patched={e.slots.some((s) => s.hasPatch)}
                thumbUrl={thumbFor(e)}
                counts={countsFor(e)}
              />
            ))}
          </div>
        </Section>
      ) : null}

      {latest.length > 0 ? (
        <Section title={t("latest.title")} subtitle={t("latest.caption")}>
          <div className={styles.featuredScroll}>
            {latest.map((e) => (
              <CardTile
                key={e.configId}
                configId={e.configId}
                modelId={e.facets.modelId}
                effort={e.facets.effort}
                arm={e.facets.arm}
                grade={e.facets.grade}
                patched={e.slots.some((s) => s.hasPatch)}
                thumbUrl={thumbFor(e)}
                counts={countsFor(e)}
              />
            ))}
          </div>
        </Section>
      ) : null}

      <GateFlow strings={gateStrings} />
    </div>
  );
}
