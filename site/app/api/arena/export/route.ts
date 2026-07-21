// GET /api/arena/export — full votes CSV (open data). Header disclaimer states
// these are unauthenticated, trivially-gameable web votes (spec §3.2).
import { NextResponse } from "next/server";

import { exportCsv } from "@/lib/arena";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const csv = exportCsv();
  return new NextResponse(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": 'attachment; filename="arena-votes.csv"',
      "Cache-Control": "no-store",
    },
  });
}
