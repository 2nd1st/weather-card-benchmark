"use client";

import { useEffect, useState } from "react";
import { useLocale } from "next-intl";
import { EmptyState } from "@/app/components/EmptyState";
import { AttributionBar } from "@/app/components/AttributionBar";
import styles from "../arena.module.css";

// Community UI-preference leaderboard (spec §4 arena, amended §0.1). Framed as
// UI preference — which cards people prefer to look at — NOT a capability
// measure. Default order = community preference (most-preferred first) among
// rows with sufficient data; `insufficient` rows are grouped below, grey, with
// NO numbers and NO axis position (a position is a number too). A toggle drops
// back to neutral slug order (the order the API/CSV emit). BT `strength` lives
// in the API/CSV, not this default view.

interface StatsItem {
  configId: string;
  n: number;
  nVoters: number;
  prefRate: number | null;
  ci: [number, number] | null;
  strength: number | null;
  bothBadRate: number;
  maxSingleVoterShare: number;
  insufficient: boolean;
}
interface StatsResponse {
  totalVotes: number;
  arenaVotes: number;
  gateVotes: number;
  methodUrl: string;
  angle: string;
  variant: string;
  items: StatsItem[];
}

export interface LabelInfo {
  modelId: string;
  effort: string | null;
  arm: string;
}

export interface LeaderboardStrings {
  title: string;
  framing: string;
  gameability: string;
  csv: string;
  insufficientGroup: string;
  insufficientSummary: string; // "{n} configs below the vote threshold"
  insufficientShow: string;
  insufficientHide: string;
  orderPref: string;
  orderNeutral: string;
  variantPqTip: string; // P-q one-line explanation (title tooltip)
  variantPminTip: string; // P-min one-line explanation (title tooltip)
  empty: string;
  emptyMsg: string;
  colVoters: string; // "{n} voters" template with {n}
  attributionExtra: string; // "community UI preference · n={n}"
}

const VARIANTS = ["P-q", "P-min"] as const;

const VARIANT_TIP_KEY: Record<(typeof VARIANTS)[number], "variantPqTip" | "variantPminTip"> = {
  "P-q": "variantPqTip",
  "P-min": "variantPminTip",
};

function fmtLabel(info: LabelInfo | undefined, configId: string): { model: string; effort: string | null; arm: string } {
  if (!info) return { model: configId, effort: null, arm: "" };
  return { model: info.modelId, effort: info.effort, arm: info.arm };
}

