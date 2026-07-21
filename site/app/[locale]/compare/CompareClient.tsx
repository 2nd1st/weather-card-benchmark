"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { Modal } from "@/app/components/Modal";
import { EmptyState } from "@/app/components/EmptyState";
import { AttributionBar } from "@/app/components/AttributionBar";
import { ArmBadge, GradeBadge, PatchedBadge } from "@/app/components/Badge";
import { CITIES, DELAY_PRESETS } from "@/lib/cities";
import type { LiveParams } from "@/lib/live/inject";
import { VARIANTS, type Variant } from "@/lib/variant";
import type { Grade } from "@/app/components/Badge";
import { CompareFrame } from "./CompareFrame";
import styles from "./compare.module.css";

// CompareClient (spec §4 /compare, owner B3). The whole interactive surface:
//   - a searchable registry picker (Modal) to pick 2–4 configs;
//   - ONE shared city + date + delay control row + a synchronized "reload all";
//   - a variant toggle (P-min / P-q) and per-pane slot cycling;
//   - side-by-side live CardFrames (untrusted output, sandboxed, interactive);
//   - a tombstone panel for a pick with no valid slot in the chosen variant;
//   - URL state (?ids=&city=&date=&variant=&delay=) synced via replaceState so
//     the arrangement is shareable and drives OG via /api/og/compare?ids=.
// Partial data never crashes: an empty registry → empty state; a stale id → dropped.

export interface CompareSlot {
  index: number;
  state: string;
  /** ORIGINAL card.html URL — present only for valid slots. */
  cardUrl: string | null;
  thumbUrl: string | null;
}
export interface CompareEntry {
  configId: string;
  family: string;
  modelId: string;
  effort: string | null;
  arm: string;
  grade: Grade;
  label: string;
  hasPatch: boolean;
  validCount: Record<Variant, number>;
  slots: Record<Variant, CompareSlot[]>;
}

export interface CompareInitial {
  ids: string[];
  city: string;
  date: string;
  variant: Variant;
  delay: number;
}

// Was 4 (2026-07-20: raise the cap). The .panes grid is auto-fit
// minmax(300px,1fr) so it wraps to rows for any count; the only real ceiling is
// that each pane is a LIVE iframe, so this stays a generous-but-bounded 12 rather
// than truly unlimited (188 live cards would freeze the tab).
const MAX_PICKS = 12;

function cityByName(name: string) {
  return CITIES.find((c) => c.name === name) ?? CITIES[0];
}

