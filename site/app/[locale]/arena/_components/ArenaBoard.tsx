"use client";

import { useCallback, useEffect, useState } from "react";
import { useLocale } from "next-intl";
import { Link } from "@/i18n/navigation";
import { EmptyState } from "@/app/components/EmptyState";
import { AttributionBar } from "@/app/components/AttributionBar";
import { PairStage, type PairStrings } from "./PairStage";
import { Leaderboard, type LabelInfo, type LeaderboardStrings } from "./Leaderboard";
import styles from "../arena.module.css";
import {
  fetchPair,
  createShare,
  absoluteUrl,
  xIntentUrl,
  getTally,
  type ClientPair,
  type VoteResult,
} from "./voteClient";

// Arena hub (spec §4 '/arena'). Pair mode only in v1 (quad cut, §8). Angle
// picker overall/visual/clarity drives BOTH the blind pair and the leaderboard.
// The reveal is the single highest-share-intent moment — it ships with a share
// surface (share-matchup permalink + X intent) plus open-in-compare.

const ANGLES = ["overall", "visual", "clarity"] as const;
type Angle = (typeof ANGLES)[number];

export interface ArenaStrings {
  angleLabels: Record<Angle, string>;
  metaVariant: string;
  metaCity: string;
  metaFixture: string;
  metaIdentical: string;
  tally: string; // "you've cast {n} choices"
  pair: PairStrings;
  reveal: {
    next: string;
    share: string;
    compare: string;
  };
  share: {
    permalinkLabel: string;
    copy: string;
    copied: string;
    x: string;
    text: string; // template with {url}
  };
  unavailableTitle: string;
  unavailableMsg: string;
  faultTitle: string;
  faultMsg: string;
  retry: string;
  leaderboard: LeaderboardStrings;
  attributionExtra: string;
}

/** A pair-fetch failure that is OUR fault, not a property of the pool.
 *  The API returns 503 + a named reason (pool-too-small) for genuine
 *  unavailability; anything else reaching the client is a 500 ("error") or a
 *  dead transport ("network"). Allow-list the data reasons so a NEW server-side
 *  failure code defaults to "fault" rather than silently claiming empty data. */
const POOL_REASONS = new Set(["pool-too-small", "share-not-found", "bad-share"]);

function isFault(code: string): boolean {
  return !POOL_REASONS.has(code);
}

export function ArenaBoard({
  labels,
  strings,
}: {
  labels: Record<string, LabelInfo>;
  strings: ArenaStrings;
}) {
  const locale = useLocale();
  const nf = new Intl.NumberFormat(locale);
  const [angle, setAngle] = useState<Angle>("overall");
  const [pair, setPair] = useState<ClientPair | null>(null);
  const [errCode, setErrCode] = useState<string | null>(null);
  const [tally, setTally] = useState(0);
  const [share, setShare] = useState<{ url: string; x: string; copied: boolean } | null>(null);

  useEffect(() => setTally(getTally()), []);

  const load = useCallback(
    async (a: Angle) => {
      setPair(null);
      setErrCode(null);
      setShare(null);
      const r = await fetchPair(a, "arena");
      if (r.ok) setPair(r.pair);
      else setErrCode(r.code);
    },
    [],
  );

  useEffect(() => {
    load(angle);
  }, [angle, load]);

  const onVoted = useCallback(() => setTally(getTally()), []);

  async function onShare(token: string) {
    const r = await createShare(token);
    if (!r) return;
    const url = absoluteUrl(r.url);
    setShare({ url, x: xIntentUrl(strings.share.text.replace("{url}", url)), copied: false });
  }

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setShare((s) => (s ? { ...s, copied: true } : s));
    } catch {
      /* ignore */
    }
  }

  function revealActions(result: VoteResult) {
    const a = result.reveal[0]?.configId;
    const b = result.reveal[1]?.configId;
    const city = pair?.city ?? "";
    const compareHref =
      a && b ? `/compare?ids=${encodeURIComponent(a)},${encodeURIComponent(b)}&city=${encodeURIComponent(city)}` : "/compare";
    return (
      <>
        <button className="btn btn-hero" onClick={() => load(angle)}>
          {strings.reveal.next} →
        </button>
        {pair ? (
          <button className="btn btn-violet" onClick={() => onShare(pair.token)}>
            {strings.reveal.share}
          </button>
        ) : null}
        <Link className="btn" href={compareHref}>
          {strings.reveal.compare}
        </Link>
        {share ? (
          <div className={`${styles.shareSheet}`} style={{ flexBasis: "100%" }}>
            <div className={styles.shareUrlRow}>
              <span className={styles.shareUrl}>{share.url}</span>
              <button className="btn btn-ghost" onClick={() => copy(share.url)}>
                {share.copied ? strings.share.copied : strings.share.copy}
              </button>
              <a className="btn btn-violet" href={share.x} target="_blank" rel="noopener noreferrer">
                {strings.share.x}
              </a>
            </div>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <>
      <div className={styles.arenaHead}>
        <div className={styles.arenaHeadControls}>
          <div className="seg violet" role="group" aria-label="angle">
            {ANGLES.map((a) => (
              <button key={a} className={angle === a ? "on" : undefined} onClick={() => setAngle(a)}>
                {strings.angleLabels[a]}
              </button>
            ))}
          </div>
        </div>
        <div className={styles.tally}>{strings.tally.replace("{n}", nf.format(tally))}</div>
      </div>

      {pair ? (
        <div className="gate-meta" style={{ marginBottom: 14 }}>
          <span>
            {strings.metaVariant} <b>{pair.variant}</b>
          </span>
          <span>
            {strings.metaCity} <b>{pair.city}</b>
          </span>
          <span>
            {strings.metaFixture} <b>{pair.date}</b>
          </span>
          <span>{strings.metaIdentical}</span>
        </div>
      ) : null}

      {errCode ? (
        // Two very different things used to share one message. A 503 from the
        // API carries a real reason code (pool-too-small) and means the data is
        // thin; "error"/"network" mean the SERVER or the transport failed and
        // says nothing about the pool. Claiming "no qualified cards yet" for the
        // latter sent a real outage looking like a data gap (2026-08-04: a
        // Mach-O better_sqlite3.node made every /api/arena/* 500 while the pool
        // was full). Never explain a fault as a data state.
        isFault(errCode) ? (
          <EmptyState
            title={strings.faultTitle}
            message={strings.faultMsg}
            icon="!"
            action={
              <button className="btn" onClick={() => load(angle)}>
                {strings.retry}
              </button>
            }
          />
        ) : (
          <EmptyState title={strings.unavailableTitle} message={strings.unavailableMsg} icon="◌" />
        )
      ) : pair ? (
        <div className="exhibit">
          <PairStage
            key={pair.token}
            pair={pair}
            locale={locale}
            strings={strings.pair}
            onVoted={onVoted}
            renderRevealActions={revealActions}
          />
          <AttributionBar extra={strings.attributionExtra} />
        </div>
      ) : (
        <div className={styles.dwellHint}>{strings.pair.loading}…</div>
      )}

      <Leaderboard angle={angle} labels={labels} strings={strings.leaderboard} />
    </>
  );
}