export function Leaderboard({
  angle,
  labels,
  strings,
}: {
  angle: string;
  labels: Record<string, LabelInfo>;
  strings: LeaderboardStrings;
}) {
  const locale = useLocale();
  const nf = new Intl.NumberFormat(locale);
  const pf = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 });

  const [variant, setVariant] = useState<(typeof VARIANTS)[number]>("P-q");
  const [order, setOrder] = useState<"pref" | "neutral">("pref");
  const [data, setData] = useState<StatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [showInsufficient, setShowInsufficient] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetch(`/api/arena/stats?angle=${encodeURIComponent(angle)}&variant=${variant}`, {
      cache: "no-store",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: StatsResponse | null) => {
        if (alive) {
          setData(d);
          setLoading(false);
        }
      })
      .catch(() => {
        if (alive) {
          setData(null);
          setLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [angle, variant]);

  const items = data?.items ?? [];
  const sufficient = items.filter((i) => !i.insufficient && i.prefRate != null);
  const insufficient = items.filter((i) => i.insufficient || i.prefRate == null);

  if (order === "pref") {
    sufficient.sort((a, b) => (b.prefRate ?? 0) - (a.prefRate ?? 0));
  }
  // neutral: API already emits slug order → leave as-is

  const nothing = !loading && items.length === 0;

  return (
    <div className={`prefstrip panelbox ${styles.board}`}>
      <div className={styles.boardHead}>
        <h3>{strings.title}</h3>
        <div className={styles.orderToggle}>
          <div className="seg violet">
            <button className={order === "pref" ? "on" : undefined} onClick={() => setOrder("pref")}>
              {strings.orderPref}
            </button>
            <button className={order === "neutral" ? "on" : undefined} onClick={() => setOrder("neutral")}>
              {strings.orderNeutral}
            </button>
          </div>
          <div className="seg" style={{ marginInlineStart: 8 }}>
            {VARIANTS.map((v) => (
              <button
                key={v}
                className={variant === v ? "on" : undefined}
                title={strings[VARIANT_TIP_KEY[v]]}
                onClick={() => setVariant(v)}
              >
                {v}
              </button>
            ))}
          </div>
        </div>
      </div>
      <p className={styles.boardFraming}>{strings.framing}</p>
      <p className={styles.boardGameability}>{strings.gameability}</p>

      {nothing ? (
        <EmptyState title={strings.empty} message={strings.emptyMsg} icon="○" />
      ) : (
        <>
          {sufficient.map((it) => {
            const l = fmtLabel(labels[it.configId], it.configId);
            const pref = it.prefRate ?? 0;
            const ci = it.ci ?? [pref, pref];
            return (
              <div className={styles.prow} key={it.configId}>
                <span className={styles.pl}>
                  {l.model}
                  {l.effort ? <span className={styles.eff}> @ {l.effort}</span> : null}
                  {l.arm ? <span className={styles.arm}> · {l.arm}</span> : null}
                </span>
                <div className={styles.track} aria-hidden>
                  <span
                    className={styles.ci}
                    style={{ left: `${ci[0] * 100}%`, width: `${Math.max(0, (ci[1] - ci[0]) * 100)}%` }}
                  />
                  <span className={styles.dot} style={{ left: `${pref * 100}%` }} />
                </div>
                <span className={styles.pnums}>
                  <b>{pf.format(pref)}</b> · n={nf.format(it.n)} · {strings.colVoters.replace("{n}", nf.format(it.nVoters))}
                  {it.bothBadRate > 0 ? <> · both-bad {pf.format(it.bothBadRate)}</> : null}
                </span>
              </div>
            );
          })}

          {insufficient.length > 0 ? (
            <div className={styles.insufficientBlock}>
              {/* collapse the sparse n=1 rows behind a one-line summary so they
                  don't dominate the section as a long grey dump (brief E). */}
              <button
                type="button"
                className={styles.insufficientToggle}
                aria-expanded={showInsufficient}
                onClick={() => setShowInsufficient((s) => !s)}
              >
                <span>{strings.insufficientSummary.replace("{n}", nf.format(insufficient.length))}</span>
                <span className={styles.insufficientAction}>
                  {showInsufficient ? strings.insufficientHide : strings.insufficientShow}
                </span>
              </button>
              {showInsufficient
                ? insufficient.map((it) => {
                    const l = fmtLabel(labels[it.configId], it.configId);
                    return (
                      <div className={`${styles.prow} ${styles.prowGrey}`} key={it.configId}>
                        <span className={styles.pl}>
                          {l.model}
                          {l.effort ? <span className={styles.eff}> @ {l.effort}</span> : null}
                          {l.arm ? <span className={styles.arm}> · {l.arm}</span> : null}
                        </span>
                        <span />
                        <span className={styles.greyNum}>n={nf.format(it.n)}</span>
                      </div>
                    );
                  })
                : null}
            </div>
          ) : null}
        </>
      )}

      <div className={styles.boardFoot}>
        <a href="/api/arena/export" download>
          {strings.csv}
        </a>
        {data ? <span>{nf.format(data.arenaVotes)} · {nf.format(data.totalVotes)}</span> : null}
      </div>
      <AttributionBar extra={strings.attributionExtra.replace("{n}", nf.format(data?.arenaVotes ?? 0))} />
    </div>
  );
}
