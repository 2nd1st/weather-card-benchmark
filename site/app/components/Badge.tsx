import type { ReactNode } from "react";

// Instrument badges (spec §5). All badge TEXT stays English — grade labels,
// arm names, effort tokens are "instrument chips" that never carry CJK (§6
// stays-English list), so these components take no translations.

export type Grade = "qualified" | "dev" | "community";

const GRADE_CLASS: Record<Grade, string> = {
  qualified: "b-grade-q",
  dev: "b-grade-dev",
  community: "b-grade-com",
};

const GRADE_LABEL: Record<Grade, string> = {
  qualified: "qualified",
  dev: "dev",
  community: "community",
};

function Base({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span className={className ? `b ${className}` : "b"} title={title}>
      {children}
    </span>
  );
}

/** Arm token: api / CC / codex / grok-cli / go / … (neutral outline). */
export function ArmBadge({ arm }: { arm: string }) {
  return <Base className="b-arm">{arm}</Base>;
}

/**
 * Grade badge — renders ONLY when the grade carries information.
 *
 * Since the sub2 dev batches were deleted, every published config is
 * "qualified"; a chip that reads the same on all 186 seats is pure noise, so
 * the qualified case renders nothing (2026-07-20: "不需要显示通道了,反正
 * 只有一个"). The dev / community chips are DELIBERATELY kept: if such data
 * ever lands again the warning must reappear on its own, and `unreviewed`
 * still flags a community run awaiting maintainer review (spec §3.3
 * quarantine). The grade plumbing itself is untouched — it still gates the
 * arena pair pool in lib/arena.ts.
 */
export function GradeBadge({
  grade,
  unreviewed,
}: {
  grade: Grade;
  unreviewed?: boolean;
}) {
  if (grade === "qualified") return null;
  if (grade === "community" && unreviewed) {
    return (
      <Base className="b-unreviewed" title="community run, not yet maintainer-reviewed">
        unreviewed community
      </Base>
    );
  }
  return <Base className={GRADE_CLASS[grade]}>{GRADE_LABEL[grade]}</Base>;
}

/** Effort token chip (low / med / high / xhigh / …). */
export function EffortBadge({ effort }: { effort: string }) {
  return <Base className="b-effort">{effort}</Base>;
}

/** Warn-fill "patched" tag — a hand-fixed render (original always preserved). */
export function PatchedBadge() {
  return (
    <Base className="b-patched" title="render hand-fixed; original preserved & viewable">
      patched
    </Base>
  );
}

/** Generic pass-through badge for one-off uses. */
export function Badge({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <Base className={className} title={title}>
      {children}
    </Base>
  );
}
