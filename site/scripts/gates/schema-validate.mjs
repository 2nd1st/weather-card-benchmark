#!/usr/bin/env node
// ===========================================================================
// site/scripts/gates/schema-validate.mjs — the batch SCHEMA DEPLOY GATE (S1;
// DATA-LAYOUT §4: "runner and site both validate every artifact against the
// locked schema bytes"). Validates the ENTIRE loaded batch against the eleven
// frozen draft-2020-12 schemas in data/SCHEMA/ BEFORE `next build` touches it,
// so a malformed batch is caught up front rather than mid-page-generation.
//
// This is the standalone build-pipeline twin of lib/schema.ts (which validates
// lazily as pages read artifacts). It honors the SAME WCB_VALIDATE contract, so
// the gate and the loader never disagree:
//
//   WCB_VALIDATE=strict|1|true  → any shape violation FAILS the build (non-zero).
//                                 Point at a conforming batch in CI/prod.
//   WCB_VALIDATE=warn (DEFAULT)  → validate + log every violation loudly, exit 0.
//                                 Lets the DEV smoke batch build: it carries the
//                                 single pre-declared N=2 relaxation (frozen
//                                 schema enumerates N∈{3,4,5}) documented in the
//                                 batch CONFORMANCE-REPORT / DECISIONS-M0.5 §1.
//   WCB_VALIDATE=off|0|false     → skip (fast local iteration only).
//
// Data resolution mirrors lib/paths.ts: WCB_DATA_DIR = the primary BATCH dir
// (default = M1 dev batch); the data ROOT (index.json + all batch dirs) is its
// parent. Schemas: WCB_SCHEMA_DIR (default <repo>/data/SCHEMA).
// ===========================================================================

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SITE_DIR = path.resolve(__dirname, "..", "..");
const REPO_ROOT = path.resolve(SITE_DIR, "..");

const DEV_DEFAULT_BATCH_DIR = path.join(
  REPO_ROOT,
  "data",
  "batches-dev",
  "2026-07-16--m1-dev-e2e-smoke",
);

function primaryBatchDir() {
  const env = process.env.WCB_DATA_DIR;
  return path.resolve(env && env.length > 0 ? env : DEV_DEFAULT_BATCH_DIR);
}
function schemaDir() {
  const env = process.env.WCB_SCHEMA_DIR;
  if (env && env.length > 0) return path.resolve(env);
  return path.join(REPO_ROOT, "data", "SCHEMA");
}

function validateMode() {
  const v = (process.env.WCB_VALIDATE ?? "warn").toLowerCase();
  if (v === "strict" || v === "1" || v === "true") return "strict";
  if (v === "off" || v === "0" || v === "false") return "off";
  return "warn";
}

const SCHEMA_DIR = schemaDir();

let _ajv = null;
function ajv() {
  if (_ajv) return _ajv;
  const instance = new Ajv2020({ strict: false, allErrors: true, allowUnionTypes: true });
  addFormats(instance);
  _ajv = instance;
  return instance;
}
const _validators = new Map();
function validator(schemaFile) {
  const cached = _validators.get(schemaFile);
  if (cached) return cached;
  const schema = JSON.parse(fs.readFileSync(path.join(SCHEMA_DIR, schemaFile), "utf8"));
  const fn = ajv().compile(schema);
  _validators.set(schemaFile, fn);
  return fn;
}

const violations = [];
let checked = 0;

/** Validate one artifact; record violations. Missing file is reported unless optional. */
function check(sourcePath, schemaFile, { optional = false } = {}) {
  if (!fs.existsSync(sourcePath)) {
    if (!optional) {
      violations.push({ file: path.relative(REPO_ROOT, sourcePath), errs: ["(missing file)"] });
    }
    return;
  }
  let data;
  try {
    data = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
  } catch (e) {
    violations.push({ file: path.relative(REPO_ROOT, sourcePath), errs: [`JSON parse: ${e.message}`] });
    return;
  }
  checked++;
  const fn = validator(schemaFile);
  if (!fn(data)) {
    const errs = (fn.errors ?? []).map(
      (e) => `${e.instancePath || "(root)"} ${e.message ?? ""}`.trim(),
    );
    violations.push({ file: path.relative(REPO_ROOT, sourcePath), schema: schemaFile, errs });
  }
}

