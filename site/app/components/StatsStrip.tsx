import { useLocale } from "next-intl";
import type { ReactNode } from "react";

// StatsStrip (spec §4 landing / shared). Big mono numerals — models / configs /
// valid cards / community votes. ALL numerals through Intl.NumberFormat(locale)
// (spec §6, no hardcoded en-US). Values come live from registry + DB; the
// component only formats + renders. Optional scope note keeps the headline
// figures honest against the default gallery view ("qualified channel").

export interface StatItem {
  value: number;
  /** already-localized label. */
  label: string;
}

export function StatsStrip({
  stats,
  scopeNote,
  ariaLabel,
}: {
  stats: StatItem[];
  scopeNote?: ReactNode;
  ariaLabel?: string;
}) {
  const locale = useLocale();
  const nf = new Intl.NumberFormat(locale);
  return (
    <>
      <div className="stats" role="list" aria-label={ariaLabel}>
        {stats.map((s, i) => (
          <div className="stat" role="listitem" key={i}>
            <div className="n">{nf.format(s.value)}</div>
            <div className="l">{s.label}</div>
          </div>
        ))}
      </div>
      {scopeNote ? <div className="stats-scope">{scopeNote}</div> : null}
    </>
  );
}
