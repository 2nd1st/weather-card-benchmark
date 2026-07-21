// Minimal ESM resolve hook so a plain `node` process can import the site's
// TypeScript lib modules directly (Node v26 strips TS types natively; it just
// won't guess extensions or the `@/` alias). Used only by arena-selftest.mjs.
import { pathToFileURL } from "node:url";
import path from "node:path";

const SITE = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");

export async function resolve(specifier, context, next) {
  // `@/` alias -> site root
  if (specifier.startsWith("@/")) {
    specifier = pathToFileURL(path.join(SITE, specifier.slice(2))).href;
  }
  // Try the specifier as-is first.
  try {
    return await next(specifier, context);
  } catch (e) {
    // Retry extensionless relative/aliased specifiers with common suffixes.
    const isPathish = specifier.startsWith(".") || specifier.startsWith("file:");
    if (!isPathish) throw e;
    for (const suffix of [".ts", ".js", ".mjs", "/index.ts", "/index.js"]) {
      try {
        return await next(specifier + suffix, context);
      } catch {
        /* keep trying */
      }
    }
    throw e;
  }
}
