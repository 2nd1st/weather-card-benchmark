"use client";

import { useCallback, useEffect, useState } from "react";
import styles from "./methodology.module.css";

// Provenance ledger (audit item 11, P0). The raw session table used to break the
// desktop layout: names/dates wrapped into fragments while manifest hashes were
// hard-clipped at the right edge. Here every identifier is single-line with an
// ellipsis + a copy affordance, the table scrolls horizontally inside its own
// container, and the full ledger sits behind a "view all" disclosure so a compact
// recent window leads instead of a wall of hashes.

export interface ProvRow {
  id: string;
  date: string;
  nConfigs: number;
  variants: string[];
  hash: string;
}

interface Cols {
  session: string;
  date: string;
  configs: string;
  variants: string;
  hash: string;
}

interface Strings {
  summary: string;
  viewAll: string;
  viewLess: string;
  copy: string;
  copied: string;
}

function CopyButton({ value, labels }: { value: string; labels: Strings }) {
  const [done, setDone] = useState(false);
  const copy = useCallback(() => {
    void navigator.clipboard?.writeText(value).then(
      () => {
        setDone(true);
        window.setTimeout(() => setDone(false), 1200);
      },
      () => {
        /* clipboard blocked — the full value is still in the title tooltip */
      },
    );
  }, [value]);
  return (
    <button
      type="button"
      className={styles.copyBtn}
      onClick={copy}
      aria-label={`${labels.copy}: ${value}`}
      title={done ? labels.copied : labels.copy}
      data-done={done ? "1" : undefined}
    >
      {done ? "✓" : "⧉"}
    </button>
  );
}

export function ProvenanceTable({
  rows,
  cols,
  strings,
  recentCount = 6,
}: {
  rows: ProvRow[];
  cols: Cols;
  strings: Strings;
  recentCount?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasMore = rows.length > recentCount;
  const shown = expanded || !hasMore ? rows : rows.slice(0, recentCount);

  // Deep-links from /findings target a session anchor (#<batchId>). If that row
  // sits in the collapsed tail, expand so the anchor exists, then scroll to it.
  useEffect(() => {
    if (expanded || !hasMore) return;
    const id = decodeURIComponent(window.location.hash.slice(1));
    if (!id) return;
    const idx = rows.findIndex((r) => r.id === id);
    if (idx >= recentCount) {
      setExpanded(true);
      requestAnimationFrame(() =>
        document.getElementById(id)?.scrollIntoView({ block: "start" }),
      );
    }
  }, [rows, recentCount, hasMore, expanded]);

  return (
    <div>
      <div className={styles.provSummary}>{strings.summary}</div>
      <div className={styles.tableWrap}>
        <table className={styles.tbl}>
          <thead>
            <tr>
              <th>{cols.session}</th>
              <th>{cols.date}</th>
              <th>{cols.configs}</th>
              <th>{cols.variants}</th>
              <th>{cols.hash}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((b) => (
              <tr key={b.id} id={b.id}>
                <td>
                  <div className={styles.idCell}>
                    <span className={styles.idText} title={b.id}>
                      {b.id}
                    </span>
                    <CopyButton value={b.id} labels={strings} />
                  </div>
                </td>
                <td className={styles.nowrap}>{b.date}</td>
                <td className="mono">{b.nConfigs}</td>
                <td>
                  <span className={styles.chips}>
                    {b.variants.map((v) => (
                      <span key={v} className="b b-arm">
                        {v}
                      </span>
                    ))}
                  </span>
                </td>
                <td>
                  <div className={styles.idCell}>
                    <span className={styles.hashText} title={b.hash}>
                      {b.hash}
                    </span>
                    <CopyButton value={b.hash} labels={strings} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hasMore ? (
        <button
          type="button"
          className={styles.viewAll}
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? strings.viewLess : strings.viewAll}
        </button>
      ) : null}
    </div>
  );
}
