// ===========================================================================
// app/api/om/store.ts — the weather proxy resolver behind the Next /api/om
// route (spec §3.2 / §4.3). Ports site/cache-api/server.mjs into the standalone
// Next server so interactive live rendering (compare page, gallery live toggle,
// card run-live) has the `/api/om/{forecast,archive}` surface the injected cards
// call — the old separate cache-api process is gone.
//
// Resolution order mirrors cache-api, but the operational cache is IN-MEMORY
// (lib/singleton, spec §3.5 single-process) instead of writing into the deploy
// tree (§3.6 read-only in prod):
//
//   1. frozen records  — data/weather-db/records/<ep>/<lat>,<lon>/<date>.json,
//      seeded from every batch's weather-snapshot.json. Deterministic fixture
//      truth; the live default date hits this so the first paint is offline.
//   2. disk url-cache  — data/weather-db/url-cache/<sha256(url)[:32]>.json, the
//      seeded operational cache (read-only here).
//   3. in-memory cache — singleton Map, populated by prior live fetches.
//   4. live Open-Meteo — rate-limited, timed-out; result cached in memory.
//
// Upstream down ⇒ any already-stored (location,date) still serves. All disk
// reads are read-only; nothing here mutates data/.
// ===========================================================================

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

import { singleton } from "@/lib/singleton";
import { dataRoot } from "@/lib/paths";

export type OmEndpoint = "forecast" | "archive" | "geocoding";

export function isOmEndpoint(v: string): v is OmEndpoint {
  return v === "forecast" || v === "archive" || v === "geocoding";
}

const UPSTREAM: Record<OmEndpoint, string> = {
  forecast: "https://api.open-meteo.com/v1/forecast",
  archive: "https://archive-api.open-meteo.com/v1/archive",
  geocoding: "https://geocoding-api.open-meteo.com/v1/search",
};

// Endpoints backed by a frozen (location,date) records tier.
const HAS_RECORDS: Record<OmEndpoint, boolean> = {
  forecast: true,
  archive: true,
  geocoding: false,
};

const RATE_MIN_INTERVAL_MS = Number(process.env.WCB_UPSTREAM_MIN_INTERVAL_MS || 1100);
const FETCH_TIMEOUT_MS = Number(process.env.WCB_UPSTREAM_TIMEOUT_MS || 15000);
const ALLOW_LIVE = (process.env.WCB_ALLOW_LIVE ?? "1") !== "0";
const COORD_EPS = 1e-3; // Open-Meteo grid tolerance (mirror cache-api)
const MEM_CACHE_MAX = 500;

function weatherDbDir(): string {
  const env = process.env.WCB_WEATHER_DB;
  if (env && env.length > 0) return path.resolve(env);
  // data/weather-db is a sibling of the data root (default data/batches-dev).
  return path.resolve(dataRoot(), "..", "weather-db");
}

// ---------------------------- store-key helpers ----------------------------

function normCoord(s: string): number {
  const n = Number(s);
  return Number.isFinite(n) ? n : NaN;
}

function recordsDir(): string {
  return path.join(weatherDbDir(), "records");
}

function urlCacheDir(): string {
  return path.join(weatherDbDir(), "url-cache");
}

/** Structured-tier lookup: exact path, then tolerant lat/lon scan for the date.
 *  (byte-identical logic to cache-api server.mjs lookupRecord). */
function lookupRecord(
  endpoint: string,
  lat: string,
  lon: string,
  date: string,
): Buffer | null {
  const dir = recordsDir();
  const exact = path.join(dir, endpoint, `${lat},${lon}`, `${date}.json`);
  try {
    if (fs.existsSync(exact)) return fs.readFileSync(exact);
  } catch {
    /* fall through */
  }

  const endpointDir = path.join(dir, endpoint);
  let coordDirs: string[];
  try {
    if (!fs.existsSync(endpointDir)) return null;
    coordDirs = fs.readdirSync(endpointDir);
  } catch {
    return null;
  }
  const wantLat = normCoord(lat);
  const wantLon = normCoord(lon);
  if (Number.isNaN(wantLat) || Number.isNaN(wantLon)) return null;
  for (const coordDir of coordDirs) {
    const comma = coordDir.lastIndexOf(",");
    if (comma < 0) continue;
    const la = normCoord(coordDir.slice(0, comma));
    const lo = normCoord(coordDir.slice(comma + 1));
    if (Number.isNaN(la) || Number.isNaN(lo)) continue;
    if (Math.abs(la - wantLat) <= COORD_EPS && Math.abs(lo - wantLon) <= COORD_EPS) {
      const cand = path.join(endpointDir, coordDir, `${date}.json`);
      try {
        if (fs.existsSync(cand)) return fs.readFileSync(cand);
      } catch {
        /* keep scanning */
      }
    }
  }
  return null;
}

