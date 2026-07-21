// Server-side board shaper (B6). Turns lib/coverage's flat CoverageCell[] into
// the arm-sectioned, per-model-row board the /progress board renders, plus the
// honesty-kit totals, the natively-derived 中国 gap, and the cost table — all as
// plain serializable data handed to the client ProgressBoard. Seed-or-degrade:
// every source is optional; absence yields an empty board, never a crash.

import "server-only";

import {
  coverage,
  coverageTotals,
  coverageAsOf,
  loadCostReference,
  loadHarnessPlan,
  type CoverageCell,
  type CoverageStatus,
  type CostReferenceEntry,
} from "@/lib/coverage";
import { loadRegistry } from "@/lib/registry";
import type { Grade } from "@/lib/registry";
import type { ArmGroup } from "@/lib/channel";

// --------------------------- serializable board types ----------------------

export interface BoardFraction {
  variant: string;
  valid: number;
  total: number;
}
export interface BoardCell {
  family: string;
  modelId: string;
  effort: string | null;
  arm: string;
  status: CoverageStatus;
  grade: Grade | null;
  n: number;
  fractions: BoardFraction[] | null;
  awaitingEn: string | null;
  awaitingZh: string | null;
  configId: string | null;
}
export interface BoardRow {
  family: string;
  modelId: string;
  /** this model's own planned efforts, compact + ordered — one chip per cell,
   *  never padded to a shared column set (no ghost cells). */
  cells: BoardCell[];
}
export interface BoardArm {
  arm: string;
  armGroup: ArmGroup;
  planSource: "api-matrix" | "harness-plan";
  harnessEn: string | null;
  harnessZh: string | null;
  rows: BoardRow[];
}
export interface BoardTotals {
  planned: number;
  landed: number;
  partial: number;
  attempted: number;
  missing: number;
  paused: number;
  validSlots: number;
}
export interface RegistryTotals {
  configs: number;
  models: number;
  validSlots: number;
  batches: number;
}
export interface ChinaGap {
  families: number;
  zeroDataFamilies: number;
  apiSeats: number;
  apiMissing: number;
  landed: number;
  planned: number;
}
export interface CostRow {
  family: string;
  arm: string;
  loUsd: number;
  hiUsd: number;
  basis: string | null;
  noteEn: string | null;
  noteZh: string | null;
}
export interface CostTable {
  perConfigCalls: number | null;
  noteEn: string | null;
  noteZh: string | null;
  rows: CostRow[];
}
export interface BoardData {
  arms: BoardArm[];
  totals: BoardTotals;
  registryTotals: RegistryTotals;
  chinaGap: ChinaGap;
  cost: CostTable | null;
  asOf: string;
}

// ------------------------------- effort order ------------------------------

const EFFORT_RANK: Record<string, number> = {
  none: 0,
  minimal: 1,
  off: 2,
  no_think: 2,
  disabled: 2,
  low: 3,
  med: 4,
  medium: 4,
  auto: 5,
  high: 6,
  xhigh: 7,
  max: 8,
  on: 9,
  enabled: 9,
  default: 10,
  ultra: 11,
};

function effRank(e: string | null): number {
  if (e === null) return -1; // no-effort bucket sorts first
  const num = Number(e);
  if (!Number.isNaN(num)) return 100 + num; // numeric budgets after named, numeric order
  return EFFORT_RANK[e.toLowerCase()] ?? 50;
}

function effSortKey(e: string | null): string {
  return e === null ? " default" : e;
}

// --------------------------- Chinese-model classifier ----------------------
//
// Region is not a field on any record, so the classifier below encodes domain
// knowledge (which families/model ids are Chinese first-party). Only the
// CLASSIFICATION is fixed here — every COUNT the gap headline shows is derived
// from live coverage data (spec §4 /progress: "never hardcoded counts"). The
// "multi" arm family (opencode/go gateways) mixes CN + grok, so classification
// falls to the model id there.

const CN_FAMILIES = new Set([
  "deepseek", "qwen", "kimi", "glm", "minimax", "mimo", "hunyuan", "step",
  "doubao", "ernie", "spark", "intern", "longcat", "ring", "kat", "pangu",
]);
const CN_MODEL_PREFIXES = [
  "qwen", "kimi", "glm", "minimax", "mimo", "deepseek", "hy", "ernie",
  "spark", "intern", "longcat", "ring", "kat", "doubao", "step", "pangu",
];

function isChinese(family: string, modelId: string): boolean {
  const fam = family.toLowerCase();
  if (CN_FAMILIES.has(fam)) return true;
  const m = modelId.toLowerCase();
  return CN_MODEL_PREFIXES.some((p) => m.startsWith(p));
}

// -------------------------------- shaping ----------------------------------

function toBoardCell(c: CoverageCell): BoardCell {
  return {
    family: c.cell.family,
    modelId: c.cell.modelId,
    effort: c.cell.effort,
    arm: c.cell.arm,
    status: c.status,
    grade: c.grade ?? null,
    n: c.n,
    fractions: c.validFraction
      ? c.validFraction.map((f) => ({ variant: f.variant, valid: f.valid, total: f.total }))
      : null,
    awaitingEn: c.awaiting?.en ?? null,
    awaitingZh: c.awaiting?.zh ?? null,
    configId: c.entry?.configId ?? null,
  };
}

