"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { GradeBadge, ArmBadge } from "@/app/components/Badge";
import { ScaledExhibit } from "@/app/components/CardFrame";
import styles from "../arena.module.css";
import {
  castVote,
  bumpTally,
  type ClientPair,
  type Choice,
  type VoteResult,
} from "./voteClient";

// PairStage — the single blind-pair renderer reused by the gate, the arena hub,
// and the shared-matchup page. Model output is UNTRUSTED: each card is rendered
// verbatim as a full sandboxed srcdoc the server already built via lib/live
// (allow-scripts, opaque origin, no identity in the payload — spec §3.2/§4.0).
//
// Choice buttons stay DISABLED until BOTH iframes report load AND a 2.5s dwell
// floor has elapsed (fairness + natural dwell; the server also rejects <2.5s
// votes with 410, spec §3.2). On mobile the inline choice row is replaced by a
// sticky bottom vote bar (CSS); both live in the DOM.

export interface PairStrings {
  cardA: string;
  cardB: string;
  identityHidden: string;
  preferA: string;
  preferB: string;
  tie: string;
  tieSub: string;
  bothBad: string;
  bothBadSub: string;
  revealTitle: string;
  yourPick: string;
  tooFast: string;
  voteError: string;
  loading: string;
  /** unobtrusive chip when a card loads but paints/reports nothing (brief C1). */
  noOutput: string;
}

/** Client-only ≤700px matcher (spec §4.0 mobile gate). Reads matchMedia in the
 *  lazy initializer so the first client render is already correct (the gate
 *  PairStage only ever renders client-side, so there is no SSR mismatch). */
function useIsMobile(): boolean {
  const [m, setM] = useState<boolean>(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia("(max-width: 700px)").matches
      : false,
  );
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(max-width: 700px)");
    const on = () => setM(mq.matches);
    on();
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return m;
}

