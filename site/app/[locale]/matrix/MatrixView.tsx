"use client";

// /matrix client half (spec §4 matrix). Ported from app/matrix/MatrixView with
// the mockup-a restrained (hairline/ink) register. PRESERVED VERBATIM from the
// original — regressing either is the one unforgivable failure of this port:
//   • the 208px fixed row-head column (var --mx-head) that keeps long labels
//     from blowing out the grid / misaligning the canvas, and
//   • the figure/figcaption tooltip with the diagonal single-figure guard
//     (hover.r !== hover.c) so a self-consistency cell never renders a duplicate.
//
// ADDED for v3:
//   • the page auto-picks the best measured set (page.tsx defaultBatchId — the
//     set with the most configs passing the default filters); one quiet mono line
//     names it. No picker; the measured set is not a user-facing axis.
//   • grade filter (default qualified-only, AUTO-RELAXED to every grade the set
//     carries when qualified-only is too sparse), arm filter, variant toggle.
//   • contrast-stretch colour mode (default on): the eligible CROSS medians of
//     the active channel are remapped p5–p95 onto the ramp; fixed [0,1] stays.
//   • row/col ordering: catalog (default) or hierarchical-clustering leaves.
//   • cell-click → pair-detail drawer (absorbs the old /pair explorer):
//     both cards + per-metric breakdown from the pairs JSONs.
//
// Similarity summary + slot-pair detail are fetched client-side from /b/ (public,
// mirrored by copy-assets) so no multi-MB cell payload ships and a session switch
// re-derives everything. All colour/gating goes through lib/neutral (measurement
// presentation); channel vocabulary through lib/variant.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import {
  cellColor,
  isSufficient,
  neutralColor,
  compareSlug,
  INSUFFICIENT_COLOR,
} from "@/lib/neutral";
import {
  channelFamily,
  isDiagnosticChannel,
  variantToDir,
  type Channel,
  type ChannelFamily,
  type Variant,
} from "@/lib/variant";
import { effortRung, rungOrder } from "@/lib/effort";
import type { ArmGroup } from "@/lib/channel";
import type { Grade } from "@/app/components/Badge";
import { PairDrawer, type SummaryCell } from "./PairDrawer";
import styles from "./matrix.module.css";

// -- module-class helper: maps scoped names via `styles`, passes globals through.
const sc = (...names: Array<string | false | null | undefined>): string =>
  names
    .filter((n): n is string => !!n)
    .map((n) => styles[n] ?? n)
    .join(" ");

// ------------------------------- Props -------------------------------------

export interface MatrixConfig {
  id: string;
  label: string;
  family: string;
  effort: string | null;
  grade: Grade;
  arm: string;
  /** api-shaped vs harness-shaped arm (protocol "app" folds into harness). */
  armGroup: ArmGroup;
  /** representative-slot thumb.webp URL per variant (null = no valid slot). */
  thumb: { "P-min": string | null; "P-q": string | null };
}

export interface MatrixSession {
  batchId: string;
  event: string;
  date: string;
  city: string;
  variants: Variant[];
  configs: MatrixConfig[];
  arms: string[];
  grades: Grade[];
}

interface Props {
  sessions: MatrixSession[];
  defaultBatchId: string;
  channels: Channel[];
}

// ---------------------------- Geometry helpers -----------------------------

/** Model portion of a config_id — everything before the effort suffix. The
 *  matrix roster carries no modelId field, and the id is the only place it is. */
const modelOf = (configId: string): string => configId.split("-eff-")[0];

const clampInt = (x: number, lo: number, hi: number) =>
  Math.max(lo, Math.min(hi, x));

const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);

/** Main-view cell edge in CSS px. Two deliberately different readings:
 *
 *  DETAIL (default) — 11px floor, every row/column carries its config name, and
 *  the full-bleed container scrolls horizontally when that overflows. This is
 *  the view for actually reading a pair.
 *
 *  FIT — the whole matrix inside the viewport, so block structure is legible at
 *  a glance. The first attempt kept per-config names and they collapsed into an
 *  unreadable smear (193 names sharing ~384px). Names are therefore DROPPED here
 *  by design; identity comes from the model-cluster bands beside the grid plus
 *  the hover crosshair (Leo 2026-07-20: "不显示具体模型名 然后显示模型簇"). */
const MIN_CELL_FIT = 3;
const MIN_CELL_DETAIL = 11;
const MAX_CELL = 96;
function mainCellSize(n: number, availW: number, fit: boolean): number {
  if (n <= 0) return MAX_CELL;
  return clampInt(
    Math.floor(availW / n),
    fit ? MIN_CELL_FIT : MIN_CELL_DETAIL,
    MAX_CELL,
  );
}

/** Head gutter for the cluster bands in fit mode — much thinner than the 208px
 *  name column, because a family name is short and there is only one per run. */
const BAND_PX = 84;

/** Synthetic channel id for the all-channel consensus view. Not a measurement
 *  the runner produces — it is derived in the client from the real channels, so
 *  it never appears in a summary file or a pair document. */
const MERGED_CHANNEL = "merged" as const;
/** Per-family consensus alongside the all-channel one. The single `merged` mixes
 *  7 visual with 13 structural channels, so a pair that looks nothing alike but
 *  shares a code style still scores mid — the family merges are how you see
 *  which kind of similarity is doing the work. */
const FAMILY_MERGE = { v: "v-merged", c: "c-merged", d: "d-merged", x: "x-merged" } as const;
const MERGE_CHANNELS = [
  MERGED_CHANNEL,
  ...(Object.values(FAMILY_MERGE) as string[]),
] as const;
const isMergeChannel = (ch: string): boolean =>
  (MERGE_CHANNELS as readonly string[]).includes(ch);
/** The family a merge pseudo-channel folds, or null for the all-channel merge. */
const mergeFamilyOf = (ch: string): ChannelFamily | null => {
  for (const [fam, id] of Object.entries(FAMILY_MERGE)) {
    if (id === ch) return fam as ChannelFamily;
  }
  return null;
};
/** What the view can have selected: a real measured Channel, or the derived
 *  consensus. Kept distinct from `Channel` so nothing can accidentally hand
 *  "merged" to code that indexes real summary/pair documents. */
type ViewChannel = Channel | (typeof MERGE_CHANNELS)[number];
/** Shape of similarity/merge-domain.lock.json — the frozen per-channel stretch
 *  domain. Written by runner/tools/build_merge_domain_lock.py; the `schema` field
 *  is checked before use so a future format can never be silently misread. */
const MERGE_LOCK_SCHEMA = "merge-domain-lock/1";
interface MergeDomainLock {
  schema: string;
  batch_id: string;
  variants: Record<string, { channels: Record<string, { lo: number; hi: number }> }>;
}

/** A merged cell needs at least this many contributing channels to be shown;
 *  below it the average says more about which channels happened to be eligible
 *  than about the pair. */
const MERGE_MIN_CH = 6;
/** Minimum contributing channels for a family merge — 6 would disqualify every
 *  family (d has only 3 formal channels), so it scales with what the family has
 *  while still refusing an average over one or two lucky channels. */
