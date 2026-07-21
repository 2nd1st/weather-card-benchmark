// Channel grading + arm grouping — the SINGLE source of truth for the
// qualified / dev / community split and the api/harness wall sections
// (DECISIONS-M0 "index.schema 1.1.0" ruling, 2026-07-17; v3 §2.2 adds the
// community grade).
//
// Data-driven, never guessed from ids alone: grade is read off the transport
// descriptor. Everything except the private sub2 reverse-proxy is a legitimate,
// reproducible channel. Pure string logic — client-safe, no fs.

export type ChannelGrade = "qualified" | "dev" | "community";
export type ArmGroup = "api" | "harness";

/**
 * transport descriptor (+ optional configId) → grade. (v3) CHECK ORDER IS
 * COMMUNITY-FIRST:
 *   1. transport contains "community" → "community"
 *      (externally-contributed runs; the runner guarantees a community
 *      transport contains NO "sub2" substring, so a community batch is never
 *      misgraded dev just because a downstream leg also mentions sub2).
 *   2. else transport contains "sub2" → "dev"  (private reverse-proxy; non-publishable)
 *   3. else configId matches the sub2-codex arm signature → "dev"  (see below)
 *   4. else                          → "qualified"
 *
 * configId GUARD (P0 data-integrity, 2026-07-17): the sub2-reverse-proxy
 * codex batches (2026-07-17--dev-codex-gpt-*) are the codex CLI pointed at a
 * sub2api provider, but they record transport "codex-cli-isolated-home" with
 * endpoint=null — there is NO "sub2" marker ANYWHERE in their config.json. On
 * transport alone they grade "qualified", which (observed) leaked dev data into
 * the gallery/featured "qualified" badge AND into the arena blind-pair voting
 * pool (config gpt-5.6-sol-eff-medium--cli--codex--dev served in a real pair).
 * So when a configId is supplied we also treat the sub2-codex arm as dev: the
 * arm signature is "--cli--codex--" (the plain codex CLI arm) but NOT
 * "--cli--codex-oauth--". The oauth variant (transport "codex-cli-oauth-chatgpt")
 * is a GENUINE ChatGPT-plan direct login and stays qualified — hence the
 * longest-match exclusion, mirroring armOf() in lib/label.ts.
 * These sub2-codex batches are scheduled for deletion in data-cleanup; this
 * rule guards the qualified/dev split until they are removed.
 *
 * configId is optional to keep backward compat with transport-only callers;
 * omitting it simply skips the arm guard.
 */
export function channelGradeOf(transport: string, configId?: string): ChannelGrade {
  const t = transport.toLowerCase();
  if (t.includes("community")) return "community";
  if (t.includes("sub2")) return "dev";
  if (configId) {
    const c = configId.toLowerCase();
    if (c.includes("--cli--codex--") && !c.includes("--cli--codex-oauth--")) {
      return "dev";
    }
  }
  return "qualified";
}

/** api-shaped vs harness-shaped arm. CLI protocols are harness; the app-only
 *  manual tier ("app", e.g. ChatGPT app codex @ultra) is also harness-shaped
 *  (matches its harness-plan arm_group); gateways (opencode-go) are api-shaped
 *  even though the billing is a subscription. */
export function armGroupOf(protocol: string): ArmGroup {
  return protocol === "cli" || protocol === "app" ? "harness" : "api";
}

export const GRADE_LABEL: Record<ChannelGrade, string> = {
  qualified: "qualified channels",
  dev: "dev (sub2) — non-publishable",
  community: "community — unreviewed",
};

export const ARM_GROUP_LABEL: Record<ArmGroup, string> = {
  api: "API arm",
  harness: "Harness arm (CLI)",
};