export function CompareClient({
  entries,
  initial,
}: {
  entries: CompareEntry[];
  initial: CompareInitial;
}) {
  const t = useTranslations("compare");
  const byId = useMemo(() => {
    const m = new Map<string, CompareEntry>();
    for (const e of entries) m.set(e.configId, e);
    return m;
  }, [entries]);

  // keep only ids that still exist in the registry, cap at MAX_PICKS
  const [pickedIds, setPickedIds] = useState<string[]>(() =>
    initial.ids.filter((id) => byId.has(id)).slice(0, MAX_PICKS),
  );
  const [variant, setVariant] = useState<Variant>(initial.variant);
  const [cityName, setCityName] = useState<string>(initial.city);
  const [date, setDate] = useState<string>(initial.date);
  const [delay, setDelay] = useState<number>(initial.delay);
  const [generation, setGeneration] = useState(0);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  // per-pane valid-slot position, keyed by `${configId}::${variant}`
  const [slotPos, setSlotPos] = useState<Record<string, number>>({});

  const picked = useMemo(
    () => pickedIds.map((id) => byId.get(id)).filter((e): e is CompareEntry => !!e),
    [pickedIds, byId],
  );

  const city = cityByName(cityName);
  const params: LiveParams = useMemo(
    () => ({ lat: city.lat, lon: city.lon, name: city.name, date }),
    [city.lat, city.lon, city.name, date],
  );

  // reload all frames in sync whenever a shared control changes
  const bump = useCallback(() => setGeneration((g) => g + 1), []);

  function onCity(name: string) {
    setCityName(name);
    bump();
  }
  function onDate(v: string) {
    setDate(v);
    bump();
  }
  function onDelay(ms: number) {
    setDelay(ms);
    bump();
  }

  function addConfig(id: string) {
    setPickedIds((prev) =>
      prev.includes(id) || prev.length >= MAX_PICKS ? prev : [...prev, id],
    );
  }
  function removeConfig(id: string) {
    setPickedIds((prev) => prev.filter((x) => x !== id));
  }

  // --- URL state sync (shareable; drives OG). replaceState, no reload. ---
  useEffect(() => {
    if (typeof window === "undefined") return;
    const q = new URLSearchParams();
    // Only carry the shared arrangement once at least one config is picked —
    // a bare /compare visit keeps a clean URL.
    if (pickedIds.length) {
      q.set("ids", pickedIds.join(","));
      if (cityName) q.set("city", cityName);
      if (date) q.set("date", date);
      if (variant !== "P-min") q.set("variant", variant);
      if (delay) q.set("delay", String(delay));
    }
    const qs = q.toString();
    const url = window.location.pathname + (qs ? `?${qs}` : "");
    window.history.replaceState(null, "", url);
  }, [pickedIds, cityName, date, variant, delay]);

  function copyLink() {
    if (typeof window === "undefined") return;
    navigator.clipboard?.writeText(window.location.href).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      },
      () => {},
    );
  }

  const canAdd = picked.length < MAX_PICKS;

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <div>
          <h1>{t("title")}</h1>
          <p>{t("subtitle")}</p>
        </div>
        <div className={styles.headActions}>
          <button
            className="btn btn-ghost"
            onClick={copyLink}
            disabled={picked.length === 0}
          >
            {copied ? t("share.copied") : t("share.copy")}
          </button>
          <button
            className="btn btn-acc"
            onClick={() => setPickerOpen(true)}
            disabled={!canAdd}
          >
            + {t("add")}
          </button>
        </div>
      </div>

      {/* shared control row */}
      <div className={`panelbox ${styles.controls}`}>
        <div className={styles.ctl}>
          <span className={styles.gl}>{t("controls.variant")}</span>
          <div className="seg">
            {VARIANTS.map((v) => (
              <button
                key={v}
                className={variant === v ? "on" : undefined}
                title={t(`controls.variantTitle.${v}`)}
                onClick={() => {
                  setVariant(v);
                  bump();
                }}
              >
                {v}
              </button>
            ))}
          </div>
        </div>
        <div className={styles.ctl}>
          <span className={styles.gl}>{t("controls.city")}</span>
          <select
            className={styles.sel}
            value={cityName}
            onChange={(e) => onCity(e.target.value)}
          >
            {CITIES.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.ctl}>
          <span className={styles.gl}>{t("controls.date")}</span>
          <input
            className={styles.sel}
            type="date"
            value={date}
            onChange={(e) => onDate(e.target.value)}
          />
        </div>
        <div className={styles.ctl}>
          <span className={styles.gl}>{t("controls.delay")}</span>
          <select
            className={styles.sel}
            value={delay}
            onChange={(e) => onDelay(Number(e.target.value))}
          >
            {DELAY_PRESETS.map((d) => (
              <option key={d.ms} value={d.ms}>
                {d.label}
              </option>
            ))}
          </select>
        </div>
        <span className={styles.spacer} />
        <button className="btn" onClick={bump} title={t("controls.reloadHint")}>
          ↻ {t("controls.reload")}
        </button>
      </div>

      {/* one-line gloss on the P-min / P-q shorthand */}
      <p className={styles.variantHint}>{t("controls.variantHint")}</p>

      {/* panes */}
      {picked.length === 0 ? (
        <EmptyState
          title={t("empty.title")}
          message={t("empty.message")}
          action={
            <button className="btn btn-acc" onClick={() => setPickerOpen(true)}>
              + {t("add")}
            </button>
          }
        />
      ) : (
        <div className={styles.stage}>
          <div className={styles.panes}>
            {picked.map((e) => (
              <Pane
                key={e.configId}
                entry={e}
                variant={variant}
                params={params}
                delayMs={delay}
                generation={generation}
                pos={slotPos[`${e.configId}::${variant}`] ?? 0}
                onCycle={(next) =>
                  setSlotPos((prev) => ({
                    ...prev,
                    [`${e.configId}::${variant}`]: next,
                  }))
                }
                onRemove={() => removeConfig(e.configId)}
              />
            ))}
          </div>
          <div className={styles.attrRow}>
            <AttributionBar />
          </div>
        </div>
      )}

      <p className="dim" style={{ marginTop: 18, fontSize: 12 }}>
        {t("live.note")}
      </p>

      <ConfigPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        entries={entries}
        picked={pickedIds}
        variant={variant}
        full={!canAdd}
        onAdd={(id) => {
          addConfig(id);
          if (pickedIds.length + 1 >= MAX_PICKS) setPickerOpen(false);
        }}
        onRemove={removeConfig}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// One compare pane: header (badges + slot cycle + remove) and either a live
// frame or a tombstone when the chosen variant has no valid slot.
// ---------------------------------------------------------------------------