const familyMergeMin = (size: number): number => Math.max(2, Math.ceil(size * 0.6));

/** Contiguous runs of one family in the CURRENT display order. In catalog order
 *  these are the natural model clusters; in clustered order they fragment, which
 *  is itself informative (the similarity clustering disagreed with the catalog).
 *  Runs shorter than the label height stay as tinted blocks with no text. */
interface ClusterRun {
  family: string;
  start: number;
  len: number;
}
function clusterRuns(items: { family: string }[]): ClusterRun[] {
  const out: ClusterRun[] = [];
  for (let i = 0; i < items.length; i++) {
    const f = items[i].family;
    if (out.length > 0 && out[out.length - 1].family === f) out[out.length - 1].len += 1;
    else out.push({ family: f, start: i, len: 1 });
  }
  return out;
}

/** Below this many configs the matrix is too sparse to read — auto-relax the
 *  grade filter instead of leaving the view thin. */
const MIN_USEFUL = 5;
/** Row-head column width (must match --mx-head in matrix.module.css). */
const HEAD_PX = 208;

/** Grade set to show for a measured set on first paint: qualified-only, but
 *  auto-relaxed to every grade the set carries when qualified-only would be too
 *  sparse — so the matrix always opens at its best readable state. */
function defaultGrades(s: MatrixSession): Set<Grade> {
  const qualified = s.configs.filter((c) => c.grade === "qualified").length;
  return qualified < MIN_USEFUL ? new Set(s.grades) : new Set<Grade>(["qualified"]);
}

interface ThemeColors {
  acc: string;
  line: string;
}

function readThemeColors(): ThemeColors {
  if (typeof window === "undefined") {
    return { acc: "#37D6E4", line: "#232C41" };
  }
  const s = getComputedStyle(document.documentElement);
  const g = (k: string, fb: string) => s.getPropertyValue(k).trim() || fb;
  return {
    acc: g("--acc", "#37D6E4"),
    line: g("--line", "#232C41"),
  };
}

const fmt = (x: number) => x.toFixed(3);

const GRADES: Grade[] = ["qualified", "dev", "community"];

/**
 * Agglomerative average-linkage (UPGMA) leaf order over `m` items given a leaf
 * distance function `d`. Naive O(m³) — fine at n≤200. Returns a permutation of
 * [0..m) that places similar items adjacent so the heatmap shows block structure.
 */
function clusterLeafOrder(m: number, d: (i: number, j: number) => number): number[] {
  if (m <= 2) return Array.from({ length: m }, (_, i) => i);
  const key = (a: number, b: number) => (a < b ? `${a}-${b}` : `${b}-${a}`);
  const cdist = new Map<string, number>();
  for (let i = 0; i < m; i++) {
    for (let j = i + 1; j < m; j++) cdist.set(key(i, j), d(i, j));
  }
  const size = new Map<number, number>();
  const leaves = new Map<number, number[]>();
  let active: number[] = [];
  for (let i = 0; i < m; i++) {
    active.push(i);
    size.set(i, 1);
    leaves.set(i, [i]);
  }
  let nextId = m;
  while (active.length > 1) {
    // closest pair of active clusters
    let bi = 0;
    let bj = 1;
    let best = Infinity;
    for (let a = 0; a < active.length; a++) {
      for (let b = a + 1; b < active.length; b++) {
        const dd = cdist.get(key(active[a], active[b])) ?? 0.6;
        if (dd < best) {
          best = dd;
          bi = a;
          bj = b;
        }
      }
    }
    const ca = active[bi];
    const cb = active[bj];
    const sa = size.get(ca) as number;
    const sb = size.get(cb) as number;
    const nid = nextId++;
    size.set(nid, sa + sb);
    leaves.set(nid, [...(leaves.get(ca) as number[]), ...(leaves.get(cb) as number[])]);
    // UPGMA: size-weighted average of the two merged distances to each other cluster
    for (const cc of active) {
      if (cc === ca || cc === cb) continue;
      const dac = cdist.get(key(ca, cc)) as number;
      const dbc = cdist.get(key(cb, cc)) as number;
      cdist.set(key(nid, cc), (sa * dac + sb * dbc) / (sa + sb));
    }
    active = active.filter((c) => c !== ca && c !== cb);
    active.push(nid);
  }
  return leaves.get(active[0]) as number[];
}

// =============================== Component ==================================

