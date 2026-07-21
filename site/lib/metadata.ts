import type { Metadata } from "next";

// Canonical share/OG metadata builder (spec §4.9). Every page routes its
// <head> through buildMeta so the site accumulates search weight + link caches
// on ONE domain and unfurls cleanly on social. This is A2's sole lib/ carve-out.

/** Primary canonical domain. Override per-deploy with NEXT_PUBLIC_SITE_URL. */
export const SITE_URL =
  process.env.NEXT_PUBLIC_SITE_URL ?? "https://weathercard.secondfirst.ai";
export const SITE_NAME = "weather-card-benchmark";
export const DEFAULT_OG_IMAGE = "/og/mosaic.png";
const OG_W = 1200;
const OG_H = 630;

export type MetaLocale = "en" | "zh";

export interface BuildMetaInput {
  title: string;
  description: string;
  /** absolute or root-relative image URL; defaults to the branded mosaic. */
  image?: string;
  /** locale-agnostic pathname, e.g. "/gallery" or "/card/abc". Always leading-slash. */
  path: string;
  locale: MetaLocale;
}

/** en is unprefixed, zh lives under /zh (localePrefix "as-needed", spec §1). */
function localizedPath(path: string, locale: MetaLocale): string {
  const clean = path === "" ? "/" : path.startsWith("/") ? path : `/${path}`;
  if (locale === "en") return clean;
  return clean === "/" ? "/zh" : `/zh${clean}`;
}

function abs(url: string): string {
  return url.startsWith("http") ? url : `${SITE_URL}${url.startsWith("/") ? "" : "/"}${url}`;
}

// The root layout title template ("%s · SITE_NAME", app/layout.tsx) appends the
// site name to EVERY document <title>. Some page titles already embed it — the
// i18n meta.title strings carry a trailing "— weather-card-benchmark", and
// /progress + /contribute append "· weather-card-benchmark" in-page — so the
// suffix rendered twice. Strip any trailing "<sep> SITE_NAME" here so the
// template adds it exactly once, and og/twitter read the clean page name
// (openGraph.siteName already carries the brand separately).
const SITE_SUFFIX_RE = new RegExp(
  `\\s*[—–·|]\\s*${SITE_NAME.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`,
);
function stripSiteName(title: string): string {
  const bare = title.replace(SITE_SUFFIX_RE, "").trim();
  return bare || title;
}

export function buildMeta({
  title,
  description,
  image,
  path,
  locale,
}: BuildMetaInput): Metadata {
  const enUrl = abs(localizedPath(path, "en"));
  const zhUrl = abs(localizedPath(path, "zh"));
  const canonical = locale === "en" ? enUrl : zhUrl;
  const imageUrl = abs(image ?? DEFAULT_OG_IMAGE);
  const ogLocale = locale === "zh" ? "zh_CN" : "en_US";
  // Clean page name; the layout template re-appends the site name exactly once.
  const pageTitle = stripSiteName(title);

  return {
    metadataBase: new URL(SITE_URL),
    title: pageTitle,
    description,
    alternates: {
      canonical,
      languages: {
        en: enUrl,
        "zh-CN": zhUrl,
        "x-default": enUrl,
      },
    },
    openGraph: {
      type: "website",
      siteName: SITE_NAME,
      title: pageTitle,
      description,
      url: canonical,
      locale: ogLocale,
      images: [{ url: imageUrl, width: OG_W, height: OG_H, alt: pageTitle }],
    },
    twitter: {
      card: "summary_large_image",
      title: pageTitle,
      description,
      images: [imageUrl],
    },
  };
}