function buildArm(arm: string, cells: CoverageCell[]): BoardArm {
  const first = cells[0];
  // rows: one per model, ordered by (family, modelId). Each row carries ONLY
  // that model's own planned efforts — sorted low→high, compact, no shared
  // column set — so a row never renders ghost cells for efforts it never planned.
  const rowMap = new Map<string, BoardRow>();
  for (const c of cells) {
    const key = `${c.cell.family} ${c.cell.modelId}`;
    let row = rowMap.get(key);
    if (!row) {
      row = { family: c.cell.family, modelId: c.cell.modelId, cells: [] };
      rowMap.set(key, row);
    }
    row.cells.push(toBoardCell(c));
  }
  const rows = [...rowMap.values()]
    .map((row) => {
      row.cells.sort((a, b) => {
        const d = effRank(a.effort) - effRank(b.effort);
        return d !== 0 ? d : effSortKey(a.effort).localeCompare(effSortKey(b.effort));
      });
      return row;
    })
    .sort((a, b) => a.family.localeCompare(b.family) || a.modelId.localeCompare(b.modelId));

  return {
    arm,
    armGroup: first.armGroup,
    planSource: first.planSource,
    harnessEn: null,
    harnessZh: null,
    rows,
  };
}

function costTable(): CostTable | null {
  const cost = loadCostReference();
  if (!cost) return null;
  return {
    perConfigCalls: typeof cost.per_config_calls === "number" ? cost.per_config_calls : null,
    noteEn: cost.note_en ?? null,
    noteZh: cost.note_zh ?? null,
    rows: (cost.entries ?? []).map((e: CostReferenceEntry) => ({
      family: e.family,
      arm: e.arm,
      loUsd: e.per_call_usd?.[0] ?? 0,
      hiUsd: e.per_call_usd?.[1] ?? 0,
      basis: e.basis ?? null,
      noteEn: e.note_en ?? null,
      noteZh: e.note_zh ?? null,
    })),
  };
}

function chinaGap(cells: CoverageCell[]): ChinaGap {
  const cn = cells.filter((c) => isChinese(c.cell.family, c.cell.modelId));
  const famHasData = new Map<string, boolean>();
  const famSeen = new Set<string>();
  let apiSeats = 0;
  let apiMissing = 0;
  let landed = 0;
  for (const c of cn) {
    famSeen.add(c.cell.family);
    const has = c.status === "landed" || c.status === "partial";
    if (has) landed += 1;
    famHasData.set(c.cell.family, (famHasData.get(c.cell.family) ?? false) || has);
    if (c.cell.arm === "api") {
      apiSeats += 1;
      if (c.status === "missing") apiMissing += 1;
    }
  }
  let zeroDataFamilies = 0;
  for (const f of famSeen) if (!famHasData.get(f)) zeroDataFamilies += 1;
  return {
    families: famSeen.size,
    zeroDataFamilies,
    apiSeats,
    apiMissing,
    landed,
    planned: cn.length,
  };
}

// Arm section order: api (matrix) first, then harness arms alphabetically.
function armSortKey(a: BoardArm): string {
  return (a.planSource === "api-matrix" ? "0" : "1") + a.arm;
}

export function buildBoard(): BoardData {
  const cells = coverage();
  const byArm = new Map<string, CoverageCell[]>();
  for (const c of cells) {
    const arr = byArm.get(c.cell.arm);
    if (arr) arr.push(c);
    else byArm.set(c.cell.arm, [c]);
  }
  // arm → harness description (plan source B carries human labels)
  const harnessDesc = new Map<string, { en: string | null; zh: string | null }>();
  try {
    for (const a of loadHarnessPlan()?.arms ?? []) {
      if (a?.arm) harnessDesc.set(a.arm, { en: a.harness_en ?? null, zh: a.harness_zh ?? null });
    }
  } catch {
    /* degrade to no descriptions */
  }

  const arms = [...byArm.entries()]
    .map(([arm, list]) => {
      const built = buildArm(arm, list);
      const desc = harnessDesc.get(arm);
      if (desc) {
        built.harnessEn = desc.en;
        built.harnessZh = desc.zh;
      }
      return built;
    })
    .sort((a, b) => armSortKey(a).localeCompare(armSortKey(b)));

  const t = coverageTotals(cells);
  let reg: RegistryTotals = { configs: 0, models: 0, validSlots: 0, batches: 0 };
  try {
    reg = loadRegistry().totals;
  } catch {
    /* registry unavailable → zeros (degrade, never crash) */
  }

  return {
    arms,
    totals: {
      planned: t.planned,
      landed: t.landed,
      partial: t.partial,
      attempted: t.attempted,
      missing: t.missing,
      paused: t.paused,
      validSlots: t.validSlots,
    },
    registryTotals: reg,
    chinaGap: chinaGap(cells),
    cost: costTable(),
    asOf: coverageAsOf(),
  };
}
