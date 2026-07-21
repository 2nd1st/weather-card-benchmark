// POST /api/contribute/estimate {cells} -> {calls, usd:[lo,hi], notes} (spec §3.3).
import { NextRequest, NextResponse } from "next/server";

import { estimate, type ContributeCell } from "@/lib/contribute";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as { cells?: ContributeCell[] } | null;
  const cells = Array.isArray(body?.cells) ? (body!.cells as ContributeCell[]) : [];
  const est = await estimate(cells);
  return NextResponse.json(est);
}
