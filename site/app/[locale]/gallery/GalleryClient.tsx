"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";

import { useRouter, usePathname, Link } from "@/i18n/navigation";
import {
  FilterBar,
  FacetGroup,
  FacetChip,
  FilterToggle,
  SearchInput,
  ToolsSide,
} from "@/app/components/FilterBar";
import { CardTile } from "@/app/components/CardTile";
import { CardFrame } from "@/app/components/CardFrame";
import { EmptyState } from "@/app/components/EmptyState";
import type { Grade } from "@/app/components/Badge";
import type { GalleryEntry, GalleryFacets } from "./types";
import styles from "./gallery.module.css";

// The gallery client: filter state, URL round-trip, virtualized-by-pagination
// grid, and a budgeted live-render layer. Order is FIXED (registry browse order
// — family → model → effort → arm); there is no sort control. Every facet +
// live / variant / density / q round-trips through the URL so a filtered view is
// a shareable link (and survives the LocaleSwitch, which preserves the query).

const PAGE = 48; // pagination window; grid stays bounded on first paint (§4).
const LIVE_CAP = 8; // hard concurrency cap on live iframes (§4 budget).
const DELAYS = [0, 600, 1500]; // simulated latency options for live mode (ms).
const DENSITY_KEY = "wcb.gallery.density";
// render-census flags: browser-local only, never sent anywhere.
const FLAG_STORE = "wcb.gallery.renderFlags";
// The 2026-07-19/20 render census is finished, so the flag buttons are parked
// rather than deleted — the sweep tooling (slot pin, localStorage records,
// export) is intact and one flag flips it back on for the next census.
const FLAGS_ENABLED = false;

/** Stable identity for a flagged card: config + variant + the slot on screen.
 *  Suffixed with ::shot or ::live by the caller. */
function flagKeyFor(e: GalleryEntry, variant: string, slotSel: number | null): string {
  const idx =
    slotSel === null
      ? (e.rep[variant]?.slotIndex ?? "none")
      : ((e.allSlots[variant] ?? []).find((s) => s.slotIndex === slotSel)?.slotIndex ?? "none");
  return `${e.configId}::${variant}::${idx}`;
}

type Density = "comfortable" | "compact";

