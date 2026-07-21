// ===========================================================================
// lib/contribute.ts — the community-run pipeline (spec §3.3, flag-gated).
//
// Zero-trust key handling: an API key lives ONLY in an in-process Map behind
// lib/singleton (never persisted, never logged), is used solely for the selected
// runs, and is dropped after. Keys are memory-only BY DESIGN, so a restart loses
// them — boot reconciliation flips any interrupted job to failed:key-lost.
//
// Caps: ≤ 4 pending jobs (queue-full), ≤ 12 cells per job. Worker runs one at a
// time, writes a generated yaml WITHOUT the key (credential_ref only), spawns the
// runner with the key in env only, then pushes the landed batch to a REVIEW
// branch (lib/github). Job timeout 60 min.
// ===========================================================================

import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { getDb } from "./db";
import { singleton } from "./singleton";
import { commitBatch } from "./github";

export interface ContributeCell {
  family: string;
  modelId: string;
  effort: string | null;
  arm: string;
  /** slots per variant (default 4). */
  n?: number;
}

export interface Estimate {
  calls: number;
  usd: [number, number];
  notes?: string;
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "rejected";

export interface JobRow {
  id: string;
  ts: string;
  status: JobStatus;
  provider: string;
  cells_json: string;
  est_usd_lo: number | null;
  est_usd_hi: number | null;
  fail_code: string | null;
  contact: string | null;
  note: string | null;
}

const MAX_PENDING = 4;
const MAX_CELLS = 12;
const JOB_TIMEOUT_MS = 60 * 60 * 1000;

export function contributeEnabled(): boolean {
  return process.env.WCB_CONTRIBUTE_ENABLED === "1";
}

// -------------------------- lazy coverage (A1) -----------------------------

/* eslint-disable @typescript-eslint/no-explicit-any */
async function loadCoverage(): Promise<any | null> {
  // string-typed specifier: tsc treats as `any` (lib/coverage is A1's, may not
  // exist at this file's typecheck time); webpack resolves the ./ context leaf.
  const leaf: string = "coverage";
  try {
    return await import(`./${leaf}`);
  } catch {
    return null;
  }
}
/* eslint-enable @typescript-eslint/no-explicit-any */

/** Cost/volume estimate for a set of cells (delegates to lib/coverage). */
export async function estimate(cells: ContributeCell[]): Promise<Estimate> {
  const cov = await loadCoverage();
  if (cov && typeof cov.estimate === "function") {
    try {
      const r = cov.estimate(cells);
      return { calls: r.calls, usd: r.usd, notes: r.notes };
    } catch {
      /* fall through to local */
    }
  }
  // Local fallback: calls = n×2 variants per cell; usd unknown → [0,0] with note.
  const calls = cells.reduce((s, c) => s + (c.n ?? 4) * 2, 0);
  return { calls, usd: [0, 0], notes: "cost reference unavailable — estimate is call-count only" };
}

// ------------------------------ in-memory key Map --------------------------

interface KeyStore {
  map: Map<string, { provider: string; apiKey: string }>;
}
function keys(): KeyStore {
  return singleton("contribute.keys", () => ({ map: new Map() }));
}

// ------------------------------ boot reconcile -----------------------------

function reconcileOnBoot(): void {
  const db = getDb();
  db.prepare(
    `UPDATE contribute_jobs SET status='failed', fail_code='key-lost'
      WHERE status IN ('queued','running')`,
  ).run();
}

/** Run boot reconciliation exactly once per process. */
function ensureBooted(): void {
  singleton("contribute.booted", () => {
    reconcileOnBoot();
    return true;
  });
}

// --------------------------------- submit ----------------------------------

export type SubmitResult =
  | { ok: true; jobId: string }
  | { ok: false; status: number; code: string };

function pendingCount(): number {
  const db = getDb();
  const r = db
    .prepare(`SELECT COUNT(*) AS n FROM contribute_jobs WHERE status IN ('queued','running')`)
    .get() as { n: number };
  return r.n ?? 0;
}

async function validateCells(cells: ContributeCell[]): Promise<boolean> {
  const cov = await loadCoverage();
  if (!cov || typeof cov.coverage !== "function") return true; // cannot validate → allow (best-effort)
  try {
    const board = cov.coverage() as Array<{ cell: ContributeCell; status: string }>;
    const allowed = new Set(
      board
        .filter((c) => c.status === "missing" || c.status === "attempted")
        .map((c) => keyOf(c.cell)),
    );
    return cells.every((c) => allowed.has(keyOf(c)));
  } catch {
    return true;
  }
}

function keyOf(c: ContributeCell): string {
  return `${c.family}|${c.modelId}|${c.effort ?? ""}|${c.arm}`;
}

export async function submitJob(input: {
  cells: ContributeCell[];
  provider: string;
  apiKey: string;
  contact?: string;
  note?: string;
}): Promise<SubmitResult> {
  ensureBooted();

  if (!contributeEnabled()) return { ok: false, status: 503, code: "beta" };
  if (!input.cells || input.cells.length === 0) return { ok: false, status: 400, code: "no-cells" };
  if (input.cells.length > MAX_CELLS) return { ok: false, status: 400, code: "cell-cap" };
  if (!input.apiKey) return { ok: false, status: 400, code: "no-key" };
  if (pendingCount() >= MAX_PENDING) return { ok: false, status: 429, code: "queue-full" };

  const okCells = await validateCells(input.cells);
  if (!okCells) return { ok: false, status: 400, code: "bad-cells" };

  const est = await estimate(input.cells);
  const jobId = cryptoRandomId();
  getDb()
    .prepare(
      `INSERT INTO contribute_jobs (id, ts, status, provider, cells_json, est_usd_lo, est_usd_hi, contact, note)
       VALUES (?,?,'queued',?,?,?,?,?,?)`,
    )
    .run(
      jobId,
      new Date().toISOString(),
      input.provider,
      JSON.stringify(input.cells),
      est.usd[0],
      est.usd[1],
      input.contact ?? null,
      input.note ?? null,
    );

  keys().map.set(jobId, { provider: input.provider, apiKey: input.apiKey });
  scheduleQueue();
  return { ok: true, jobId };
}

export function getJob(id: string): JobRow | null {
  ensureBooted();
  const row = getDb().prepare(`SELECT * FROM contribute_jobs WHERE id=?`).get(id) as JobRow | undefined;
  return row ?? null;
}

// --------------------------------- worker ----------------------------------

interface QueueState {
  running: boolean;
}
function queueState(): QueueState {
  return singleton("contribute.queue", () => ({ running: false }));
}

function scheduleQueue(): void {
  const q = queueState();
  if (q.running) return;
  q.running = true;
  // fire-and-forget; the worker resets running when the queue drains
  void drainQueue().finally(() => {
    q.running = false;
  });
}

async function drainQueue(): Promise<void> {
  const db = getDb();
  for (;;) {
    const job = db
      .prepare(`SELECT * FROM contribute_jobs WHERE status='queued' ORDER BY ts ASC LIMIT 1`)
      .get() as JobRow | undefined;
    if (!job) return;
    await runOne(job);
  }
}

function setStatus(id: string, status: JobStatus, failCode?: string): void {
  getDb()
    .prepare(`UPDATE contribute_jobs SET status=?, fail_code=? WHERE id=?`)
    .run(status, failCode ?? null, id);
}

function runnerDir(): string | null {
  const d = process.env.WCB_RUNNER_DIR;
  return d && d.length > 0 ? d : null;
}

async function runOne(job: JobRow): Promise<void> {
  const held = keys().map.get(job.id);
  if (!held) {
    // key lost (restart between submit and run) — memory-only by design
    setStatus(job.id, "failed", "key-lost");
    return;
  }
  const runner = runnerDir();
  if (!runner) {
    keys().map.delete(job.id);
    setStatus(job.id, "failed", "runner-exit");
    return;
  }

  setStatus(job.id, "running");
  const cells: ContributeCell[] = safeParse(job.cells_json) ?? [];
  const date = new Date().toISOString().slice(0, 10);
  const batchId = `${date}--community-${job.id}`;

  // Write the generated yaml WITHOUT the key (credential_ref only).
  const cfgDir = path.join(runner, "configs", "community");
  const yamlPath = path.join(cfgDir, `${job.id}.yaml`);
  try {
    fs.mkdirSync(cfgDir, { recursive: true });
    fs.writeFileSync(yamlPath, buildYaml(job, cells, batchId), "utf8");
  } catch (e) {
    keys().map.delete(job.id);
    setStatus(job.id, "failed", "runner-exit");
    return;
  }

  const ok = await spawnRunner(runner, yamlPath, held.apiKey);
  // Drop the key immediately after the run, whatever the outcome.
  keys().map.delete(job.id);

  if (ok !== 0) {
    setStatus(job.id, "failed", ok === -1 ? "timeout" : "runner-exit");
    return;
  }

  // Push the landed batch to a REVIEW branch (best-effort; never main).
  try {
    const D = await import("./data");
    const batchDir = D.batchDir(batchId);
    commitBatch(batchDir, job.id, `community run ${batchId}`);
  } catch {
    /* commit is best-effort */
  }
  setStatus(job.id, "done");
}

/** Resolve 0 = success, non-zero = runner-exit, -1 = timeout. */
function spawnRunner(runner: string, yamlPath: string, apiKey: string): Promise<number> {
  return new Promise((resolve) => {
    const child = spawn(
      "python3",
      [path.join(runner, "run_batch.py"), "--config", yamlPath, "--skip-completed"],
      {
        cwd: runner,
        env: { ...process.env, WCB_COMMUNITY_KEY: apiKey },
        stdio: "ignore",
      },
    );
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try {
        child.kill("SIGKILL");
      } catch {
        /* ignore */
      }
      resolve(-1);
    }, JOB_TIMEOUT_MS);

    child.on("error", () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(1);
    });
    child.on("exit", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(code ?? 1);
    });
  });
}

