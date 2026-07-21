// POST /api/arena/share {token} — reveal-time matchup permalink (spec §3.2).
// Creates a shares row from the (consumed) pair's refs; the link re-issues a
// FRESH blind pair for the recipient.
import { NextRequest, NextResponse } from "next/server";

import { createShare } from "@/lib/arena";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as { token?: unknown } | null;
  if (!body || typeof body.token !== "string") {
    return NextResponse.json({ code: "bad-request" }, { status: 400 });
  }
  const r = createShare(body.token);
  if (!r) return NextResponse.json({ code: "pair-not-found" }, { status: 404 });
  return NextResponse.json({ shareId: r.shareId, url: r.url });
}
