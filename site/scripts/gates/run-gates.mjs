#!/usr/bin/env node
// ===========================================================================
// site/scripts/gates/run-gates.mjs — the S1 DEPLOY-GATE runner. Runs every
// build gate in order and fails the build (non-zero exit) if any gate fails,
// so the offending build never ships and the previous version stays online
// (PROJECT-DESIGN §1.1 "gate 失败则保持旧版在线", §4.4).
//
// Wired into the build pipeline via package.json `prebuild` (after copy-assets):
//   prebuild = copy-assets  →  run-gates  →  next build
//
// Gates:
//   1. schema-validate.mjs    — whole-batch JSON-Schema validation (honors
//                               WCB_VALIDATE; strict fails, warn logs+passes).
//
// (The forbidden-language copy lint was retired 2026-07-18 with the positioning
// change: community UI-preference voting uses natural vocabulary.)
//
// Each gate is a standalone Node script run in its own process, so a crash in
// one is contained and reported with its own exit code.
// ===========================================================================

import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const GATES = [
  { name: "schema-validate", file: "schema-validate.mjs" },
];

let failed = 0;
for (const gate of GATES) {
  const res = spawnSync(process.execPath, [path.join(__dirname, gate.file)], {
    stdio: "inherit",
    env: process.env,
  });
  if (res.status !== 0) {
    failed++;
    console.error(`[gates] gate "${gate.name}" exited ${res.status ?? res.signal}.`);
  }
}

if (failed > 0) {
  console.error(`\n[gates] BLOCKED — ${failed} gate(s) failed. Build aborted; previous version stays online.`);
  process.exit(1);
}
console.log("[gates] all deploy gates passed.");
