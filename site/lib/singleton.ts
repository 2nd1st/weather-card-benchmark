// ===========================================================================
// lib/singleton.ts — cross-route in-process state (spec §3.5).
//
// Next bundles each route separately, so a plain module-level `const map = new
// Map()` written by /api/contribute/submit may literally NOT be the same Map the
// queue worker (a different route bundle) reads. Everything that must be shared
// across route bundles in one Node process goes through here, keyed on a single
// `globalThis.__wcb` bag that survives bundle boundaries and HMR.
//
// Deploy constraint (normative, spec §3.5): SINGLE Node process — no pm2
// cluster, no multi-instance. Every mechanism built on this assumes it.
// ===========================================================================

type Bag = Record<string, unknown>;

function bag(): Bag {
  const g = globalThis as unknown as { __wcb?: Bag };
  if (!g.__wcb) g.__wcb = {};
  return g.__wcb;
}

/**
 * Return the process-global value for `key`, lazily initialised exactly once.
 * All handles/caches (DB, rate-limit buckets, contribute queue + key Map, BT
 * stats cache, registry cache) MUST route through this.
 */
export function singleton<T>(key: string, init: () => T): T {
  const b = bag();
  if (!(key in b)) b[key] = init();
  return b[key] as T;
}

/** Overwrite the process-global value for `key` (used by boot reconciliation). */
export function setSingleton<T>(key: string, value: T): void {
  bag()[key] = value;
}

/** Read without initialising; undefined when never set. */
export function peekSingleton<T>(key: string): T | undefined {
  return bag()[key] as T | undefined;
}
