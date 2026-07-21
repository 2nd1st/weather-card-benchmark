import createMiddleware from "next-intl/middleware";
import { routing } from "./i18n/routing";

// next-intl locale middleware (spec §1). The matcher is PINNED so the locale
// layer never intercepts API routes, the /b/* copied assets, /og/* static
// images, /downloads/* data packs, or file requests. File detection is
// EXTENSION-ANCHORED (`\.ext$`), NOT "any dot in the path": config ids contain
// dots (gpt-5.4, grok-4.5, qwen3.7-max …) and a bare `.*\..*` exclusion made
// every /card/<dotted-id> bypass the locale layer and 404. Do not loosen the
// api/b/og exclusions — the middleware rewriting /api/* breaks the arena.
export default createMiddleware(routing);

export const config = {
  matcher: [
    "/((?!api|b/|og/|downloads/|_next|.*\\.(?:ico|svg|png|jpe?g|webp|gif|css|js|mjs|txt|xml|json|map|woff2?)$).*)",
  ],
};
