// ===========================================================================
// lib/arena.ts — blind-pair issuance, voting, and Bradley-Terry aggregation
// (spec §3.2). Integrity-critical ("LAW-level"): blindness is enforced at the
// protocol level — opaque single-use tokens (the pairs row id, no decodable
// payload), ORIGINAL card.html only, qualified-grade pool only.
//
// This module deliberately depends on NO not-yet-built peer module at load time:
//   • the sampling pool is derived from lib/data (loaded lazily) — grading is
//     defensive (qualified only; dev behind WCB_ARENA_INCLUDE_DEV; community is
//     quarantined = excluded until a reviewed-inclusion path is wired by
//     integration once lib/registry's reviewed flag exists);
//   • srcdoc rendering uses lib/live's buildSrcdoc/stubFetch when present and
//     otherwise degrades to the raw (still sandboxed) html — rendering never
//     blocks the vote loop.
// A test seam (setPoolProvider) lets the self-test drive the whole loop against
// a synthetic pool with no data root.
//
// The voting MATH here is FIXED and published verbatim on /methodology#voting.
// ===========================================================================

import crypto from "node:crypto";

import { getDb } from "./db";
import { singleton } from "./singleton";
import { compareSlug } from "./neutral";
import { CITIES } from "./cities";
import { armOf, configLabel } from "./label";
import { channelGradeOf } from "./channel";
import { VARIANTS, type Variant } from "./variant";

// --------------------------------- types -----------------------------------

export type Angle = string; // free-text column; v1 UI uses overall|visual|clarity
export const ANGLES = ["overall", "visual", "clarity"] as const;
export type PairSource = "gate" | "arena" | "share";
export type Choice = "a" | "b" | "tie" | "both_bad";
export type Grade = "qualified" | "dev" | "community";

/** A ref stored in pairs.refs_json / echoed into vote provenance. */
export interface PairRef {
  configId: string;
  batchId: string;
  slot: number;
  sha256: string;
  /** presented position (0 = left, 1 = right) after the random flip. */
  pos: number;
}

export interface IssuedPair {
  token: string;
  mode: "pair";
  angle: Angle;
  variant: Variant;
  city: string;
  date: string;
  /** presented order (index === presented position). `shotUrl` is a BLIND,
   *  inlined data: URI of the recorded shot.webp (gate source only) — no
   *  identity path — that the mobile gate renders instead of a live frame. */
  cards: { html: string; shotUrl?: string }[];
}

export interface RevealItem {
  configId: string;
  batchId: string;
  label: string;
  arm: string;
  grade: Grade;
}

export type VoteOutcome =
  | { ok: true; reveal: RevealItem[]; shareUrl?: string }
  | { ok: false; status: number; code: string };

