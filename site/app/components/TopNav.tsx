"use client";

import { Suspense } from "react";
import { useTranslations } from "next-intl";
import { Link, usePathname } from "@/i18n/navigation";
import { LocaleSwitch } from "./LocaleSwitch";
import styles from "./TopNav.module.css";

// TopNav (spec §5 navigation — enumerated, never improvise). Primary row is
// EXACTLY 5: Gallery · Arena · Compare · Matrix · Progress. Overflow lives in a
// single "more" disclosure: Contribute · Findings (only when findings exist) ·
// Methodology · GitHub. /card and /model are leaf pages, never in nav.
//
// ≤700px: the primary row + "more" + gh collapse into ONE menu disclosure so the
// header is only brand + locale + menu button (never clips past a 320–390px
// gutter). The desktop layout is unchanged — the mobile menu is display:none
// above 700px and the desktop links are display:none below it.

const GITHUB_URL = "https://github.com/2nd1st/weather-card-benchmark";

const PRIMARY = [
  { seg: "gallery", key: "nav.gallery" },
  { seg: "arena", key: "nav.arena" },
  // the community ranking sits next to the arena that produces it
  { seg: "rank", key: "nav.rank" },
  { seg: "compare", key: "nav.compare" },
  { seg: "matrix", key: "nav.matrix" },
  { seg: "progress", key: "nav.progress" },
] as const;

// Segments that live under the "more" disclosure — used to light "more" active
// when the current page is one of them (spec §5, brief B).
const MORE_SEGS = new Set(["contribute", "findings", "methodology"]);

function GitHubMark() {
  // GitHub octocat mark (recognizable at 14px) + a compact label, replacing the
  // cryptic "gh". fill: currentColor so it inherits the nav ink tokens.
  return (
    <span className={styles.ghMark}>
      <svg viewBox="0 0 16 16" aria-hidden focusable="false">
        <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z" />
      </svg>
      GitHub
    </span>
  );
}

function MenuIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden focusable="false">
      <path d="M3 6h14M3 10h14M3 14h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" fill="none" />
    </svg>
  );
}

export function TopNav({ hasFindings = false }: { hasFindings?: boolean }) {
  const t = useTranslations("common");
  const pathname = usePathname(); // locale-agnostic, e.g. "/gallery"
  const topSeg = pathname.split("/").filter(Boolean)[0] ?? "";
  const moreActive = MORE_SEGS.has(topSeg);

  const overflowLinks = (
    <>
      <Link href="/contribute" className={topSeg === "contribute" ? "on" : undefined}>
        {t("nav.contribute")}
      </Link>
      {hasFindings ? (
        <Link href="/findings" className={topSeg === "findings" ? "on" : undefined}>
          {t("nav.findings")}
        </Link>
      ) : null}
      <Link href="/methodology" className={topSeg === "methodology" ? "on" : undefined}>
        {t("nav.methodology")}
      </Link>
      <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
        {t("nav.github")}
      </a>
    </>
  );

  return (
    <nav className="site-nav" aria-label="primary">
      <div className="wrap">
        <Link className="brand" href="/">
          <span className="brand-dot" aria-hidden />
          weather-card-benchmark
        </Link>

        <div className={`nav-links ${styles.desktopLinks}`}>
          {PRIMARY.map((item) => (
            <Link
              key={item.seg}
              href={`/${item.seg}`}
              className={topSeg === item.seg ? "on" : undefined}
              aria-current={topSeg === item.seg ? "page" : undefined}
            >
              {t(item.key)}
            </Link>
          ))}
        </div>

        <div className={`nav-side ${styles.side}`}>
          <details
            className={`nav-more ${styles.moreDetails} ${styles.desktopMore} ${moreActive ? styles.moreOn : ""}`}
          >
            <summary aria-current={moreActive ? "true" : undefined}>
              {t("nav.more")}
              <span className={styles.chevron} aria-hidden>
                ▾
              </span>
            </summary>
            <div className="nav-more-menu">{overflowLinks}</div>
          </details>

          {/* LocaleSwitch reads useSearchParams (to preserve query on locale
              switch); on statically-prerendered routes (revalidate) that needs
              a Suspense boundary or the export bails out (missing-suspense). */}
          <Suspense fallback={<div className="loc" aria-hidden />}>
            <LocaleSwitch />
          </Suspense>

          <a
            className={`nav-gh ${styles.desktopGh}`}
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="GitHub"
          >
            <GitHubMark />
          </a>

          {/* ≤700px only: single disclosure holding the whole nav. */}
          <details className={styles.mobileMenu}>
            <summary aria-label={t("nav.more")}>
              <MenuIcon />
            </summary>
            <div className={styles.menuPanel}>
              {PRIMARY.map((item) => (
                <Link
                  key={item.seg}
                  href={`/${item.seg}`}
                  className={topSeg === item.seg ? "on" : undefined}
                  aria-current={topSeg === item.seg ? "page" : undefined}
                >
                  {t(item.key)}
                </Link>
              ))}
              <div className={styles.menuSep} aria-hidden />
              {overflowLinks}
            </div>
          </details>
        </div>
      </div>
    </nav>
  );
}