function listDirs(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name);
}
function listSlotIndices(slotsDir) {
  if (!fs.existsSync(slotsDir)) return [];
  return fs
    .readdirSync(slotsDir, { withFileTypes: true })
    .filter((d) => d.isDirectory() && /^\d+$/.test(d.name))
    .map((d) => d.name)
    .sort((a, b) => Number(a) - Number(b));
}

function main() {
  const mode = validateMode();
  if (mode === "off") {
    console.log("[gate:schema] SKIP (WCB_VALIDATE=off).");
    return;
  }

  const batchDir = primaryBatchDir();
  const dataRoot = path.dirname(batchDir);
  const batchId = path.basename(batchDir);

  // Site-authored SEED files (v3 §2.4/§9). Optional — seed-or-degrade: a missing
  // seed must never fail the build; but when present it must conform.
  check(path.join(REPO_ROOT, "data", "cost-reference.json"), "cost-reference.schema.json", { optional: true });
  check(path.join(REPO_ROOT, "data", "harness-plan.json"), "harness-plan.schema.json", { optional: true });
  check(path.join(REPO_ROOT, "data", "findings.json"), "findings.schema.json", { optional: true });

  // index.json (data root)
  check(path.join(dataRoot, "index.json"), "index.schema.json");

  // Batch-level artifacts.
  check(path.join(batchDir, "manifest.json"), "manifest.schema.json");
  check(path.join(batchDir, "weather-snapshot.json"), "weather-snapshot.schema.json");
  check(path.join(batchDir, "stats.json"), "stats.schema.json");
  check(path.join(batchDir, "probes.json"), "probes.schema.json", { optional: true });

  // Per-config: config.json, send-log.json, and every slot meta under both variants.
  const configsDir = path.join(batchDir, "configs");
  const VARIANT_DIRS = ["min", "q"];
  for (const configId of listDirs(configsDir)) {
    const cdir = path.join(configsDir, configId);
    check(path.join(cdir, "config.json"), "config.schema.json");
    check(path.join(cdir, "send-log.json"), "send-log.schema.json");
    for (const vdir of VARIANT_DIRS) {
      const slotsDir = path.join(cdir, vdir, "slots");
      for (const k of listSlotIndices(slotsDir)) {
        check(path.join(slotsDir, k, "meta.json"), "slot-meta.schema.json");
        // Patch layer (v3 §2.3): optional slot-level patch.json.
        check(path.join(slotsDir, k, "patch.json"), "patch.schema.json", { optional: true });
      }
    }
  }

  // Similarity: per-variant summary + every materialized pair file.
  const simDir = path.join(batchDir, "similarity");
  if (fs.existsSync(simDir)) {
    for (const name of fs.readdirSync(simDir)) {
      const full = path.join(simDir, name);
      if (name.startsWith("summary--") && name.endsWith(".json")) {
        check(full, "similarity-summary.schema.json");
      }
    }
    const pairsDir = path.join(simDir, "pairs");
    if (fs.existsSync(pairsDir)) {
      for (const name of fs.readdirSync(pairsDir)) {
        if (name.endsWith(".json")) check(path.join(pairsDir, name), "similarity-pairs.schema.json");
      }
    }
  }

  // ------------------------------- Verdict -------------------------------
  if (violations.length === 0) {
    console.log(
      `[gate:schema] OK — ${checked} artifact(s) in batch "${batchId}" conform to data/SCHEMA/ (mode=${mode}).`,
    );
    return;
  }

  const header =
    `[gate:schema] ${mode === "strict" ? "FAIL" : "WARN"} — ${violations.length} artifact(s) ` +
    `violate the frozen schema in batch "${batchId}" (mode=${mode}):`;
  const log = mode === "strict" ? console.error : console.warn;
  log(`\n${header}`);
  for (const v of violations) {
    log(`  ${v.file}${v.schema ? ` [${v.schema}]` : ""}`);
    for (const e of v.errs) log(`      ${e}`);
  }
  if (mode === "strict") {
    console.error(
      `\n[gate:schema] set WCB_VALIDATE=strict only against a CONFORMING batch; ` +
        `the dev smoke batch carries the pre-declared N=2 relaxation (see the batch ` +
        `CONFORMANCE-REPORT) and must build under the default warn mode.\n`,
    );
    process.exit(1);
  }
  console.warn(
    `[gate:schema] warn mode — build continues. Every violation above must be an ` +
      `expected/documented deviation (dev batch: N=2 only). Real drift → fix the batch.`,
  );
}

main();
