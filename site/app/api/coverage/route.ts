// GET /api/coverage — coverage cells JSON (public, spec §3.3). Degrades to an
// empty board when lib/coverage (A1) is not present, never crashes.
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  // string-typed leaf: tsc treats the specifier as `any` (lib/coverage is A1's).
  const leaf: string = "coverage";
  try {
    const cov = await import(`../../../lib/${leaf}`);
    const cells = typeof cov.coverage === "function" ? cov.coverage() : [];
    const totals = typeof cov.coverageTotals === "function" ? cov.coverageTotals() : null;
    const asOf = typeof cov.coverageAsOf === "function" ? cov.coverageAsOf() : null;
    return NextResponse.json(
      { cells, totals, asOf },
      { headers: { "Cache-Control": "public, max-age=300" } },
    );
  } catch {
    return NextResponse.json({ cells: [], totals: null, asOf: null, note: "coverage unavailable" });
  }
}
