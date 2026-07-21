"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { cellKey, type TrayCell } from "../progress/cells";
import styles from "./contribute.module.css";

// /contribute client form (spec §4, §3.3). Tray (from /progress) → live estimate
// (public /api/contribute/estimate) → submit (POST /api/contribute/submit, key
// held in memory only). Flag-off path shows the beta notice + run-locally & PR
// commands. All server error codes surface i18n'd. The §3.3 honesty block is
// rendered verbatim beside the key field.

interface EstimateResult {
  calls: number;
  usd: [number, number];
  notes?: string | string[];
}

const PROVIDERS = [
  "openai", "anthropic", "xai", "gemini", "mistral",
  "deepseek", "dashscope", "moonshot", "zai", "minimax",
];

// Server error code → message key under contribute.errors.*
const ERROR_KEYS: Record<string, string> = {
  beta: "beta",
  "queue-full": "queueFull",
  "cell-cap": "cellCap",
  "no-cells": "noCells",
  "no-key": "noKey",
  "bad-cells": "badCells",
  "bad-request": "badRequest",
  network: "network",
};

export function ContributeForm({
  initialCells,
  enabled,
}: {
  initialCells: TrayCell[];
  enabled: boolean;
}) {
  const t = useTranslations("contribute");
  const [cells, setCells] = useState<TrayCell[]>(initialCells);
  const [provider, setProvider] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [contact, setContact] = useState("");
  const [note, setNote] = useState("");
  const [estimate, setEstimate] = useState<EstimateResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; code?: string; jobId?: string } | null>(null);

  // Live estimate whenever the tray changes (public endpoint).
  useEffect(() => {
    if (cells.length === 0) {
      setEstimate(null);
      return;
    }
    let cancelled = false;
    fetch("/api/contribute/estimate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ cells }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data) setEstimate(data as EstimateResult);
      })
      .catch(() => {
        /* estimate is best-effort; a failed fetch just leaves it blank */
      });
    return () => {
      cancelled = true;
    };
  }, [cells]);

  const removeCell = useCallback((key: string) => {
    setCells((prev) => prev.filter((c) => cellKey(c) !== key));
  }, []);

  const estNote = useMemo(() => {
    if (!estimate?.notes) return "";
    return Array.isArray(estimate.notes) ? estimate.notes.join(" · ") : estimate.notes;
  }, [estimate]);

  const submit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (cells.length === 0 || !provider || !apiKey) return;
      setSubmitting(true);
      setResult(null);
      try {
        const res = await fetch("/api/contribute/submit", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            cells,
            provider,
            apiKey,
            contact: contact || undefined,
            note: note || undefined,
          }),
        });
        const data = (await res.json().catch(() => ({}))) as { ok?: boolean; code?: string; jobId?: string };
        if (res.ok && data.ok) {
          setResult({ ok: true, jobId: data.jobId });
          setApiKey(""); // never keep the key in state after use
        } else {
          setResult({ ok: false, code: data.code ?? "badRequest" });
        }
      } catch {
        setResult({ ok: false, code: "network" });
      } finally {
        setSubmitting(false);
      }
    },
    [cells, provider, apiKey, contact, note],
  );

  if (cells.length === 0) {
    return <EmptyContribute t={t} />;
  }

  return (
    <div>
      <div className={styles.grid2}>
        {/* ---- left: tray + estimate ---- */}
        <div>
          <div className={styles.panel}>
            <h3>{t("tray.title")}</h3>
            <ul className={styles.tray}>
              {cells.map((c) => {
                const key = cellKey(c);
                return (
                  <li key={key} className={styles.trayItem}>
                    <span className={styles.trayId}>{c.modelId}</span>
                    {c.effort ? <span className={styles.trayEff}>@ {c.effort}</span> : null}
                    <span className={styles.trayMeta}>
                      <span className="b b-arm">{c.arm}</span>
                      <span className={styles.trayFam}>{c.family}</span>
                      <button
                        type="button"
                        className={styles.remove}
                        aria-label={t("tray.remove")}
                        onClick={() => removeCell(key)}
                      >
                        ×
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>

          <div className={styles.estimate}>
            <div>
              <span className={styles.fig}>{estimate ? estimate.calls : cells.reduce((s, c) => s + c.n * 2, 0)}</span>
              <span className={styles.figL}>{t("estimate.calls")}</span>
            </div>
            <div>
              <span className={styles.fig}>
                {estimate && (estimate.usd[0] > 0 || estimate.usd[1] > 0)
                  ? `$${estimate.usd[0].toFixed(2)} – $${estimate.usd[1].toFixed(2)}`
                  : t("estimate.unknown")}
              </span>
              <span className={styles.figL}>{t("estimate.usd")}</span>
            </div>
            {estNote ? <span className={styles.estNote}>{estNote}</span> : null}
          </div>
          <p className="sec-sub" style={{ fontSize: 11, marginBlockStart: 4 }}>
            {t("estimate.disclaimer")}
          </p>
        </div>

        {/* ---- right: submit form OR beta path ---- */}
        <div>
          {enabled ? (
            <form className={styles.panel} onSubmit={submit}>
              <h3>{t("form.title")}</h3>

              <div className={styles.field}>
                <label htmlFor="cb-provider">{t("form.provider")}</label>
                <input
                  id="cb-provider"
                  className={styles.input}
                  list="cb-providers"
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                  placeholder="openai"
                  autoComplete="off"
                  required
                />
                <datalist id="cb-providers">
                  {PROVIDERS.map((p) => (
                    <option key={p} value={p} />
                  ))}
                </datalist>
              </div>

              <div className={styles.field}>
                <label htmlFor="cb-key">{t("form.apiKey")}</label>
                <input
                  id="cb-key"
                  className={styles.input}
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="sk-…"
                  autoComplete="off"
                  spellCheck={false}
                  required
                />
              </div>

              <div className={styles.field}>
                <label htmlFor="cb-contact">
                  {t("form.contact")} <span className={styles.opt}>{t("form.optional")}</span>
                </label>
                <input
                  id="cb-contact"
                  className={styles.input}
                  value={contact}
                  onChange={(e) => setContact(e.target.value)}
                  placeholder="github handle / email"
                  autoComplete="off"
                />
              </div>

              <div className={styles.field}>
                <label htmlFor="cb-note">
                  {t("form.note")} <span className={styles.opt}>{t("form.optional")}</span>
                </label>
                <input
                  id="cb-note"
                  className={styles.input}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  autoComplete="off"
                />
              </div>

              {/* §3.3 honesty block — verbatim */}
              <div className={styles.honesty}>
                <h4>{t("honesty.title")}</h4>
                <ul>
                  <li>{t("honesty.memory")}</li>
                  <li>{t("honesty.scope")}</li>
                  <li>{t("honesty.opensource")}</li>
                  <li>{t("honesty.review")}</li>
                  <li>{t("honesty.zerotrust")}</li>
                </ul>
              </div>

              <div className={styles.actions}>
                <button
                  type="submit"
                  className="btn btn-violet"
                  disabled={submitting || !provider || !apiKey}
                >
                  {submitting ? t("form.submitting") : t("form.submit")}
                </button>
              </div>

              {result ? (
                <div className={`${styles.status} ${result.ok ? styles.ok : styles.err}`} role="status">
                  {result.ok
                    ? t("result.queued", { jobId: result.jobId ?? "" })
                    : t(`errors.${ERROR_KEYS[result.code ?? ""] ?? "badRequest"}`)}
                </div>
              ) : null}
            </form>
          ) : (
            <BetaBlock t={t} />
          )}
        </div>
      </div>
    </div>
  );
}

/** Grid / cell-selection glyph (audit: the hollow ○ read as a stuck spinner). */
function GridIcon() {
  return (
    <svg
      className={styles.emptyIcon}
      width="26"
      height="26"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M3 9h18M3 15h18M9 3v18M15 3v18" />
      {/* one selected cell */}
      <rect x="15" y="9" width="6" height="6" fill="currentColor" stroke="none" opacity="0.9" />
    </svg>
  );
}

/** Empty tray: a capped, tightened panel + a compact 3-step primer so the page
 *  never reads as unfinished (audit items 5–7). */
function EmptyContribute({ t }: { t: ReturnType<typeof useTranslations> }) {
  const steps = [
    { n: "1", label: t("steps.pick") },
    { n: "2", label: t("steps.estimate") },
    { n: "3", label: t("steps.run") },
  ];
  return (
    <div>
      <div className={styles.empty}>
        <GridIcon />
        <h3>{t("empty.title")}</h3>
        <p>{t("empty.message")}</p>
        <Link className="btn btn-violet" href="/progress">
          {t("empty.action")}
        </Link>
      </div>

      <div className={styles.steps} aria-label={t("steps.title")}>
        <span className={styles.stepsTitle}>{t("steps.title")}</span>
        <ol className={styles.stepList}>
          {steps.map((s) => (
            <li key={s.n} className={styles.step}>
              <span className={styles.stepN}>{s.n}</span>
              <span>{s.label}</span>
            </li>
          ))}
        </ol>
        <p className={styles.stepCost}>{t("steps.cost")}</p>
      </div>
    </div>
  );
}

function BetaBlock({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <div className={styles.beta}>
      <h3>{t("beta.title")}</h3>
      <p>{t("beta.body")}</p>
      {/* Commands stay English (code / instrument tokens, §6 stays-English). */}
      <div className={styles.cmd}>
        <span className={styles.c}>git clone</span> https://github.com/2nd1st/weather-card-benchmark{"\n"}
        <span className={styles.c}>cd</span> weather-card-benchmark/runner{"\n"}
        <span className={styles.c}>export</span> WCB_OPENAI_KEY=sk-…{"\n"}
        <span className={styles.c}>python3</span> run_batch.py --config configs/your-cells.yaml --skip-completed{"\n"}
        <span className={styles.c}># then open a pull request with the landed batch</span>
      </div>
      <p className={styles.zerotrust}>
        {t("beta.pr")}{" "}
        <Link href="/methodology">{t("beta.methodology")}</Link>
      </p>
    </div>
  );
}
