import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import { marked } from "marked";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import { buildMeta } from "@/lib/metadata";
import { listBatches } from "@/lib/data";
import { listBatchPatches, type PatchEntry } from "@/lib/patch";
import { readPrompts } from "@/lib/prompt";
import { PromptPanel } from "@/app/components/PromptPanel";
import { MethodologyToc } from "./MethodologyToc";
import { ProvenanceTable, type ProvRow } from "./ProvenanceTable";
import styles from "./methodology.module.css";

// /methodology (spec §4). Ported into the new shell + authored sections:
// how similarity is measured, patch policy (§2.3 four invariants verbatim +
// auto-generated patch list), voting math (#voting, §3.2 verbatim), contribute
// zero-trust path, provenance/batches appendix (old /batches lives here, with
// per-batch anchors that /findings refs link into), and an i18n note.
// ISR: revalidate 300 (§1 rendering table). Partial-data safe (#4).
export const revalidate = 300;

type LocaleParam = { locale: string };

/** Rows shown before the provenance ledger's "view all" disclosure (item 11). */
const PROV_RECENT = 6;

function loc(l: string): "en" | "zh" {
  return l === "zh" ? "zh" : "en";
}

export async function generateMetadata({
  params,
}: {
  params: Promise<LocaleParam>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "methodology" });
  return buildMeta({
    title: t("meta.title"),
    description: t("meta.description"),
    path: "/methodology",
    locale: loc(locale),
  });
}

/** Every patch in the data root (auto-list, §2.3). Iterates all batches; dedups
 *  a patch that appears in both a source and its merged copy by slot identity. */
