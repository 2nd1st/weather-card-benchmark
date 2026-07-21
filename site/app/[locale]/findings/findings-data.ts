// Loader for data/findings.json (spec §4 /findings, §9 seed-or-degrade).
// B7-owned helper. fs-backed, server/build only, fully defensive: any read or
// parse error degrades to `null` so an absent/empty/broken seed file renders an
// EmptyState instead of crashing the route (non-negotiable #4, §7.8).

import fs from "node:fs";
import path from "node:path";

export interface FindingFigure {
  label_en: string;
  label_zh: string;
  value: string;
}

export interface FindingItem {
  id: string;
  title_en: string;
  title_zh: string;
  body_en: string;
  body_zh: string;
  figures?: FindingFigure[];
  refs?: string[];
  caveat_en?: string;
  caveat_zh?: string;
}

export interface FindingsDoc {
  version?: number;
  updated?: string;
  note_en?: string;
  note_zh?: string;
  findings: FindingItem[];
}

/** Candidate paths, mirroring lib/paths root resolution + the layout probe. */
function candidatePaths(): string[] {
  const out: string[] = [];
  const root = process.env.WCB_DATA_ROOT;
  if (root) out.push(path.join(path.dirname(root), "findings.json"));
  const legacy = process.env.WCB_DATA_DIR;
  if (legacy) out.push(path.join(path.dirname(legacy), "findings.json"));
  out.push(path.join(process.cwd(), "..", "data", "findings.json"));
  out.push(path.join(process.cwd(), "data", "findings.json"));
  return out;
}

function isFinding(v: unknown): v is FindingItem {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  return (
    typeof o.id === "string" &&
    typeof o.title_en === "string" &&
    typeof o.body_en === "string"
  );
}

/**
 * Load and validate findings.json. Returns null when no readable file exists or
 * it holds no valid observations — callers render the EmptyState in that case.
 */
export function loadFindings(): FindingsDoc | null {
  for (const p of candidatePaths()) {
    let parsed: unknown;
    try {
      if (!fs.existsSync(p)) continue;
      parsed = JSON.parse(fs.readFileSync(p, "utf8"));
    } catch {
      continue; // broken file → try the next candidate / degrade to null
    }
    const rawList = Array.isArray(parsed)
      ? parsed
      : (parsed as { findings?: unknown })?.findings;
    if (!Array.isArray(rawList)) continue;
    const findings = rawList.filter(isFinding);
    const doc = (Array.isArray(parsed) ? {} : parsed) as Record<string, unknown>;
    return {
      version: typeof doc.version === "number" ? doc.version : undefined,
      updated: typeof doc.updated === "string" ? doc.updated : undefined,
      note_en: typeof doc.note_en === "string" ? doc.note_en : undefined,
      note_zh: typeof doc.note_zh === "string" ? doc.note_zh : undefined,
      findings,
    };
  }
  return null;
}
