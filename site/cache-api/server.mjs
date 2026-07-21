#!/usr/bin/env node
// ===========================================================================
// site/cache-api/server.mjs — the append-only weather cache-API (PROJECT-DESIGN
// §4.3, S7). A single-file Node service (no deps) serving:
//
//     GET /api/om/forecast?...   → https://api.open-meteo.com/v1/forecast
//     GET /api/om/archive?...    → https://archive-api.open-meteo.com/v1/archive
//     GET /health                → liveness + store counts
//
// Model-authored weather cards only ever call /api/om/{forecast,archive} (the
// prompt contract — immutable). The live /live gallery renders those cards in
// the visitor's browser; they carry no batch context and none is needed
// (§4.3): the request's own start_date/end_date selects the day, and the store
// holds the exact bytes that day was measured with.
//
// TWO-TIER, (location,date)-keyed store — both tiers FIRST-WRITER-WINS:
//
//   weather-db/records/<endpoint>/<lat>,<lon>/<date>.json
//       Authoritative fixture data seeded from every batch's frozen
//       weather-snapshot.json (§4.3 "按 (location,date) 并入"). Matched
//       tolerantly (lat/lon within EPS, date exact) so query-param reordering
//       or coordinate rounding still hits. The frozen artifact stores decimal
//       STRINGS for hash reproducibility; this tier is re-materialized to the
//       Open-Meteo NUMBER wire shape at seed time (CONTRACT-NOTES / DATA-LAYOUT
//       §weather-snapshot).
//
//   weather-db/url-cache/<sha256(upstreamURL)[:32]>.json
//       The operational exact-URL cache. Key scheme is byte-identical to the
//       trial proxy (trial-20260715/serve.py), so the trial's api-cache/ files
//       are merged in verbatim, and every on-miss live fetch persists here.
//
// Resolution order: records (frozen truth) → url-cache → rate-limited live
// fetch from the real Open-Meteo (+persist). Upstream down ⇒ any already-stored
// (location,date) still serves. CORS is "*" on every response (§4.2 live cards
// run under an opaque sandbox origin).
// ===========================================================================

import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

// --------------------------------- Config ----------------------------------

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", ".."); // site/cache-api → repo

function envPath(name, fallback) {
  const v = process.env[name];
  return v && v.length > 0 ? path.resolve(v) : fallback;
}

const PORT = Number(process.env.WCB_CACHE_PORT || 8935);
const WEATHER_DB = envPath("WCB_WEATHER_DB", path.join(REPO_ROOT, "data", "weather-db"));
// Data root that holds index.json + all batch dirs (snapshots are seeded from here).
const DATA_ROOT = envPath("WCB_DATA_ROOT", path.join(REPO_ROOT, "data", "batches-dev"));
const TRIAL_CACHE = envPath(
  "WCB_TRIAL_CACHE",
  path.join(REPO_ROOT, "trial-20260715", "api-cache"),
);
// Min gap between two upstream fetches (Open-Meteo free tier is generous but we
// stay a polite citizen — self-hosted red line, §4.3).
const RATE_MIN_INTERVAL_MS = Number(process.env.WCB_UPSTREAM_MIN_INTERVAL_MS || 1100);
const FETCH_TIMEOUT_MS = Number(process.env.WCB_UPSTREAM_TIMEOUT_MS || 30000);
const ALLOW_LIVE = (process.env.WCB_ALLOW_LIVE ?? "1") !== "0";

const UPSTREAM = {
  "/api/om/forecast": "https://api.open-meteo.com/v1/forecast",
  "/api/om/archive": "https://archive-api.open-meteo.com/v1/archive",
};

const RECORDS_DIR = path.join(WEATHER_DB, "records");
const URLCACHE_DIR = path.join(WEATHER_DB, "url-cache");
// lat/lon match tolerance for the structured tier (Open-Meteo grid ~0.05°; a card
// may send the prompt's coord while the snapshot froze the same request coord).
const COORD_EPS = 1e-3;

// ---------------------------- Number materialize ---------------------------
//
// The frozen snapshot encodes every non-integer numeric as a canonical decimal
// STRING (hash discipline). Serving must re-materialize the JSON-number wire
// shape. A string is a materializable number iff it is a plain decimal literal —
// this deliberately EXCLUDES ISO times ("2026-07-15", "2026-07-15T00:00"),
// units ("°C"), and enums ("iso8601", "GMT"), which must stay strings.