function Pane({
  entry,
  variant,
  params,
  delayMs,
  generation,
  pos,
  onCycle,
  onRemove,
}: {
  entry: CompareEntry;
  variant: Variant;
  params: LiveParams;
  delayMs: number;
  generation: number;
  pos: number;
  onCycle: (next: number) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("compare");
  const validSlots = entry.slots[variant].filter(
    (s) => s.state === "valid" && s.cardUrl,
  );
  const clamped = validSlots.length ? ((pos % validSlots.length) + validSlots.length) % validSlots.length : 0;
  const slot = validSlots[clamped];

  return (
    <article className={`panelbox ${styles.pane}`}>
      <div className={styles.paneHead}>
        <span className={styles.paneName} title={entry.label}>
          {entry.modelId}
        </span>
        <div className={styles.paneChips}>
          {entry.effort ? <span className="b b-effort">{entry.effort}</span> : null}
          <ArmBadge arm={entry.arm} />
          <GradeBadge grade={entry.grade} />
          {entry.hasPatch ? <PatchedBadge /> : null}
        </div>
        <div className={styles.paneCtl}>
          {validSlots.length > 1 ? (
            <span className={styles.slotcycle}>
              <button
                onClick={() => onCycle(clamped - 1)}
                aria-label="previous slot"
              >
                ‹
              </button>
              <span>
                {t("slot.cycle", {
                  index: clamped + 1,
                  total: validSlots.length,
                })}
              </span>
              <button onClick={() => onCycle(clamped + 1)} aria-label="next slot">
                ›
              </button>
            </span>
          ) : validSlots.length === 1 ? (
            <span>{t("slot.one")}</span>
          ) : null}
          <Link href={`/card/${encodeURIComponent(entry.configId)}`}>
            {t("openCard")}
          </Link>
          <button
            className={styles.paneClose}
            onClick={onRemove}
            aria-label={t("remove")}
          >
            ✕
          </button>
        </div>
      </div>

      <div className={styles.frameWrap}>
        {slot && slot.cardUrl ? (
          <CompareFrame
            cardUrl={slot.cardUrl}
            params={params}
            delayMs={delayMs}
            generation={generation}
            title={`${entry.label} slot ${slot.index}`}
            loadingLabel={t("live.loading")}
            unavailableLabel={t("live.unavailable")}
          />
        ) : (
          <Tombstone entry={entry} variant={variant} />
        )}
      </div>
    </article>
  );
}

function Tombstone({ entry, variant }: { entry: CompareEntry; variant: Variant }) {
  const t = useTranslations("compare");
  const slots = entry.slots[variant];
  return (
    <div className={styles.tomb}>
      <div className={styles.tombTitle}>{t("tombstone.title")}</div>
      <div className={styles.tombMsg}>
        {t("tombstone.message", { variant })}
      </div>
      {slots.length ? (
        <div className={styles.schips}>
          {slots.map((s) => (
            <span
              key={s.index}
              className={s.state === "valid" ? `${styles.schip} ${styles.ok}` : styles.schip}
            >
              <i />
              slot {s.index} · {s.state}
            </span>
          ))}
        </div>
      ) : null}
      <Link href={`/card/${encodeURIComponent(entry.configId)}`}>
        {t("openCard")}
      </Link>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Config picker — searchable registry list inside a Modal.
// ---------------------------------------------------------------------------

function ConfigPicker({
  open,
  onClose,
  entries,
  picked,
  variant,
  full,
  onAdd,
  onRemove,
}: {
  open: boolean;
  onClose: () => void;
  entries: CompareEntry[];
  picked: string[];
  variant: Variant;
  full: boolean;
  onAdd: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  const t = useTranslations("compare");
  const [q, setQ] = useState("");

  const results = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((e) =>
      `${e.label} ${e.configId} ${e.family} ${e.arm} ${e.grade}`
        .toLowerCase()
        .includes(needle),
    );
  }, [q, entries]);

  return (
    <Modal open={open} onClose={onClose} title={t("picker.title")} labelledBy="cmp-pick">
      <input
        className={styles.pickSearch}
        placeholder={t("picker.search")}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        autoFocus
      />
      {full ? <div className={styles.pickFull}>{t("picker.full")}</div> : null}
      {results.length === 0 ? (
        <div className={styles.pickEmpty}>{t("picker.noResults")}</div>
      ) : (
        <div className={styles.pickList}>
          {results.map((e) => {
            const isPicked = picked.includes(e.configId);
            const validHere = e.validCount[variant];
            return (
              <button
                key={e.configId}
                className={isPicked ? `${styles.pickRow} ${styles.on}` : styles.pickRow}
                disabled={!isPicked && full}
                onClick={() => (isPicked ? onRemove(e.configId) : onAdd(e.configId))}
              >
                <span className={styles.pickMain}>
                  <span className={styles.pickName} title={e.label}>
                    {e.modelId}
                    {e.effort ? ` @${e.effort}` : ""}
                  </span>
                </span>
                <span className={styles.pickBadges}>
                  <span className="b b-arm">{e.arm}</span>
                  <GradeBadge grade={e.grade} />
                  <span className="dim" style={{ font: "10px var(--font-mono)" }}>
                    {t("picker.validShort", { count: validHere })}
                  </span>
                </span>
                {isPicked ? <span className={styles.pickState}>{t("picker.added")}</span> : null}
              </button>
            );
          })}
        </div>
      )}
    </Modal>
  );
}
