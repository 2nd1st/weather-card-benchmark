import type { Metadata } from "next";
import { setRequestLocale, getTranslations } from "next-intl/server";
import { buildMeta, type MetaLocale } from "@/lib/metadata";
import { Section } from "@/app/components/Section";
import { decodeCells } from "../progress/cells";
import { ContributeForm } from "./ContributeForm";

// /contribute — cells tray → estimate → submit (spec §4, §3.3). force-dynamic:
// reads the WCB_CONTRIBUTE_ENABLED flag and the incoming ?cells query per
// request. The pipeline is flag-gated; when off the form shows the run-locally
// & PR path (ContributeForm.BetaBlock).

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "contribute" });
  return buildMeta({
    title: `${t("meta.title")} · weather-card-benchmark`,
    description: t("meta.description"),
    path: "/contribute",
    locale: locale as MetaLocale,
  });
}

export default async function ContributePage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ cells?: string | string[] }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "contribute" });

  const sp = await searchParams;
  const cells = decodeCells(sp.cells);
  // Mirrors lib/contribute.contributeEnabled() — the pipeline runs only when the
  // flag is explicitly on; otherwise the beta / run-locally path is shown.
  const enabled = process.env.WCB_CONTRIBUTE_ENABLED === "1";

  return (
    // bare `.wrap` — this is an interactive/form surface, so it rides the shared
    // grid (nav + footer gutters), not the prose reading measure. See globals.css
    // CONTAINER RULE.
    <div className="wrap">
      <Section title={t("title")} subtitle={t("subtitle")}>
        <ContributeForm initialCells={cells} enabled={enabled} />
      </Section>
    </div>
  );
}