/** url-cache key — sha256 of the resolved upstream URL, [:32] (trial-identical). */
function urlKey(upstreamUrl: string): string {
  return crypto.createHash("sha256").update(upstreamUrl).digest("hex").slice(0, 32);
}

function diskUrlCache(upstreamUrl: string): Buffer | null {
  const p = path.join(urlCacheDir(), `${urlKey(upstreamUrl)}.json`);
  try {
    if (fs.existsSync(p)) return fs.readFileSync(p);
  } catch {
    /* miss */
  }
  return null;
}

// ------------------------------ in-memory cache ----------------------------

interface MemEntry {
  status: number;
  body: string;
}

function memCache(): Map<string, MemEntry> {
  return singleton("om.memcache", () => new Map<string, MemEntry>());
}

// --------------------------- rate-limited fetch ----------------------------

interface RateState {
  last: number;
  chain: Promise<unknown>;
}

function rateState(): RateState {
  return singleton<RateState>("om.rate", () => ({ last: 0, chain: Promise.resolve() }));
}

/** Serialize upstream fetches (concurrency 1) and enforce a min inter-request
 *  gap — polite Open-Meteo citizen (mirror cache-api). */
function rateLimitedFetch(url: string): Promise<{ status: number; body: string }> {
  const st = rateState();
  const run = async () => {
    const wait = st.last + RATE_MIN_INTERVAL_MS - Date.now();
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    st.last = Date.now();
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const res = await fetch(url, { signal: ctrl.signal });
      const body = await res.text();
      return { status: res.status, body };
    } finally {
      clearTimeout(timer);
    }
  };
  const result = st.chain.then(run, run);
  st.chain = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

// --------------------------------- resolve ---------------------------------

export interface OmResult {
  status: number;
  body: string;
  /** which tier answered — for diagnostics / test assertions. */
  source: "records" | "url-cache" | "mem" | "live" | "disabled" | "error";
}

/**
 * Resolve one weather request. `rawQuery` is the request's raw query string
 * (no leading '?'). Order: frozen records → disk url-cache → in-memory →
 * rate-limited live Open-Meteo (cached in memory on success).
 */
export async function resolveOm(endpoint: OmEndpoint, rawQuery: string): Promise<OmResult> {
  const query = new URLSearchParams(rawQuery);

  // 1) frozen records (forecast/archive only)
  if (HAS_RECORDS[endpoint]) {
    const lat = query.get("latitude") ?? query.get("lat");
    const lon = query.get("longitude") ?? query.get("lon");
    const date = query.get("start_date") ?? query.get("date") ?? "current";
    if (lat != null && lon != null) {
      const rec = lookupRecord(endpoint, lat, lon, date);
      if (rec) return { status: 200, body: rec.toString("utf8"), source: "records" };
    }
  }

  const upstreamUrl = UPSTREAM[endpoint] + (rawQuery ? `?${rawQuery}` : "");

  // 2) disk url-cache (seeded, read-only)
  const disk = diskUrlCache(upstreamUrl);
  if (disk) return { status: 200, body: disk.toString("utf8"), source: "url-cache" };

  // 3) in-memory operational cache
  const cache = memCache();
  const hit = cache.get(upstreamUrl);
  if (hit) return { status: hit.status, body: hit.body, source: "mem" };

  // 4) live fetch (rate-limited) + cache in memory
  if (!ALLOW_LIVE) {
    return {
      status: 503,
      body: JSON.stringify({ error: "cache miss and live fetch disabled", reason: "no-store-entry" }),
      source: "disabled",
    };
  }
  try {
    const { status, body } = await rateLimitedFetch(upstreamUrl);
    if (status === 200) {
      if (cache.size >= MEM_CACHE_MAX) {
        const oldest = cache.keys().next().value;
        if (oldest !== undefined) cache.delete(oldest);
      }
      cache.set(upstreamUrl, { status, body });
    }
    return { status, body, source: "live" };
  } catch (e) {
    return {
      status: 502,
      body: JSON.stringify({ error: String((e as Error)?.message ?? e) }),
      source: "error",
    };
  }
}
