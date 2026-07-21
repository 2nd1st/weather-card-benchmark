"use client";

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { AttributionBar } from "@/app/components/AttributionBar";
import { EmptyState } from "@/app/components/EmptyState";
import { cellKey, encodeCells, type TrayCell } from "./cells";
import type { BoardArm, BoardCell, BoardData } from "./board";
import styles from "./progress.module.css";

// Client coverage board (spec §4 /progress). Renders the arm-sectioned board as
// per-model rows of inline effort chips (each row shows ONLY that model's own
// planned efforts — no shared column matrix, no ghost cells), status-coloured +
// grade-tinted. On top of the raw board it adds the usability layer the audit
// asked for: a lead "landed / planned" metric with a progress bar, a sticky
// toolbar (all | gaps only | landed only segmented control + model search +
// compact status key), collapsible arm sections, a colour-blind-safe status
// glyph inside every chip, a cost-reference disclosure, and the contribute tray.
// Restrained register — shaping is server-side (board.ts); this is presentation
// + local view state only.

const STATUS_ORDER = ["landed", "partial", "attempted", "missing", "paused"] as const;
const STATUS_CLASS: Record<string, string> = {
  landed: styles.st_landed,
  partial: styles.st_partial,
  attempted: styles.st_attempted,
  missing: styles.st_missing,
  paused: styles.st_paused,
};
// Colour-blind-safe glyph per status — distinct SHAPES so state survives without
// colour (audit: "—" chips relied entirely on colour). Documented in the legend.
const STATUS_GLYPH: Record<string, string> = {
  landed: "✓",
  partial: "◐",
  attempted: "!",
  missing: "·",
  paused: "‖",
};
const GRADE_CLASS: Record<string, string> = {
  dev: styles.g_dev,
  community: styles.g_community,
};

type ShowMode = "all" | "gaps" | "landed";

/** Which statuses a show-mode keeps. "gaps" = anything short of fully landed
 *  (excluding paused, which is a deliberate hold, not a gap). */
function cellVisible(mode: ShowMode, status: string): boolean {
  if (mode === "all") return true;
  if (mode === "landed") return status === "landed";
  return status === "missing" || status === "attempted" || status === "partial";
}

