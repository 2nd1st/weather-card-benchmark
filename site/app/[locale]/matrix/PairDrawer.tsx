"use client";

// Cell-click pair-detail drawer (spec §4 matrix — "absorbs the old /pair
// explorer"). Two cards side by side, the 17-channel aggregate similarity bars
// (from the already-fetched summary cells, gated), and the per-slot-pair raw
// breakdown fetched from the pairs JSON. Ranks nothing; similarity is a
// measurement. Rendered as an overlay drawer so the matrix stays put behind it.

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { isSufficient, neutralColor, INSUFFICIENT_LABEL } from "@/lib/neutral";
import {
  CHANNELS,
  channelFamily,
  isDiagnosticChannel,
  type Channel,
  type Variant,
} from "@/lib/variant";
import type { MatrixConfig } from "./MatrixView";
import styles from "./matrix.module.css";

const sc = (...names: Array<string | false | null | undefined>): string =>
  names.filter((n): n is string => !!n).map((n) => styles[n] ?? n).join(" ");

// One fixed-[0,1] neutral (viridis) similarity bar. Colour + gate come straight
// from lib/neutral (the measurement-presentation primitives); an ineligible cell
// shows a grey stub and NO number. Module-scoped so no global CSS is authored.
function MetricRow({
  channel,
  value,
  gate,
}: {
  channel: Channel;
  value: number | null;
  gate?: { kind: "self" | "cross"; n_eff: number; m_a: number; m_b: number };
}) {
  const sufficient =
    value != null &&
    (gate
      ? isSufficient({ ...gate, median: value })
      : true);
  const fam = channelFamily(channel);
  const diag = isDiagnosticChannel(channel);
  return (
    <div className={sc("mx-sbar-row")}>
      <span className={sc("mx-sbar-label", `mx-fam-${fam}`)}>
        {channel}
        {diag && <span className={sc("mx-sbar-diag")}>diag</span>}
      </span>
      <div className={sc("mx-sbar-track")} aria-hidden>
        {sufficient ? (
          <div
            className={sc("mx-sbar-fill")}
            style={{
              width: `${Math.round((value as number) * 100)}%`,
              background: neutralColor(value as number),
            }}
          />
        ) : (
          <div className={sc("mx-sbar-ins")} />
        )}
      </div>
      <span className={sc("mx-sbar-value")}>
        {sufficient ? (
          (value as number).toFixed(3)
        ) : (
          <em className={sc("mx-sub")}>{INSUFFICIENT_LABEL}</em>
        )}
      </span>
    </div>
  );
}

export interface SummaryCell {
  config_a: string;
  config_b: string;
  channel: Channel;
  kind: "self" | "cross";
  median: number | null;
  iqr: { p25: number; p75: number } | null;
  n_eff: number;
  m_a: number;
  m_b: number;
}

interface PairItem {
  slot_a: number;
  slot_b: number;
  s: Partial<Record<Channel, number | null>>;
}
interface PairDetail {
  config_a: string;
  config_b: string;
  kind: "self" | "cross";
  pairs: PairItem[];
}

