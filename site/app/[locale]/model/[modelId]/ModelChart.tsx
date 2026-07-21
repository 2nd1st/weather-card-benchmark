"use client";

import { useMemo, useState } from "react";
import { useTranslations, useLocale } from "next-intl";
import styles from "./model.module.css";

// Effort-response mini-chart (spec §4): output tokens / html bytes vs effort,
// one line per arm — the "fable-14×" visual. Pure SVG, tiny toggle; no chart
// library. Violet is reserved for community elements, so this uses the cyan /
// neutral instrument palette only. The growth multiple (low → max) is the
// shareable takeaway, so it is computed from the data and headlined above the
// plot with the two endpoints ringed + labelled on the curve.

export interface ChartPoint {
  effortIndex: number;
  tokens: number | null;
  bytes: number | null;
}
export interface ChartSeries {
  arm: string;
  points: ChartPoint[];
}
export interface ModelChartData {
  efforts: string[];
  series: ChartSeries[];
}

const COLORS = ["#37D6E4", "#3BD97F", "#F5B454", "#93A0B8", "#5C6883", "#E8ECF4"];
const W = 640;
const H = 196; // ~25% shorter than the old 260 (audit: chart dominated the page)
const PAD_L = 56;
const PAD_R = 20;
const PAD_T = 26; // headroom for the ringed max-endpoint label
const PAD_B = 34;

// Readable arm expansions (arm tokens stay English, §6). Falls back to the slug.
const ARM_LABELS: Record<string, string> = {
  cc: "claude-code (CLI)",
  api: "API",
  codex: "codex (CLI)",
  "codex-oauth": "codex (OAuth)",
  "grok-cli": "grok (CLI)",
  go: "Go (native)",
};
function armLabel(arm: string): string {
  return ARM_LABELS[arm.toLowerCase()] ?? arm;
}

/** Smallest "nice" bound ≥ v (rounded 1/2/2.5/5/10 × 10ⁿ) for tidy tick labels. */
function niceCeil(v: number): number {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  const m = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
  return m * p;
}

function fmtMult(m: number): string {
  return m >= 100 ? Math.round(m).toString() : m.toFixed(1);
}