/** Compact lock mark for the "identity hidden" badge (brief C2). */
function LockIcon() {
  return (
    <svg className={styles.lockGlyph} viewBox="0 0 24 24" aria-hidden focusable="false">
      <rect x="5" y="11" width="14" height="9" rx="2" fill="currentColor" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

/** Frozen miniature of a card for the reveal rows (brief D). Reuses the blind
 *  static shot when the payload carries one (gate pairs); otherwise a tiny
 *  non-interactive content-fit ScaledExhibit of the same srcdoc. */
function CardThumb({
  card,
  label,
}: {
  card: { html: string; shotUrl?: string };
  label: string;
}) {
  return (
    <div className={styles.rthumb} aria-hidden>
      {card.shotUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img className={styles.rthumbImg} src={card.shotUrl} alt="" />
      ) : (
        <ScaledExhibit
          srcdoc={card.html || "<!doctype html><meta charset=utf-8>"}
          title={label}
          fit="content"
        />
      )}
    </div>
  );
}

function CardViewport({
  html,
  shotUrl,
  staticShot,
  label,
  hidden,
  loadingLabel,
  noOutputLabel,
  onLoaded,
  paneAspect,
  onRect,
}: {
  html: string;
  /** blind inlined shot data: URI (gate pairs only). */
  shotUrl?: string;
  /** mobile gate: render the recorded static shot instead of a live frame. */
  staticShot: boolean;
  label: string;
  hidden: string;
  loadingLabel: string;
  noOutputLabel: string;
  onLoaded: () => void;
  /** shared pane aspect (w/h) — identical both sides, adapted to the pair's content. */
  paneAspect: number;
  /** surfaces the card's reported content bbox so the pair can adapt its panes. */
  onRect?: (r: { w: number; h: number }) => void;
}) {
  const [loaded, setLoaded] = useState(false);
  const [gotRect, setGotRect] = useState(false);
  const [failed, setFailed] = useState(false);
  const reported = useRef(false);

  // count each card at most once toward the both-loaded vote gate, whether it
  // settled as a static shot or a live frame.
  const report = () => {
    setLoaded(true);
    if (!reported.current) {
      reported.current = true;
      onLoaded();
    }
  };

  const useShot = staticShot && !!shotUrl;

  // render-failed heuristic (brief C1): a LIVE frame that loaded but never
  // reported a content bbox within ~4s reads as a dead/blank card — surface an
  // unobtrusive chip so it never looks like site breakage. Static shots always
  // paint, so they never trip this; a valid bbox report clears it.
  useEffect(() => {
    if (useShot || !loaded || gotRect) return;
    const t = setTimeout(() => setFailed(true), 4000);
    return () => clearTimeout(t);
  }, [useShot, loaded, gotRect]);

  return (
    <div className={`blindframe panelbox ${styles.stageCard}`}>
      <div className="blind-tag">
        <span className="who">{label}</span>
        <span className={styles.lockBadge}>
          <LockIcon />
          {hidden}
        </span>
      </div>
      {/* Both sides render the SAME measured 1280×800 viewport (see CardFrame /
          ScaledExhibit — runner VIEWPORT parity): scaled equally to equal panes,
          so "equal viewports" now means the exact pixels each card was built for,
          not a portrait 560×720 crop. */}
      <div
        className={styles.viewport}
        // Live pairs adapt the pane to the pair's content shape (portrait cards
        // get portrait panes — no dead side gutters); static shots keep 1280/800.
        style={useShot ? undefined : { aspectRatio: String(paneAspect) }}
      >
        {!loaded ? <div className={styles.shimmer} aria-hidden /> : null}
        {useShot ? (
          // Static recorded shot (1280×800 too) — instant first paint, honest
          // same-shape both sides, non-interactive (spec §4.0). alt is the blind
          // A/B label only.
          // eslint-disable-next-line @next/next/no-img-element
          <img className={styles.shotImg} src={shotUrl} alt={label} onLoad={report} />
        ) : (
          <ScaledExhibit
            srcdoc={html || "<!doctype html><meta charset=utf-8>"}
            title={`${label} — ${loadingLabel}`}
            onLoad={report}
            // content-fit BOTH sides identically — blind-fair framing that zooms
            // each card out of its dead 1280×800 background onto the card itself.
            fit="content"
            onContentRect={(r) => {
              if (!r) return;
              setGotRect(true);
              setFailed(false);
              onRect?.(r);
            }}
          />
        )}
        {failed ? <div className={styles.noOutput}>{noOutputLabel}</div> : null}
      </div>
    </div>
  );
}

export function PairStage({
  pair,
  locale,
  strings,
  mobileStatic = false,
  onVoted,
  renderRevealActions,
}: {
  pair: ClientPair;
  locale: string;
  strings: PairStrings;
  /** gate-only (spec §4.0): on ≤700px render static shots both sides instead of
   *  live frames. Arena hub / shared matchup leave this false (live both sides). */
  mobileStatic?: boolean;
  /** parent hook (gate counts, tally) — fires once per successful vote. */
  onVoted?: (result: VoteResult) => void;
  /** parent-supplied reveal actions (next / share / compare). */
  renderRevealActions?: (result: VoteResult) => ReactNode;
}) {
  const isMobile = useIsMobile();
  // Same mode both sides: only go static when BOTH cards carry a shot.
  const bothHaveShots = pair.cards.every((c) => !!c.shotUrl);
  const staticShot = mobileStatic && isMobile && bothHaveShots;
  const [loadedCount, setLoadedCount] = useState(0);
  const [dwellReady, setDwellReady] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<VoteResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const shownAt = useRef<number>(Date.now());

  // reset on a new token (parent swaps pair for "next")
  useEffect(() => {
    setLoadedCount(0);
    setDwellReady(false);
    setSubmitting(false);
    setResult(null);
    setError(null);
    shownAt.current = Date.now();
    const t = setTimeout(() => setDwellReady(true), 2500);
    return () => clearTimeout(t);
  }, [pair.token]);

  const bothLoaded = loadedCount >= pair.cards.length;
  const canVote = bothLoaded && dwellReady && !submitting && !result;

  // Shared adaptive pane aspect: both panes take the SAME aspect (blind-fair),
  // chosen from the pair's reported content shapes — the more-landscape of the
  // two, clamped to [2:3 portrait, 16:10 landscape]. Most cards are portrait,
  // so panes start portrait-ish and only widen when a landscape card demands it.
  const [rectAspects, setRectAspects] = useState<Record<number, number>>({});
  useEffect(() => setRectAspects({}), [pair.token]);
  const paneAspect = useMemo(() => {
    const vals = Object.values(rectAspects);
    const a = vals.length ? Math.max(...vals) : 0.8;
    return Math.min(1280 / 800, Math.max(2 / 3, a));
  }, [rectAspects]);

  async function choose(choice: Choice) {
    if (!canVote) return;
    setSubmitting(true);
    setError(null);
    const latency = Date.now() - shownAt.current;
    const out = await castVote({ token: pair.token, choice, latencyMs: latency, locale });
    if (!out.ok) {
      setSubmitting(false);
      if (out.code === "too-fast") setError(strings.tooFast);
      else setError(strings.voteError);
      return;
    }
    const res: VoteResult = { reveal: out.reveal, shareUrl: out.shareUrl, choice };
    bumpTally();
    setResult(res);
    setSubmitting(false);
    onVoted?.(res);
  }

  const buttons = useMemo(
    () =>
      [
        { key: "a" as Choice, label: strings.preferA, cls: "" },
        { key: "b" as Choice, label: strings.preferB, cls: "alt" },
        { key: "tie" as Choice, label: strings.tie, sub: strings.tieSub, cls: "tie" },
        { key: "both_bad" as Choice, label: strings.bothBad, sub: strings.bothBadSub, cls: "reject" },
      ],
    [strings],
  );

  if (result) {
    const pickedIdx = result.choice === "a" ? 0 : result.choice === "b" ? 1 : -1;
    return (
      <div className={`reveal panelbox exhibit ${styles.reveal}`}>
        <div className="halo" aria-hidden />
        <div className={styles.revealHead}>
          <span className="spark">✦</span> {strings.revealTitle}
        </div>
        <div className={styles.revealRows}>
          {result.reveal.map((r, i) => (
            <div key={r.configId} className={`${styles.rrow} ${i === pickedIdx ? styles.picked : ""}`}>
              {/* keep the two cards visible at reveal — a frozen thumbnail beside
                  each identity so the pick still has a face (brief D). */}
              <CardThumb card={pair.cards[i] ?? { html: "" }} label={i === 0 ? strings.cardA : strings.cardB} />
              <span className={styles.slotLetter}>{i === 0 ? "A" : "B"}</span>
              <span className={styles.rname}>{r.label}</span>
              <ArmBadge arm={r.arm} />
              <GradeBadge grade={r.grade} />
              {i === pickedIdx ? <span className={styles.youpick}>{strings.yourPick}</span> : null}
            </div>
          ))}
        </div>
        {renderRevealActions ? (
          <div className={styles.revealActions}>{renderRevealActions(result)}</div>
        ) : null}
      </div>
    );
  }

  return (
    <div>
      <div className={styles.stage}>
        {pair.cards.map((c, i) => (
          <CardViewport
            key={`${pair.token}-${i}`}
            html={c.html}
            shotUrl={c.shotUrl}
            staticShot={staticShot}
            label={i === 0 ? strings.cardA : strings.cardB}
            hidden={strings.identityHidden}
            loadingLabel={strings.loading}
            noOutputLabel={strings.noOutput}
            onLoaded={() => setLoadedCount((n) => n + 1)}
            paneAspect={paneAspect}
            onRect={(r) => setRectAspects((prev) => ({ ...prev, [i]: r.w / r.h }))}
          />
        ))}
      </div>

      {/* desktop inline choices — four equal-width controls (brief C3) */}
      <div className={styles.choicesInline}>
        {buttons.map((b) => (
          <button
            key={b.key}
            className={`choice ${b.cls}`}
            disabled={!canVote}
            onClick={() => choose(b.key)}
          >
            {b.label}
            {b.sub ? <small>{b.sub}</small> : null}
          </button>
        ))}
      </div>

      {/* mobile sticky vote bar — contained 2×2 grid (brief A) */}
      <div className={styles.stickyBar}>
        {buttons.map((b) => (
          <button
            key={b.key}
            className={b.cls === "alt" ? "alt" : b.cls === "mute" ? "mute" : undefined}
            disabled={!canVote}
            onClick={() => choose(b.key)}
          >
            {b.label}
          </button>
        ))}
      </div>

      {error ? <div className={styles.notice}>{error}</div> : null}
    </div>
  );
}
