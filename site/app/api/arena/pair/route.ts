// GET /api/arena/pair?angle=<a>&source=gate|arena|share — issue a blind pair.
// Opaque single-use token, no identities in the payload (spec §3.2).
import { NextRequest, NextResponse } from "next/server";

import { issuePair, ArenaUnavailable, type PairSource } from "@/lib/arena";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const angle = sp.get("angle") || "overall";
  const raw = sp.get("source");
  const source: PairSource = raw === "gate" || raw === "share" ? raw : "arena";
  try {
    const pair = await issuePair({ angle, source });
    return NextResponse.json(pair, { headers: { "Cache-Control": "no-store" } });
  } catch (e) {
    if (e instanceof ArenaUnavailable) {
      return NextResponse.json({ code: e.code }, { status: 503 });
    }
    return NextResponse.json({ code: "error" }, { status: 500 });
  }
}