export function ProgressBoard({ data }: { data: BoardData }) {
  const t = useTranslations("progress");
  const locale = useLocale();
  const nf = useMemo(() => new Intl.NumberFormat(locale), [locale]);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // ---- view state (audit: gaps-in-focus) ----
  const [showMode, setShowMode] = useState<ShowMode>("all");
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  // key → TrayCell for every selectable (missing/attempted) cell
  const selectableByKey = useMemo(() => {
    const m = new Map<string, TrayCell>();
    for (const arm of data.arms) {
      for (const row of arm.rows) {
        for (const cell of row.cells) {
          if (cell.status === "missing" || cell.status === "attempted") {
            m.set(cellKey(cell), {
              family: cell.family,
              modelId: cell.modelId,
              effort: cell.effort,
              arm: cell.arm,
              n: cell.n,
            });
          }
        }
      }
    }
    return m;
  }, [data]);

  function toggle(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else if (next.size < 12) next.add(key); // server per-job ceiling (spec §3.3)
      return next;
    });
  }

  function toggleArm(arm: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(arm)) next.delete(arm);
      else next.add(arm);
      return next;
    });
  }

  const trayCells = useMemo(
    () => [...selected].map((k) => selectableByKey.get(k)).filter(Boolean) as TrayCell[],
    [selected, selectableByKey],
  );
  const trayCalls = trayCells.reduce((s, c) => s + c.n * 2, 0);
  const contributeHref =
    trayCells.length > 0
      ? `/contribute?cells=${encodeURIComponent(encodeCells(trayCells))}`
      : "/contribute";

  const asOfLabel = useMemo(() => {
    try {
      return new Intl.DateTimeFormat(locale, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(data.asOf));
    } catch {
      return data.asOf;
    }
  }, [locale, data.asOf]);

  // ---- filtered board (search + show-mode) ----
  const filteredArms = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matchRow = (row: BoardArm["rows"][number]) =>
      !q ||
      row.modelId.toLowerCase().includes(q) ||
      row.family.toLowerCase().includes(q);
    return data.arms
      .filter((a) => a.rows.length > 0)
      .map((arm) => ({
        ...arm,
        rows: arm.rows
          .filter(matchRow)
          .map((row) => ({
            ...row,
            cells: row.cells.filter((c) => cellVisible(showMode, c.status)),
          }))
          .filter((row) => row.cells.length > 0),
      }))
      .filter((arm) => arm.rows.length > 0);
  }, [data.arms, query, showMode]);

  const g = data.chinaGap;
  const hasAnyPlan = data.arms.some((a) => a.rows.length > 0);
  const pct =
    data.totals.planned > 0
      ? Math.round((data.totals.landed / data.totals.planned) * 100)
      : 0;

  return (
    <div className={styles.board}>
      {/* intro + compact "last updated" directly under it (audit #27) */}
      <div className={styles.head}>
        <p className="sec-sub" style={{ margin: 0, maxWidth: "72ch" }}>
          {t("intro")}
        </p>
        <span className={styles.asof}>
          {t("asOf")} <b>{asOfLabel}</b>
        </span>
      </div>

      {/* ---- lead metric: landed / planned + progress bar (the headline answer) ---- */}
      <div className={styles.lead} role="group" aria-label={t("summary.aria")}>
        <div className={styles.leadTop}>
          <div className={styles.leadNums}>
            <span className={styles.leadLanded}>{nf.format(data.totals.landed)}</span>
            <span className={styles.leadDenom}>/ {nf.format(data.totals.planned)}</span>
          </div>
          <span className={styles.leadPct}>{pct}%</span>
        </div>
        <div className={styles.leadBar} aria-hidden>
          <span style={{ width: `${pct}%` }} />
        </div>
        <div className={styles.leadCaption}>
          {t("summary.landedCaption", { pct, planned: nf.format(data.totals.planned) })}
        </div>
      </div>

      {/* ---- secondary totals (valid-slot volume demoted out of the headline) ---- */}
      <div className={styles.totals} role="list" aria-label={t("totals.aria")}>
        <div className={`${styles.total} ${styles.landed}`} role="listitem">
          <div className="n">{nf.format(data.totals.landed)}</div>
          <div className="l">{t("totals.landed")}</div>
        </div>
        <div className={`${styles.total} ${styles.partial}`} role="listitem">
          <div className="n">{nf.format(data.totals.partial)}</div>
          <div className="l">{t("totals.partial")}</div>
        </div>
        <div className={`${styles.total} ${styles.gap}`} role="listitem">
          <div className="n">{nf.format(data.totals.missing + data.totals.attempted)}</div>
          <div className="l">{t("totals.gap")}</div>
        </div>
        <div className={`${styles.total} ${styles.paused}`} role="listitem">
          <div className="n">{nf.format(data.totals.paused)}</div>
          <div className="l">{t("totals.paused")}</div>
        </div>
        <div className={styles.total} role="listitem">
          <div className="n">{nf.format(data.totals.planned)}</div>
          <div className="l">{t("totals.planned")}</div>
        </div>
      </div>
      <p className={styles.slotsNote}>{t("summary.validSlots", { n: nf.format(data.totals.validSlots) })}</p>

      {/* ---- 中国 gap callout (natively written) — cyan info accent, metrics gridded right ---- */}
      {g.planned > 0 ? (
        <div className={styles.china}>
          <div className={styles.chinaMain}>
            <h3>{t("china.headline", { apiMissing: g.apiMissing, zeroFamilies: g.zeroDataFamilies })}</h3>
            <p>{t("china.body")}</p>
          </div>
          <div className={styles.figs}>
            <span>
              <b>{nf.format(g.families)}</b>
              {t("china.families")}
            </span>
            <span>
              <b>
                {nf.format(g.apiMissing)} / {nf.format(g.apiSeats)}
              </b>
              {t("china.apiSeats")}
            </span>
            <span>
              <b>{nf.format(g.zeroDataFamilies)}</b>
              {t("china.zeroData")}
            </span>
            <span>
              <b>
                {nf.format(g.landed)} / {nf.format(g.planned)}
              </b>
              {t("china.landed")}
            </span>
          </div>
        </div>
      ) : null}

      {/* ---- full legend (with definitions) — the reference key ---- */}
      <div className={styles.legend}>
        {STATUS_ORDER.map((s) => (
          <span key={s} className={styles.item}>
            <span className={`${styles.swatch} ${STATUS_CLASS[s]}`} aria-hidden>
              {STATUS_GLYPH[s]}
            </span>
            <span className={styles.itemName}>{t(`status.${s}`)}</span>
            <span className={styles.def}>— {t(`statusDef.${s}`)}</span>
          </span>
        ))}
      </div>

      {/* ---- sticky toolbar: show-control + search + compact status key ---- */}
      {hasAnyPlan ? (
        <div className={styles.toolbar}>
          <div className={styles.toolLeft}>
            <span className={styles.toolLabel}>{t("filter.show")}</span>
            <div className="seg" role="group" aria-label={t("filter.show")}>
              <button
                type="button"
                className={showMode === "all" ? "on" : ""}
                aria-pressed={showMode === "all"}
                onClick={() => setShowMode("all")}
              >
                {t("filter.all")}
              </button>
              <button
                type="button"
                className={showMode === "gaps" ? "on" : ""}
                aria-pressed={showMode === "gaps"}
                onClick={() => setShowMode("gaps")}
              >
                {t("filter.gaps")}
              </button>
              <button
                type="button"
                className={showMode === "landed" ? "on" : ""}
                aria-pressed={showMode === "landed"}
                onClick={() => setShowMode("landed")}
              >
                {t("filter.landedOnly")}
              </button>
            </div>
            <div className={styles.search}>
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("filter.searchPlaceholder")}
                aria-label={t("filter.searchAria")}
                className={styles.searchInput}
              />
            </div>
          </div>
          <div className={styles.keyRow} aria-hidden>
            {STATUS_ORDER.map((s) => (
              <span key={s} className={styles.keyItem}>
                <span className={`${styles.swatch} ${STATUS_CLASS[s]}`}>{STATUS_GLYPH[s]}</span>
                {t(`status.${s}`)}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {/* ---- arm sections (collapsible; sticky heads) ---- */}
      {!hasAnyPlan ? (
        <EmptyState title={t("empty.title")} message={t("empty.message")} />
      ) : filteredArms.length === 0 ? (
        <p className={styles.noMatch}>{t("filter.noMatch")}</p>
      ) : (
        filteredArms.map((arm) => (
          <ArmSection
            key={arm.arm}
            arm={arm}
            locale={locale}
            selected={selected}
            onToggle={toggle}
            collapsed={collapsed.has(arm.arm)}
            onToggleArm={() => toggleArm(arm.arm)}
            t={t}
          />
        ))
      )}

      {/* ---- cost reference (collapsed behind a disclosure) ---- */}
      {data.cost && data.cost.rows.length > 0 ? (
        <details className={styles.costDisclosure}>
          <summary className={styles.costSummary}>
            <span className="sec" style={{ fontSize: 16 }}>
              {t("cost.title")}
            </span>
            <span className={styles.costHint}>{t("cost.subtitle")}</span>
          </summary>
          <div className={styles.costWrap}>
            <table className={styles.cost}>
              <thead>
                <tr>
                  <th>{t("cost.family")}</th>
                  <th>{t("cost.arm")}</th>
                  <th>{t("cost.range")}</th>
                  <th>{t("cost.basis")}</th>
                  <th>{t("cost.note")}</th>
                </tr>
              </thead>
              <tbody>
                {data.cost.rows.map((r) => (
                  <tr key={`${r.family}|${r.arm}`}>
                    <td>{r.family}</td>
                    <td>{r.arm}</td>
                    <td className="usd">
                      {r.loUsd === 0 && r.hiUsd === 0
                        ? t("cost.free")
                        : `$${r.loUsd} – $${r.hiUsd}`}
                    </td>
                    <td className="basis">{r.basis ?? "—"}</td>
                    <td className="note">{(locale === "zh" ? r.noteZh : r.noteEn) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className={styles.disclaimer}>{t("cost.disclaimer")}</p>
        </details>
      ) : null}

      <div style={{ marginBlockStart: 24 }}>
        <AttributionBar />
      </div>

      {/* ---- contribute tray ---- */}
      {trayCells.length > 0 ? (
        <div className={styles.tray}>
          <span className={styles.count}>
            {t("tray.count", { n: trayCells.length })}
          </span>
          <span className={styles.est}>{t("tray.calls", { calls: trayCalls })}</span>
          <div className={styles.trayActions}>
            <button className="btn btn-ghost" onClick={() => setSelected(new Set())}>
              {t("tray.clear")}
            </button>
            <Link className="btn btn-violet" href={contributeHref}>
              {t("tray.go")}
            </Link>
          </div>
        </div>
      ) : (
        <p className={styles.hint}>{t("tray.hint")}</p>
      )}
    </div>
  );
}

function ArmSection({
  arm,
  locale,
  selected,
  onToggle,
  collapsed,
  onToggleArm,
  t,
}: {
  arm: BoardArm;
  locale: string;
  selected: Set<string>;
  onToggle: (key: string) => void;
  collapsed: boolean;
  onToggleArm: () => void;
  t: ReturnType<typeof useTranslations>;
}) {
  const desc = locale === "zh" ? arm.harnessZh : arm.harnessEn;
  return (
    <section className={styles.arm}>
      <button
        type="button"
        className={styles.armHead}
        aria-expanded={!collapsed}
        onClick={onToggleArm}
      >
        <span className={styles.armChevron} aria-hidden>
          {collapsed ? "▸" : "▾"}
        </span>
        <span className={styles.armName}>{arm.arm}</span>
        <span className={styles.armGroupTag}>{arm.armGroup}</span>
        <span className={styles.armCount}>{t("filter.models", { n: arm.rows.length })}</span>
        {desc ? <span className={styles.armDesc}>{desc}</span> : null}
      </button>
      {!collapsed ? (
        <div className={styles.rows} role="list">
          {arm.rows.map((row) => (
            <div className={styles.row} role="listitem" key={`${row.family}|${row.modelId}`}>
              <div className={styles.rowLabel}>
                <span className={styles.rid}>{row.modelId}</span>
                <span className={styles.rfam}>{row.family}</span>
              </div>
              <div className={styles.chips}>
                {row.cells.map((cell, i) => (
                  <Chip key={i} cell={cell} selected={selected} onToggle={onToggle} t={t} />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

/** One effort chip: a colour-blind-safe status glyph + the effort token (for an
 *  effortless model only the glyph shows — no bare "—"). Status is carried by
 *  glyph AND colour/fill/border; gap chips (missing/attempted) are clickable and
 *  reveal a "+" on hover. */
function Chip({
  cell,
  selected,
  onToggle,
  t,
}: {
  cell: BoardCell;
  selected: Set<string>;
  onToggle: (key: string) => void;
  t: ReturnType<typeof useTranslations>;
}) {
  const gradeCls = cell.grade && GRADE_CLASS[cell.grade] ? GRADE_CLASS[cell.grade] : "";
  const base = `${styles.chip} ${STATUS_CLASS[cell.status]} ${gradeCls}`.trim();

  // tooltip: status + per-variant valid/total (honesty kit) + awaiting note
  const frac = cell.fractions
    ? cell.fractions.map((f) => `${f.variant} ${f.valid}/${f.total}`).join(" · ")
    : "";
  const awaitNote = cell.awaitingEn || cell.awaitingZh ? ` · ${cell.awaitingEn ?? cell.awaitingZh}` : "";
  const title = `${cell.modelId}${cell.effort ? ` @ ${cell.effort}` : ""} · ${t(`status.${cell.status}`)}${
    frac ? ` · ${frac}` : ""
  }${cell.grade && cell.grade !== "qualified" ? ` · ${cell.grade}` : ""}${awaitNote}`;

  const glyph = (
    <span className={styles.chipGlyph} aria-hidden>
      {STATUS_GLYPH[cell.status]}
    </span>
  );
  const label =
    cell.effort !== null ? <span className={styles.chipText}>{cell.effort}</span> : null;

  const selectable = cell.status === "missing" || cell.status === "attempted";
  if (selectable) {
    const key = cellKey(cell);
    const isSel = selected.has(key);
    return (
      <button
        type="button"
        className={`${base} ${styles.chipBtn} ${isSel ? styles.selected : ""}`.trim()}
        title={title}
        aria-pressed={isSel}
        aria-label={title}
        onClick={() => onToggle(key)}
      >
        {glyph}
        {label}
        <span className={styles.plus} aria-hidden>
          +
        </span>
      </button>
    );
  }
  return (
    <span className={base} title={title} aria-label={title}>
      {glyph}
      {label}
    </span>
  );
}
