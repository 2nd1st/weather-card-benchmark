import { defineRouting } from "next-intl/routing";

// Locales: en (default, unprefixed) + zh (`/zh/*`). `localePrefix: "as-needed"`
// keeps the English URLs clean while giving Chinese a stable prefix. Every
// route lives under `app/[locale]/` and inherits this config (spec §1, §6).
export const routing = defineRouting({
  locales: ["en", "zh"],
  defaultLocale: "en",
  localePrefix: "as-needed",
});

export type Locale = (typeof routing.locales)[number];
