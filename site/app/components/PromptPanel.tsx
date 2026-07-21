"use client";

import { useState } from "react";

// The exact task text every model was handed, shown verbatim. Two variants
// (P-min / P-q) share one panel with a segmented switch, because the whole point
// is that a reader can see how little separates them — the benchmark's fairness
// claim rests on both being identical for every model.
//
// Text is passed in from a server component (lib/prompt.ts reads + hashes it);
// this component is presentation only.

export interface PromptPanelDoc {
  variant: string;
  href: string;
  text: string;
  sha256: string;
}

export function PromptPanel({
  docs,
  compact = false,
  labels,
}: {
  docs: PromptPanelDoc[];
  /** homepage use: clamp the body and skip the hash line. */
  compact?: boolean;
  labels: { heading: string; note: string; download: string };
}) {
  const [idx, setIdx] = useState(0);
  if (docs.length === 0) return null;
  const doc = docs[Math.min(idx, docs.length - 1)];

  return (
    <div className="pp">
      <div className="pp-head">
        <span className="pp-title">{labels.heading}</span>
        {docs.length > 1 ? (
          <div className="seg" role="group" aria-label={labels.heading}>
            {docs.map((d, i) => (
              <button
                key={d.variant}
                type="button"
                className={i === idx ? "on" : undefined}
                aria-pressed={i === idx}
                onClick={() => setIdx(i)}
              >
                {d.variant}
              </button>
            ))}
          </div>
        ) : null}
        <a className="pp-dl" href={doc.href} download>
          {labels.download}
        </a>
      </div>
      <pre className={compact ? "pp-body pp-clamp" : "pp-body"}>{doc.text}</pre>
      {compact ? null : (
        <div className="pp-foot">
          <span className="pp-note">{labels.note}</span>
          <code className="pp-sha" title="sha256 of the bytes shown above">
            sha256 {doc.sha256.slice(0, 16)}…
          </code>
        </div>
      )}
    </div>
  );
}
