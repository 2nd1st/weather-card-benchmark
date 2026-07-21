import type { ReactNode } from "react";
import { notFound } from "next/navigation";
import { NextIntlClientProvider, hasLocale } from "next-intl";
import { setRequestLocale, getMessages } from "next-intl/server";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { routing } from "@/i18n/routing";
import { TopNav } from "@/app/components/TopNav";
import { Footer } from "@/app/components/Footer";

// Locale shell (spec §1, §4.0, §5, §6): the dark-instrument frame every page
// inherits — atmosphere field, TopNav, Footer — plus the pre-hydration
// anti-flicker gate stamp. The stamp reads localStorage before paint and marks
// <html data-gate>, so the gate OVERLAY (never a redirect) shows or stays hidden
// with no flash; localStorage unavailable → fail open (data-gate="done").

// Pre-hydration inline script (spec §4.0). Kept tiny; runs during HTML parse.
const GATE_STAMP =
  "try{var v=localStorage.getItem('wcb.gate.v1');" +
  "document.documentElement.setAttribute('data-gate',v?'done':'pending');}" +
  "catch(e){document.documentElement.setAttribute('data-gate','done');}";

/**
 * Findings nav item renders only when `data/findings.json` has ≥1 entry
 * (spec §5, §7.8 seed-or-degrade). Resolved defensively — absence or any read
 * error hides the item rather than crashing the shell.
 */
function readHasFindings(): boolean {
  const candidates: string[] = [];
  const root = process.env.WCB_DATA_ROOT;
  if (root) candidates.push(path.join(path.dirname(root), "findings.json"));
  candidates.push(path.join(process.cwd(), "..", "data", "findings.json"));
  candidates.push(path.join(process.cwd(), "data", "findings.json"));
  for (const p of candidates) {
    try {
      if (!existsSync(p)) continue;
      const parsed = JSON.parse(readFileSync(p, "utf8"));
      const list = Array.isArray(parsed) ? parsed : parsed?.findings;
      if (Array.isArray(list) && list.length > 0) return true;
    } catch {
      // ignore and try the next candidate / degrade to hidden
    }
  }
  return false;
}

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();

  // Enable static rendering for this locale segment.
  setRequestLocale(locale);
  const messages = await getMessages();
  const hasFindings = readHasFindings();

  return (
    <html lang={locale} suppressHydrationWarning>
      <body>
        <script dangerouslySetInnerHTML={{ __html: GATE_STAMP }} />
        <div className="atmo" aria-hidden />
        <NextIntlClientProvider messages={messages}>
          <TopNav hasFindings={hasFindings} />
          <main className="site-main">{children}</main>
          <Footer hasFindings={hasFindings} />
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
