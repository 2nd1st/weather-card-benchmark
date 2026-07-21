// POST /api/contribute/submit {cells, provider, apiKey, contact?} (spec §3.3).
// Flag-gated: 503 {code:"beta"} unless WCB_CONTRIBUTE_ENABLED=1. The key is held
// in memory only (never persisted/logged) — see lib/contribute.
import { NextRequest, NextResponse } from "next/server";

import { submitJob, type ContributeCell } from "@/lib/contribute";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as
    | { cells?: ContributeCell[]; provider?: unknown; apiKey?: unknown; contact?: unknown; note?: unknown }
    | null;
  if (!body || typeof body.provider !== "string" || typeof body.apiKey !== "string") {
    return NextResponse.json({ code: "bad-request" }, { status: 400 });
  }
  const cells = Array.isArray(body.cells) ? (body.cells as ContributeCell[]) : [];
  const res = await submitJob({
    cells,
    provider: body.provider,
    apiKey: body.apiKey,
    contact: typeof body.contact === "string" ? body.contact : undefined,
    note: typeof body.note === "string" ? body.note : undefined,
  });
  if (!res.ok) return NextResponse.json({ code: res.code }, { status: res.status });
  return NextResponse.json({ ok: true, jobId: res.jobId });
}
