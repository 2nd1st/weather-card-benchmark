import { useTranslations } from "next-intl";
import { SITE_URL } from "@/lib/metadata";
import type { ReactNode } from "react";

// AttributionBar (spec §4.9). A slim mono line rendered INSIDE the
// screenshot-likely bounds of compare panels, arena stats, progress board,
// model chart, and the card exhibit frame — so the canonical URL and the
// "same prompt · same data" framing survive into wild screenshots (page copy
// can't ride along in a screenshot; pixels can).

const DOMAIN = SITE_URL.replace(/^https?:\/\//, "");

export function AttributionBar({ extra }: { extra?: ReactNode }) {
  const t = useTranslations("common");
  return (
    <div className="attribution" aria-hidden>
      <b>{DOMAIN}</b>
      <span className="dot">·</span>
      <span>{t("attribution.tagline")}</span>
      {extra ? (
        <>
          <span className="dot">·</span>
          <span>{extra}</span>
        </>
      ) : null}
    </div>
  );
}
