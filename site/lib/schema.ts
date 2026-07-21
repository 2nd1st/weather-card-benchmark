// Build-time JSON-Schema validation harness.
//
// Every artifact the loader reads is validated against the frozen schema in
// data/SCHEMA/ (the eleven draft-2020-12 schemas — DATA-LAYOUT §1). This is the
// site half of the "runner and site both validate every artifact against the
// locked schema bytes" rule (DATA-LAYOUT §4).
//
// WCB_VALIDATE modes:
//   "strict"  — a shape violation THROWS at build time (CI/prod: point at a
//               conforming batch and set this so a broken batch can never ship).
//   "warn"    — DEFAULT. Validate and log every violation loudly, but continue.
//               This is what lets the DEV smoke batch build: it carries the one
//               documented pre-declared relaxation (N=2, whereas the frozen
//               manifest schema enumerates N∈{3,4,5}) — DECISIONS-M0.5 §1 /
//               M1 §3 / CONFORMANCE-REPORT. Real violations still surface.
//   "off"     — skip validation entirely (fast local iteration only).

import fs from "node:fs";
import path from "node:path";
import Ajv2020, { type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

export type SchemaFile =
  | "manifest.schema.json"
  | "config.schema.json"
  | "slot-meta.schema.json"
  | "similarity-summary.schema.json"
  | "index.schema.json"
  | "weather-snapshot.schema.json"
  | "probes.schema.json"
  | "similarity-pairs.schema.json"
  | "stats.schema.json"
  | "fidelity-trace.schema.json"
  | "send-log.schema.json";

/** Resolve the frozen-schema directory (data/SCHEMA). Override with WCB_SCHEMA_DIR. */
export function schemaDir(): string {
  const env = process.env.WCB_SCHEMA_DIR;
  if (env && env.length > 0) return path.resolve(env);
  // SCHEMA is a sibling of the data root (…/data/SCHEMA next to …/data/batches-dev).
  // The standalone server chdir's to its own dir, so a cwd-relative default breaks
  // at runtime; derive from the mandatory WCB_DATA_ROOT so schemas resolve without
  // a second env (a missing SCHEMA dir throws in the validator → empty registry).
  const root = process.env.WCB_DATA_ROOT;
  if (root && root.length > 0) return path.join(path.dirname(path.resolve(root)), "SCHEMA");
  // Build runs from site/; schemas live at <repo>/data/SCHEMA.
  return path.resolve(process.cwd(), "..", "data", "SCHEMA");
}

type ValidateMode = "strict" | "warn" | "off";
function validateMode(): ValidateMode {
  const v = (process.env.WCB_VALIDATE ?? "warn").toLowerCase();
  if (v === "strict" || v === "1" || v === "true") return "strict";
  if (v === "off" || v === "0" || v === "false") return "off";
  return "warn";
}

const _warned = new Set<string>();

let _ajv: Ajv2020 | null = null;
function ajv(): Ajv2020 {
  if (_ajv) return _ajv;
  // strict:false — schemas carry $comment/schema_version metadata and rich
  // descriptions; we validate shape, not meta-schema pedantry.
  const instance = new Ajv2020({ strict: false, allErrors: true, allowUnionTypes: true });
  addFormats(instance);
  _ajv = instance;
  return instance;
}

const _validators = new Map<SchemaFile, ValidateFunction>();
function validator(file: SchemaFile): ValidateFunction {
  const cached = _validators.get(file);
  if (cached) return cached;
  const raw = fs.readFileSync(path.join(schemaDir(), file), "utf8");
  const schema = JSON.parse(raw);
  const fn = ajv().compile(schema);
  _validators.set(file, fn);
  return fn;
}

/**
 * Validate `data` against a frozen schema and return it typed as `T`.
 * Throws with the artifact path + ajv error list on failure.
 */
export function validate<T>(file: SchemaFile, data: unknown, sourcePath: string): T {
  const mode = validateMode();
  if (mode === "off") return data as T;
  const fn = validator(file);
  if (!fn(data)) {
    const errs = (fn.errors ?? [])
      .map((e) => `  ${e.instancePath || "(root)"} ${e.message ?? ""}`.trimEnd())
      .join("\n");
    const msg = `Schema validation failed [${file}] for ${sourcePath}:\n${errs}`;
    if (mode === "strict") throw new Error(msg);
    // warn mode: log once per (file+source) so build logs stay readable.
    const key = `${file}::${sourcePath}`;
    if (!_warned.has(key)) {
      _warned.add(key);
      console.warn(`[wcb:schema] ${msg}`);
    }
  }
  return data as T;
}

/** Read a JSON file, validate it against `file`, return it typed. */
export function readJsonValidated<T>(sourcePath: string, file: SchemaFile): T {
  const raw = fs.readFileSync(sourcePath, "utf8");
  const data = JSON.parse(raw);
  // SIMILARITY EXEMPTION (P0 prod hang, 2026-07-19): the derived similarity
  // artifacts are enormous (the 114-config unified summary--P-min is 32MB /
  // 109k cells) and ajv over them takes MINUTES per cold load even with
  // uniqueItems disabled — one request pinned the server and every
  // card/model/gallery render behind it. These files are produced and
  // schema-checked by the runner (the only writer); read-time validation adds
  // no integrity and unbounded latency, so they are read trusted.
  if (file === "similarity-summary.schema.json" || file === "similarity-pairs.schema.json") {
    return data as T;
  }
  return validate<T>(file, data, sourcePath);
}