export interface StatsItem {
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

export interface StatsResponse {
  totalVotes: number;
  arenaVotes: number;
  gateVotes: number;
  methodUrl: string;
  angle: Angle;
  variant: Variant;
  items: StatsItem[];
}

// --------------------------- lazy peer modules -----------------------------
//
// Loaded at call time, never at module load, so this module imports cleanly with
// no data root and before lib/live exists.

/* eslint-disable @typescript-eslint/no-explicit-any */
async function loadData(): Promise<any | null> {
  try {
    return await import("./data");
  } catch {
    return null;
  }
}

async function loadLive(): Promise<any | null> {
  // Fully-dynamic specifier: tsc treats it as `any` (no resolution), webpack
  // builds a ./live context that resolves once lib/live/inject lands.
  const leaf: string = "inject";
  try {
    return await import(`./live/${leaf}`);
  } catch {
    return null;
  }
}
/* eslint-enable @typescript-eslint/no-explicit-any */

// -------------------------------- the pool ---------------------------------

export interface PoolEntry {
  configId: string;
  batchId: string;
  /** valid ORIGINAL slots for the chosen variant. */
  slots: { index: number; sha256: string | null }[];
}

type PoolProvider = (variant: Variant) => PoolEntry[] | Promise<PoolEntry[]>;

let _poolProvider: PoolProvider | null = null;

/** Test seam: override the pool source (self-test injects a synthetic pool). */
export function setPoolProvider(fn: PoolProvider | null): void {
  _poolProvider = fn;
}

function includeDev(): boolean {
  return process.env.WCB_ARENA_INCLUDE_DEV === "1";
}

/** True when a config's transport marks it as a quarantined community run. */
function isCommunity(transport: string, batchId: string): boolean {
  return (
    transport.toLowerCase().includes("community") ||
    batchId.toLowerCase().includes("community")
  );
}

/**
 * Build the sampling pool from lib/data. Qualified only (dev behind
 * WCB_ARENA_INCLUDE_DEV); community quarantined (excluded). configId is deduped
 * across batches preferring more valid slots, tie → newer created_at.
 */
async function poolFromData(variant: Variant): Promise<PoolEntry[]> {
  const D = await loadData();
  if (!D) return [];
  let batches: Array<{ id: string; created_at: string }>;
  try {
    batches = D.listBatches();
  } catch {
    return [];
  }

  // configId -> winning candidate across batches
  const best = new Map<
    string,
    { batchId: string; createdAt: string; totalValid: number; slots: { index: number; sha256: string | null }[] }
  >();

  for (const b of batches) {
    let configs: Array<{ config_id: string; transport: string }>;
    try {
      configs = D.loadConfigs(b.id);
    } catch {
      continue;
    }
    for (const cfg of configs) {
      const transport = String(cfg.transport ?? "");
      // configId-aware: sub2-era codex batches carry no "sub2" transport marker,
      // so without cfg.config_id they'd grade "qualified" and leak into the blind
      // pair pool (P0). See channelGradeOf() in lib/channel.ts.
      const grade = channelGradeOf(transport, cfg.config_id);
      if (grade === "dev" && !includeDev()) continue;
      if (isCommunity(transport, b.id)) continue; // quarantine

      // valid slots per variant (dedup key) + this variant's valid slots
      let totalValid = 0;
      let variantSlots: { index: number; sha256: string | null }[] = [];
      for (const v of VARIANTS) {
        let metas: Array<{ slot_index: number; state: string }>;
        try {
          metas = D.loadSlots(b.id, cfg.config_id, v);
        } catch {
          metas = [];
        }
        const valid = metas.filter((m) => m.state === "valid");
        totalValid += valid.length;
        if (v === variant) {
          variantSlots = valid.map((m) => ({ index: m.slot_index, sha256: null }));
        }
      }
      if (totalValid === 0) continue;

      const prev = best.get(cfg.config_id);
      if (
        !prev ||
        totalValid > prev.totalValid ||
        (totalValid === prev.totalValid && b.created_at > prev.createdAt)
      ) {
        best.set(cfg.config_id, {
          batchId: b.id,
          createdAt: b.created_at,
          totalValid,
          slots: variantSlots,
        });
      }
    }
  }

  const out: PoolEntry[] = [];
  for (const [configId, c] of best) {
    if (c.slots.length === 0) continue; // no valid slot in THIS variant
    out.push({ configId, batchId: c.batchId, slots: c.slots });
  }
  return out;
}

async function getPool(variant: Variant): Promise<PoolEntry[]> {
  if (_poolProvider) return await _poolProvider(variant);
  return await poolFromData(variant);
}

// ------------------------------- vote counts -------------------------------

/** votes(config) across both sides (arena+share+gate all count for weighting). */
function voteCountsByConfig(): Map<string, number> {
  const db = getDb();
  const m = new Map<string, number>();
  const add = (c: string | null, n: number) => {
    if (!c) return;
    m.set(c, (m.get(c) ?? 0) + n);
  };
  for (const r of db.prepare(`SELECT config_a AS c, COUNT(*) AS n FROM votes GROUP BY config_a`).all() as Array<{ c: string; n: number }>) {
    add(r.c, r.n);
  }
  for (const r of db.prepare(`SELECT config_b AS c, COUNT(*) AS n FROM votes WHERE config_b IS NOT NULL GROUP BY config_b`).all() as Array<{ c: string; n: number }>) {
    add(r.c, r.n);
  }
  return m;
}

// --------------------------------- sampling --------------------------------

function pick<T>(arr: readonly T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

/** Weighted pick by weight(item); falls back to uniform if all weights ≤ 0. */
function weightedPick<T>(items: readonly T[], weight: (t: T) => number): T {
  let total = 0;
  const w = items.map((it) => {
    const x = Math.max(0, weight(it));
    total += x;
    return x;
  });
  if (total <= 0) return pick(items);
  let r = Math.random() * total;
  for (let i = 0; i < items.length; i++) {
    r -= w[i];
    if (r <= 0) return items[i];
  }
  return items[items.length - 1];
}

/** Choose the issuing variant: prefer P-q when it has ≥2 configs. */
async function chooseVariant(): Promise<{ variant: Variant; pool: PoolEntry[] }> {
  const q = await getPool("P-q");
  if (q.length >= 2) return { variant: "P-q", pool: q };
  const min = await getPool("P-min");
  if (min.length >= 2) return { variant: "P-min", pool: min };
  // return the larger for a truthful error path
  return q.length >= min.length ? { variant: "P-q", pool: q } : { variant: "P-min", pool: min };
}

// ------------------------------- html + sha --------------------------------

async function slotHtmlAndSha(
  batchId: string,
  configId: string,
  variant: Variant,
  index: number,
  knownSha: string | null,
): Promise<{ html: string; sha256: string }> {
  const D = await loadData();
  let html = "";
  try {
    html = D ? D.loadCardHtml(batchId, configId, variant, index) : "";
  } catch {
    html = "";
  }
  const sha =
    knownSha ??
    (html ? crypto.createHash("sha256").update(html, "utf8").digest("hex") : "");
  return { html, sha256: sha };
}

interface Located {
  city: string;
  date: string;
  /** snapshot payload to feed stubFetch; undefined → raw fetch fallback. */
  snapshot: unknown | undefined;
}

/** Same fixture BOTH sides: derive city/date/snapshot from one batch when we
 *  can (offline-deterministic), else a random preset city + today (live). */
async function locate(batchId: string): Promise<Located> {
  const D = await loadData();
  if (D) {
    try {
      const snap = D.loadWeatherSnapshot(batchId);
      const locs: Array<{ city: string; date: string }> = snap?.locations ?? [];
      if (locs.length > 0) {
        const loc = pick(locs);
        // narrow the snapshot to the chosen location so both cards get one fixture
        const narrowed = { ...snap, locations: locs.filter((l) => l.city === loc.city && l.date === loc.date) };
        return { city: loc.city, date: loc.date, snapshot: narrowed };
      }
    } catch {
      /* fall through */
    }
  }
  return { city: pick(CITIES).name, date: new Date().toISOString().slice(0, 10), snapshot: undefined };
}

/** Simulated API latency for arena pairs (2026-07-19): the frozen fixture
 *  answers instantly, which makes the cards pop fully-formed — a small fixed
 *  delay lets voters SEE each card's loading behavior (skeleton/spinner/error
 *  handling is part of the UI being judged). One constant for BOTH sides of
 *  every pair — a per-side difference would bias perceived snappiness. */
const PAIR_SIM_LATENCY_MS = 700;

async function renderCard(html: string, loc: Located): Promise<string> {
  if (!html) return "";
  const live = await loadLive();
  if (live && typeof live.buildSrcdoc === "function") {
    try {
      // new lib/live signature: buildSrcdoc(html, { origin?, snapshot?, delayMs? })
      return live.buildSrcdoc(html, { snapshot: loc.snapshot, delayMs: PAIR_SIM_LATENCY_MS });
    } catch {
      /* degrade */
    }
  }
  return html; // sandboxed raw html; weather fetch degrades, integrity intact
}

// -------------------------------- sweep ------------------------------------

const SWEEP_TTL_MS = 15 * 60 * 1000;

/** Delete unconsumed pairs older than 15 min (cheap; run on issuance). */
function sweep(): void {
  const cutoff = new Date(Date.now() - SWEEP_TTL_MS).toISOString();
  getDb().prepare(`DELETE FROM pairs WHERE consumed=0 AND ts < ?`).run(cutoff);
}

// ------------------------------ pair issuance ------------------------------

function newToken(): string {
  return crypto.randomBytes(32).toString("hex");
}

/** Insert a pairs row from two logical sides [A, B] and return the response. */
async function issueFromSides(
  angle: Angle,
  variant: Variant,
  source: PairSource,
  sides: Array<{ configId: string; batchId: string; index: number; sha256: string | null }>,
  shareId: string | null,
): Promise<IssuedPair> {
  const loc = await locate(sides[0].batchId);

  // resolve html + sha for each side
  const resolved = await Promise.all(
    sides.map((s) => slotHtmlAndSha(s.batchId, s.configId, variant, s.index, s.sha256)),
  );

  // random left/right flip: assign presented positions
  const flip = Math.random() < 0.5;
  const refs: PairRef[] = sides.map((s, i) => ({
    configId: s.configId,
    batchId: s.batchId,
    slot: s.index,
    sha256: resolved[i].sha256,
    pos: flip ? 1 - i : i,
  }));

  // Mobile gate renders a static shot both sides (spec §4.0). Inline the
  // recorded shot.webp as a BLIND data: URI (no identity path) — gate source
  // only, so arena/share payloads stay lean. Best-effort: a missing shot omits
  // shotUrl and the client falls back to the (already sandboxed) live frame.
  const D = source === "gate" ? await loadData() : null;
  const shotFor = (ref: PairRef): string | undefined => {
    if (!D || typeof D.loadShot !== "function") return undefined;
    try {
      const buf: Buffer | null = D.loadShot(ref.batchId, ref.configId, variant, ref.slot);
      if (!buf || buf.length === 0) return undefined;
      return `data:image/webp;base64,${buf.toString("base64")}`;
    } catch {
      return undefined;
    }
  };

  // cards ordered by presented position
  const cards: { html: string; shotUrl?: string }[] = new Array(sides.length);
  await Promise.all(
    refs.map(async (ref, i) => {
      cards[ref.pos] = { html: await renderCard(resolved[i].html, loc), shotUrl: shotFor(ref) };
    }),
  );

  const token = newToken();
  const ts = new Date().toISOString();
  getDb()
    .prepare(
      `INSERT INTO pairs (id, ts, mode, angle, variant, city, date, refs_json, source, share_id, consumed)
       VALUES (?, ?, 'pair', ?, ?, ?, ?, ?, ?, ?, 0)`,
    )
    .run(token, ts, angle, variant, loc.city, loc.date, JSON.stringify(refs), source, shareId);

  return { token, mode: "pair", angle, variant, city: loc.city, date: loc.date, cards };
}

export class ArenaUnavailable extends Error {
  code: string;
  constructor(code: string) {
    super(code);
    this.code = code;
  }
}

/**
 * Issue a fresh blind pair. Side A weighted ∝ 1/(1+votes); side B UNIFORM over
 * the rest (under-voted nodes always connect to the graph core → keeps BT
 * identifiable; never weight both sides). Same variant, distinct configs.
 */
export async function issuePair(opts: { angle: Angle; source: PairSource }): Promise<IssuedPair> {
  sweep();
  const { variant, pool } = await chooseVariant();
  if (pool.length < 2) throw new ArenaUnavailable("pool-too-small");

  const counts = voteCountsByConfig();
  const sideA = weightedPick(pool, (e) => 1 / (1 + (counts.get(e.configId) ?? 0)));
  const rest = pool.filter((e) => e.configId !== sideA.configId);
  const sideB = pick(rest); // uniform

  const a = { configId: sideA.configId, batchId: sideA.batchId, ...pick(sideA.slots) };
  const b = { configId: sideB.configId, batchId: sideB.batchId, ...pick(sideB.slots) };
  return issueFromSides(opts.angle, variant, opts.source, [a, b], null);
}

// -------------------------------- voting -----------------------------------

const DWELL_MIN_MS = 2500;
const DWELL_MAX_MS = 10 * 60 * 1000;

interface PairRow {
  id: string;
  ts: string;
  angle: string;
  variant: string;
  city: string;
  date: string;
  refs_json: string;
  source: PairSource;
  consumed: number;
}

/**
 * Record a vote. Atomic single-use consume kills replay + post-reveal stuffing
 * by construction. Server dwell (now - issue) rejects impossible-speed votes.
 * The caller (route) must run the rate-limit gate first and pass ip_hash.
 */
export async function castVote(opts: {
  token: string;
  choice: Choice;
  voterId: string;
  ipHash: string;
  latencyMs?: number | null;
  locale?: string | null;
}): Promise<VoteOutcome> {
  const db = getDb();
  const row = db.prepare(`SELECT * FROM pairs WHERE id=?`).get(opts.token) as PairRow | undefined;
  if (!row || row.consumed) return { ok: false, status: 410, code: "used-or-expired" };

  const dwell = Date.now() - Date.parse(row.ts);
  if (dwell < DWELL_MIN_MS) return { ok: false, status: 410, code: "too-fast" };
  if (dwell > DWELL_MAX_MS) return { ok: false, status: 410, code: "expired" };

  // atomic consume — only one caller wins the race
  const consumed = db.prepare(`UPDATE pairs SET consumed=1 WHERE id=? AND consumed=0`).run(opts.token);
  if (consumed.changes !== 1) return { ok: false, status: 410, code: "used-or-expired" };

  let refs: PairRef[];
  try {
    refs = JSON.parse(row.refs_json);
  } catch {
    return { ok: false, status: 500, code: "bad-pair" };
  }
  const a = refs[0];
  const b = refs[1] ?? null;

  db.prepare(
    `INSERT INTO votes
      (ts, pair_id, voter_id, ip_hash, mode, angle, variant, city, date,
       config_a, batch_a, slot_a, sha_a, config_b, batch_b, slot_b, sha_b,
       choice, source, patched_served, latency_ms, dwell_ms, locale)
     VALUES (?,?,?,?,?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,0,?,?,?)`,
  ).run(
    new Date().toISOString(),
    opts.token,
    opts.voterId,
    opts.ipHash,
    "pair",
    row.angle,
    row.variant,
    row.city,
    row.date,
    a.configId,
    a.batchId,
    a.slot,
    a.sha256,
    b?.configId ?? null,
    b?.batchId ?? null,
    b?.slot ?? null,
    b?.sha256 ?? null,
    opts.choice,
    row.source,
    opts.latencyMs ?? null,
    dwell,
    opts.locale ?? null,
  );

  const reveal = await buildReveal(refs);
  return { ok: true, reveal };
}

async function buildReveal(refs: PairRef[]): Promise<RevealItem[]> {
  const D = await loadData();
  const ordered = [...refs].sort((x, y) => x.pos - y.pos);
  return Promise.all(
    ordered.map(async (ref) => {
      let label = ref.configId;
      let grade: Grade = "qualified";
      try {
        if (D) {
          const cfg = D.loadConfig(ref.batchId, ref.configId);
          label = configLabel({
            config_id: cfg.config_id,
            model_id: cfg.model_id,
            effort: cfg.effort,
            protocol: cfg.protocol,
          });
          grade = isCommunity(String(cfg.transport ?? ""), ref.batchId)
            ? "community"
            : (channelGradeOf(String(cfg.transport ?? ""), cfg.config_id) as Grade);
        }
      } catch {
        /* keep fallbacks */
      }
      return { configId: ref.configId, batchId: ref.batchId, label, arm: armOf(ref.configId), grade };
    }),
  );
}

// --------------------------------- shares ----------------------------------

export interface ShareResult {
  shareId: string;
  url: string;
}

interface SharePayload {
  variant: Variant;
  refs: PairRef[];
}

/** Parse a shares.refs_json, tolerating the bare-array back-compat shape. */
function parseSharePayload(raw: string): SharePayload | null {
  try {
    const j = JSON.parse(raw);
    if (Array.isArray(j)) return { variant: "P-q", refs: j as PairRef[] };
    if (j && Array.isArray(j.refs)) {
      return { variant: (j.variant as Variant) ?? "P-q", refs: j.refs as PairRef[] };
    }
    return null;
  } catch {
    return null;
  }
}

/** Create a reveal-time matchup permalink from a (consumed) pair's refs. */
export function createShare(token: string): ShareResult | null {
  const db = getDb();
  const row = db
    .prepare(`SELECT refs_json, variant FROM pairs WHERE id=?`)
    .get(token) as { refs_json: string; variant: string } | undefined;
  if (!row) return null;
  let refs: PairRef[];
  try {
    refs = JSON.parse(row.refs_json);
  } catch {
    return null;
  }
  const shareId = crypto.randomBytes(12).toString("hex");
  const payload: SharePayload = { variant: row.variant as Variant, refs };
  db.prepare(`INSERT INTO shares (id, ts, refs_json) VALUES (?,?,?)`).run(
    shareId,
    new Date().toISOString(),
    JSON.stringify(payload),
  );
  return { shareId, url: `/arena/m/${shareId}` };
}

/** Issue a FRESH blind pair from a share's refs (source='share'). */
export async function issueFromShare(shareId: string, angle: Angle): Promise<IssuedPair> {
  const db = getDb();
  const row = db.prepare(`SELECT refs_json FROM shares WHERE id=?`).get(shareId) as { refs_json: string } | undefined;
  if (!row) throw new ArenaUnavailable("share-not-found");
  const payload = parseSharePayload(row.refs_json);
  if (!payload) throw new ArenaUnavailable("bad-share");
  // Preserve the ORIGINAL variant so we serve the exact slots the matchup used.
  const sides = payload.refs.map((r) => ({ configId: r.configId, batchId: r.batchId, index: r.slot, sha256: r.sha256 }));
  return issueFromSides(angle, payload.variant, "share", sides, shareId);
}

// ---------------------------- read a share (page) --------------------------

export function getShareRefs(shareId: string): PairRef[] | null {
  const db = getDb();
  const row = db.prepare(`SELECT refs_json FROM shares WHERE id=?`).get(shareId) as { refs_json: string } | undefined;
  if (!row) return null;
  const payload = parseSharePayload(row.refs_json);
  return payload ? payload.refs : null;
}

// ================================ aggregation ==============================
//
// The voting math, FIXED and published on /methodology#voting:
//   • Bradley-Terry (iterative MM) over PAIR votes only, per angle × variant.
//   • tie counts ½ to each side; both_bad is EXCLUDED from BT (otherwise it's a
//     suppression lever) and reported separately as bothBadRate.
//   • Default aggregation EXCLUDES source='gate' (coerced unlock votes); arena +
//     share count. Gate rows remain in the CSV export, labelled.
//   • insufficient = n < 20 || nVoters < 8 → prefRate/ci/strength are null.
// Cached 5 min via singleton.

const STATS_TTL_MS = 5 * 60 * 1000;
const Z = 1.959963984540054; // 95%

interface VoteRow {
  voter_id: string;
  config_a: string;
  config_b: string | null;
  choice: Choice;
}

function wilson(k: number, n: number): [number, number] {
  if (n <= 0) return [0, 0];
  const phat = k / n;
  const denom = 1 + (Z * Z) / n;
  const center = (phat + (Z * Z) / (2 * n)) / denom;
  const margin = (Z / denom) * Math.sqrt((phat * (1 - phat)) / n + (Z * Z) / (4 * n * n));
  return [Math.max(0, center - margin), Math.min(1, center + margin)];
}

/** Bradley-Terry strengths (normalised to geometric mean 1) over pair votes. */
export function bradleyTerry(rows: VoteRow[]): Map<string, number> {
  // wins[i][j] = wins of i over j (tie → 0.5 each). both_bad excluded.
  const wins = new Map<string, Map<string, number>>();
  const configs = new Set<string>();
  const bump = (i: string, j: string, v: number) => {
    configs.add(i);
    configs.add(j);
    let row = wins.get(i);
    if (!row) wins.set(i, (row = new Map()));
    row.set(j, (row.get(j) ?? 0) + v);
  };
  for (const r of rows) {
    if (!r.config_b) continue;
    const a = r.config_a;
    const b = r.config_b;
    if (r.choice === "a") bump(a, b, 1);
    else if (r.choice === "b") bump(b, a, 1);
    else if (r.choice === "tie") {
      bump(a, b, 0.5);
      bump(b, a, 0.5);
    }
    // both_bad: excluded
  }
  const ids = [...configs];
  if (ids.length === 0) return new Map();

  const W = new Map<string, number>(); // total wins
  const games = new Map<string, Map<string, number>>(); // symmetric game counts
  for (const i of ids) {
    let tot = 0;
    for (const j of ids) {
      if (i === j) continue;
      const wij = wins.get(i)?.get(j) ?? 0;
      const wji = wins.get(j)?.get(i) ?? 0;
      tot += wij;
      let g = games.get(i);
      if (!g) games.set(i, (g = new Map()));
      g.set(j, wij + wji);
    }
    W.set(i, tot);
  }

  let p = new Map<string, number>(ids.map((id) => [id, 1]));
  for (let iter = 0; iter < 200; iter++) {
    const next = new Map<string, number>();
    for (const i of ids) {
      let denom = 0;
      const gi = games.get(i);
      if (gi) {
        for (const [j, nij] of gi) {
          if (nij <= 0) continue;
          denom += nij / (p.get(i)! + p.get(j)!);
        }
      }
      const wi = W.get(i) ?? 0;
      next.set(i, denom > 0 ? wi / denom : p.get(i)!);
    }
    // normalise to geometric mean 1 (guard zeros)
    let logSum = 0;
    let cnt = 0;
    for (const v of next.values()) {
      if (v > 0) {
        logSum += Math.log(v);
        cnt++;
      }
    }
    const gm = cnt > 0 ? Math.exp(logSum / cnt) : 1;
    if (gm > 0) for (const id of ids) next.set(id, (next.get(id) ?? 0) / gm);
    p = next;
  }
  return p;
}

function computeStats(angle: Angle, variant: Variant): StatsResponse {
  const db = getDb();

  const counts = db
    .prepare(
      `SELECT
         COUNT(*) AS total,
         SUM(CASE WHEN source IN ('arena','share') THEN 1 ELSE 0 END) AS arena,
         SUM(CASE WHEN source='gate' THEN 1 ELSE 0 END) AS gate
       FROM votes WHERE angle=? AND variant=?`,
    )
    .get(angle, variant) as { total: number; arena: number; gate: number };

  // default aggregation excludes gate
  const rows = db
    .prepare(
      `SELECT voter_id, config_a, config_b, choice
         FROM votes
        WHERE angle=? AND variant=? AND source IN ('arena','share')`,
    )
    .all(angle, variant) as VoteRow[];

  const bt = bradleyTerry(rows);

  // per-config tallies
  interface Tally {
    wins: number;
    losses: number;
    ties: number;
    both_bad: number;
    voters: Map<string, number>;
  }
  const tally = new Map<string, Tally>();
  const get = (c: string): Tally => {
    let t = tally.get(c);
    if (!t) tally.set(c, (t = { wins: 0, losses: 0, ties: 0, both_bad: 0, voters: new Map() }));
    return t;
  };
  for (const r of rows) {
    if (!r.config_b) continue;
    const sides: Array<{ c: string; self: "a" | "b" }> = [
      { c: r.config_a, self: "a" },
      { c: r.config_b, self: "b" },
    ];
    for (const { c, self } of sides) {
      const t = get(c);
      t.voters.set(r.voter_id, (t.voters.get(r.voter_id) ?? 0) + 1);
      if (r.choice === "both_bad") t.both_bad++;
      else if (r.choice === "tie") t.ties++;
      else if (r.choice === self) t.wins++;
      else t.losses++;
    }
  }

  const items: StatsItem[] = [];
  for (const [configId, t] of tally) {
    const n = t.wins + t.losses + t.ties + t.both_bad;
    const nVoters = t.voters.size;
    const decisiveTie = t.wins + t.losses + t.ties;
    const bothBadRate = n > 0 ? t.both_bad / n : 0;
    let maxShare = 0;
    for (const v of t.voters.values()) maxShare = Math.max(maxShare, n > 0 ? v / n : 0);
    const insufficient = n < 20 || nVoters < 8;
    let prefRate: number | null = null;
    let ci: [number, number] | null = null;
    let strength: number | null = null;
    if (!insufficient && decisiveTie > 0) {
      const k = t.wins + 0.5 * t.ties;
      prefRate = k / decisiveTie;
      ci = wilson(k, decisiveTie);
      strength = bt.get(configId) ?? null;
    }
    items.push({ configId, n, nVoters, prefRate, ci, strength, bothBadRate, maxSingleVoterShare: maxShare, insufficient });
  }

  // API/CSV emit in NEUTRAL slug order (reproducible exports)
  items.sort((x, y) => compareSlug(x.configId, y.configId));

  return {
    totalVotes: counts.total ?? 0,
    arenaVotes: counts.arena ?? 0,
    gateVotes: counts.gate ?? 0,
    methodUrl: "/methodology#voting",
    angle,
    variant,
    items,
  };
}

interface CacheEntry {
  ts: number;
  data: StatsResponse;
}

/** 5-min cached stats for an angle × variant. */
export function stats(angle: Angle, variant: Variant): StatsResponse {
  const cache = singleton("arena.stats.cache", () => new Map<string, CacheEntry>());
  const key = `${angle}|${variant}`;
  const hit = cache.get(key);
  const now = Date.now();
  if (hit && now - hit.ts < STATS_TTL_MS) return hit.data;
  const data = computeStats(angle, variant);
  cache.set(key, { ts: now, data });
  return data;
}

/** BT strengths only (kept for API/CSV consumers). */
export function strengths(angle: Angle, variant: Variant): Map<string, number> {
  const out = new Map<string, number>();
  for (const it of stats(angle, variant).items) {
    if (it.strength != null) out.set(it.configId, it.strength);
  }
  return out;
}

// --------------------------------- export ----------------------------------

const CSV_DISCLAIMER =
  "# unauthenticated, localStorage-identity web votes — trivially gameable; treat as community signal, not measurement.";

const CSV_COLS = [
  "id", "ts", "voter_id", "ip_hash", "mode", "angle", "variant", "city", "date",
  "config_a", "batch_a", "slot_a", "sha_a",
  "config_b", "batch_b", "slot_b", "sha_b",
  "choice", "source", "patched_served", "latency_ms", "dwell_ms", "locale",
] as const;

function csvCell(v: unknown): string {
  if (v == null) return "";
  let s = String(v);
  if (s.includes(",") || s.includes('"') || s.includes("\n")) s = `"${s.replace(/"/g, '""')}"`;
  return s;
}

/** Full votes CSV (open data). ip_hash + voter_id truncated to 12 hex. */
export function exportCsv(): string {
  const db = getDb();
  const rows = db.prepare(`SELECT * FROM votes ORDER BY id ASC`).all() as Array<Record<string, unknown>>;
  const lines: string[] = [CSV_DISCLAIMER, CSV_COLS.join(",")];
  for (const r of rows) {
    const rec: Record<string, unknown> = {
      ...r,
      voter_id: String(r.voter_id ?? "").slice(0, 12),
      ip_hash: String(r.ip_hash ?? "").slice(0, 12),
    };
    lines.push(CSV_COLS.map((c) => csvCell(rec[c])).join(","));
  }
  return lines.join("\n") + "\n";
}