function buildYaml(job: JobRow, cells: ContributeCell[], batchId: string): string {
  // Minimal, key-free config. transport is prefixed `community-` → grade
  // community (quarantined until a maintainer reviews). credential_ref points at
  // the env var the runner reads; the key itself is never written here.
  const lines: string[] = [];
  lines.push(`# generated community run — DO NOT COMMIT KEYS`);
  lines.push(`batch_id: ${batchId}`);
  lines.push(`event: community-${job.id}`);
  lines.push(`transport: community-${job.provider}`);
  lines.push(`credential_ref: WCB_COMMUNITY_KEY`);
  lines.push(`provider: ${job.provider}`);
  lines.push(`cells:`);
  for (const c of cells) {
    lines.push(`  - family: ${c.family}`);
    lines.push(`    model_id: ${c.modelId}`);
    lines.push(`    effort: ${c.effort ?? "null"}`);
    lines.push(`    arm: ${c.arm}`);
    lines.push(`    n: ${c.n ?? 4}`);
  }
  return lines.join("\n") + "\n";
}

function safeParse<T>(s: string): T | null {
  try {
    return JSON.parse(s) as T;
  } catch {
    return null;
  }
}

function cryptoRandomId(): string {
  return crypto.randomBytes(12).toString("hex");
}
