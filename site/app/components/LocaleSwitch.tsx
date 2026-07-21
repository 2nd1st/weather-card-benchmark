"use client";

import { useLocale, useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { Link, usePathname } from "@/i18n/navigation";
import { routing } from "@/i18n/routing";

// LocaleSwitch (spec §6). Preserves the pathname AND the full query string — a
// filtered gallery (`?family=gpt&live=1`) must survive the language switch. The
// next-intl <Link> with an explicit `locale` prop re-emits the same
// locale-agnostic path under the target locale prefix; we re-attach the current
// search params so filter/URL state round-trips.

const SHORT: Record<string, string> = { en: "EN", zh: "中文" };

export function LocaleSwitch() {
  const active = useLocale();
  const t = useTranslations("common");
  const pathname = usePathname(); // locale-agnostic
  const searchParams = useSearchParams();
  const qs = searchParams.toString();
  const href = qs ? `${pathname}?${qs}` : pathname;

  return (
    <div className="loc" role="group" aria-label={t("locale.switchLabel")}>
      {routing.locales.map((loc) => (
        <Link
          key={loc}
          href={href}
          locale={loc}
          className={loc === active ? "on" : undefined}
          aria-current={loc === active ? "true" : undefined}
        >
          {SHORT[loc] ?? loc}
        </Link>
      ))}
    </div>
  );
}
