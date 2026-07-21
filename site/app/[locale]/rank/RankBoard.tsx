"use client";

import { useState } from "react";

import { Leaderboard, type LabelInfo, type LeaderboardStrings } from "../arena/_components/Leaderboard";

// Standalone community ranking. The leaderboard itself already existed inside
// the arena page, where it sat below the voting flow and was easy to miss; this
// page gives it a home of its own and adds the angle switch (the arena pins one
// angle at a time because you vote on one angle at a time — a ranking page has
// no such constraint).

// NB: the angle list is owned by the SERVER page and passed in. A plain
// constant exported from a "use client" module is a client-reference proxy on
// the server side, not the array — importing it there fails at prerender with
// "ANGLES is not iterable".
export function RankBoard({
  angles,
  labels,
  strings,
  angleLabels,
  angleLabel,
}: {
  angles: readonly string[];
  labels: Record<string, LabelInfo>;
  strings: LeaderboardStrings;
  angleLabels: Record<string, string>;
  angleLabel: string;
}) {
  const [angle, setAngle] = useState<string>(angles[0] ?? "overall");

  return (
    <>
      <div className="toolbar" style={{ marginBottom: 18 }}>
        <span style={{ font: "600 12px var(--font-mono)", color: "var(--ink-dim)" }}>
          {angleLabel}
        </span>
        <div className="seg" role="group" aria-label={angleLabel}>
          {angles.map((a) => (
            <button
              key={a}
              type="button"
              className={angle === a ? "on" : undefined}
              aria-pressed={angle === a}
              onClick={() => setAngle(a)}
            >
              {angleLabels[a] ?? a}
            </button>
          ))}
        </div>
      </div>
      {/* keyed on angle so the leaderboard refetches cleanly on switch */}
      <Leaderboard key={angle} angle={angle} labels={labels} strings={strings} />
    </>
  );
}
