// Shared, framework-neutral presentation helpers for the detail pages
// (/config, /card, /pair). PURE + client-safe: no node fs, no hooks — so the
// same module renders inside server components AND client components. All
// colour/gating goes through lib/neutral (the anti-ranking LAW); channel
// vocabulary through lib/variant.

import { neutralColor, isSufficient, INSUFFICIENT_LABEL } from "@/lib/neutral";
import { channelFamily, isDiagnosticChannel, type Channel } from "@/lib/variant";
import type { SlotState, FieldState } from "@/lib/data";

// --------------------------- slot terminal state ---------------------------

const SLOT_STATE_LABEL: Record<SlotState, string> = {
  valid: "valid",
  "model-failed": "model failed",
  "infra-failed": "infra failed",
  "acceptance-unknown": "acceptance unknown",
  unreachable: "unreachable",
  "rate-limited-exhausted": "rate-limited (exhausted)",
};

export function SlotStateBadge({ state }: { state: SlotState }) {
  const failed = state !== "valid";
  return (
    <span className={failed ? "state-badge state-fail" : "state-badge state-ok"}>
      {SLOT_STATE_LABEL[state]}
    </span>
  );
}

// --------------------------- fidelity field state --------------------------

const FIELD_STATE_LABEL: Record<FieldState, string> = {
  match: "match",
  mismatch: "mismatch",
  ambiguous: "ambiguous",
  "not-found": "not found",
};

export function FieldStateBadge({ state }: { state: FieldState | "match" | "not-found" }) {
  const s = state as FieldState;
  const cls =
    s === "match"
      ? "field-badge field-match"
      : s === "mismatch"
        ? "field-badge field-mismatch"
        : "field-badge field-other";
  return <span className={cls}>{FIELD_STATE_LABEL[s] ?? s}</span>;
}

// ------------------------- one channel similarity bar ----------------------
//
// A fixed [0,1] neutral sequential bar (viridis). The value NEVER stretches to fill and the
// colour comes straight from lib/neutral.neutralColor (the LAW). When the cell is
// ineligible (gate) we render an "insufficient data" grey stub and NO number.

export interface SbarGate {
  n_eff: number;
  m_a: number;
  m_b: number;
  kind: "self" | "cross";
}

export function ChannelBar({
  channel,
  value,
  gate,
}: {
  channel: Channel;
  value: number | null;
  gate?: SbarGate;
}) {
  const sufficient =
    value != null &&
    (gate
      ? isSufficient({
          kind: gate.kind,
          n_eff: gate.n_eff,
          m_a: gate.m_a,
          m_b: gate.m_b,
          median: value,
        })
      : true);
  const fam = channelFamily(channel);
  const diag = isDiagnosticChannel(channel);
  return (
    <div className="sbar-row" title={diag ? "diagnostic channel (not in the formal 15)" : undefined}>
      <span className={`sbar-label fam-${fam}`}>
        {channel}
        {diag && <span className="sbar-diag">diag</span>}
      </span>
      <div className="sbar-track" aria-hidden>
        {sufficient ? (
          <div
            className="sbar-fill"
            style={{ width: `${Math.round((value as number) * 100)}%`, background: neutralColor(value as number) }}
          />
        ) : (
          <div className="sbar-insufficient" />
        )}
      </div>
      <span className="sbar-value">
        {sufficient ? (value as number).toFixed(3) : <em className="dim">{INSUFFICIENT_LABEL}</em>}
      </span>
    </div>
  );
}

// ------------------------------ tiny helpers -------------------------------

export function fmtCost(cost: string | null): string {
  if (cost == null) return "—";
  return `$${cost}`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "\u2014";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function fmtInt(n: number | null | undefined): string {
  return n == null ? "\u2014" : n.toLocaleString("en-US");
}
