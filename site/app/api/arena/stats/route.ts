// GET /api/arena/stats?angle=&variant= — BT + preference stats (spec §3.2).
// Items in neutral slug order; insufficient rows carry no numbers.
import { NextRequest, NextResponse } from "next/server";

import { stats } from "@/lib/arena";
import type { Variant } from "@/lib/variant";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const angle = sp.get("angle") || "overall";
  const v = sp.get("variant");
  const variant: Variant = v === "P-min" ? "P-min" : "P-q";
  const data = stats(angle, variant);
  return NextResponse.json(data, {
    headers: { "Cache-Control": "public, max-age=60" },
  });
}
