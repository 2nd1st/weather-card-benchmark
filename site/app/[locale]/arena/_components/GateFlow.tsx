"use client";

import { useCallback, useEffect, useState } from "react";
import { useLocale } from "next-intl";
import { GateOverlay } from "@/app/components/GateOverlay";
import { PairStage, type PairStrings } from "./PairStage";
import styles from "../arena.module.css";
import {
  fetchPair,
  isGateDone,
  markGateDone,
  absoluteUrl,
  xIntentUrl,
  type ClientPair,
} from "./voteClient";

// Arena gate (spec §4.0). An OVERLAY above the real SSR landing — never a
// redirect. Fresh visitors cast 3 blind votes (source='gate', excluded from the
// default aggregation) to unlock; a visible "browse without voting" skip records
// nothing. Visibility is CSS-driven by data-gate on <html> (stamped pre-paint by
// the layout); this component only fetches + drives once the gate is unfinished.

export interface GateStrings {
  title: string;
  sub: string;
  skip: string;
  metaAngle: string;
  metaVariant: string;
  metaCity: string;
  metaFixture: string;
  nextPick: string;
  continueLabel: string;
  pair: PairStrings;
  unlock: {
    title: string;
    body: string;
    enter: string;
    shareLabel: string;
    copy: string;
    copied: string;
    x: string;
    text: string; // template with {url}
  };
}

const TOTAL = (() => {
  const raw = Number(process.env.NEXT_PUBLIC_WCB_GATE_VOTES);
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 3;
})();

export function GateFlow({ strings }: { strings: GateStrings }) {
  const locale = useLocale();
  const [started, setStarted] = useState(false);
  const [pair, setPair] = useState<ClientPair | null>(null);
  const [count, setCount] = useState(0);
  const [phase, setPhase] = useState<"voting" | "unlock">("voting");
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setPair(null);
    const r = await fetchPair("overall", "gate");
    if (r.ok) setPair(r.pair);
    // pool unavailable → the gate can't coerce a vote it can't offer: fail open.
    else markGateDone();
  }, []);

  useEffect(() => {
    if (isGateDone()) return; // returning visitor — overlay is CSS-hidden; don't fetch
    setStarted(true);
    load();
  }, [load]);

  const onVoted = useCallback(() => {
    setCount((c) => c + 1);
  }, []);

  function dismiss() {
    markGateDone();
  }

  async function copyText(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      /* ignore */
    }
  }

  const meta = (
    <>
      <span>
        {strings.metaAngle} <b>overall</b>
      </span>
      {pair ? (
        <>
          <span>
            {strings.metaVariant} <b>{pair.variant}</b>
          </span>
          <span>
            {strings.metaCity} <b>{pair.city}</b>
          </span>
          <span>
            {strings.metaFixture} <b>{pair.date}</b>
          </span>
        </>
      ) : null}
    </>
  );

  const siteUrl = absoluteUrl("/");
  const shareText = strings.unlock.text.replace("{url}", siteUrl);

  const unlockPanel = (
    <div className={styles.unlock}>
      <div className={styles.unlockHead}>
        <span className="spark">✦</span> {strings.unlock.title}
      </div>
      <p className={styles.unlockBody}>{strings.unlock.body}</p>
      <div className={styles.unlockActions}>
        <button className="btn btn-hero" onClick={dismiss}>
          {strings.unlock.enter} →
        </button>
        <button className="btn btn-ghost" onClick={() => copyText(shareText)}>
          {copied ? strings.unlock.copied : strings.unlock.copy}
        </button>
        <a
          className="btn btn-violet"
          href={xIntentUrl(shareText)}
          target="_blank"
          rel="noopener noreferrer"
        >
          {strings.unlock.x}
        </a>
      </div>
    </div>
  );

  return (
    <GateOverlay
      title={strings.title}
      subtitle={phase === "voting" ? strings.sub : undefined}
      done={Math.min(count, TOTAL)}
      total={TOTAL}
      meta={phase === "voting" ? meta : undefined}
      skipLabel={strings.skip}
      onSkip={dismiss}
      canSkip={phase === "voting" && !!pair}
    >
      {phase === "unlock" ? (
        unlockPanel
      ) : started && pair ? (
        <PairStage
          key={pair.token}
          pair={pair}
          locale={locale}
          strings={strings.pair}
          mobileStatic
          onVoted={onVoted}
          renderRevealActions={() => {
            const reachedTotal = count >= TOTAL;
            return (
              <button
                className="btn btn-hero"
                onClick={() => (reachedTotal ? setPhase("unlock") : load())}
              >
                {reachedTotal ? strings.continueLabel : strings.nextPick} →
              </button>
            );
          }}
        />
      ) : (
        <div className={styles.dwellHint}>{strings.pair.loading}…</div>
      )}
    </GateOverlay>
  );
}