const DECIMAL_RE = /^-?\d+(?:\.\d+)?$/;

function materializeNumbers(value) {
  if (typeof value === "string") {
    return DECIMAL_RE.test(value) && Number.isFinite(Number(value))
      ? Number(value)
      : value;
  }
  if (Array.isArray(value)) return value.map(materializeNumbers);
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = materializeNumbers(v);
    return out;
  }
  return value;
}

// ------------------------------- Store keys --------------------------------

function normCoord(s) {
  const n = Number(s);
  return Number.isFinite(n) ? n : NaN;
}

/** Directory holding the structured records for (endpoint, lat, lon). */
function recordDir(endpoint, lat, lon) {
  return path.join(RECORDS_DIR, endpoint, `${lat},${lon}`);
}

function recordPath(endpoint, lat, lon, date) {
  return path.join(recordDir(endpoint, lat, lon), `${date}.json`);
}

/** url-cache key — sha256 of the resolved upstream URL, [:32] (trial-identical). */
function urlKey(upstreamUrl) {
  return crypto.createHash("sha256").update(upstreamUrl).digest("hex").slice(0, 32);
}

function urlCachePath(upstreamUrl) {
  return path.join(URLCACHE_DIR, `${urlKey(upstreamUrl)}.json`);
}

function writeFirstWriterWins(file, bytes) {
  if (fs.existsSync(file)) return false; // first writer wins — never overwrite
  fs.mkdirSync(path.dirname(file), { recursive: true });
  // Exclusive create closes the last TOCTOU gap between concurrent writers.
  try {
    const fd = fs.openSync(file, "wx");
    fs.writeSync(fd, bytes);
    fs.closeSync(fd);
    return true;
  } catch (e) {
    if (e && e.code === "EEXIST") return false;
    throw e;
  }
}

// --------------------------------- Seeding ---------------------------------

function listBatchDirs() {
  const indexPath = path.join(DATA_ROOT, "index.json");
  const ids = new Set();
  if (fs.existsSync(indexPath)) {
    try {
      const idx = JSON.parse(fs.readFileSync(indexPath, "utf8"));
      for (const b of idx.batches ?? []) if (b?.id) ids.add(b.id);
    } catch {
      /* ignore malformed index; fall through to dir scan */
    }
  }
  // Also scan the data root for any batch dir carrying a weather-snapshot.json.
  if (fs.existsSync(DATA_ROOT)) {
    for (const name of fs.readdirSync(DATA_ROOT)) {
      const bdir = path.join(DATA_ROOT, name);
      try {
        if (
          fs.statSync(bdir).isDirectory() &&
          fs.existsSync(path.join(bdir, "weather-snapshot.json"))
        ) {
          ids.add(name);
        }
      } catch {
        /* skip */
      }
    }
  }
  return [...ids].map((id) => path.join(DATA_ROOT, id));
}

/** Merge every batch snapshot's (location,date) responses into the records tier. */
function seedFromSnapshots() {
  let wrote = 0;
  let seen = 0;
  for (const bdir of listBatchDirs()) {
    const snapPath = path.join(bdir, "weather-snapshot.json");
    if (!fs.existsSync(snapPath)) continue;
    let snap;
    try {
      snap = JSON.parse(fs.readFileSync(snapPath, "utf8"));
    } catch {
      continue;
    }
    for (const loc of snap.locations ?? []) {
      if (!loc || loc.endpoint == null || loc.lat == null || loc.date == null) continue;
      seen++;
      const materialized = materializeNumbers(loc.response ?? {});
      const bytes = JSON.stringify(materialized);
      const file = recordPath(loc.endpoint, loc.lat, loc.lon, loc.date);
      if (writeFirstWriterWins(file, bytes)) wrote++;
    }
  }
  return { wrote, seen };
}

