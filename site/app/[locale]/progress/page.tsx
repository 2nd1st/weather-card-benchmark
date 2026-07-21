import type { Metadata } from "next";
import { setRequestLocale, getTranslations } from "next-intl/server";
import { buildMeta, type MetaLocale } from "@/lib/metadata";
import { Section } from "@/app/components/Section";
import { buildBoard } from "./board";
import { ProgressBoard } from "./ProgressBoard";

// /progress — coverage board (spec §4). force-dynamic: it reads the live data
// root (registry + seed files) on a running server; static prerendering would
// freeze the board (rendering policy table §1). Everything is derived from the
// matrix plan + harness-plan + registry — no hardcoded model lists.

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "progress" });
  return buildMeta({
    title: `${t("meta.title")} · weather-card-benchmark`,
    description: t("meta.description"),
    path: "/progress",
    locale: locale as MetaLocale,
  });
}

export default async function ProgressPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "progress" });

  // Partial data is normal (non-negotiable #4): buildBoard degrades to an empty
  // board rather than throwing when seeds/registry are absent.
  let board;
  try {
    board = buildBoard();
  } catch {
    board = null;
  }

  return (
    <div className="wrap">
      <Section title={t("title")}>
        {board ? (
          <ProgressBoard data={board} />
        ) : (
          <p className="sec-sub">{t("empty.message")}</p>
        )}
      </Section>
    </div>
  );
}
