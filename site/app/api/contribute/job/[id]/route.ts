// GET /api/contribute/job/<id> -> job status (spec §3.3).
import { NextRequest, NextResponse } from "next/server";

import { getJob } from "@/lib/contribute";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const job = getJob(id);
  if (!job) return NextResponse.json({ code: "not-found" }, { status: 404 });
  return NextResponse.json({
    id: job.id,
    status: job.status,
    provider: job.provider,
    failCode: job.fail_code,
    ts: job.ts,
    estUsd: [job.est_usd_lo, job.est_usd_hi],
  });
}
