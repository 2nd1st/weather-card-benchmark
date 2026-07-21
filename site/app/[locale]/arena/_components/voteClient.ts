// Client-side helpers for the blind-vote surfaces (gate · arena · shared
// matchup). Deliberately imports NO server module (lib/arena pulls in
// node:crypto + better-sqlite3), so these types are structural mirrors of the
// arena API payloads. All storage access is guarded for SSR.

export type Choice = "a" | "b" | "tie" | "both_bad";
export type PairSource = "gate" | "arena" | "share";
export type Grade = "qualified" | "dev" | "community";

/** Mirror of lib/arena IssuedPair (server builds each card.html as a full
 *  sandboxed srcdoc; the client renders it verbatim — no identity leaks). */
export interface ClientPair {
  token: string;
  mode: "pair";
  angle: string;
  variant: string;
  city: string;
  date: string;
  /** `shotUrl` is a blind inlined data: URI (gate pairs only) used by the mobile
   *  gate to render a static shot instead of a live frame — no identity leak. */
  cards: { html: string; shotUrl?: string }[];
}

export interface RevealItem {
  configId: string;
  batchId: string;
  label: string;
  arm: string;
  grade: Grade;
}

export interface VoteResult {
  reveal: RevealItem[];
  shareUrl?: string;
  choice: Choice;
}

const VOTER_KEY = "wcb.voter.v1";
const TALLY_KEY = "wcb.tally.v1";
export const GATE_KEY = "wcb.gate.v1";

/** Stable per-browser voter id (localStorage). Not an identity claim — the CSV
 *  disclaimer already states these are unauthenticated web votes. */
export function getVoterId(): string {
  if (typeof window === "undefined") return "ssr";
  try {
    let id = window.localStorage.getItem(VOTER_KEY);
    if (!id) {
      id =
        (window.crypto?.randomUUID?.() as string | undefined) ??
        `v-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      window.localStorage.setItem(VOTER_KEY, id);
    }
    return id;
  } catch {
    return "anon";
  }
}

export function getTally(): number {
  if (typeof window === "undefined") return 0;
  try {
    return Number(window.localStorage.getItem(TALLY_KEY)) || 0;
  } catch {
    return 0;
  }
}

export function bumpTally(): number {
  const next = getTally() + 1;
  try {
    window.localStorage.setItem(TALLY_KEY, String(next));
  } catch {
    /* ignore */
  }
  return next;
}

export function isGateDone(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return !!window.localStorage.getItem(GATE_KEY);
  } catch {
    return true; // fail open (spec §4.0)
  }
}

/** Mark the gate satisfied and dismiss the overlay via the anti-flicker stamp. */
export function markGateDone(): void {
  try {
    window.localStorage.setItem(GATE_KEY, "1");
  } catch {
    /* ignore */
  }
  try {
    document.documentElement.setAttribute("data-gate", "done");
  } catch {
    /* ignore */
  }
}

type FetchPairResult = { ok: true; pair: ClientPair } | { ok: false; code: string };

export async function fetchPair(angle: string, source: PairSource): Promise<FetchPairResult> {
  try {
    const res = await fetch(
      `/api/arena/pair?angle=${encodeURIComponent(angle)}&source=${source}`,
      { cache: "no-store" },
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return { ok: false, code: (body as { code?: string }).code ?? "error" };
    }
    return { ok: true, pair: (await res.json()) as ClientPair };
  } catch {
    return { ok: false, code: "network" };
  }
}

type CastResult =
  | { ok: true; reveal: RevealItem[]; shareUrl?: string }
  | { ok: false; code: string };

export async function castVote(input: {
  token: string;
  choice: Choice;
  latencyMs: number | null;
  locale: string;
}): Promise<CastResult> {
  try {
    const res = await fetch("/api/vote", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        token: input.token,
        choice: input.choice,
        voterId: getVoterId(),
        latencyMs: input.latencyMs,
        locale: input.locale,
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok || !(body as { ok?: boolean }).ok) {
      return { ok: false, code: (body as { code?: string }).code ?? "error" };
    }
    const b = body as { reveal: RevealItem[]; shareUrl?: string };
    return { ok: true, reveal: b.reveal, shareUrl: b.shareUrl };
  } catch {
    return { ok: false, code: "network" };
  }
}

export async function createShare(token: string): Promise<{ shareId: string; url: string } | null> {
  try {
    const res = await fetch("/api/arena/share", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (!res.ok) return null;
    return (await res.json()) as { shareId: string; url: string };
  } catch {
    return null;
  }
}

/** Absolute URL for a root-relative path using the live origin (works in any env). */
export function absoluteUrl(path: string): string {
  if (typeof window === "undefined") return path;
  return `${window.location.origin}${path}`;
}

/** Plain twitter.com/intent/post link (no SDK, spec §4.9). */
export function xIntentUrl(text: string): string {
  return `https://twitter.com/intent/post?text=${encodeURIComponent(text)}`;
}