export function PairDrawer({
  batchId,
  variant,
  a,
  b,
  configs,
  channels,
  summaryCells,
  thumbUrl,
  onClose,
}: {
  batchId: string;
  variant: Variant;
  a: string;
  b: string;
  configs: MatrixConfig[];
  channels: Channel[];
  summaryCells: SummaryCell[];
  thumbUrl: (configId: string, slot: number) => string;
  onClose: () => void;
}) {
  const t = useTranslations("matrix");
  const [detail, setDetail] = useState<PairDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selPairIdx, setSelPairIdx] = useState(0);

  const cfgA = configs.find((c) => c.id === a);
  const cfgB = configs.find((c) => c.id === b);
  const kind: "self" | "cross" = a === b ? "self" : "cross";

  // Load slot-pair detail (canonical order first, then swapped).
  const loadDetail = useCallback(async () => {
    setErr(null);
    setDetail(null);
    setSelPairIdx(0);
    const [lo, hi] = a <= b ? [a, b] : [b, a];
    for (const [x, y] of [
      [lo, hi],
      [hi, lo],
    ]) {
      try {
        const res = await fetch(
          `/b/${batchId}/similarity/pairs/${x}--${y}--${variant}.json`,
        );
        if (res.ok) {
          setDetail(await res.json());
          return;
        }
      } catch {
        /* try swapped */
      }
    }
    setErr(t("drawer.noDetail"));
  }, [batchId, a, b, variant, t]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  // Esc to close.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function cell(ch: Channel): SummaryCell | undefined {
    return summaryCells.find(
      (c) =>
        c.channel === ch &&
        ((c.config_a === a && c.config_b === b) ||
          (c.config_a === b && c.config_b === a)),
    );
  }

  const selPair = detail?.pairs[selPairIdx];

  return (
    <div className={sc("mx-drawer-backdrop")} onClick={onClose}>
      <div
        className={sc("mx-drawer")}
        role="dialog"
        aria-label={t("drawer.title")}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={sc("mx-drawer-head")}>
          <div>
            <h2 className={sc("mx-drawer-title")}>{t("drawer.title")}</h2>
            <p className={sc("mx-sub")}>{t("drawer.subtitle")}</p>
          </div>
          <button
            type="button"
            className={sc("mx-drawer-close")}
            onClick={onClose}
            aria-label={t("drawer.close")}
          >
            ✕
          </button>
        </div>

        <div className={sc("mx-drawer-meta")}>
          <span className="b">{kind === "self" ? t("drawer.self") : t("drawer.cross")}</span>
          <span className={sc("mx-sub")}>{variant}</span>
        </div>

        {/* two cards side by side */}
        <div className={sc("mx-pair-cards")}>
          <PairCardHead
            cfg={cfgA}
            slot={selPair?.slot_a}
            thumb={selPair ? thumbUrl(a, selPair.slot_a) : null}
            noSlot={t("drawer.noSlot")}
            slotLabel={t("drawer.slot")}
            openCard={t("drawer.openCard")}
          />
          <PairCardHead
            cfg={cfgB}
            slot={selPair?.slot_b}
            thumb={selPair ? thumbUrl(b, selPair.slot_b) : null}
            noSlot={t("drawer.noSlot")}
            slotLabel={t("drawer.slot")}
            openCard={t("drawer.openCard")}
          />
        </div>

        {err && (
          <p className={sc("mx-sub")} style={{ color: "var(--bad)" }}>
            {err}
          </p>
        )}

        {/* aggregate channels (from summary, gated) */}
        <section className={sc("mx-drawer-sec")}>
          <h3 className={sc("mx-drawer-h")}>{t("drawer.aggregateHeading")}</h3>
          <p className={sc("mx-sub")}>{t("drawer.aggregateNote")}</p>
          <div className={sc("mx-sbar-list")}>
            {CHANNELS.map((ch) => {
              const c = cell(ch);
              return (
                <MetricRow
                  key={ch}
                  channel={ch}
                  value={c?.median ?? null}
                  gate={
                    c
                      ? { kind: c.kind, n_eff: c.n_eff, m_a: c.m_a, m_b: c.m_b }
                      : undefined
                  }
                />
              );
            })}
          </div>
        </section>

        {/* per slot-pair (raw, ungated) */}
        <section className={sc("mx-drawer-sec")}>
          <h3 className={sc("mx-drawer-h")}>{t("drawer.perPairHeading")}</h3>
          {!detail ? (
            <p className={sc("mx-sub")}>{t("drawer.loadingDetail")}</p>
          ) : detail.pairs.length === 0 ? (
            <p className={sc("mx-sub")}>{t("drawer.noPairs")}</p>
          ) : (
            <>
              <div className={sc("mx-chip-row")}>
                {detail.pairs.map((p, i) => (
                  <button
                    key={`${p.slot_a}-${p.slot_b}`}
                    type="button"
                    className={i === selPairIdx ? "chip on" : "chip"}
                    onClick={() => setSelPairIdx(i)}
                  >
                    {p.slot_a} × {p.slot_b}
                  </button>
                ))}
              </div>
              {selPair && (
                <div className={sc("mx-sbar-list")} style={{ marginTop: "0.6rem" }}>
                  {CHANNELS.map((ch) => (
                    <MetricRow key={ch} channel={ch} value={selPair.s[ch] ?? null} />
                  ))}
                </div>
              )}
              <p className={sc("mx-sub")}>{t("drawer.perPairNote")}</p>
            </>
          )}
        </section>

        <nav className={sc("mx-drawer-nav")}>
          {cfgA && (
            <Link href={`/card/${encodeURIComponent(a)}`}>{cfgA.label} ↗</Link>
          )}
          {cfgB && a !== b && (
            <Link href={`/card/${encodeURIComponent(b)}`}>{cfgB.label} ↗</Link>
          )}
          {a !== b && (
            <Link
              href={`/compare?ids=${encodeURIComponent(a)},${encodeURIComponent(b)}`}
            >
              {t("drawer.openCompare")}
            </Link>
          )}
        </nav>
      </div>
    </div>
  );
}

function PairCardHead({
  cfg,
  slot,
  thumb,
  noSlot,
  slotLabel,
  openCard,
}: {
  cfg?: MatrixConfig;
  slot?: number;
  thumb: string | null;
  noSlot: string;
  slotLabel: string;
  openCard: string;
}) {
  if (!cfg) return <div className={sc("mx-pair-card", "mx-sub")}>—</div>;
  return (
    <div className={sc("mx-pair-card")}>
      <div className={sc("mx-pair-card-head")}>
        <span className={sc("mx-pair-name")}>
          <span className={sc("mx-pair-fam")}>{cfg.family}</span> {cfg.label}
        </span>
        <Link href={`/card/${encodeURIComponent(cfg.id)}`} className={sc("mx-pair-mini")}>
          {openCard}
        </Link>
      </div>
      <div className={sc("mx-pair-shot")}>
        {thumb ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={thumb} alt={`${cfg.label} slot ${slot}`} loading="lazy" />
        ) : (
          <div className={sc("mx-pair-noshot", "mx-sub")}>{noSlot}</div>
        )}
      </div>
      {slot != null && (
        <div className={sc("mx-sub")}>
          {slotLabel} {slot}
        </div>
      )}
    </div>
  );
}
