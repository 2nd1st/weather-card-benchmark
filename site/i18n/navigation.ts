import { createNavigation } from "next-intl/navigation";
import { routing } from "./routing";

// Locale-aware navigation primitives. `Link`, `useRouter`, `usePathname`,
// `redirect`, `getPathname` all preserve the active locale automatically —
// components never hand-build `/zh/...` prefixes (spec §6).
export const { Link, redirect, usePathname, useRouter, getPathname } =
  createNavigation(routing);
