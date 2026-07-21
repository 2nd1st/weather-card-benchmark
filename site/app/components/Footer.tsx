import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";

// Footer (spec §5). Holds the overflow nav — Contribute · Findings (only when
// findings exist) · Methodology · GitHub — plus a natural, non-preachy tagline
// (§0.1: no self-righteous neutrality copy). Two honesty notes remain as
// methodology, not messaging: similarity is a measurement, votes are UI
// preference.

const GITHUB_URL = "https://github.com/2nd1st/weather-card-benchmark";

export function Footer({ hasFindings = false }: { hasFindings?: boolean }) {
  const t = useTranslations("common");
  return (
    <footer className="site-footer">
      <div className="wrap foot-in">
        <p>{t("footer.tagline")}</p>
        <div className="foot-links">
          <Link href="/methodology">{t("nav.methodology")}</Link>
          <Link href="/contribute">{t("nav.contribute")}</Link>
          {hasFindings ? <Link href="/findings">{t("nav.findings")}</Link> : null}
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            {t("nav.github")}
          </a>
        </div>
      </div>
    </footer>
  );
}
