// ===========================================================================
// GET /api/om/{forecast,archive,geocoding}?<open-meteo query> — the weather
// proxy for interactive live rendering (spec §3.2 / §4.3). Replaces the old
// standalone site/cache-api/server.mjs: the injected live cards (lib/live,
// <base href="{origin}/">) resolve `/api/om/*` to THIS origin, so the new Next
// standalone server must own the route or every live fetch 404s.
//
// Untrusted model cards run under an opaque sandbox origin (non-neg #3), so CORS
// is "*" on every response. force-dynamic + nodejs runtime: the resolver reads
// the frozen weather-db, keeps a process-global in-memory cache, and does
// rate-limited live Open-Meteo fetches (see ./store).
// ===========================================================================

import { NextRequest, NextResponse } from "next/server";

import { resolveOm, isOmEndpoint } from "../store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CORS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "*",
};

function jsonResponse(status: number, body: string): NextResponse {
  return new NextResponse(body, {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      ...CORS,
    },
  });
}

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await ctx.params;
  // Cards call /api/om/forecast etc.; take the LAST segment as the endpoint so a
  // stray rewrite prefix (e.g. /api/om/v1/forecast) still resolves.
  const endpoint = Array.isArray(path) && path.length > 0 ? path[path.length - 1] : "";

  if (!isOmEndpoint(endpoint)) {
    return jsonResponse(404, JSON.stringify({ error: "not found", endpoint }));
  }

  const rawQuery = req.nextUrl.search.startsWith("?")
    ? req.nextUrl.search.slice(1)
    : req.nextUrl.search;

  const result = await resolveOm(endpoint, rawQuery);
  return jsonResponse(result.status, result.body);
}

export async function OPTIONS(): Promise<NextResponse> {
  return new NextResponse(null, { status: 204, headers: CORS });
}