function parseCsv(v: string | null): string[] {
  if (!v) return [];
  return v
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function fmtDelay(ms: number): string {
  return ms <= 0 ? "0s" : `${(ms / 1000).toFixed(1)}s`;
}

export function GalleryClient({
  entries,
  facets,
  total,
}: {
  entries: GalleryEntry[];
  facets: GalleryFacets;
  total: number;
}) {
  const t = useTranslations("gallery");
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // ---- state (initialized from the URL so shared links restore verbatim) ----
  const [familySel, setFamilySel] = useState<string[]>(() =>
    parseCsv(searchParams.get("family")),
  );
  const [armSel, setArmSel] = useState<string[]>(() => parseCsv(searchParams.get("arm")));
  const [gradeSel, setGradeSel] = useState<string[]>(() =>
    parseCsv(searchParams.get("grade")),
  );
  const [q, setQ] = useState(() => searchParams.get("q") ?? "");
  const [live, setLive] = useState(() => searchParams.get("live") === "1");
  const [variant, setVariant] = useState<string>(() =>
    searchParams.get("variant") === "P-q" ? "P-q" : "P-min",
  );
  const [density, setDensity] = useState<Density>(() =>
    searchParams.get("density") === "compact" ? "compact" : "comfortable",
  );
  const [validOnly, setValidOnly] = useState(() => searchParams.get("valid") === "1");
  const [hasPatch, setHasPatch] = useState(() => searchParams.get("patch") === "1");
  const [showDev, setShowDev] = useState(() => searchParams.get("dev") === "1");
  // ---- render-census debug tools (opt-in; invisible until a slot is pinned) ----
  const [slotSel, setSlotSel] = useState<number | null>(() => {
    const raw = searchParams.get("slot");
    if (raw === null || raw === "") return null;
    const n = Number(raw);
    return Number.isInteger(n) && n >= 0 ? n : null;
  });
  // flagged cards persist in localStorage so a multi-session sweep survives
  // reloads; the set is exportable as a work list. Never leaves the browser.
  const [flags, setFlags] = useState<Set<string>>(new Set());
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(FLAG_STORE);
      if (raw) setFlags(new Set(JSON.parse(raw) as string[]));
    } catch {
      /* corrupt/absent storage → start empty */
    }
  }, []);
  const toggleFlag = useCallback((key: string) => {
    setFlags((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      try {
        window.localStorage.setItem(FLAG_STORE, JSON.stringify([...next]));
      } catch {
        /* storage full/blocked → in-memory only */
      }
      return next;
    });
  }, []);

  const [limit, setLimit] = useState(PAGE);
  const [liveGen, setLiveGen] = useState(0);
  const [delayMs, setDelayMs] = useState(0);
  // mobile-only: the facet body collapses behind a "Filters · N" disclosure so
  // the first result is not pushed far below the fold (audit §3). Desktop CSS
  // shows the body regardless of this flag.
  const [filtersOpen, setFiltersOpen] = useState(false);

  // ---- live budget: at most LIVE_CAP in-viewport tiles render a live frame ----
  const [visibleLiveIds, setVisibleLiveIds] = useState<string[]>([]);
  const enterLive = useCallback((id: string) => {
    setVisibleLiveIds((o) => (o.includes(id) ? o : [...o, id]));
  }, []);
  const leaveLive = useCallback((id: string) => {
    setVisibleLiveIds((o) => (o.includes(id) ? o.filter((x) => x !== id) : o));
  }, []);
  const activeLive = useMemo(
    () => new Set(visibleLiveIds.slice(0, LIVE_CAP)),
    [visibleLiveIds],
  );
  useEffect(() => {
    if (!live) setVisibleLiveIds([]);
  }, [live]);

  // ---- density persistence (URL wins; else the saved preference) ----
  useEffect(() => {
    if (!searchParams.get("density")) {
      try {
        const saved = localStorage.getItem(DENSITY_KEY);
        if (saved === "compact" || saved === "comfortable") setDensity(saved);
      } catch {
        /* localStorage unavailable — keep the default */
      }
    }
    // run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(DENSITY_KEY, density);
    } catch {
      /* ignore */
    }
  }, [density]);

  // ---- URL round-trip (replace, no history spam, preserves locale) ----
  const firstPush = useRef(true);
  useEffect(() => {
    if (firstPush.current) {
      firstPush.current = false;
      return;
    }
    const p = new URLSearchParams();
    if (familySel.length) p.set("family", familySel.join(","));
    if (armSel.length) p.set("arm", armSel.join(","));
    if (gradeSel.length) p.set("grade", gradeSel.join(","));
    if (q.trim()) p.set("q", q.trim());
    if (live) p.set("live", "1");
    if (variant !== "P-min") p.set("variant", variant);
    if (density !== "comfortable") p.set("density", density);
    if (validOnly) p.set("valid", "1");
    if (hasPatch) p.set("patch", "1");
    if (showDev) p.set("dev", "1");
    if (slotSel !== null) p.set("slot", String(slotSel));
    const qs = p.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [
    familySel,
    armSel,
    gradeSel,
    q,
    live,
    variant,
    density,
    validOnly,
    hasPatch,
    slotSel,
    showDev,
    router,
    pathname,
  ]);

  // ---- grade facets: dev is hidden until the honesty toggle is on (§4) ----
  const visibleGrades = useMemo<Grade[]>(
    () => (showDev ? facets.grades : facets.grades.filter((g) => g !== "dev")),
    [facets.grades, showDev],
  );
  const visibleGradeSet = useMemo(() => new Set(visibleGrades), [visibleGrades]);
  const gradeSelActive = useMemo(
    () => gradeSel.filter((g) => visibleGradeSet.has(g as Grade)),
    [gradeSel, visibleGradeSet],
  );

  const familySet = useMemo(() => new Set(familySel), [familySel]);
  const armSet = useMemo(() => new Set(armSel), [armSel]);
  const gradeActiveSet = useMemo(() => new Set(gradeSelActive), [gradeSelActive]);
  const needle = q.trim().toLowerCase();

  // ---- filter (OR within a facet, AND across facets); order untouched ----
  const matched = useMemo(() => {
    return entries.filter((e) => {
      // configs with ZERO valid output in every variant are excluded from the
      // gallery entirely (2026-07-18) — their failure signal lives on the
      // progress board's attempted cells, not as blank tiles here.
      if (!Object.values(e.validByVariant).some(Boolean)) return false;
      if (!visibleGradeSet.has(e.grade)) return false;
      if (gradeActiveSet.size && !gradeActiveSet.has(e.grade)) return false;
      if (familySet.size && !familySet.has(e.family)) return false;
      if (armSet.size && !armSet.has(e.arm)) return false;
      if (needle && !e.modelId.toLowerCase().includes(needle)) return false;
      if (validOnly && !e.validByVariant[variant]) return false;
      if (hasPatch && !e.patched) return false;
      // slot debug pin: keep only configs that actually HAVE that slot index in
      // the current variant, so the sweep is an apples-to-apples slot view.
      if (
        slotSel !== null &&
        !(e.allSlots[variant] ?? []).some((s) => s.slotIndex === slotSel)
      ) {
        return false;
      }
      return true;
    });
  }, [
    entries,
    visibleGradeSet,
    gradeActiveSet,
    familySet,
    armSet,
    needle,
    validOnly,
    hasPatch,
    variant,
    slotSel,
  ]);

  // reset the pagination window whenever the result set changes
  useEffect(() => {
    setLimit(PAGE);
  }, [matched]);

  const shown = useMemo(() => matched.slice(0, limit), [matched, limit]);
  const hiddenDev = useMemo(
    () => (showDev ? 0 : entries.filter((e) => e.grade === "dev").length),
    [entries, showDev],
  );
  // no-output configs excluded from the grid (honest pointer to the progress board)
  const hiddenNoOutput = useMemo(
    () => entries.filter((e) => !Object.values(e.validByVariant).some(Boolean)).length,
    [entries],
  );

  const nf = useMemo(() => new Intl.NumberFormat(locale), [locale]);

  // ---- toggle helpers ----
  const toggle = (arr: string[], set: (v: string[]) => void) => (v: string) =>
    set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const toggleFamily = toggle(familySel, setFamilySel);
  const toggleArm = toggle(armSel, setArmSel);
  const toggleGrade = toggle(gradeSel, setGradeSel);

  const clearFilters = () => {
    setFamilySel([]);
    setArmSel([]);
    setGradeSel([]);
    setQ("");
    setValidOnly(false);
    setHasPatch(false);
  };

  const armChip = (arm: string): ReactNode => (
    <FacetChip
      key={arm}
      label={arm}
      active={armSet.has(arm)}
      onToggle={() => toggleArm(arm)}
    />
  );

  // active-filter summary shown while the mobile panel is collapsed — every
  // in-effect facet as a removable chip (audit §3).
  const activeFilters = useMemo(() => {
    const out: { key: string; label: string; onRemove: () => void }[] = [];
    for (const f of familySel) out.push({ key: `fam:${f}`, label: f, onRemove: () => toggleFamily(f) });
    for (const a of armSel) out.push({ key: `arm:${a}`, label: a, onRemove: () => toggleArm(a) });
    for (const g of gradeSelActive) out.push({ key: `grade:${g}`, label: g, onRemove: () => toggleGrade(g) });
    if (q.trim()) out.push({ key: "q", label: `"${q.trim()}"`, onRemove: () => setQ("") });
    if (validOnly) out.push({ key: "valid", label: t("toggle.validOnly"), onRemove: () => setValidOnly(false) });
    if (hasPatch) out.push({ key: "patch", label: t("toggle.hasPatch"), onRemove: () => setHasPatch(false) });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [familySel, armSel, gradeSelActive, q, validOnly, hasPatch]);

  return (
    <div className={styles.page}>
      <header className="gallery-head" style={{ margin: "8px 0 22px" }}>
        <h1 className="sec-title">{t("title")}</h1>
        <p className="sec-sub">{t("intro")}</p>
      </header>

      <FilterBar ariaLabel={t("title")}>
        <div className={styles.barHead}>
          <SearchInput
            value={q}
            onChange={setQ}
            placeholder={t("search.placeholder")}
            ariaLabel={t("search.label")}
          />
          <button
            type="button"
            className={styles.filtersToggle}
            data-open={filtersOpen}
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen((o) => !o)}
          >
            {filtersOpen
              ? t("filters.hide")
              : t("filters.show", { count: nf.format(matched.length) })}
            <span className={styles.caret} aria-hidden>
              ▾
            </span>
          </button>
        </div>

        {activeFilters.length > 0 ? (
          <div className={styles.activeChips}>
            <span className={styles.activeLabel}>{t("filters.active")}</span>
            {activeFilters.map((f) => (
              <button
                key={f.key}
                type="button"
                className={styles.activeChip}
                onClick={f.onRemove}
                aria-label={`${t("filters.remove")}: ${f.label}`}
              >
                {f.label}
                <span className={styles.x} aria-hidden>
                  ×
                </span>
              </button>
            ))}
            <button type="button" className={styles.activeClear} onClick={clearFilters}>
              {t("filters.clearAll")}
            </button>
          </div>
        ) : null}

        <div className={styles.facetBody} data-open={filtersOpen}>
          {facets.families.length > 0 ? (
            <FacetGroup label={t("facet.family")}>
              {facets.families.map((f) => (
                <FacetChip
                  key={f}
                  label={f}
                  active={familySet.has(f)}
                  onToggle={() => toggleFamily(f)}
                />
              ))}
            </FacetGroup>
          ) : null}

          {facets.armGroups.api.length > 0 ? (
            <FacetGroup label={`${t("facet.arm")} · ${t("facet.api")}`}>
              {facets.armGroups.api.map(armChip)}
            </FacetGroup>
          ) : null}
          {facets.armGroups.harness.length > 0 ? (
            <FacetGroup label={t("facet.harness")}>
              {facets.armGroups.harness.map(armChip)}
            </FacetGroup>
          ) : null}

          {visibleGrades.length > 0 ? (
            <FacetGroup label={t("facet.grade")}>
              {visibleGrades.map((g) => (
                <FacetChip
                  key={g}
                  label={g}
                  active={gradeActiveSet.has(g)}
                  onToggle={() => toggleGrade(g)}
                  violet={g === "community"}
                />
              ))}
            </FacetGroup>
          ) : null}

          <ToolsSide>
            <FilterToggle label={t("toggle.live")} on={live} onToggle={() => setLive(!live)} />
          <FilterToggle
            label={t("toggle.validOnly")}
            on={validOnly}
            onToggle={() => setValidOnly(!validOnly)}
          />
          <FilterToggle
            label={t("toggle.hasPatch")}
            on={hasPatch}
            onToggle={() => setHasPatch(!hasPatch)}
          />
          <FilterToggle
            label={t("toggle.showDev")}
            on={showDev}
            onToggle={() => setShowDev(!showDev)}
          />

          <div className="seg" role="group" aria-label={t("variant.label")}>
            {facets.variants.map((v) => (
              <button
                key={v}
                type="button"
                className={variant === v ? "on" : undefined}
                onClick={() => setVariant(v)}
              >
                {v}
              </button>
            ))}
          </div>

          <div className="seg" role="group" aria-label={t("density.label")}>
            <button
              type="button"
              className={density === "comfortable" ? "on" : undefined}
              onClick={() => setDensity("comfortable")}
            >
              {t("density.comfortable")}
            </button>
            <button
              type="button"
              className={density === "compact" ? "on" : undefined}
              onClick={() => setDensity("compact")}
            >
              {t("density.compact")}
            </button>
          </div>

          {live ? (
            <>
              <div className="seg" role="group" aria-label={t("delay.label")}>
                {DELAYS.map((d) => (
                  <button
                    key={d}
                    type="button"
                    className={delayMs === d ? "on" : undefined}
                    onClick={() => setDelayMs(d)}
                  >
                    {fmtDelay(d)}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setLiveGen((g) => g + 1)}
              >
                {t("reloadLive")}
              </button>
            </>
          ) : null}

          {/* ---- render-census debug tools (maintainer-facing; labels stay
                 English like the other instrument chips). "auto" keeps the
                 normal representative-slot gallery; pinning an index turns the
                 grid into a same-slot sweep and reveals the flag buttons. ---- */}
          {facets.slotIndices.length > 1 ? (
            <div className="seg" role="group" aria-label="debug: slot">
              <button
                type="button"
                className={slotSel === null ? "on" : undefined}
                onClick={() => setSlotSel(null)}
                title="representative slot per config (default gallery)"
              >
                slot: auto
              </button>
              {facets.slotIndices.map((i) => (
                <button
                  key={i}
                  type="button"
                  className={slotSel === i ? "on" : undefined}
                  onClick={() => setSlotSel(i)}
                  title={`pin slot ${i} across every config that has it`}
                >
                  {i}
                </button>
              ))}
            </div>
          ) : null}

          {FLAGS_ENABLED && slotSel !== null ? (
            <div className="seg" role="group" aria-label="render flags">
              <button type="button" disabled title="cards you marked as broken">
                ⚑ {flags.size}
              </button>
              <button
                type="button"
                disabled={flags.size === 0}
                title="copy the flagged list as JSON"
                onClick={() => {
                  const payload = JSON.stringify([...flags].sort(), null, 2);
                  navigator.clipboard?.writeText(payload).catch(() => {
                    /* clipboard blocked — fall back to a console dump */
                    console.log("[render-flags]", payload);
                  });
                }}
              >
                copy
              </button>
              <button
                type="button"
                disabled={flags.size === 0}
                title="clear every recorded flag"
                onClick={() => {
                  setFlags(new Set());
                  try {
                    window.localStorage.removeItem(FLAG_STORE);
                  } catch {
                    /* ignore */
                  }
                }}
              >
                clear
              </button>
            </div>
          ) : null}
          </ToolsSide>
        </div>
      </FilterBar>

      {showDev ? (
        <p
          className="gallery-devnote sec-sub"
          style={{ color: "var(--warn)", margin: "-8px 0 14px" }}
        >
          {t("devNote")}
        </p>
      ) : null}

      <div className={styles.resultbar}>
        <span className={styles.resultCount}>
          {t("result.showing", {
            shown: nf.format(matched.length),
            total: nf.format(total),
          })}
        </span>
        <span className={styles.resultDiag}>
          {live ? <span>{t("result.live")}</span> : null}
          <span>{t("result.order")}</span>
          {hiddenNoOutput > 0 ? (
            <span>
              <Link href="/progress" className="dim">
                {t("result.noOutputHidden", { count: nf.format(hiddenNoOutput) })}
              </Link>
            </span>
          ) : null}
        </span>
      </div>

      {matched.length === 0 ? (
        <EmptyState
          title={t("empty.title")}
          message={t("empty.message")}
          action={
            <div
              className="gallery-empty-actions"
              style={{ display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap" }}
            >
              <button type="button" className="btn" onClick={clearFilters}>
                {t("empty.clear")}
              </button>
              {hiddenDev > 0 ? (
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => setShowDev(true)}
                >
                  {t("empty.enableDev", { count: nf.format(hiddenDev) })}
                </button>
              ) : null}
            </div>
          }
        />
      ) : (
        <>
          <div className={density === "compact" ? "grid compact" : "grid"}>
            {shown.map((e) => (
              <GalleryTile
                key={e.configId}
                entry={e}
                variant={variant}
                live={live}
                isActive={activeLive.has(e.configId)}
                onEnter={enterLive}
                onLeave={leaveLive}
                delayMs={delayMs}
                generation={liveGen}
                liveLabel={t("toggle.live")}
                blankLabel={t("blank")}
                slotSel={slotSel}
                flaggedShot={flags.has(`${flagKeyFor(e, variant, slotSel)}::shot`)}
                flaggedLive={flags.has(`${flagKeyFor(e, variant, slotSel)}::live`)}
                onToggleFlag={toggleFlag}
              />
            ))}
          </div>
          {matched.length > limit ? (
            <div className={styles.loadWrap}>
              <button
                type="button"
                className={`btn ${styles.loadBtn}`}
                onClick={() => setLimit((l) => l + PAGE)}
              >
                {t("loadMore", {
                  count: nf.format(Math.min(PAGE, matched.length - limit)),
                })}
              </button>
              <span className={styles.loadRemaining}>
                {t("loadRemaining", {
                  count: nf.format(matched.length - limit),
                })}
              </span>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// One grid cell. Off-live it is a still thumb; on-live and within the budget it
// mounts a sandboxed CardFrame. It reports its own viewport intersection to the
// parent's budget so only the on-screen ≤8 tiles ever run untrusted card JS.
// ---------------------------------------------------------------------------
function GalleryTile({
  entry,
  variant,
  live,
  isActive,
  onEnter,
  onLeave,
  delayMs,
  generation,
  liveLabel,
  blankLabel,
  slotSel,
  flaggedShot,
  flaggedLive,
  onToggleFlag,
}: {
  entry: GalleryEntry;
  variant: string;
  live: boolean;
  isActive: boolean;
  onEnter: (id: string) => void;
  onLeave: (id: string) => void;
  delayMs: number;
  generation: number;
  liveLabel: string;
  blankLabel: string;
  /** debug: pin a specific slot index instead of the representative one. */
  slotSel: number | null;
  /** debug: the captured screenshot is recorded as wrong. */
  flaggedShot: boolean;
  /** debug: the live (JS-executed) render is recorded as wrong. */
  flaggedLive: boolean;
  onToggleFlag: (key: string) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const id = entry.configId;

  useEffect(() => {
    if (!live) return;
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (obsEntries) => {
        for (const oe of obsEntries) {
          if (oe.isIntersecting) onEnter(id);
          else onLeave(id);
        }
      },
      { root: null, rootMargin: "300px 0px", threshold: 0 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      onLeave(id);
    };
  }, [live, id, onEnter, onLeave]);

  // debug slot pin: show the requested index when set, else the representative.
  const rep =
    slotSel === null
      ? (entry.rep[variant] ?? null)
      : ((entry.allSlots[variant] ?? []).find((s) => s.slotIndex === slotSel) ?? null);
  const flagKey = `${entry.configId}::${variant}::${rep?.slotIndex ?? "none"}`;
  const activeLive = live && isActive && !!rep;
  const media =
    activeLive && rep ? (
      <CardFrame
        cardUrl={rep.cardUrl}
        title={entry.modelId}
        thumbUrl={rep.thumbUrl}
        delayMs={delayMs}
        generation={generation}
        active
        lazy={false}
        // gallery live tiles content-fit so the grid shows the card, not a
        // UNIFORM grid framing: full 1280×800 viewport, identical to the static
        // thumbs — per-card content-zoom at tile size made every tile a
        // different scale/height ("size全变形"). Grid > per-tile fill here.
        // letterbox = parent-owned box: the exhibit must NOT reserve its own
        // height inside the fixed-aspect .thumb (height→aspect→width feedback
        // shrank live tiles to ~177px while img tiles sat at the 268px column).
        fit="viewport"
        letterbox
      />
    ) : undefined;

  return (
    <div ref={ref} className="tilewrap" style={{ display: "flex", position: "relative" }}>
      <CardTile
        configId={entry.configId}
        modelId={entry.modelId}
        effort={entry.effort}
        arm={entry.arm}
        grade={entry.grade}
        unreviewed={entry.unreviewed}
        patched={entry.patched}
        thumbUrl={rep?.thumbUrl ?? null}
        live={activeLive}
        counts={entry.counts}
        liveLabel={liveLabel}
        media={media}
        suppressQualifiedBadge
        blankLabel={blankLabel}
      />
      {/* debug-only render-census controls: record "this card looks wrong" to
          localStorage so a whole sweep can be exported as a work list. Only
          rendered while the slot debug filter is engaged, so the public gallery
          is untouched.

          TWO independent marks, because the two failure modes are unrelated and
          need different fixes: a card can screenshot fine but break when its JS
          actually runs (live), or render live but have a bad captured shot. One
          combined flag would throw that distinction away. */}
      {FLAGS_ENABLED && slotSel !== null ? (
        <div className="gx-flags">
          <button
            type="button"
            className={`gx-flag${flaggedShot ? " on" : ""}`}
            title={flaggedShot ? "screenshot marked bad — click to clear" : "the captured screenshot looks wrong"}
            aria-pressed={flaggedShot}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onToggleFlag(`${flagKey}::shot`);
            }}
          >
            {flaggedShot ? "⚑ shot" : "⚐ shot"}
          </button>
          <button
            type="button"
            className={`gx-flag${flaggedLive ? " on" : ""}`}
            title={flaggedLive ? "live render marked bad — click to clear" : "the live render looks wrong"}
            aria-pressed={flaggedLive}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onToggleFlag(`${flagKey}::live`);
            }}
          >
            {flaggedLive ? "⚑ live" : "⚐ live"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
