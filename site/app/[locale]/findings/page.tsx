import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import { buildMeta } from "@/lib/metadata";
import { EmptyState } from "@/app/components/EmptyState";
import { loadRegistry } from "@/lib/registry";
import { loadFindings, type FindingItem } from "./findings-data";
import styles from "./findings.module.css";

// /findings (spec §4, §1 rendering policy). Observation cards from
// data/findings.json — descriptive measurements, each linked to the raw data
// that produced it. Seed-or-degrade: absent/empty file → EmptyState and the nav
// item is already hidden by the shell (§7.8). ISR: revalidate 300 (§1 table).
export const revalidate = 300;

type LocaleParam = { locale: string };

function loc(l: string): "en" | "zh" {
  return l === "zh" ? "zh" : "en";
}

export async function generateMetadata({
  params,
}: {
  params: Promise<LocaleParam>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "findings" });
  return buildMeta({
    title: t("meta.title"),
    description: t("meta.description"),
    path: "/findings",
    locale: loc(locale),
  });
}

/** Registry families, defensively (used only to derive a gallery filter link). */
function familyList(): string[] {
  try {
    return loadRegistry().facets.families;
  } catch {
    return [];
  }
}

/** Best-effort gallery filter for a finding: the first registry family whose
 *  token appears in the finding's English text. Degrades to an unfiltered
 *  gallery link when nothing matches — never a hardcoded model list. */
function galleryFamily(f: FindingItem, families: string[]): string | null {
  const hay = `${f.title_en} ${f.body_en}`.toLowerCase();
  for (const fam of families) {
    const tok = fam.toLowerCase();
    if (tok.length >= 2 && hay.includes(tok)) return fam;
  }
  return null;
}

/** Concise semantic label for a run/batch id — drop the leading YYYY-MM-DD-- so
 *  the channel/model reads first (audit item 10). Full id stays in the link
 *  title/href for copy + provenance anchoring. */
function sourceLabel(id: string): string {
  return id.replace(/^\d{4}-\d{2}-\d{2}--/, "");
}

function fmtDate(value: string | undefined, locale: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(d);
  } catch {
    return value;
  }
}

export default async function FindingsPage({
  params,
}: {
  params: Promise<LocaleParam>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("findings");
  const L = loc(locale);

  const doc = loadFindings();
  const findings = doc?.findings ?? [];

  if (findings.length === 0) {
    return (
      <div className="wrap measure">
        <div className={styles.page}>
          <div className={styles.head}>
            <div className={styles.kick}>weather-card-benchmark</div>
            <h1 className={styles.title}>{t("title")}</h1>
          </div>
          <div style={{ marginTop: "clamp(28px,4vw,44px)" }}>
            <EmptyState
              title={t("empty.title")}
              message={t("empty.message")}
              action={
                <Link className="btn btn-ghost" href="/gallery">
                  {t("empty.action")}
                </Link>
              }
            />
          </div>
        </div>
      </div>
    );
  }

  const families = familyList();
  const note = L === "zh" ? doc?.note_zh || doc?.note_en : doc?.note_en;
  const updated = fmtDate(doc?.updated, locale);

  return (
    <div className="wrap measure">
      <div className={styles.page}>
        <header className={styles.head}>
          <div className={styles.kick}>weather-card-benchmark</div>
          <h1 className={styles.title}>{t("title")}</h1>
          <p className={styles.lead}>{note || t("lead")}</p>
          <div className={styles.metaline}>
            <span className="mono">{t("count", { n: findings.length })}</span>
            {updated ? <span className="mono">{t("updated", { date: updated })}</span> : null}
          </div>
        </header>

        <div className={styles.list}>
          {findings.map((f, i) => {
            const title = L === "zh" ? f.title_zh || f.title_en : f.title_en;
            const body = L === "zh" ? f.body_zh || f.body_en : f.body_en;
            const caveat = L === "zh" ? f.caveat_zh || f.caveat_en : f.caveat_en;
            const fam = galleryFamily(f, families);
            const refs = (f.refs ?? []).filter((r) => typeof r === "string");
            return (
              <article key={f.id} id={f.id} className={styles.card}>
                <div className={styles.cardHead}>
                  <span className={styles.idx}>
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <h2 className={styles.cardTitle}>{title}</h2>
                </div>
                <p className={styles.body}>{body}</p>

                {f.figures && f.figures.length > 0 ? (
                  <div className={styles.figs}>
                    {f.figures.map((fig, j) => (
                      <div key={j} className={styles.fig}>
                        <span className={styles.figLabel}>
                          {L === "zh" ? fig.label_zh || fig.label_en : fig.label_en}
                        </span>
                        <span className={styles.figValue}>{fig.value}</span>
                      </div>
                    ))}
                  </div>
                ) : null}

                {caveat ? (
                  <div className={styles.caveat}>
                    <b>{t("labels.caveat")}</b>
                    <span>{caveat}</span>
                  </div>
                ) : null}

                <div className={styles.foot}>
                  {refs.length > 0 ? (
                    <div className={styles.srcs}>
                      <span className="lbl">{t("labels.sources")}</span>
                      {refs.map((r) => (
                        <Link
                          key={r}
                          className={styles.ref}
                          href={`/methodology#${r}`}
                          title={r}
                        >
                          {sourceLabel(r)}
                        </Link>
                      ))}
                    </div>
                  ) : null}
                  <Link
                    className={styles.browse}
                    href={fam ? `/gallery?family=${encodeURIComponent(fam)}` : "/gallery"}
                  >
                    {fam ? t("labels.browseFamily", { family: fam }) : t("labels.browse")} →
                  </Link>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}
