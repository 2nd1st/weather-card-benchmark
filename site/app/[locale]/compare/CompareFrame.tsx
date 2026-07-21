"use client";

import { useEffect, useState } from "react";
import { buildSrcdoc, type LiveParams } from "@/lib/live/inject";
import { ScaledExhibit, EXHIBIT_W, EXHIBIT_H } from "@/app/components/CardFrame";
import styles from "./compare.module.css";

// CompareFrame (spec §4 /compare). An INTERACTIVE live exhibit frame: the raw
// card.html is fetched (cached), run through lib/live buildSrcdoc (base-href for
// /api/om + city-param shim + delay), and drawn through the shared ScaledExhibit
// at the measured-viewport 1280×800 (see CardFrame — runner VIEWPORT parity).
// Untrusted model output stays inside <iframe sandbox="allow-scripts" srcdoc>
// with NO allow-same-origin (non-neg #3) and NO pointer-events blocking — the
// frame stays fully interactive (spec §4 acceptance: "iframes stay interactive").
//
// The shared control row hands every mounted frame the SAME `params` (city/date)
// + `delayMs`; bumping `generation` re-keys the iframe so all frames reload in
// sync. Only 2–4 frames ever exist here, so they mount immediately (no lazy IO).

const htmlCache = new Map<string, Promise<string>>();
function fetchCard(url: string): Promise<string> {
  let p = htmlCache.get(url);
  if (!p) {
    p = fetch(url).then((r) => {
      if (!r.ok) throw new Error(`card.html ${r.status}`);
      return r.text();
    });
    htmlCache.set(url, p);
  }
  return p;
}

export function CompareFrame({
  cardUrl,
  params,
  delayMs = 0,
  generation = 0,
  title,
  loadingLabel = "rendering",
  unavailableLabel = "card unavailable",
}: {
  /** public URL of the ORIGINAL card.html for the chosen valid slot. */
  cardUrl: string;
  /** shared city/date the card should render (injected via URLSearchParams shim). */
  params: LiveParams;
  /** simulated API latency (display layer). */
  delayMs?: number;
  /** bump to force a fresh document (re-run card scripts) on any control change. */
  generation?: number;
  title: string;
  loadingLabel?: string;
  unavailableLabel?: string;
}) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState(false);
  // per-pane "has this frame finished (re)loading" gate — drives the loading
  // overlay so a pane never shows a previous city while the new one loads.
  const [loaded, setLoaded] = useState(false);

  // fetch card.html when the URL changes (slot cycle → different slot html).
  useEffect(() => {
    let alive = true;
    setHtml(null);
    setError(false);
    fetchCard(cardUrl)
      .then((t) => {
        if (alive) setHtml(t);
      })
      .catch(() => {
        if (alive) setError(true);
      });
    return () => {
      alive = false;
    };
  }, [cardUrl]);

  // Any reload — a new slot (cardUrl) OR a shared-state bump (generation,
  // which re-keys the iframe below) — re-arms the loading gate. It clears again
  // when the freshly-mounted iframe fires onLoad, guaranteeing the overlay sits
  // over the frame from the moment shared state changes until the new render
  // paints. NEVER a stale-city poster underneath (removed): panes can't mix.
  useEffect(() => {
    setLoaded(false);
  }, [cardUrl, generation]);

  const origin =
    typeof window !== "undefined" ? window.location.origin : undefined;
  const srcdoc =
    html != null ? buildSrcdoc(html, { origin, params, delayMs }) : null;

  return (
    <div
      className="cardframe"
      style={{ aspectRatio: `${EXHIBIT_W} / ${EXHIBIT_H}` }}
    >
      {error ? (
        <div className="cf-msg">{unavailableLabel}</div>
      ) : (
        <>
          {srcdoc != null ? (
            <ScaledExhibit
              srcdoc={srcdoc}
              title={title}
              frameKey={generation}
              fit="content"
              onLoad={() => setLoaded(true)}
            />
          ) : null}
          {!loaded ? (
            <div className={styles.frameLoading}>
              <span className="cf-shimmer" aria-hidden />
              <span className={styles.frameLoadLabel}>{loadingLabel}</span>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
