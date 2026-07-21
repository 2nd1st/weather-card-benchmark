// ===========================================================================
// lib/db.ts — the SQLite handle + schema (spec §3.1).
//
// better-sqlite3, WAL. One handle per process, behind lib/singleton (§3.5).
// Path: WCB_DB_PATH || <cwd>/.data/app.db. In prod WCB_DB_PATH is MANDATORY and
// MUST live outside the deploy tree (§3.6) — votes are the only unrecoverable
// data this system produces and a redeploy must never be able to delete them.
//
// Migrations are inline and versioned via `PRAGMA user_version`. Never edit a
// shipped migration in place; append a new one.
// ===========================================================================

import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";

import { singleton } from "./singleton";

export type DB = Database.Database;

function resolveDbPath(): string {
  const env = process.env.WCB_DB_PATH;
  if (env && env.length > 0) return path.resolve(env);
  return path.join(process.cwd(), ".data", "app.db");
}

const MIGRATIONS: ReadonlyArray<(db: DB) => void> = [
  // ---- v1: pairs / votes / shares / contribute_jobs (spec §3.1) ----
  (db) => {
    db.exec(`
      CREATE TABLE pairs (
        id       TEXT PRIMARY KEY,                                  -- hex(randomBytes(32)); opaque, no payload
        ts       TEXT NOT NULL,                                     -- issue time (server-side dwell basis)
        mode     TEXT NOT NULL CHECK (mode IN ('pair','quad')),     -- 'quad' reserved; v1 issues 'pair' only
        angle    TEXT NOT NULL,
        variant  TEXT NOT NULL,
        city     TEXT NOT NULL,
        date     TEXT NOT NULL,
        refs_json TEXT NOT NULL,                                    -- [{configId,batchId,slot,sha256,pos}]
        source   TEXT NOT NULL CHECK (source IN ('gate','arena','share')),
        share_id TEXT,
        consumed INTEGER NOT NULL DEFAULT 0
      );
      CREATE INDEX idx_pairs_open ON pairs (consumed, ts);

      CREATE TABLE votes (
        id       INTEGER PRIMARY KEY,
        ts       TEXT NOT NULL,
        pair_id  TEXT NOT NULL UNIQUE REFERENCES pairs(id),
        voter_id TEXT NOT NULL,
        ip_hash  TEXT NOT NULL,
        mode     TEXT NOT NULL,
        angle    TEXT NOT NULL,
        variant  TEXT NOT NULL,
        city     TEXT NOT NULL,
        date     TEXT NOT NULL,
        config_a TEXT NOT NULL, batch_a TEXT NOT NULL, slot_a INTEGER NOT NULL, sha_a TEXT NOT NULL,
        config_b TEXT,          batch_b TEXT,          slot_b INTEGER,          sha_b TEXT,
        choice   TEXT NOT NULL CHECK (choice IN ('a','b','tie','both_bad')),
        source   TEXT NOT NULL CHECK (source IN ('gate','arena','share')),
        patched_served INTEGER NOT NULL DEFAULT 0,                  -- audit: MUST always be 0
        latency_ms INTEGER, dwell_ms INTEGER, locale TEXT
      );
      CREATE INDEX idx_votes_angle_variant ON votes (angle, variant, source);
      CREATE INDEX idx_votes_config_a ON votes (config_a);
      CREATE INDEX idx_votes_config_b ON votes (config_b);

      CREATE TABLE shares (
        id       TEXT PRIMARY KEY,
        ts       TEXT NOT NULL,
        refs_json TEXT NOT NULL
      );

      CREATE TABLE contribute_jobs (
        id         TEXT PRIMARY KEY,
        ts         TEXT NOT NULL,
        status     TEXT NOT NULL,                                   -- queued|running|done|failed|rejected
        provider   TEXT NOT NULL,
        cells_json TEXT NOT NULL,
        est_usd_lo REAL, est_usd_hi REAL,
        fail_code  TEXT,                                            -- key-lost|timeout|runner-exit|...
        contact    TEXT, note TEXT
      );
      CREATE INDEX idx_jobs_status ON contribute_jobs (status);
    `);
  },
];

function migrate(db: DB): void {
  let version = Number(db.pragma("user_version", { simple: true })) || 0;
  for (; version < MIGRATIONS.length; version++) {
    const step = MIGRATIONS[version];
    const tx = db.transaction(() => {
      step(db);
      db.pragma(`user_version = ${version + 1}`);
    });
    tx();
  }
}

function open(): DB {
  const p = resolveDbPath();
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const db = new Database(p);
  db.pragma("journal_mode = WAL");
  db.pragma("busy_timeout = 5000");
  db.pragma("foreign_keys = ON");
  migrate(db);
  return db;
}

/** The one shared better-sqlite3 handle for this process. */
export function getDb(): DB {
  return singleton("db.handle", open);
}

/** Absolute path the handle is (or would be) opened at — for DEPLOY diagnostics. */
export function dbPath(): string {
  return resolveDbPath();
}
