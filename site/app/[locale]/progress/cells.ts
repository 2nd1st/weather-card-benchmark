// Shared cell codec (B6). The /progress contribute tray hands a set of
// missing/attempted cells to /contribute through the `cells` query param; both
// surfaces round-trip through this one encoder so the shape never drifts. Kept
// pure + dependency-free so it imports cleanly into server and client code.

export interface TrayCell {
  family: string;
  modelId: string;
  effort: string | null;
  arm: string;
  /** planned slots per variant (default 4) — drives the cost estimate. */
  n: number;
}

/** Stable identity for de-dup / selection toggling. */
export function cellKey(c: {
  family: string;
  modelId: string;
  effort: string | null;
  arm: string;
}): string {
  return `${c.family}|${c.modelId}|${c.effort ?? ""}|${c.arm}`;
}

/** Compact URL value. Caller wraps with encodeURIComponent when appended. */
export function encodeCells(cells: TrayCell[]): string {
  return JSON.stringify(
    cells.map((c) => ({ f: c.family, m: c.modelId, e: c.effort, a: c.arm, n: c.n })),
  );
}

/** Parse the `cells` query value. Never throws — bad input yields []. */
export function decodeCells(raw: string | string[] | undefined | null): TrayCell[] {
  if (!raw || Array.isArray(raw)) {
    if (Array.isArray(raw)) raw = raw[0];
    else return [];
  }
  if (typeof raw !== "string" || raw.length === 0) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    const out: TrayCell[] = [];
    for (const item of parsed) {
      if (!item || typeof item !== "object") continue;
      const o = item as Record<string, unknown>;
      const family = typeof o.f === "string" ? o.f : typeof o.family === "string" ? o.family : null;
      const modelId = typeof o.m === "string" ? o.m : typeof o.modelId === "string" ? o.modelId : null;
      const arm = typeof o.a === "string" ? o.a : typeof o.arm === "string" ? o.arm : null;
      if (!family || !modelId || !arm) continue;
      const effRaw = "e" in o ? o.e : o.effort;
      const effort = typeof effRaw === "string" && effRaw.length > 0 ? effRaw : null;
      const nRaw = "n" in o ? o.n : 4;
      const n = typeof nRaw === "number" && nRaw > 0 ? nRaw : 4;
      out.push({ family, modelId, effort, arm, n });
    }
    // cap defensively at the server-side per-job ceiling (spec §3.3)
    return out.slice(0, 12);
  } catch {
    return [];
  }
}
