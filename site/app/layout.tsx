import "./globals.css";
import type { Metadata } from "next";
import { SITE_URL } from "@/lib/metadata";

// Root layout is a pass-through: with next-intl's `[locale]` segment the real
// <html>/<body> shell lives in app/[locale]/layout.tsx (spec §1, §6). This file
// only imports the global stylesheet once and sets the canonical metadataBase +
// title template; per-page metadata comes from lib/metadata buildMeta.
export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    template: "%s · weather-card-benchmark",
    default: "weather-card-benchmark",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return children;
}