export function ModelChart({ data }: { data: ModelChartData }) {
  const t = useTranslations("model");
  const locale = useLocale();
  const num = useMemo(() => new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }), [locale]);
  const [metric, setMetric] = useState<"tokens" | "bytes">("tokens");

  const pick = (p: ChartPoint): number | null => (metric === "tokens" ? p.tokens : p.bytes);

  const maxY = useMemo(() => {
    let m = 0;
    for (const s of data.series) for (const p of s.points) {
      const v = pick(p);
      if (v != null && v > m) m = v;
    }
    return m;
  }, [data.series, metric]);

  // Domain with 10–15% headroom above the max, rounded to a nice tick bound so
  // no point lands on the top gridline (audit §10).
  const domain = useMemo(() => niceCeil(maxY * 1.1), [maxY]);

  // Growth story: the arm carrying the global max is the dominant series; within
  // it, min→max is the shareable multiple (e.g. "14.7× low → max").
  const growth = useMemo(() => {
    let bestIdx = -1;
    let bestPts: Array<{ i: number; v: number }> = [];
    let globalMax = -Infinity;
    for (let idx = 0; idx < data.series.length; idx++) {
      const pts: Array<{ i: number; v: number }> = [];
      for (const p of data.series[idx].points) {
        const v = pick(p);
        if (v != null && v > 0) pts.push({ i: p.effortIndex, v });
      }
      if (pts.length < 2) continue;
      let localMax = -Infinity;
      for (const p of pts) if (p.v > localMax) localMax = p.v;
      if (localMax > globalMax) {
        globalMax = localMax;
        bestIdx = idx;
        bestPts = pts;
      }
    }
    if (bestIdx < 0) return null;
    let lo = bestPts[0];
    let hi = bestPts[0];
    for (const p of bestPts) {
      if (p.v < lo.v) lo = p;
      if (p.v > hi.v) hi = p;
    }
    if (lo.v <= 0 || hi.v <= lo.v) return null;
    const mult = hi.v / lo.v;
    if (mult < 1.5) return null;
    return { idx: bestIdx, lo, hi, mult };
  }, [data.series, metric]);

  const nCols = Math.max(1, data.efforts.length);
  const x = (i: number) => (nCols === 1 ? PAD_L + (W - PAD_L - PAD_R) / 2 : PAD_L + (i * (W - PAD_L - PAD_R)) / (nCols - 1));
  const y = (v: number) => (domain <= 0 ? H - PAD_B : H - PAD_B - (v / domain) * (H - PAD_T - PAD_B));

  const hasData = maxY > 0;
  const single = data.series.length === 1;
  const growthColor = growth ? COLORS[growth.idx % COLORS.length] : COLORS[0];

  return (
    <div className={styles.chartCard}>
      <div className={styles.chartHead}>
        <div>
          <h3 className={styles.secH}>{t("chart.heading")}</h3>
          <p className={styles.secSub} style={{ marginBottom: 0 }}>{t("chart.caption")}</p>
          {single && hasData ? (
            <span className={styles.armChip}><i style={{ background: COLORS[0] }} />{armLabel(data.series[0].arm)}</span>
          ) : null}
        </div>
        <div className={styles.chartToggle} role="group" aria-label={t("chart.toggle")}>
          <button className={`${styles.tbtn}${metric === "tokens" ? " " + styles.on : ""}`} onClick={() => setMetric("tokens")}>{t("chart.tokens")}</button>
          <button className={`${styles.tbtn}${metric === "bytes" ? " " + styles.on : ""}`} onClick={() => setMetric("bytes")}>{t("chart.bytes")}</button>
        </div>
      </div>

      {!hasData ? (
        <p className={styles.chartNote}>{t("chart.noData")}</p>
      ) : (
        <>
          {growth ? (
            <div className={styles.growth}>
              <span className={styles.growthMult}>{fmtMult(growth.mult)}×</span>
              <span className={styles.growthLabel}>
                {t("chart.growth", { lo: data.efforts[growth.lo.i] ?? "", hi: data.efforts[growth.hi.i] ?? "" })}
              </span>
            </div>
          ) : null}

          <svg className={styles.chartSvg} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={growth ? t("chart.ariaGrowth", { mult: fmtMult(growth.mult) }) : t("chart.heading")}>
            {/* y gridlines */}
            {[0, 0.5, 1].map((f) => (
              <g key={f}>
                <line x1={PAD_L} x2={W - PAD_R} y1={y(domain * f)} y2={y(domain * f)} stroke="#232C41" strokeWidth="1" />
                <text x={PAD_L - 8} y={y(domain * f) + 3} textAnchor="end" fill="#93A0B8" fontSize="10" fontFamily="ui-monospace, monospace">{num.format(Math.round(domain * f))}</text>
              </g>
            ))}
            {/* x labels */}
            {data.efforts.map((e, i) => (
              <text key={e + i} x={x(i)} y={H - PAD_B + 18} textAnchor="middle" fill="#93A0B8" fontSize="10" fontFamily="ui-monospace, monospace">{e}</text>
            ))}
            {/* series */}
            {data.series.map((s, si) => {
              const color = COLORS[si % COLORS.length];
              const pts = s.points.map((p) => ({ i: p.effortIndex, v: pick(p) })).filter((p) => p.v != null) as Array<{ i: number; v: number }>;
              const path = pts.map((p, k) => `${k === 0 ? "M" : "L"}${x(p.i)},${y(p.v)}`).join(" ");
              return (
                <g key={s.arm}>
                  {pts.length > 1 ? <path d={path} fill="none" stroke={color} strokeWidth="1.5" /> : null}
                  {pts.map((p) => (<circle key={p.i} cx={x(p.i)} cy={y(p.v)} r="3" fill={color} />))}
                </g>
              );
            })}
            {/* endpoint annotations (growth story) */}
            {growth ? (
              <g>
                <circle cx={x(growth.hi.i)} cy={y(growth.hi.v)} r="4.5" fill="none" stroke={growthColor} strokeWidth="1.5" />
                <text x={x(growth.hi.i)} y={y(growth.hi.v) - 11} textAnchor={growth.hi.i >= nCols - 1 ? "end" : "middle"} fill="#E8ECF4" fontSize="11" fontFamily="ui-monospace, monospace" fontWeight="600">{num.format(growth.hi.v)}</text>
                <circle cx={x(growth.lo.i)} cy={y(growth.lo.v)} r="4.5" fill="none" stroke={growthColor} strokeWidth="1.5" />
                <text x={x(growth.lo.i)} y={y(growth.lo.v) + 19} textAnchor={growth.lo.i <= 0 ? "start" : "middle"} fill="#93A0B8" fontSize="11" fontFamily="ui-monospace, monospace">{num.format(growth.lo.v)}</text>
              </g>
            ) : null}
          </svg>

          {!single ? (
            <div className={styles.legend}>
              {data.series.map((s, si) => (
                <span key={s.arm}><i style={{ background: COLORS[si % COLORS.length] }} />{armLabel(s.arm)}</span>
              ))}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
