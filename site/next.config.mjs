import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Deploy target: a Next standalone server (Node) behind a reverse proxy. The
  // old static `output: "export"` is retired — the site now serves SSR (arena
  // votes, live registry, dynamic pages).
  output: "standalone",

  // Native modules must NOT be bundled by Next — they load from node_modules on
  // the VM (rebuilt for Linux). Bundling better-sqlite3 / sharp breaks at runtime.
  serverExternalPackages: ["better-sqlite3", "sharp"],

  // i18n/request.ts reads messages/<locale>/<ns>.json via fs at request time
  // (dynamic namespace discovery), so Next's tracer does not see them. The
  // standalone server chdir's to its own dir, so those reads resolve to
  // <standalone>/messages — force-trace the folder in so it exists there
  // (otherwise every string renders as MISSING_MESSAGE in prod). data/ (root +
  // SCHEMA + seeds) lives outside the project and ships via WCB_DATA_ROOT — see
  // DEPLOY.md; only the in-project messages/ needs tracing.
  outputFileTracingIncludes: {
    "/**": ["./messages/**/*"],
  },

  // Fail the build on type errors — the data/integrity contract must not ship broken.
  typescript: { ignoreBuildErrors: false },
  eslint: { ignoreDuringBuilds: true },

  // Retired routes fold into the unified surfaces (spec §1). Locale-prefixed
  // variants are handled by next-intl at request time; these cover the
  // canonical (en, unprefixed) paths.
  async redirects() {
    return [
      { source: "/batches", destination: "/methodology#provenance", permanent: true },
      { source: "/pair", destination: "/matrix", permanent: true },
      { source: "/live", destination: "/gallery?live=1", permanent: true },
    ];
  },

  // --- /api/om/* proxy (dev only) ---
  // Model cards fetch /api/om/{forecast,archive} (a frozen prompt contract we
  // can never change). In production Caddy reverse-proxies those paths to the
  // co-located cache-api service (same-origin). Under `next dev` there is no
  // Caddy, so proxy them to the cache-api dev service. Point WCB_CACHE_API at
  // the running cache-api (default http://localhost:8787).
  async rewrites() {
    if (process.env.NODE_ENV === "production") return [];
    const target = process.env.WCB_CACHE_API || "http://localhost:8787";
    return [{ source: "/api/om/:path*", destination: `${target}/api/om/:path*` }];
  },
};

export default withNextIntl(nextConfig);
