// POST /api/vote {token, choice, latencyMs, voterId, locale} — record a vote.
// Rate-limit gate first (per ip_hash + voter_id), then atomic single-use consume
// + dwell validation inside castVote (spec §3.2).
import { NextRequest, NextResponse } from "next/server";

import { castVote, type Choice } from "@/lib/arena";
import { consumeRate, clientIpHash } from "@/lib/ratelimit";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CHOICES: Choice[] = ["a", "b", "tie", "both_bad"];

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as
    | { token?: unknown; choice?: unknown; voterId?: unknown; latencyMs?: unknown; locale?: unknown }
    | null;

  if (!body || typeof body.token !== "string" || typeof body.voterId !== "string") {
    return NextResponse.json({ code: "bad-request" }, { status: 400 });
  }
  if (typeof body.choice !== "string" || !CHOICES.includes(body.choice as Choice)) {
    return NextResponse.json({ code: "bad-choice" }, { status: 400 });
  }

  const ipHash = clientIpHash(req.headers);
  const rl = consumeRate([`ip:${ipHash}`, `voter:${body.voterId}`]);
  if (!rl.ok) {
    return NextResponse.json({ code: rl.code ?? "rate-limited", retryAfter: rl.retryAfter }, { status: 429 });
  }

  const out = await castVote({
    token: body.token,
    choice: body.choice as Choice,
    voterId: body.voterId,
    ipHash,
    latencyMs: typeof body.latencyMs === "number" ? body.latencyMs : null,
    locale: typeof body.locale === "string" ? body.locale : null,
  });

  if (!out.ok) return NextResponse.json({ code: out.code }, { status: out.status });
  return NextResponse.json({ ok: true, reveal: out.reveal, shareUrl: out.shareUrl });
}
