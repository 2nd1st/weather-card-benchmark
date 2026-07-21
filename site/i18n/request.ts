import { getRequestConfig } from "next-intl/server";
import { hasLocale } from "next-intl";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { routing, type Locale } from "./routing";

// Merge every `messages/<locale>/<namespace>.json` file into one keyed object
// (spec §6). Each page agent adds its own namespace file (home, gallery, …);
// this loader discovers them at request time so a new namespace needs no wiring.
// Missing / malformed files are skipped rather than crashing the request — the
// site must render sensibly with partial data (non-negotiable #4).
const MESSAGES_DIR = path.join(process.cwd(), "messages");

function loadMessages(locale: Locale): Record<string, unknown> {
  const merged: Record<string, unknown> = {};
  const dir = path.join(MESSAGES_DIR, locale);
  let files: string[];
  try {
    files = readdirSync(dir);
  } catch {
    return merged;
  }
  for (const file of files) {
    if (!file.endsWith(".json")) continue;
    const ns = file.slice(0, -".json".length);
    try {
      const raw = readFileSync(path.join(dir, file), "utf8");
      merged[ns] = JSON.parse(raw);
    } catch {
      // Skip a broken namespace file rather than blanking the whole locale.
    }
  }
  return merged;
}

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale: Locale = hasLocale(routing.locales, requested)
    ? requested
    : routing.defaultLocale;

  // Fall back to the default locale's copy for any namespace the requested
  // locale has not translated yet (en is always first-authored, spec §6).
  const fallback =
    locale === routing.defaultLocale ? {} : loadMessages(routing.defaultLocale);
  const messages = { ...fallback, ...loadMessages(locale) };

  return { locale, messages };
});