/** Merge the trial proxy's api-cache/ verbatim into the url-cache tier. */
function seedFromTrialCache() {
  let wrote = 0;
  let seen = 0;
  if (!fs.existsSync(TRIAL_CACHE)) return { wrote, seen };
  fs.mkdirSync(URLCACHE_DIR, { recursive: true });
  for (const name of fs.readdirSync(TRIAL_CACHE)) {
    if (!name.endsWith(".json")) continue;
    seen++;
    const src = path.join(TRIAL_CACHE, name);
    const dst = path.join(URLCACHE_DIR, name); // trial key == our key scheme
    if (fs.existsSync(dst)) continue; // first writer wins
    try {
      const bytes = fs.readFileSync(src);
      // Validate it parses as JSON before adopting it into the store.
      JSON.parse(bytes.toString("utf8"));
      if (writeFirstWriterWins(dst, bytes)) wrote++;
    } catch {
      /* skip unparseable trial entry */
    }
  }
  return { wrote, seen };
}

function seedAll({ verbose = true } = {}) {
  fs.mkdirSync(RECORDS_DIR, { recursive: true });
  fs.mkdirSync(URLCACHE_DIR, { recursive: true });
  const snaps = seedFromSnapshots();
  const trial = seedFromTrialCache();
  writeProvenance();
  if (verbose) {
    console.log(
      `[cache-api] seed: records +${snaps.wrote}/${snaps.seen} (snapshots), ` +
        `url-cache +${trial.wrote}/${trial.seen} (trial). store=${WEATHER_DB}`,
    );
  }
  return { snaps, trial };
}

function writeProvenance() {
  const p = path.join(WEATHER_DB, "PROVENANCE.md");
  if (fs.existsSync(p)) return;
  const body = `# weather-db — append-only weather store (cache-api)

Two FIRST-WRITER-WINS tiers, generated + maintained by \`site/cache-api/server.mjs\`
(PROJECT-DESIGN §4.3). Do not hand-edit; re-seed with \`node server.mjs --seed\`.

- \`records/<endpoint>/<lat>,<lon>/<date>.json\` — authoritative fixture data,
  seeded from every batch's frozen \`weather-snapshot.json\`. Re-materialized from
  the frozen decimal-string form to the Open-Meteo JSON-number wire shape.
- \`url-cache/<sha256(upstreamURL)[:32]>.json\` — operational exact-URL cache.
  Key scheme is byte-identical to the trial proxy (\`trial-20260715/serve.py\`);
  the trial's \`api-cache/\` is merged in verbatim and every on-miss live fetch
  persists here.

Serve order: records → url-cache → rate-limited live Open-Meteo fetch (+persist).
`;
  fs.mkdirSync(WEATHER_DB, { recursive: true });
  fs.writeFileSync(p, body);
}

function countStore() {
  let records = 0;
  let urlCache = 0;
  const walk = (dir) => {
    if (!fs.existsSync(dir)) return 0;
    let n = 0;
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.isDirectory()) n += walk(path.join(dir, e.name));
      else if (e.name.endsWith(".json")) n++;
    }
    return n;
  };
  records = walk(RECORDS_DIR);
  if (fs.existsSync(URLCACHE_DIR)) {
    urlCache = fs
      .readdirSync(URLCACHE_DIR)
      .filter((n) => n.endsWith(".json")).length;
  }
  return { records, urlCache };
}

// ------------------------------ Lookup logic -------------------------------

/** Extract the (endpoint,lat,lon,date) lookup key from a request's query. */
function requestKey(endpointName, query) {
  const lat = query.get("latitude") ?? query.get("lat");
  const lon = query.get("longitude") ?? query.get("lon");
  // archive & the card's forecast path both carry start_date; fall back to a
  // bare `date`, else "current" (a param-less forecast = current weather).
  const date = query.get("start_date") ?? query.get("date") ?? "current";
  return { endpoint: endpointName, lat, lon, date };
}

/** Structured-tier lookup: exact path, then tolerant lat/lon scan for same date. */
function lookupRecord(key) {
  if (key.lat == null || key.lon == null) return null;
  const exact = recordPath(key.endpoint, key.lat, key.lon, key.date);
  if (fs.existsSync(exact)) return fs.readFileSync(exact);

  const endpointDir = path.join(RECORDS_DIR, key.endpoint);
  if (!fs.existsSync(endpointDir)) return null;
  const wantLat = normCoord(key.lat);
  const wantLon = normCoord(key.lon);
  if (Number.isNaN(wantLat) || Number.isNaN(wantLon)) return null;
  for (const coordDir of fs.readdirSync(endpointDir)) {
    const comma = coordDir.lastIndexOf(",");
    if (comma < 0) continue;
    const la = normCoord(coordDir.slice(0, comma));
    const lo = normCoord(coordDir.slice(comma + 1));
    if (Number.isNaN(la) || Number.isNaN(lo)) continue;
    if (Math.abs(la - wantLat) <= COORD_EPS && Math.abs(lo - wantLon) <= COORD_EPS) {
      const cand = path.join(endpointDir, coordDir, `${key.date}.json`);
      if (fs.existsSync(cand)) return fs.readFileSync(cand);
    }
  }
  return null;
}

