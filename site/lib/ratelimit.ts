// ===========================================================================
// lib/ratelimit.ts — vote throttle + IP identity (spec §3.2).
//
// Token bucket, 30 votes / 10 min, keyed on BOTH ip_hash and voter_id (either
// bucket empty → 429). Buckets live behind lib/singleton so they survive the
// per-route-bundle split. Single-process deploy assumed (§3.5).
//
// IP resolution is specified precisely because Cloudflare fronts the VM: keying
// on the raw socket IP would key on Cloudflare edge IPs and 429 the whole site.
//   CF-Connecting-IP     when WCB_TRUST_CF=1    (prod default — CF is trusted)
//   first X-Forwarded-For when WCB_TRUST_PROXY=1
//   else the socket address
//
// ip_hash = sha256( hkdf(WCB_ARENA_SECRET, yyyy-mm-dd) ++ ip ) — a deterministic
// DAILY salt: survives restarts, rotates every UTC day so hashes aren't a stable
// cross-day identifier. WCB_ARENA_SECRET MUST be set in prod; a random-at-boot
// secret is dev-only (rotates every restart — acceptable in dev, not prod).
// ===========================================================================

import crypto from "node:crypto";

import { singleton } from "./singleton";

// ------------------------------- token bucket ------------------------------

export const RATE_CAP = 30;
export const RATE_WINDOW_MS = 10 * 60 * 1000;
const REFILL_PER_MS = RATE_CAP / RATE_WINDOW_MS;

interface Bucket {
  tokens: number;
  last: number;
}

function buckets(): Map<string, Bucket> {
  return singleton("ratelimit.buckets", () => new Map<string, Bucket>());
}

function refill(store: Map<string, Bucket>, key: string, now: number): Bucket {
  let b = store.get(key);
  if (!b) {
    b = { tokens: RATE_CAP, last: now };
    store.set(key, b);
    return b;
  }
  const elapsed = now - b.last;
  if (elapsed > 0) {
    b.tokens = Math.min(RATE_CAP, b.tokens + elapsed * REFILL_PER_MS);
    b.last = now;
  }
  return b;
}

export interface RateResult {
  ok: boolean;
  /** i18n-able error code when blocked. */
  code?: "rate-limited";
  /** seconds until at least one token is available (best-effort hint). */
  retryAfter?: number;
}

/**
 * Atomically check + consume one token from EVERY key (per ip_hash and per
 * voter_id). Consumes from none unless all pass — so a blocked request does not
 * drain the buckets it did clear.
 */
export function consumeRate(keys: string[], now = Date.now()): RateResult {
  const store = buckets();
  const bs = keys.map((k) => refill(store, k, now));
  const short = bs.find((b) => b.tokens < 1);
  if (short) {
    const deficit = 1 - short.tokens;
    return { ok: false, code: "rate-limited", retryAfter: Math.ceil(deficit / REFILL_PER_MS / 1000) };
  }
  for (const b of bs) b.tokens -= 1;
  return { ok: true };
}

// --------------------------------- ip identity -----------------------------

/** UTC calendar day, yyyy-mm-dd. */
export function utcDay(d = new Date()): string {
  return d.toISOString().slice(0, 10);
}

function arenaSecret(): Buffer {
  const env = process.env.WCB_ARENA_SECRET;
  if (env && env.length > 0) return Buffer.from(env, "utf8");
  // Dev-only fallback: stable for the life of the process, rotates on restart.
  return singleton("arena.secret.dev", () => crypto.randomBytes(32));
}

function dailySalt(day: string): Buffer {
  const out = crypto.hkdfSync("sha256", arenaSecret(), Buffer.alloc(0), Buffer.from(day, "utf8"), 32);
  return Buffer.from(out);
}

/** Deterministic per-day hash of a client IP. */
export function ipHash(ip: string, day = utcDay()): string {
  return crypto.createHash("sha256").update(dailySalt(day)).update(ip, "utf8").digest("hex");
}

/** Minimal header accessor — a plain object or a Headers-like `.get`. */
export interface HeaderLike {
  get(name: string): string | null;
}

/**
 * Resolve the client IP per spec §3.2. `socketAddr` is the last-resort raw
 * remote address (rarely available inside Next route handlers → "0.0.0.0").
 */
export function resolveIp(headers: HeaderLike, socketAddr?: string): string {
  if (process.env.WCB_TRUST_CF === "1") {
    const cf = headers.get("cf-connecting-ip");
    if (cf) return cf.trim();
  }
  if (process.env.WCB_TRUST_PROXY === "1") {
    const xff = headers.get("x-forwarded-for");
    if (xff) {
      const first = xff.split(",")[0]?.trim();
      if (first) return first;
    }
  }
  return socketAddr && socketAddr.length > 0 ? socketAddr : "0.0.0.0";
}

/** Convenience: resolve → hash in one call. */
export function clientIpHash(headers: HeaderLike, socketAddr?: string): string {
  return ipHash(resolveIp(headers, socketAddr));
}