function allPatches(): PatchEntry[] {
  let batchIds: string[] = [];
  try {
    batchIds = listBatches().map((b) => b.id);
  } catch {
    batchIds = [];
  }
  const seen = new Set<string>();
  const out: PatchEntry[] = [];
  for (const id of batchIds) {
    let entries: PatchEntry[] = [];
    try {
      entries = listBatchPatches(id);
    } catch {
      entries = [];
    }
    for (const e of entries) {
      const key = `${e.configId}|${e.variant}|${e.slotIndex}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(e);
    }
  }
  return out.sort((a, b) => (a.configId < b.configId ? -1 : a.configId > b.configId ? 1 : 0));
}

interface Scheme {
  html: string;
  name: string;
}

/** The frozen technical comparison scheme markdown, when present. Resolves via
 *  WCB_SCHEME_MD else the checked-in trial doc; absent → null (deploy trees may
 *  not ship it — the authored sections above are the site-side authority). */
async function loadScheme(): Promise<Scheme | null> {
  const env = process.env.WCB_SCHEME_MD;
  const candidate = env
    ? path.resolve(env)
    : path.resolve(process.cwd(), "..", "trial-20260715", "COMPARISON-SCHEME-v12.md");
  try {
    const md = fs.readFileSync(candidate, "utf8");
    const html = await marked.parse(md, { async: true });
    return { html, name: path.basename(candidate) };
  } catch {
    return null;
  }
}

export default async function MethodologyPage({
  params,
}: {
  params: Promise<LocaleParam>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("methodology");

  const declarations = t.raw("declarations") as string[];
  const layers = t.raw("measure.layers") as { k: string; t: string; d: string }[];
  const invariants = t.raw("patch.invariants") as string[];
  const votingRules = t.raw("voting.rules") as string[];
  const contribRules = t.raw("contribute.rules") as string[];

  const prompts = readPrompts();
  const patches = allPatches();
  const batches = [...listBatchesSafe()].reverse(); // newest-first timeline read
  const provRows: ProvRow[] = batches.map((b) => ({
    id: b.id,
    date: b.date,
    nConfigs: b.n_configs,
    variants: b.variants,
    hash: b.manifest_hash,
  }));
  const scheme = await loadScheme();

  return (
    <div className="wrap measure">
      <div className={styles.page}>
        <header className={styles.head}>
          <div className={styles.kick}>weather-card-benchmark</div>
          <h1 className={styles.title}>{t("title")}</h1>
          <p className={styles.lead}>{t("lead")}</p>

          <div className={styles.decls}>
            {declarations.map((d, i) => (
              <div key={i} className={styles.decl}>
                <span className="n">{i + 1}</span>
                <span>{d}</span>
              </div>
            ))}
          </div>
        </header>

        {/* sticky index — lives outside the header so it stays pinned through the
            whole document, with active-section highlighting (item 12). */}
        <MethodologyToc
          label={t("onThisPage")}
          items={[
            { id: "prompt", label: t("prompt.title") },
            { id: "measure", label: t("measure.title") },
            { id: "patch", label: t("patch.title") },
            { id: "voting", label: t("voting.title") },
            { id: "contribute", label: t("contribute.title") },
            { id: "provenance", label: t("provenance.title") },
          ]}
        />

        {/* the task itself — every model got exactly this, verbatim. It leads the
            document because no other guarantee means anything without it. */}
        <section id="prompt" className="section">
          <div className="section-head">
            <div>
              <h2 className="sec">{t("prompt.title")}</h2>
              <p className="sec-sub">{t("prompt.intro")}</p>
            </div>
          </div>
          <PromptPanel
            docs={prompts}
            labels={{
              heading: t("prompt.heading"),
              note: t("prompt.note"),
              download: t("prompt.download"),
            }}
          />
        </section>

        {/* how similarity is measured */}
        <section id="measure" className="section">
          <div className="section-head">
            <div>
              <h2 className="sec">{t("measure.title")}</h2>
            </div>
          </div>
          <p className={styles.prose}>{t("measure.body")}</p>
          <div className={styles.layers}>
            {layers.map((l) => (
              <div key={l.k} className={styles.layer}>
                <div className="k">{l.k}</div>
                <div>
                  <div className="t">{l.t}</div>
                  <div className="d">{l.d}</div>
                </div>
              </div>
            ))}
          </div>
          <p className={styles.note}>{t("measure.reproNote")}</p>
        </section>

        {/* patch policy */}
        <section id="patch" className="section">
          <div className="section-head">
            <div>
              <h2 className="sec">{t("patch.title")}</h2>
              <p className="sec-sub">{t("patch.intro")}</p>
            </div>
          </div>
          <div className={styles.rules}>
            {invariants.map((inv, i) => (
              <div key={i} className={styles.rule}>
                <span>{inv}</span>
              </div>
            ))}
          </div>

          <h3 className="sec-title" style={{ marginTop: 28 }}>
            {t("patch.listTitle")}
          </h3>
          <p className="sec-sub">{t("patch.listIntro")}</p>
          {patches.length === 0 ? (
            <div className={styles.emptyLine}>{t("patch.empty")}</div>
          ) : (
            <div className={styles.tableWrap}>
              <table className={styles.tbl}>
                <thead>
                  <tr>
                    <th>{t("patch.cols.config")}</th>
                    <th>{t("patch.cols.variant")}</th>
                    <th>{t("patch.cols.category")}</th>
                    <th>{t("patch.cols.reason")}</th>
                    <th>{t("patch.cols.diff")}</th>
                  </tr>
                </thead>
                <tbody>
                  {patches.map((p) => (
                    <tr key={`${p.configId}|${p.variant}|${p.slotIndex}`}>
                      <td>
                        <Link href={`/card/${p.configId}`} className="cid">
                          {p.configId}
                        </Link>
                      </td>
                      <td>
                        {p.variant} · #{p.slotIndex}
                      </td>
                      <td>{p.note.category}</td>
                      <td style={{ whiteSpace: "normal", maxWidth: 340 }}>
                        {loc(locale) === "zh"
                          ? p.note.reason_zh || p.note.reason_en
                          : p.note.reason_en}
                      </td>
                      <td>
                        <a href={p.diffUrl} target="_blank" rel="noopener noreferrer">
                          diff ↗
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* voting math — violet register (community/vote element, §5) */}
        <section id="voting" className={`section ${styles.violet}`}>
          <div className="section-head">
            <div>
              <h2 className="sec">{t("voting.title")}</h2>
              <span className={styles.framing}>{t("voting.framing")}</span>
            </div>
          </div>
          <p className={styles.prose}>{t("voting.blind")}</p>
          <ol className={styles.rowlist}>
            {votingRules.map((r, i) => (
              <li key={i} className={styles.row}>
                <span>{r}</span>
              </li>
            ))}
          </ol>
          <div className={styles.warnline}>{t("voting.gameability")}</div>
          <div className={styles.actions}>
            <a className="btn btn-violet" href="/api/arena/export">
              {t("voting.exportLabel")} ↓
            </a>
            <Link className="btn btn-ghost" href="/arena">
              arena →
            </Link>
          </div>
        </section>

        {/* contribute zero-trust path */}
        <section id="contribute" className="section">
          <div className="section-head">
            <div>
              <h2 className="sec">{t("contribute.title")}</h2>
              <p className="sec-sub">{t("contribute.intro")}</p>
            </div>
          </div>
          <ol className={styles.rowlist}>
            {contribRules.map((r, i) => (
              <li key={i} className={styles.row}>
                <span>{r}</span>
              </li>
            ))}
          </ol>
          <p className={styles.note}>{t("contribute.review")}</p>
          <div className={styles.actions}>
            <Link className="btn btn-ghost" href="/contribute">
              {t("contribute.linkLabel")} →
            </Link>
          </div>
        </section>

        {/* provenance / batches appendix — where batches live now */}
        <section id="provenance" className="section">
          <div className="section-head">
            <div>
              <h2 className="sec">{t("provenance.title")}</h2>
              <p className="sec-sub">{t("provenance.intro")}</p>
            </div>
          </div>
          {batches.length === 0 ? (
            <div className={styles.missing}>{t("provenance.empty")}</div>
          ) : (
            <ProvenanceTable
              recentCount={PROV_RECENT}
              rows={provRows}
              cols={{
                session: t("provenance.cols.session"),
                date: t("provenance.cols.date"),
                configs: t("provenance.cols.configs"),
                variants: t("provenance.cols.variants"),
                hash: t("provenance.cols.hash"),
              }}
              strings={{
                summary: t("provenance.summary", { n: batches.length }),
                viewAll: t("provenance.viewAll", { n: batches.length }),
                viewLess: t("provenance.viewLess"),
                copy: t("provenance.copy"),
                copied: t("provenance.copied"),
              }}
            />
          )}
        </section>

        {/* full frozen technical scheme (collapsible) */}
        <section className="section">
          <div className="section-head">
            <div>
              <h2 className="sec">{t("scheme.title")}</h2>
              <p className="sec-sub">{t("scheme.note")}</p>
            </div>
          </div>
          {scheme ? (
            <details className={styles.scheme}>
              <summary>
                <span className={styles.schemeLabel}>{t("scheme.summary")}</span>
                <span className={styles.schemeFile} title={scheme.name}>
                  {scheme.name}
                </span>
              </summary>
              <div className={styles.schemeBody}>
                {/* first-party, build-time markdown — trusted content */}
                <div
                  className={styles.md}
                  dangerouslySetInnerHTML={{ __html: scheme.html }}
                />
              </div>
            </details>
          ) : (
            <div className={styles.missing}>{t("scheme.missing")}</div>
          )}
        </section>

        {/* i18n note */}
        <section className="section">
          <div className="section-head">
            <div>
              <h2 className="sec">{t("i18n.title")}</h2>
            </div>
          </div>
          <p className={styles.prose}>{t("i18n.body")}</p>
        </section>
      </div>
    </div>
  );
}

/** listBatches, guarded — an unreadable index must not crash the page (#4). */
function listBatchesSafe() {
  try {
    return listBatches();
  } catch {
    return [];
  }
}