export function MatrixView({ sessions, defaultBatchId, channels }: Props) {
  const t = useTranslations("matrix");

  // The measured set is auto-picked (page.tsx defaultBatchId) and no longer a
  // user-facing axis — the state is kept only as internal plumbing for the
  // per-set fetch / cache / effects (a unified all-configs set is coming).
  const [batchId] = useState<string>(defaultBatchId);
  const session =
    sessions.find((s) => s.batchId === batchId) ?? sessions[0];

  // variant: fall back to a variant this session actually has.
  const [variantWanted, setVariantWanted] = useState<Variant>("P-min");
  const variant: Variant = session.variants.includes(variantWanted)
    ? variantWanted
    : session.variants[0];

  // grade filter — default qualified-only, but auto-relaxed on first paint to
  // every grade the set carries when qualified-only would be too sparse (the lazy
  // initializer handles mount; the effect below handles any future set change).
  // arm filter — all on.
  const [gradeFilter, setGradeFilter] = useState<Set<Grade>>(() =>
    defaultGrades(sessions.find((s) => s.batchId === defaultBatchId) ?? sessions[0]),
  );
  const [armFilter, setArmFilter] = useState<Set<string>>(() => new Set());

  // arm-group view — the matrix defaults to the HARNESS group (api and harness
  // arms are separated by default, Leo 2026-07-19); "all" restores the mixed
  // roster. Filters rows AND cols in lockstep via baseConfigs below.
  const [armView, setArmView] = useState<ArmGroup | "all">("harness");

  const [activeChannel, setActiveChannel] = useState<ViewChannel>(channels[0]);
  const [hover, setHover] = useState<{ r: number; c: number } | null>(null);
  // Axis highlight — hovering a HEADER lights ONE strip: the row for a row
  // head, the column for a column head. A cross means "this pair", and a header
  // is not a pair (Leo); the cross stays exclusive to pointing at a cell. In
  // fit mode the heads are family bands, so the strip spans that cluster's
  // whole range — hence a range rather than a single index. Separate from
  // `hover`, which belongs to the canvas and carries the tooltip: this one must
  // not pop a tooltip for a cell nobody pointed at.
  const [axis, setAxis] = useState<
    { start: number; len: number; dir: "row" | "col" } | null
  >(null);
  const [tip, setTip] = useState<{ x: number; y: number } | null>(null);
  const [theme, setTheme] = useState<ThemeColors>(readThemeColors);
  const [drawer, setDrawer] = useState<{ a: string; b: string } | null>(null);

  // contrast-stretch colour mode (default on) — see the `stretch` memo below.
  const [contrast, setContrast] = useState<boolean>(true);
  // row/col ordering — catalog (default) or hierarchical clustering, see `perm`.
  const [order, setOrder] = useState<"catalog" | "clustered">("catalog");
  // Two readings of the same data — see mainCellSize(). Detail is the default
  // because it is the one you can actually read a pair from.
  const [width, setWidth] = useState<"detail" | "fit">("detail");

  // ---- filtered axis (catalog order): arm-group AND grade AND (arm if picked) ----
  const baseConfigs = useMemo(() => {
    const arms = armFilter;
    const kept = session.configs.filter(
      (c) =>
        (armView === "all" || c.armGroup === armView) &&
        gradeFilter.has(c.grade) &&
        (arms.size === 0 || arms.has(c.arm)),
    );
    // Catalog order sorted the config_ids as strings, so a model's tiers came
    // out alphabetically — high, low, max, medium, xhigh — which reads as noise
    // next to a heatmap whose whole point is that effort moves a card. Order by
    // the effort LADDER within each model instead (lib/effort), family and model
    // still alphabetical so the family blocks stay contiguous.
    return [...kept].sort(
      (a, b) =>
        a.family.localeCompare(b.family) ||
        modelOf(a.id).localeCompare(modelOf(b.id)) ||
        rungOrder(effortRung(a.effort)) - rungOrder(effortRung(b.effort)) ||
        String(a.effort ?? "").localeCompare(String(b.effort ?? "")) ||
        a.arm.localeCompare(b.arm) ||
        a.id.localeCompare(b.id),
    );
  }, [session, gradeFilter, armFilter, armView]);

  const n = baseConfigs.length;
  const total = session.configs.length;

  // Relaxing helps only if the set carries configs the filter currently hides.
  const relaxable = useMemo(
    () => session.configs.some((c) => !gradeFilter.has(c.grade)),
    [session, gradeFilter],
  );
  const resetGrades = useCallback(
    () => setGradeFilter(new Set(session.grades)),
    [session],
  );

  // AUTO-RELAX (zero clicks): the first time a measured set is shown, if the
  // default qualified-only view is too sparse, widen to every grade the set
  // carries. Once per set — the ref is pre-seeded with the mount set (the lazy
  // gradeFilter initializer already relaxed it) so a later manual narrow sticks.
  const relaxedSets = useRef<Set<string>>(new Set([defaultBatchId]));
  useEffect(() => {
    if (relaxedSets.current.has(batchId)) return;
    relaxedSets.current.add(batchId);
    setGradeFilter(defaultGrades(session));
  }, [batchId, session]);

  // Measure the main column so the heatmap cells fill the available width. A
  // callback ref (re)attaches the observer whenever the section mounts — the
  // matrix may appear only after the user resets a sparse filter.
  const [colW, setColW] = useState<number>(760);
  const roRef = useRef<ResizeObserver | null>(null);
  const mainSecRef = useCallback((el: HTMLElement | null) => {
    roRef.current?.disconnect();
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setColW(w);
    });
    ro.observe(el);
    roRef.current = ro;
  }, []);
  const canvasAvail = Math.max(120, colW - HEAD_PX - 8);
  const mainCell = mainCellSize(n, canvasAvail, width === "fit");
  const fitMode = width === "fit";
  // fit drops per-config names for cluster bands; detail keeps the name column
  const headPx = fitMode ? BAND_PX : HEAD_PX;
  const mainPx = n * mainCell;

  // ---- similarity summary cells: fetched per (batch, variant), cached ----
  const cellCache = useRef<Map<string, SummaryCell[]>>(new Map());
  const [cells, setCells] = useState<SummaryCell[] | null>(null);
  const [cellsErr, setCellsErr] = useState<string | null>(null);

  // ---- frozen merge domain (runner/tools/build_merge_domain_lock.py) --------
  // The per-channel contrast-stretch domain `merged` averages over. Computed
  // ONCE over the full production set and shipped as a lock, so a merged value
  // is the same number whatever the view is filtered to — and the same number
  // the README quotes. Absent (older deploys, batches with no lock) → null, and
  // mergedIndex falls back to deriving the domain from the current view, which
  // is what the client always used to do.
  const lockCache = useRef<Map<string, MergeDomainLock | null>>(new Map());
  const [mergeLock, setMergeLock] = useState<MergeDomainLock | null>(null);
  useEffect(() => {
    const cached = lockCache.current.get(batchId);
    if (cached !== undefined) {
      setMergeLock(cached);
      return;
    }
    let alive = true;
    setMergeLock(null);
    fetch(`/b/${batchId}/similarity/merge-domain.lock.json`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((j: MergeDomainLock | null) => {
        const v = j && j.schema === MERGE_LOCK_SCHEMA ? j : null;
        lockCache.current.set(batchId, v);
        if (alive) setMergeLock(v);
      });
    return () => {
      alive = false;
    };
  }, [batchId]);

  useEffect(() => {
    const key = `${batchId}::${variant}`;
    const cached = cellCache.current.get(key);
    if (cached) {
      setCells(cached);
      setCellsErr(null);
      return;
    }
    let alive = true;
    setCells(null);
    setCellsErr(null);

    // Expand the compact matrix twin (index-encoded numeric tuples + configs /
    // channels tables) back into SummaryCell[], so everything downstream is
    // unchanged. Tuple = [chIdx, aIdx, bIdx, median, p25, p75, n_eff, m_a, m_b];
    // kind is derived (aIdx === bIdx → "self"). This shrinks the fetched payload
    // ~6-8× vs the verbose summary and makes JSON.parse far cheaper.
    const expandCompact = (j: {
      configs: string[];
      channels: string[];
      cells: (number | null)[][];
    }): SummaryCell[] =>
      j.cells.map((t) => ({
        channel: j.channels[t[0] as number] as SummaryCell["channel"],
        config_a: j.configs[t[1] as number],
        config_b: j.configs[t[2] as number],
        kind: t[1] === t[2] ? "self" : "cross",
        median: t[3],
        iqr: t[4] == null ? null : { p25: t[4] as number, p75: t[5] as number },
        n_eff: (t[6] as number) ?? 0,
        m_a: (t[7] as number) ?? 0,
        m_b: (t[8] as number) ?? 0,
      }));

    const commit = (next: SummaryCell[]) => {
      if (!alive) return;
      cellCache.current.set(key, next);
      setCells(next);
    };

    fetch(`/b/${batchId}/similarity/summary-compact--${variant}.json`)
      .then((r) => {
        if (r.ok) return r.json().then((j) => commit(expandCompact(j)));
        // Fallback for older deploys that shipped only the verbose summary.
        return fetch(`/b/${batchId}/similarity/summary--${variant}.json`)
          .then((r2) =>
            r2.ok ? r2.json() : Promise.reject(new Error(`HTTP ${r2.status}`)),
          )
          .then((j: { cells: SummaryCell[] }) => commit(j.cells));
      })
      .catch((e) => alive && setCellsErr(String(e)));
    return () => {
      alive = false;
    };
  }, [batchId, variant]);

  // reset transient view state when the session changes.
  useEffect(() => {
    setHover(null);
    setTip(null);
    setDrawer(null);
    // merged is derived, not measured — it stays valid across every data set,
    // so only a REAL channel that vanished falls back to the first one.
    setActiveChannel((ch) =>
      isMergeChannel(ch) || channels.includes(ch as Channel) ? ch : channels[0],
    );
  }, [batchId, channels]);

  // ---- pair lookup: mirror the unordered summary cells into an (i,j) getter ----
  const index = useMemo(() => {
    const m = new Map<string, SummaryCell>();
    if (!cells) return m;
    for (const cell of cells) {
      const [lo, hi] =
        compareSlug(cell.config_a, cell.config_b) <= 0
          ? [cell.config_a, cell.config_b]
          : [cell.config_b, cell.config_a];
      m.set(`${cell.channel} ${lo} ${hi}`, cell);
    }
    return m;
  }, [cells]);

  // ---- row/col ordering: catalog (identity) or clustering leaves ----
  // In clustered mode we reorder rows AND cols by the average-linkage (UPGMA) leaf
  // order over the ACTIVE channel × variant across the current filtered set, so
  // similar configs sit adjacent and block structure shows. distance = 1 − median
  // for eligible cross pairs; unknown/insufficient pairs get a neutral 0.6 (a touch
  // worse than typical, so unknowns don't glue clusters). View-dependent by design;
  // memoized per (order, channel, variant [via index], filter state [via baseConfigs]).
  const perm = useMemo<number[]>(() => {
    const m = baseConfigs.length;
    const identity = Array.from({ length: m }, (_, i) => i);
    if (order !== "clustered" || m <= 2) return identity;
    const d = (i: number, j: number): number => {
      const a = baseConfigs[i].id;
      const b = baseConfigs[j].id;
      const [lo, hi] = compareSlug(a, b) <= 0 ? [a, b] : [b, a];
      const cell = index.get(`${activeChannel} ${lo} ${hi}`);
      return cell && isSufficient(cell) ? 1 - (cell.median as number) : 0.6;
    };
    return clusterLeafOrder(m, d);
  }, [order, baseConfigs, activeChannel, index]);

  // The view roster: base filtered configs permuted into the chosen order. Every
  // downstream consumer (lookup, canvas, headers, hover, tooltip, click→drawer)
  // reads THIS array, so rows/cols/labels/interactions stay perfectly aligned.
  const configs = useMemo(() => perm.map((i) => baseConfigs[i]), [perm, baseConfigs]);
  // model-cluster runs must follow `configs` — they read the DISPLAY order
  const runs = useMemo(() => clusterRuns(configs), [configs]);

  const rawLookup = useCallback(
    (channel: string, r: number, c: number): SummaryCell | undefined => {
      const a = configs[r]?.id;
      const b = configs[c]?.id;
      if (a == null || b == null) return undefined;
      const [lo, hi] = compareSlug(a, b) <= 0 ? [a, b] : [b, a];
      return index.get(`${channel} ${lo} ${hi}`);
    },
    [configs, index],
  );

  // ---- MERGED channel: consensus across every scored channel ----------------
  //
  // Channels are NOT on a common scale — v-phash clusters near 0.5, the code
  // channels sit far lower, the x-* family sits near 0.95 — so a plain mean
  // would just be "whichever channel has the widest spread and the highest
  // baseline". (Not hypothetical: adding the 5 x-* channels in v13 lifted every
  // published figure by ~0.07 for exactly that reason.) Each channel is
  // therefore contrast-stretched to [0,1] over its OWN cross-pair range first
  // and only then averaged, so every channel contributes equally (Leo
  // 2026-07-20).
  //
  // The domain itself comes from the FROZEN LOCK (2026-07-25). It used to be
  // recomputed over whatever configs the view currently showed, which meant
  // filtering the matrix changed every merged value — fine as a display, but it
  // made merged uncitable and let the site and the README disagree while both
  // calling their number "merged". p1–p99, not p5–p95: the tighter band
  // saturated a THIRD of the self-consistency diagonal at a flat 1.0.
  //
  // Diagnostic channels are excluded — the scheme marks them "displayed, never
  // statted", so folding them into a score would contradict that.
  // A pair needs at least MERGE_MIN_CH contributing channels, else it reads as
  // insufficient rather than as a confident average over two lucky channels.
  const mergedIndex = useMemo(() => {
    if (!isMergeChannel(activeChannel)) return null;
    const fam = mergeFamilyOf(activeChannel);
    const scored = channels.filter(
      (ch) => !isDiagnosticChannel(ch) && (fam === null || channelFamily(ch) === fam),
    );
    if (scored.length === 0) return null;
    const minCh = fam === null ? MERGE_MIN_CH : familyMergeMin(scored.length);

    // Per-channel stretch domain. PREFERRED: the frozen lock, computed once over
    // the whole production set — so merged does not move when the view is
    // filtered, and matches the figures published off-site. FALLBACK (no lock
    // shipped): derive it from this view's eligible cross medians, the original
    // behaviour, which is view-dependent by construction.
    const dom = new Map<string, { lo: number; hi: number }>();
    const locked = mergeLock?.variants?.[variant]?.channels;
    if (locked) {
      for (const ch of scored) {
        const d = locked[ch];
        if (d && d.hi - d.lo >= 0.02) dom.set(ch, d);
      }
    } else {
      for (const ch of scored) {
        const vals: number[] = [];
        for (let r = 0; r < n; r++) {
          for (let c = 0; c < n; c++) {
            if (r === c) continue;
            const cell = rawLookup(ch, r, c);
            if (cell && isSufficient(cell)) vals.push(cell.median as number);
          }
        }
        if (vals.length < 8) continue;
        vals.sort((a, b) => a - b);
        const pct = (p: number) => {
          const i = (vals.length - 1) * p;
          const f = Math.floor(i);
          const cl = Math.ceil(i);
          return f === cl ? vals[f] : vals[f] + (vals[cl] - vals[f]) * (i - f);
        };
        const lo = pct(0.01);
        const hi = pct(0.99);
        if (hi - lo >= 0.02) dom.set(ch, { lo, hi });
      }
    }
    // fold every pair
    const out = new Map<string, SummaryCell>();
    for (let r = 0; r < n; r++) {
      for (let c = r; c < n; c++) {
        let sum = 0;
        let k = 0;
        let nEff = 0;
        for (const ch of scored) {
          const d = dom.get(ch);
          const cell = rawLookup(ch, r, c);
          if (!d || !cell || !isSufficient(cell)) continue;
          sum += clamp01(((cell.median as number) - d.lo) / (d.hi - d.lo));
          k += 1;
          nEff = Math.max(nEff, cell.n_eff);
        }
        const a = configs[r]?.id;
        const b = configs[c]?.id;
        if (a == null || b == null) continue;
        const [lo, hi] = compareSlug(a, b) <= 0 ? [a, b] : [b, a];
        out.set(`${activeChannel} ${lo} ${hi}`, {
          config_a: lo,
          config_b: hi,
          channel: activeChannel as unknown as Channel,
          kind: r === c ? "self" : "cross",
          median: k >= minCh ? sum / k : null,
          iqr: null,
          // n_eff drives the sufficiency gate downstream; 0 when too few channels
          n_eff: k >= minCh ? nEff : 0,
          m_a: k,
          m_b: k,
        });
      }
    }
    return out;
  }, [activeChannel, channels, n, rawLookup, configs, mergeLock, variant]);

  /** Channel-aware cell access: the merged pseudo-channel is served from its own
   *  derived index, everything else straight from the summary. */
  const lookup = useCallback(
    (channel: string, r: number, c: number): SummaryCell | undefined => {
      if (!isMergeChannel(channel)) return rawLookup(channel, r, c);
      const a = configs[r]?.id;
      const b = configs[c]?.id;
      if (a == null || b == null) return undefined;
      const [lo, hi] = compareSlug(a, b) <= 0 ? [a, b] : [b, a];
      return mergedIndex?.get(`${channel} ${lo} ${hi}`);
    },
    [rawLookup, mergedIndex, configs],
  );

  // ---- contrast stretch domain: p5–p95 of the CURRENT view's CROSS medians ----
  // Real views cluster in a narrow similarity band, so the fixed [0,1] viridis
  // reads flat. In contrast mode we remap the ELIGIBLE cross (r≠c) medians of the
  // active channel onto the full ramp between the 5th and 95th percentiles. The
  // diagonal self cells are EXCLUDED here (they'd bias the domain) but still get
  // painted with the same mapping. Fall back to fixed when the view is too small
  // (<8 eligible values) or too flat (span < 0.02). Memoized per channel + variant
  // + filter state (all carried by `lookup` and `n`).
  const stretch = useMemo<{ lo: number; hi: number } | null>(() => {
    const vals: number[] = [];
    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) {
        if (r === c) continue;
        const cell = lookup(activeChannel, r, c);
        if (cell && isSufficient(cell)) vals.push(cell.median as number);
      }
    }
    if (vals.length < 8) return null;
    vals.sort((a, b) => a - b);
    const pct = (p: number) => {
      const idx = (vals.length - 1) * p;
      const lo = Math.floor(idx);
      const hi = Math.ceil(idx);
      return lo === hi ? vals[lo] : vals[lo] + (vals[hi] - vals[lo]) * (idx - lo);
    };
    const lo = pct(0.05);
    const hi = pct(0.95);
    return hi - lo < 0.02 ? null : { lo, hi };
  }, [n, activeChannel, lookup]);

  /** the domain painting actually uses: null in fixed mode or on silent fallback. */
  const stretchActive = contrast ? stretch : null;

  // ------------------------------ Drawing ------------------------------------

  const drawInto = useCallback(
    (
      canvas: HTMLCanvasElement | null,
      channel: string,
      cell: number,
      dom: { lo: number; hi: number } | null,
      opts: { grid: boolean },
    ) => {
      if (!canvas) return;
      const px = n * cell;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(px * dpr);
      canvas.height = Math.round(px * dpr);
      canvas.style.width = `${px}px`;
      canvas.style.height = `${px}px`;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, px, px);
      for (let r = 0; r < n; r++) {
        for (let c = 0; c < n; c++) {
          const data = lookup(channel, r, c);
          // Gate untouched: an ineligible cell stays grey and never a colour that
          // implies a value. An eligible cell is coloured by the fixed [0,1]
          // viridis (cellColor) or, when a stretch domain is active, by its
          // position between p5 and p95 of this view. The diagonal is painted the
          // same way (its accent border already marks it as self-consistency).
          let fill: string;
          if (!data || !isSufficient(data)) {
            fill = INSUFFICIENT_COLOR;
          } else if (dom) {
            fill = neutralColor(clamp01(((data.median as number) - dom.lo) / (dom.hi - dom.lo)));
          } else {
            fill = cellColor(data);
          }
          ctx.fillStyle = fill;
          ctx.fillRect(c * cell, r * cell, cell, cell);
          if (opts.grid && cell >= 14) {
            ctx.strokeStyle = theme.line;
            ctx.lineWidth = 1;
            ctx.strokeRect(c * cell + 0.5, r * cell + 0.5, cell - 1, cell - 1);
          }
          if (r === c) {
            // diagonal = self-consistency: accent border so it never reads as a
            // cross-model result. Accent is theme-constant.
            ctx.strokeStyle = theme.acc;
            ctx.lineWidth = cell >= 14 ? 2 : 1;
            const inset = cell >= 14 ? 1.5 : 0.5;
            ctx.strokeRect(
              c * cell + inset,
              r * cell + inset,
              cell - inset * 2,
              cell - inset * 2,
            );
          }
        }
      }
    },
    [n, lookup, theme],
  );

  // main canvas — redraws on channel / variant / theme / cells change
  const mainRef = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    drawInto(mainRef.current, activeChannel, mainCell, stretchActive, { grid: true });
  }, [drawInto, activeChannel, mainCell, stretchActive]);

  // re-read tokens + redraw when the OS theme flips.
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setTheme(readThemeColors());
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // ------------------------------ Interaction --------------------------------

  const cellFromEvent = useCallback(
    (e: React.MouseEvent<HTMLElement>): { r: number; c: number } | null => {
      const rect = e.currentTarget.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const cs = rect.width / n;
      const c = clampInt(Math.floor(x / cs), 0, n - 1);
      const r = clampInt(Math.floor(y / cs), 0, n - 1);
      return { r, c };
    },
    [n],
  );

  const onMainMove = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      const rc = cellFromEvent(e);
      setHover(rc);
      setTip({ x: e.clientX, y: e.clientY });
    },
    [cellFromEvent],
  );

  const onMainLeave = useCallback(() => {
    setHover(null);
    setTip(null);
  }, []);

  const onMainClick = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      const rc = cellFromEvent(e);
      if (!rc) return;
      const a = configs[rc.r]?.id;
      const b = configs[rc.c]?.id;
      if (a && b) setDrawer({ a, b });
    },
    [cellFromEvent, configs],
  );

  // ------------------------------ Tooltip data -------------------------------

  const hoverCell = hover ? lookup(activeChannel, hover.r, hover.c) : undefined;
  const hoverConfigA = hover ? configs[hover.r] : null;
  const hoverConfigB = hover ? configs[hover.c] : null;

  // ------------------------------ Filter toggles -----------------------------

  const toggleGrade = (g: Grade) =>
    setGradeFilter((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      // never leave zero grades — that would blank the axis with no way back.
      if (next.size === 0) next.add(g);
      return next;
    });

  const toggleArm = (arm: string) =>
    setArmFilter((prev) => {
      const next = new Set(prev);
      if (next.has(arm)) next.delete(arm);
      else next.add(arm);
      return next;
    });

  // ------------------------------ Crosshair JSX ------------------------------

  function Crosshair({ cell, size }: { cell: number; size: number }) {
    if (!hover) return null;
    return (
      <div className={sc("mx-cross")} aria-hidden>
        <div
          className={sc("mx-crossband")}
          style={{ left: 0, top: hover.r * cell, width: size, height: cell }}
        />
        <div
          className={sc("mx-crossband")}
          style={{ left: hover.c * cell, top: 0, width: cell, height: size }}
        />
        <div
          className={sc("mx-cellbox")}
          style={{ left: hover.c * cell, top: hover.r * cell, width: cell, height: cell }}
        />
      </div>
    );
  }

  /** The single strip for the hovered header — horizontal from a row head,
   *  vertical from a column head. `len` > 1 in fit mode (a whole family). */
  function AxisHighlight({ cell, size }: { cell: number; size: number }) {
    if (!axis) return null;
    const off = axis.start * cell;
    const span = axis.len * cell;
    return (
      <div className={sc("mx-cross")} aria-hidden>
        <div
          className={sc("mx-crossband")}
          style={
            axis.dir === "row"
              ? { left: 0, top: off, width: size, height: span }
              : { left: off, top: 0, width: span, height: size }
          }
        />
      </div>
    );
  }

  /** Is index i inside the hovered header range, on that header's own axis?
   *  Drives the head `hot` state — a row head must not light up because a
   *  COLUMN head is hovered, or the "one strip" reading falls apart. */
  const inAxis = (i: number, dir: "row" | "col") =>
    !!axis && axis.dir === dir && i >= axis.start && i < axis.start + axis.len;

  // ------------------------------ Render -------------------------------------

  const groups: ChannelFamily[] = ["v", "c", "d", "x"];
  const familyLabel = (fam: ChannelFamily) => t(`main.family.${fam}`);

  return (
    <div className={sc("mx-page")}>
      {/* honesty caption (measurement, not quality — §0.1 honesty rule a) */}
      <p className={sc("mx-honesty")}>{t("honesty")}</p>

      {/* the page auto-picks the best measured set (page.tsx defaultBatchId); this
          one quiet mono line names it — no picker, no session/batch vocabulary. */}
      <p className={sc("mx-setline")}>
        {t("set", { city: session.city, date: session.date, n, m: total })}
      </p>

      {/* controls: variant + grade + arm filters, legend */}
      <div className={sc("mx-controls")}>
        <div className={sc("mx-fgroup")}>
          <span className={sc("mx-gl")}>{t("filters.variant")}</span>
          <div className="seg" role="group" aria-label={t("filters.variant")}>
            {session.variants.map((v) => (
              <button
                key={v}
                type="button"
                className={v === variant ? "on" : ""}
                aria-pressed={v === variant}
                onClick={() => setVariantWanted(v)}
              >
                {v}
              </button>
            ))}
          </div>
        </div>

        <div className={sc("mx-fgroup")}>
          <span className={sc("mx-gl")}>{t("filters.grade")}</span>
          {GRADES.filter((g) => session.grades.includes(g)).map((g) => (
            <button
              key={g}
              type="button"
              className={gradeFilter.has(g) ? (g === "community" ? "chip on2" : "chip on") : "chip"}
              aria-pressed={gradeFilter.has(g)}
              onClick={() => toggleGrade(g)}
            >
              {g}
            </button>
          ))}
        </div>

        {/* arm-group split (Harness | API | all) — separated by default so a
            harness config is never mixed with an api config on first paint. */}
        <div className={sc("mx-fgroup")}>
          <span className={sc("mx-gl")}>{t("filters.armGroup")}</span>
          <div className="seg" role="group" aria-label={t("filters.armGroup")}>
            <button
              type="button"
              className={armView === "harness" ? "on" : ""}
              aria-pressed={armView === "harness"}
              onClick={() => setArmView("harness")}
            >
              {t("filters.harness")}
            </button>
            <button
              type="button"
              className={armView === "api" ? "on" : ""}
              aria-pressed={armView === "api"}
              onClick={() => setArmView("api")}
            >
              {t("filters.api")}
            </button>
            <button
              type="button"
              className={armView === "all" ? "on" : ""}
              aria-pressed={armView === "all"}
              onClick={() => setArmView("all")}
            >
              {t("filters.allArms")}
            </button>
          </div>
        </div>

        {session.arms.length > 1 && (
          <div className={sc("mx-fgroup")}>
            <span className={sc("mx-gl")}>{t("filters.arm")}</span>
            {session.arms.map((arm) => (
              <button
                key={arm}
                type="button"
                className={
                  armFilter.size === 0 || armFilter.has(arm) ? "chip on" : "chip"
                }
                aria-pressed={armFilter.size === 0 || armFilter.has(arm)}
                onClick={() => toggleArm(arm)}
              >
                {arm}
              </button>
            ))}
          </div>
        )}

        {/* row/col order — catalog (default) or hierarchical clustering. Clustered
            order is view-dependent (channel/variant/filters), hence the title. */}
        <div className={sc("mx-fgroup")} title={t("order.title")}>
          <span className={sc("mx-gl")}>{t("order.label")}</span>
          <div className="seg" role="group" aria-label={t("order.label")}>
            <button
              type="button"
              className={order === "catalog" ? "on" : ""}
              aria-pressed={order === "catalog"}
              onClick={() => setOrder("catalog")}
            >
              {t("order.catalog")}
            </button>
            <button
              type="button"
              className={order === "clustered" ? "on" : ""}
              aria-pressed={order === "clustered"}
              onClick={() => setOrder("clustered")}
            >
              {t("order.clustered")}
            </button>
          </div>
        </div>

        {/* two readings: detail = named rows, scrolls; fit = whole matrix in the
            viewport with model-cluster bands instead of per-config names */}
        <div className={sc("mx-fgroup")} title={t("width.title")}>
          <span className={sc("mx-gl")}>{t("width.label")}</span>
          <div className="seg" role="group" aria-label={t("width.label")}>
            <button
              type="button"
              className={width === "detail" ? "on" : ""}
              aria-pressed={width === "detail"}
              onClick={() => setWidth("detail")}
            >
              {t("width.detail")}
            </button>
            <button
              type="button"
              className={width === "fit" ? "on" : ""}
              aria-pressed={width === "fit"}
              onClick={() => setWidth("fit")}
            >
              {t("width.fit")}
            </button>
          </div>
        </div>


        {/* always-visible result count: how many of the set's configs the current
            filters keep (kept even though relaxing is now automatic) */}
        <span className={sc("mx-count")} role="status">
          {t("filters.count", { n, m: total })}
        </span>
      </div>

      {/* auto-relax happens silently (see the effect above) — no amber notice */}

      {n === 0 ? (
        <div className={sc("mx-empty")}>
          <div className="emptystate" role="status">
            <span className="emptystate-icon" aria-hidden>
              ○
            </span>
            <h3>{t("empty.title")}</h3>
            <p>{t("empty.message")}</p>
            {relaxable && (
              <div>
                <button type="button" className="btn btn-ghost" onClick={resetGrades}>
                  {t("notice.reset", { m: total })}
                </button>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className={sc("mx-body")}>
          {/* -------- 17 channels = compact labeled tab rail, ABOVE the matrix so
                the heatmap gets the full content width (legible names + per-channel
                description tooltip; the tiny illegible previews are gone — the main
                view IS the preview) -------- */}
          <section className={sc("mx-channels")}>
            <div className={sc("mx-channels-title")}>{t("multiples.title")}</div>
            <div className={sc("mx-channel-groups")}>
              {/* consensus across every scored channel — sits first because it is
                  the "just show me the overall picture" answer; the individual
                  channels below are the decomposition of it */}
              <div className={sc("mx-group")}>
                <div className={sc("mx-group-label")}>{t("merged.group")}</div>
                <div className={sc("mx-tabs")}>
                  <button
                    type="button"
                    className={sc("mx-tab", activeChannel === MERGED_CHANNEL && "active")}
                    onClick={() => setActiveChannel(MERGED_CHANNEL)}
                    title={t("merged.desc")}
                    aria-pressed={activeChannel === MERGED_CHANNEL}
                  >
                    <span className={sc("mx-tab-label")}>{MERGED_CHANNEL}</span>
                  </button>
                </div>
              </div>
              {groups.map((fam) => {
                const chs = channels.filter((ch) => channelFamily(ch) === fam);
                if (chs.length === 0) return null;
                return (
                  <div key={fam} className={sc("mx-group")}>
                    <div className={sc("mx-group-label", `mx-fam-${fam}`)}>{familyLabel(fam)}</div>
                    <div className={sc("mx-tabs")}>
                      {/* the family's own consensus, first — the same relationship
                          `merged` has to all channels, scoped to this family */}
                      {chs.some((ch) => !isDiagnosticChannel(ch)) ? (
                        <button
                          type="button"
                          className={sc(
                            "mx-tab",
                            "mx-tab-merge",
                            activeChannel === FAMILY_MERGE[fam] && "active",
                          )}
                          onClick={() => setActiveChannel(FAMILY_MERGE[fam])}
                          title={t("merged.familyDesc", { family: familyLabel(fam) })}
                          aria-pressed={activeChannel === FAMILY_MERGE[fam]}
                        >
                          <span className={sc("mx-tab-label")}>{FAMILY_MERGE[fam]}</span>
                        </button>
                      ) : null}
                      {chs.map((ch) => (
                        <button
                          key={ch}
                          type="button"
                          className={sc("mx-tab", ch === activeChannel && "active")}
                          onClick={() => setActiveChannel(ch)}
                          title={t(`channelDesc.${ch}`)}
                          aria-pressed={ch === activeChannel}
                        >
                          <span className={sc("mx-tab-label")}>{ch}</span>
                          {isDiagnosticChannel(ch) && (
                            <span className={sc("mx-tab-diag")} title={t("main.diagnosticTitle")}>
                              {t("main.diagnostic")}
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          {/* -------- main heatmap: headers (HTML, clickable) + canvas -------- */}
          <section className={sc("mx-main")} ref={mainSecRef}>
            <div className={sc("mx-main-title")}>
              <span className={sc("mx-chan")}>{activeChannel}</span>
              {!isMergeChannel(activeChannel) && isDiagnosticChannel(activeChannel as Channel) && (
                <span className={sc("mx-diag")} title={t("main.diagnosticTitle")}>
                  {t("main.diagnostic")}
                </span>
              )}
              <span className={sc("mx-sub")}>
                {" "}
                —{" "}
                {isMergeChannel(activeChannel)
                  ? mergeFamilyOf(activeChannel)
                    ? familyLabel(mergeFamilyOf(activeChannel) as ChannelFamily)
                    : t("merged.group")
                  : familyLabel(channelFamily(activeChannel as Channel))}
              </span>
              <span className={sc("mx-chan-desc")}>
                {isMergeChannel(activeChannel)
                  ? mergeFamilyOf(activeChannel)
                    ? t("merged.familyDesc", {
                        family: familyLabel(mergeFamilyOf(activeChannel) as ChannelFamily),
                      })
                    : t("merged.desc")
                  : t(`channelDesc.${activeChannel}`)}
              </span>
            </div>

            {/* colour-scale legend + contrast toggle, aligned above the matrix */}
            <div className={sc("mx-scale-row")}>
              <Legend t={t} width={mainPx} domain={stretchActive} />
              <div className={sc("mx-colormode")}>
                <span className={sc("mx-gl")}>{t("colormode.label")}</span>
                <div className="seg" role="group" aria-label={t("colormode.label")}>
                  <button
                    type="button"
                    className={contrast ? "on" : ""}
                    aria-pressed={contrast}
                    onClick={() => setContrast(true)}
                  >
                    {t("colormode.contrast")}
                  </button>
                  <button
                    type="button"
                    className={!contrast ? "on" : ""}
                    aria-pressed={!contrast}
                    onClick={() => setContrast(false)}
                  >
                    {t("colormode.fixed")}
                  </button>
                </div>
              </div>
            </div>

            {cellsErr ? (
              <p className={sc("mx-sub")} style={{ color: "var(--bad)" }}>
                {cellsErr}
              </p>
            ) : !cells ? (
              <p className={sc("mx-sub")}>{t("drawer.loadingSummary")}</p>
            ) : (
              <div className={sc("mx-scroll-x")}>
                <div
                  className={sc("mx-grid")}
                  style={{
                    // --mx-head parameterizes the corner size + col-head row height
                    // + row-head col width. It MUST track headPx (208 detail / 84
                    // fit); the CSS default 208px overflowed the 84px fit cells,
                    // pushing the corner/col-heads over the canvas.
                    ["--mx-head" as string]: `${headPx}px`,
                    gridTemplateColumns: `${headPx}px ${mainPx}px`,
                    gridTemplateRows: `${headPx}px ${mainPx}px`,
                  }}
                >
                  <div className={sc("mx-corner")} />
                  {/* column headers */}
                  <div className={sc("mx-colheads")} style={{ width: mainPx }}>
                    {fitMode
                      ? runs.map((run) => (
                          <div
                            key={`c-${run.start}`}
                            className={sc(
                              "mx-band",
                              "mx-band-col",
                              inAxis(run.start, "col") && "hot",
                            )}
                            style={{ width: run.len * mainCell }}
                            title={`${run.family} · ${run.len}`}
                            onMouseEnter={() =>
                              setAxis({ start: run.start, len: run.len, dir: "col" })
                            }
                            onMouseLeave={() => setAxis(null)}
                          >
                            {run.len * mainCell >= 46 ? <span>{run.family}</span> : null}
                          </div>
                        ))
                      : configs.map((cfg, c) => (
                      <Link
                        key={cfg.id}
                        href={`/card/${encodeURIComponent(cfg.id)}`}
                        className={sc("mx-colhead", (hover?.c === c || inAxis(c, "col")) && "hot")}
                        style={{ width: mainCell }}
                        title={cfg.id}
                        onMouseEnter={() => setAxis({ start: c, len: 1, dir: "col" })}
                        onMouseLeave={() => setAxis(null)}
                      >
                        <span>{cfg.label}</span>
                      </Link>
                        ))}
                  </div>
                  {/* row heads: config names in detail, model-cluster bands in fit */}
                  <div
                    className={sc("mx-rowheads")}
                    style={{ height: mainPx, width: headPx }}
                  >
                    {fitMode
                      ? runs.map((run) => (
                          <div
                            key={`r-${run.start}`}
                            className={sc(
                              "mx-band",
                              "mx-band-row",
                              inAxis(run.start, "row") && "hot",
                            )}
                            style={{ height: run.len * mainCell }}
                            title={`${run.family} · ${run.len}`}
                            onMouseEnter={() =>
                              setAxis({ start: run.start, len: run.len, dir: "row" })
                            }
                            onMouseLeave={() => setAxis(null)}
                          >
                            {run.len * mainCell >= 14 ? <span>{run.family}</span> : null}
                          </div>
                        ))
                      : configs.map((cfg, r) => (
                      <Link
                        key={cfg.id}
                        href={`/card/${encodeURIComponent(cfg.id)}`}
                        className={sc("mx-rowhead", (hover?.r === r || inAxis(r, "row")) && "hot")}
                        style={{ height: mainCell }}
                        title={cfg.id}
                        onMouseEnter={() => setAxis({ start: r, len: 1, dir: "row" })}
                        onMouseLeave={() => setAxis(null)}
                      >
                        <span>{cfg.label}</span>
                      </Link>
                        ))}
                  </div>
                  {/* canvas + crosshair overlay */}
                  <div
                    className={sc("mx-canvas-wrap")}
                    style={{ width: mainPx, height: mainPx }}
                    onMouseMove={onMainMove}
                    onMouseLeave={onMainLeave}
                    onClick={onMainClick}
                  >
                    <canvas ref={mainRef} className={sc("mx-canvas")} />
                    <AxisHighlight cell={mainCell} size={mainPx} />
                    <Crosshair cell={mainCell} size={mainPx} />
                  </div>
                </div>
              </div>
            )}

            <p className={sc("mx-hint", "mx-sub")}>{t("main.hint")}</p>
          </section>
        </div>
      )}

      {/* -------- floating tooltip (figure/figcaption; diagonal single-figure guard) -------- */}
      {tip && hover && hoverConfigA && hoverConfigB && n > 0 && (
        <div
          className={sc("mx-tooltip")}
          // clamp to the viewport — at readable size the tooltip would overflow
          // when hovering the right/bottom regions of the heatmap.
          style={{
            left: Math.max(
              8,
              Math.min(tip.x + 16, window.innerWidth - Math.min(760, window.innerWidth * 0.52) - 16),
            ),
            top: Math.max(8, Math.min(tip.y + 16, window.innerHeight - 480)),
          }}
          role="status"
        >
          <div className={sc("mx-tt-pair")}>
            <strong>{hoverConfigA.label}</strong>
            <span className={sc("mx-sub")}> × </span>
            <strong>{hoverConfigB.label}</strong>
          </div>
          {(hoverConfigA.thumb[variant] || hoverConfigB.thumb[variant]) && (
            <div className={sc("mx-tt-thumbs")}>
              <figure className={sc("mx-tt-fig")}>
                {hoverConfigA.thumb[variant] ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={hoverConfigA.thumb[variant] as string} alt={hoverConfigA.label} />
                ) : (
                  <span className={sc("mx-tt-noshot")}>{t("tooltip.noCard")}</span>
                )}
                <figcaption className={sc("mx-sub")}>{hoverConfigA.label}</figcaption>
              </figure>
              {hover.r !== hover.c && (
                <figure className={sc("mx-tt-fig")}>
                  {hoverConfigB.thumb[variant] ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={hoverConfigB.thumb[variant] as string} alt={hoverConfigB.label} />
                  ) : (
                    <span className={sc("mx-tt-noshot")}>{t("tooltip.noCard")}</span>
                  )}
                  <figcaption className={sc("mx-sub")}>{hoverConfigB.label}</figcaption>
                </figure>
              )}
            </div>
          )}
          <div className={sc("mx-tt-chan")}>
            {activeChannel}
            {hover.r === hover.c && (
              <span className={sc("mx-tt-self")}> · {t("tooltip.self")}</span>
            )}
          </div>
          {hoverCell && isSufficient(hoverCell) ? (
            <div className={sc("mx-tt-val")}>
              {t("tooltip.median")} <strong>{fmt(hoverCell.median as number)}</strong>
              {hoverCell.iqr && (
                <>
                  {" "}
                  · {t("tooltip.iqr")} [{fmt(hoverCell.iqr.p25)}, {fmt(hoverCell.iqr.p75)}]
                </>
              )}
              <div className={sc("mx-sub")}>
                {t("tooltip.nEff")} {hoverCell.n_eff} · {t("tooltip.m")} {hoverCell.m_a}/
                {hoverCell.m_b}
              </div>
            </div>
          ) : (
            <div className={sc("mx-tt-insufficient")}>
              {t("legend.insufficient")}
              <div className={sc("mx-sub")}>
                {t("tooltip.nEff")} {hoverCell?.n_eff ?? 0} · {t("tooltip.m")}{" "}
                {hoverCell?.m_a ?? 0}/{hoverCell?.m_b ?? 0}
              </div>
            </div>
          )}
        </div>
      )}

      {/* -------- cell-click pair-detail drawer (absorbs old /pair) -------- */}
      {drawer && cells && (
        <PairDrawer
          batchId={batchId}
          variant={variant}
          a={drawer.a}
          b={drawer.b}
          configs={session.configs}
          channels={channels}
          summaryCells={cells}
          thumbUrl={(id, slot) =>
            `/b/${batchId}/${id}/${variantToDir(variant)}/${slot}/thumb.webp`
          }
          onClose={() => setDrawer(null)}
        />
      )}
    </div>
  );
}

// ------------------------------- Legend ------------------------------------

function Legend({
  t,
  width,
  domain,
}: {
  t: (k: string) => string;
  width: number;
  domain: { lo: number; hi: number } | null;
}) {
  const stops = [0, 0.25, 0.5, 0.75, 1];
  // The bar is always the full viridis ramp; only the END LABELS change. In
  // contrast mode a cell at value=lo maps to ramp 0 and value=hi to ramp 1, so
  // the ramp stays full and the ticks report the actual p5/p95 endpoints.
  const gradient = `linear-gradient(90deg, ${stops
    .map((s) => `${neutralColor(s)} ${s * 100}%`)
    .join(", ")})`;
  // the bar tracks the matrix width (clamped) so it reads as the matrix's scale.
  const barW = clampInt(width || 240, 200, 420);
  const fmt2 = (x: number) => x.toFixed(2);
  return (
    <div
      className={sc("mx-legend")}
      aria-label={domain ? t("legend.stretchScale") : t("legend.scale")}
    >
      <div className={sc("mx-legend-scale")} style={{ width: barW }}>
        <span className={sc("mx-legend-bar")} style={{ background: gradient }} />
        {domain ? (
          <>
            <div className={sc("mx-legend-ticks")}>
              <span className={sc("mx-legend-tick")}>{fmt2(domain.lo)}</span>
              <span className={sc("mx-legend-tick")}>{fmt2(domain.hi)}</span>
            </div>
            <span className={sc("mx-legend-sublabel")}>{t("legend.stretchRange")}</span>
          </>
        ) : (
          <div className={sc("mx-legend-ticks")}>
            {stops.map((s) => (
              <span key={s} className={sc("mx-legend-tick")}>
                {s}
              </span>
            ))}
          </div>
        )}
      </div>
      <span className={sc("mx-legend-ins")}>
        <span className={sc("mx-legend-swatch")} style={{ background: INSUFFICIENT_COLOR }} />
        <span className={sc("mx-sub")}>{t("legend.insufficient")}</span>
      </span>
    </div>
  );
}