// --------------------------- Rate-limited fetch ----------------------------

let _lastFetchAt = 0;
let _chain = Promise.resolve();

/** Serialize upstream fetches and enforce a minimum inter-request gap. */
function rateLimitedFetch(url) {
  const run = async () => {
    const wait = _lastFetchAt + RATE_MIN_INTERVAL_MS - Date.now();
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    _lastFetchAt = Date.now();
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const res = await fetch(url, { signal: ctrl.signal });
      const body = Buffer.from(await res.arrayBuffer());
      return { status: res.status, body };
    } finally {
      clearTimeout(timer);
    }
  };
  // Queue behind the previous fetch (concurrency 1); isolate failures.
  const result = _chain.then(run, run);
  _chain = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

// -------------------------------- HTTP server ------------------------------

function sendJson(res, status, body) {
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Cache-Control": "no-store",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

async function handleProxy(res, endpointName, rawQuery) {
  const query = new URLSearchParams(rawQuery);
  const key = requestKey(endpointName, query);

  // 1) authoritative frozen records
  const rec = lookupRecord(key);
  if (rec) return sendJson(res, 200, rec);

  // 2) operational exact-URL cache
  const upstreamUrl = UPSTREAM[`/api/om/${endpointName}`] + (rawQuery ? `?${rawQuery}` : "");
  const ucPath = urlCachePath(upstreamUrl);
  if (fs.existsSync(ucPath)) return sendJson(res, 200, fs.readFileSync(ucPath));

  // 3) live fetch (rate-limited) + persist to both tiers (first-writer-wins)
  if (!ALLOW_LIVE) {
    return sendJson(
      res,
      503,
      JSON.stringify({ error: "cache miss and live fetch disabled", reason: "no-store-entry" }),
    );
  }
  try {
    const { status, body } = await rateLimitedFetch(upstreamUrl);
    if (status === 200) {
      // persist raw wire bytes to url-cache
      writeFirstWriterWins(ucPath, body);
      // and, when we can key it, to the structured tier
      if (key.lat != null && key.lon != null && key.date != null) {
        writeFirstWriterWins(recordPath(key.endpoint, key.lat, key.lon, key.date), body);
      }
    }
    return sendJson(res, status, body);
  } catch (e) {
    return sendJson(res, 502, JSON.stringify({ error: String(e?.message ?? e) }));
  }
}

function createServer() {
  return http.createServer((req, res) => {
    const { pathname, search } = new URL(req.url, "http://localhost");
    const rawQuery = search.startsWith("?") ? search.slice(1) : search;

    if (req.method === "OPTIONS") {
      res.writeHead(204, {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
      });
      return res.end();
    }
    if (req.method !== "GET") return sendJson(res, 405, JSON.stringify({ error: "GET only" }));

    if (pathname === "/health") {
      return sendJson(
        res,
        200,
        JSON.stringify({ ok: true, store: WEATHER_DB, counts: countStore(), live: ALLOW_LIVE }),
      );
    }
    if (pathname === "/api/om/forecast") return handleProxy(res, "forecast", rawQuery);
    if (pathname === "/api/om/archive") return handleProxy(res, "archive", rawQuery);

    return sendJson(res, 404, JSON.stringify({ error: "not found", path: pathname }));
  });
}

// --------------------------------- Entry -----------------------------------

const argv = process.argv.slice(2);
if (argv.includes("--seed") || argv.includes("--reseed")) {
  seedAll();
  process.exit(0);
}

// Auto-seed on startup so a fresh checkout has the fixture data + trial cache.
seedAll();
const server = createServer();
server.listen(PORT, () => {
  const c = countStore();
  console.log(
    `[cache-api] listening on :${PORT} — /api/om/{forecast,archive}, /health · ` +
      `store ${c.records} record(s) + ${c.urlCache} url-cache · live=${ALLOW_LIVE}`,
  );
});
