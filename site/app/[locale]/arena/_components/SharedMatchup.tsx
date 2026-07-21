"use client";

import { useLocale } from "next-intl";
import { Link } from "@/i18n/navigation";
import { AttributionBar } from "@/app/components/AttributionBar";
import { PairStage, type PairStrings } from "./PairStage";
import styles from "../arena.module.css";
import { absoluteUrl, xIntentUrl, type ClientPair, type VoteResult } from "./voteClient";

// Shared-matchup client (spec §4 '/arena/m/[id]'). The server has already issued
// a FRESH blind pair from the share's refs (source='share'); the recipient sees
// the same matchup BLIND, votes, gets the reveal, then is invited into /arena.
// Never gated. No "next pair" — a shared link is one matchup.

export interface SharedStrings {
  toArena: string;
  compare: string;
  shareAgain: string;
  copy: string;
  copied: string;
  x: string;
  shareText: string; // template with {url}
  pair: PairStrings;
  attributionExtra: string;
}

export function SharedMatchup({
  pair,
  shareId,
  strings,
}: {
  pair: ClientPair;
  shareId: string;
  strings: SharedStrings;
}) {
  const locale = useLocale();
  const permalink = absoluteUrl(`/arena/m/${shareId}`);

  function revealActions(result: VoteResult) {
    const a = result.reveal[0]?.configId;
    const b = result.reveal[1]?.configId;
    const compareHref =
      a && b
        ? `/compare?ids=${encodeURIComponent(a)},${encodeURIComponent(b)}&city=${encodeURIComponent(pair.city)}`
        : "/compare";
    return (
      <>
        <Link className="btn btn-hero" href="/arena">
          {strings.toArena} →
        </Link>
        <Link className="btn" href={compareHref}>
          {strings.compare}
        </Link>
        <a
          className="btn btn-violet"
          href={xIntentUrl(strings.shareText.replace("{url}", permalink))}
          target="_blank"
          rel="noopener noreferrer"
        >
          {strings.x}
        </a>
      </>
    );
  }

  return (
    <div className="exhibit">
      <PairStage
        key={pair.token}
        pair={pair}
        locale={locale}
        strings={strings.pair}
        renderRevealActions={revealActions}
      />
      <AttributionBar extra={strings.attributionExtra} />
    </div>
  );
}
