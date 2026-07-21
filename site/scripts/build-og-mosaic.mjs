// Deterministic OG mosaic (v3 §4.9): composite qualified-entry thumbnails into
// a 1200×630 PNG at public/og/mosaic.png. Used by /, /gallery, and as the
// global OG fallback. Qualified-only (dev / community are not publishable OG).
//
// Runs in prebuild AFTER copy-assets (it reads the mirrored thumbs from
// public/b). Fully deterministic: configs are taken in byte order, one thumb per
// config, so the same data always yields the same mosaic.
//
// Env: WCB_DATA_ROOT (grade is read from each config.json in the data root).

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

// Repo-relative dev fallback (prod sets WCB_DATA_ROOT). Private repo ships the
// full set under data/batches-dev; the public OSS repo ships a flagship subset
// under data/batches — prefer whichever exists. scripts/ -> site/ -> repo root.
const DEV_DEFAULT_ROOT = (() => {
  const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
  const dev = path.join(repo, "data", "batches-dev");
  const pub = path.join(repo, "data", "batches");
  if (fs.existsSync(path.join(dev, "index.json"))) return dev;
  if (fs.existsSync(path.join(pub, "index.json"))) return pub;
  return dev;
})();

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SITE_DIR = path.resolve(__dirname, "..");
const PUBLIC_B = path.join(SITE_DIR, "public", "b");
const OUT_DIR = path.join(SITE_DIR, "public", "og");
const OUT = path.join(OUT_DIR, "mosaic.png");

const W = 1200;
const H = 630;
const COLS = 6;
const ROWS = 3;
const TILE_W = Math.floor(W / COLS); // 200
const TILE_H = Math.floor(H / ROWS); // 210
const BG = { r: 11, g: 14, b: 20 }; // --bg #0B0E14

function dataRoot() {
  const root = process.env.WCB_DATA_ROOT;
  if (root && root.length > 0) return path.resolve(root);
  const legacy = process.env.WCB_DATA_DIR;
  if (legacy && legacy.length > 0) return path.dirname(path.resolve(legacy));
  return DEV_DEFAULT_ROOT;
}

/** Community-first grade check (mirror of lib/channel.channelGradeOf). */
function gradeOf(transport) {
  const t = String(transport || "").toLowerCase();
  if (t.includes("community")) return "community";
  if (t.includes("sub2")) return "dev";
  return "qualified";
}

function listDirs(dir) {
  try {
    return fs
      .readdirSync(dir, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name);
  } catch {
    return null;
  }
}

/** One thumb per QUALIFIED config, byte-order deterministic. */
function collectThumbs(root) {
  const byConfig = new Map(); // configId → absolute thumb path
  const gradeCache = new Map(); // `${batch}/${config}` → grade
  const batches = (listDirs(PUBLIC_B) ?? []).sort();
  for (const batch of batches) {
    const configs = (listDirs(path.join(PUBLIC_B, batch)) ?? []).sort();
    for (const config of configs) {
      if (config === "similarity") continue;
      if (byConfig.has(config)) continue; // first (sorted) batch wins — deterministic
      // grade from the data-root config.json.
      const gk = `${batch}/${config}`;
      let grade = gradeCache.get(gk);
      if (grade === undefined) {
        try {
          const cfg = JSON.parse(
            fs.readFileSync(path.join(root, batch, "configs", config, "config.json"), "utf8"),
          );
          grade = gradeOf(cfg.transport);
        } catch {
          grade = "qualified"; // unknown → treat as qualified (config-driven, best effort)
        }
        gradeCache.set(gk, grade);
      }
      if (grade !== "qualified") continue;
      // find the byte-first thumb. NOTE the PUBLIC layout flattens the on-disk
      // "slots" segment: /b/<batch>/<config>/<vdir>/<slot>/thumb.webp.
      let picked = null;
      const cdir = path.join(PUBLIC_B, batch, config);
      for (const vdir of (listDirs(cdir) ?? []).sort()) {
        const vpath = path.join(cdir, vdir);
        const slots = (listDirs(vpath) ?? [])
          .filter((s) => /^\d+$/.test(s))
          .sort((a, b) => Number(a) - Number(b));
        for (const slot of slots) {
          const t = path.join(vpath, slot, "thumb.webp");
          if (fs.existsSync(t)) {
            picked = t;
            break;
          }
        }
        if (picked) break;
      }
      if (picked) byConfig.set(config, picked);
    }
  }
  return [...byConfig.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
    .map(([, p]) => p);
}

async function main() {
  const root = dataRoot();
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const thumbs = collectThumbs(root).slice(0, COLS * ROWS);

  const base = sharp({
    create: { width: W, height: H, channels: 3, background: BG },
  });

  const composites = [];
  for (let i = 0; i < thumbs.length; i++) {
    const col = i % COLS;
    const row = Math.floor(i / COLS);
    try {
      const tile = await sharp(thumbs[i])
        .resize(TILE_W, TILE_H, { fit: "cover", position: "top" })
        .toBuffer();
      composites.push({ input: tile, left: col * TILE_W, top: row * TILE_H });
    } catch {
      /* skip an unreadable thumb — the dark background shows through */
    }
  }

  await base.composite(composites).png().toFile(OUT);
  console.log(
    `[build-og-mosaic] wrote ${OUT} (${W}×${H}) from ${composites.length}/${thumbs.length} qualified thumb(s).`,
  );
}

main().catch((e) => {
  // Never fail the build over a decorative OG image — write a plain dark canvas
  // so the fallback URL still resolves (seed-or-degrade spirit).
  console.warn(`[build-og-mosaic] degraded (${e?.message ?? e}); writing plain canvas.`);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  sharp({ create: { width: W, height: H, channels: 3, background: BG } })
    .png()
    .toFile(OUT)
    .then(() => console.log(`[build-og-mosaic] wrote plain ${OUT}.`))
    .catch((e2) => console.error(`[build-og-mosaic] FAILED: ${e2?.message ?? e2}`));
});
