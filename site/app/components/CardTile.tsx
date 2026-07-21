import type { ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import { ArmBadge, GradeBadge, PatchedBadge, type Grade } from "./Badge";

// CardTile (spec §5, §4.9 gallery). A gallery exhibit: thumb (frozen shot.webp)
// or an injected live frame, the instrument label (model @ effort · arm · grade
// · patched), and per-variant valid counts. Click → /card/<configId> (locale
// preserved via next-intl Link). Props are structural — decoupled from the
// registry so this stays the single source of shell truth.

export interface VariantCount {
  /** variant token, e.g. "P-min" / "P-q" (stays English). */
  variant: string;
  valid: number;
  total: number;
}

function countClass(c: VariantCount): string {
  if (c.total > 0 && c.valid === 0) return "bad";
  if (c.valid < c.total) return "part";
  return "ok";
}

export function CardTile({
  configId,
  modelId,
  effort,
  arm,
  grade,
  unreviewed,
  patched,
  thumbUrl,
  live,
  counts,
  liveLabel = "live",
  media,
  suppressQualifiedBadge,
  blankLabel,
}: {
  configId: string;
  modelId: string;
  effort?: string | null;
  arm: string;
  grade: Grade;
  unreviewed?: boolean;
  patched?: boolean;
  thumbUrl?: string | null;
  /** show the "live" pill (a live frame is/became active for this tile). */
  live?: boolean;
  counts?: VariantCount[];
  liveLabel?: string;
  /** optional live media node (a CardFrame) rendered in place of the thumb. */
  media?: ReactNode;
  /** hide the redundant "qualified" grade badge when the surface guarantees it
   *  (gallery default view). community/dev grades always keep their badge, so an
   *  un-badged tile reads as "qualified" — the baseline (audit §2). */
  suppressQualifiedBadge?: boolean;
  /** accessible label for the empty-media placeholder (no thumb + no live). */
  blankLabel?: string;
}) {
  // Effort lives ONCE, on the identity line (`model @ effort`); the duplicate
  // effort pill in the badge row is dropped (audit §2). The grade badge is kept
  // only for the meaningful exceptions (community / dev / unreviewed) when the
  // qualified baseline is suppressed.
  const showGrade = !(
    suppressQualifiedBadge &&
    grade === "qualified" &&
    !unreviewed
  );
  return (
    <Link className="tile" href={`/card/${configId}`}>
      <div className="thumb">
        {live ? <span className="tile-live">{liveLabel}</span> : null}
        {media ? (
          media
        ) : thumbUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={thumbUrl} alt="" loading="lazy" />
        ) : (
          // blank-but-valid: no fake error, just a framed placeholder so an
          // empty render still reads as an intentional panel (audit §6).
          <span className="tile-blank" aria-hidden title={blankLabel}>
            ◫
          </span>
        )}
      </div>
      <div className="tlabel">
        <div className="tname">
          <span className="txt">{modelId}</span>
          {effort ? <span className="eff">@ {effort}</span> : null}
        </div>
        <div className="trow">
          <ArmBadge arm={arm} />
          {showGrade ? <GradeBadge grade={grade} unreviewed={unreviewed} /> : null}
          {patched ? <PatchedBadge /> : null}
        </div>
        {counts && counts.length > 0 ? (
          <div className="tcounts">
            {counts.map((c) => (
              <span key={c.variant}>
                {c.variant}{" "}
                <span className={countClass(c)}>
                  {c.valid}/{c.total}
                </span>
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </Link>
  );
}
