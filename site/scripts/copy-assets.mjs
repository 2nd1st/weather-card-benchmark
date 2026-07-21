// Build-time asset copy (v3 §2.5/§10 A1): mirror each slot's shot.webp /
// thumb.webp / card.html (+ patch files) and the similarity JSON out of the
// (off-site, huge) data batch dirs into site/public/b/... so the standalone
// server publishes them at /b/<batchId>/<configId>/<variantDir>/<slot>/...
//
// URL scheme MUST match lib/paths.ts (that TS module can't be imported here).
// Runs on `predev` and `prebuild` (build-og-mosaic runs after this).
//
// v3 changes:
//   - SKIP merged_into batches. The registry dedupes to non-merged winners;
//     a merged batch's duplicate identity-bearing HTML must NOT ship.
//   - Mirror the patch layer: patch.json + card.patched.html + patch.diff.
//   - INCREMENTAL copy (mtime/size) instead of rm-rf + full recopy; prune only
//     public/b/<batchId> dirs no longer in the ship set.
//
// Env: WCB_DATA_ROOT = the data root (index.json + batch dirs). Legacy
//   WCB_DATA_DIR (a batch dir) → its parent is the root.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

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
const REPO_DIR = path.resolve(SITE_DIR, "..");
// The two prompts every card was generated from. Their sha256 already travels in
// each manifest; mirroring the TEXT lets the site show what the models were
// actually asked (and lets a reader download it verbatim).
// Prefer the stable public prompts/ dir; fall back to the historical trial dir.
const PROMPT_SRC_DIR = fs.existsSync(
  path.join(REPO_DIR, "prompts", "prompt-v5-min.txt"),
)
  ? path.join(REPO_DIR, "prompts")
  : path.join(REPO_DIR, "trial-20260715");
const PROMPT_FILES = ["prompt-v5-min.txt", "prompt-v5-q.txt"];
const PUBLIC_PROMPTS = path.join(SITE_DIR, "public", "prompts");

const ASSET_FILES = ["shot.webp", "thumb.webp", "card.html"];
// Patch layer (v3 §2.3) — mirrored so the card page's original/patched toggle
// and the methodology auto-list resolve from public URLs.
const PATCH_FILES = ["patch.json", "card.patched.html", "patch.diff"];
const VARIANT_DIRS = ["min", "q"];

function dataRoot() {
  const root = process.env.WCB_DATA_ROOT;
  if (root && root.length > 0) return path.resolve(root);
  const legacy = process.env.WCB_DATA_DIR;
  if (legacy && legacy.length > 0) return path.dirname(path.resolve(legacy));
  return DEV_DEFAULT_ROOT;
}

/** Copy src → dst only when dst is missing or differs in size / is older. */
function copyIfChanged(src, dst) {
  let sstat;
  try {
    sstat = fs.statSync(src);
  } catch {
    return false;
  }
  try {
    const dstat = fs.statSync(dst);
    if (dstat.size === sstat.size && dstat.mtimeMs >= sstat.mtimeMs) return false;
  } catch {
    /* dst missing → copy */
  }
  fs.mkdirSync(path.dirname(dst), { recursive: true });
  fs.copyFileSync(src, dst);
  return true;
}

/**
 * Emit a COMPACT twin of a summary--<v>.json for the client matrix.
 *
 * The verbose summary repeats each ~50-char config_id twice per cell across
 * hundreds of thousands of cells (the 192-config unified summary is ~95MB /
 * ~10MB gzip); the browser must download and parse all of it just to draw one
 * channel's heatmap. The compact twin indexes config_id + channel to integers
 * and stores each cell as a numeric tuple, cutting the payload ~6-8× and making
 * JSON.parse far cheaper. The verbose file is untouched (server-side loaders +
 * schema validation still use it). Regenerated only when the source is newer.
 *
 *   cell tuple = [channelIdx, aIdx, bIdx, median, p25, p75, n_eff, m_a, m_b]
 *   kind is derived client-side (aIdx === bIdx → "self", else "cross").
 */
function writeCompactSummary(srcPath, dstPath) {
  try {
    const s = fs.statSync(srcPath);
    const d = fs.statSync(dstPath);
    if (d.mtimeMs >= s.mtimeMs) return false;
  } catch {
    /* dst missing → build */
  }
  let summ;
  try {
    summ = JSON.parse(fs.readFileSync(srcPath, "utf8"));
  } catch {
    return false;
  }
  const cells = summ.cells ?? [];
  const configs =
    summ.configs ?? [...new Set(cells.flatMap((c) => [c.config_a, c.config_b]))];
  const channels = summ.channels ?? [...new Set(cells.map((c) => c.channel))];
  const chIdx = new Map(channels.map((c, i) => [c, i]));
  const cfgIdx = new Map(configs.map((c, i) => [c, i]));
  // Similarity values only feed a [0,1] color ramp + a short tooltip readout, so
  // 4 decimals is lossless for display and lets the floats gzip far better
  // (verbose ~10MB gzip → compact ~3MB gzip on the 192-config unified summary).
  const r4 = (x) => (x == null ? null : Math.round(x * 1e4) / 1e4);
  const packed = cells.map((c) => [
    chIdx.get(c.channel),
    cfgIdx.get(c.config_a),
    cfgIdx.get(c.config_b),
    r4(c.median),
    c.iqr ? r4(c.iqr.p25) : null,
    c.iqr ? r4(c.iqr.p75) : null,
    c.n_eff,
    c.m_a,
    c.m_b,
  ]);
  fs.mkdirSync(path.dirname(dstPath), { recursive: true });
  fs.writeFileSync(
    dstPath,
    JSON.stringify({
      batch_id: summ.batch_id,
      variant: summ.variant,
      configs,
      channels,
      cells: packed,
    }),
  );
  return true;
}

/** Readable batches on disk (manifest present), with their merged flag. */
function readableBatches(root) {
  const indexPath = path.join(root, "index.json");
  const out = [];
  if (fs.existsSync(indexPath)) {
    try {
      const index = JSON.parse(fs.readFileSync(indexPath, "utf8"));
      for (const b of index.batches ?? []) {
        if (!b || !b.id) continue;
        if (!fs.existsSync(path.join(root, b.id, "manifest.json"))) continue;
        out.push({ id: b.id, merged: !!b.merged_into });
      }
      return out;
    } catch {
      /* fall through to dir scan */
    }
  }
  if (fs.existsSync(root)) {
    for (const name of fs.readdirSync(root)) {
      if (fs.existsSync(path.join(root, name, "manifest.json"))) {
        out.push({ id: name, merged: false });
      }
    }
  }
  return out;
}

function listConfigDirs(bdir) {
  const configsDir = path.join(bdir, "configs");
  if (!fs.existsSync(configsDir)) return [];
  const out = [];
  for (const name of fs.readdirSync(configsDir)) {
    let isDir = false;
    try {
      isDir = fs.statSync(path.join(configsDir, name)).isDirectory();
    } catch {
      isDir = false;
    }
    if (isDir) out.push(name);
  }
  return out;
}

function main() {
  const root = dataRoot();
  const batches = readableBatches(root);

  // configIds present in any NON-MERGED batch — these are the "duplicate"
  // identities whose copy in a merged batch must NOT ship. A config that exists
  // ONLY in a merged batch (an orphan, e.g. an effort added after the merge) is
  // NOT a duplicate and DOES ship, so the registry's winner assets are present.
  const nonMergedConfigIds = new Set();
  for (const b of batches) {
    if (b.merged) continue;
    for (const c of listConfigDirs(path.join(root, b.id))) nonMergedConfigIds.add(c);
  }

  // Plan the (batchId → configIds) ship list.
  const shipConfigs = new Map(); // batchId → string[]
  for (const b of batches) {
    const cfgs = listConfigDirs(path.join(root, b.id));
    const chosen = b.merged
      ? cfgs.filter((c) => !nonMergedConfigIds.has(c)) // orphans only
      : cfgs;
    if (chosen.length > 0) shipConfigs.set(b.id, chosen);
  }
  const shipSet = new Set(shipConfigs.keys());

  // Prune public/b/<batchId> dirs no longer in the ship set — targeted, not rm-rf.
  let pruned = 0;
  if (fs.existsSync(PUBLIC_B)) {
    for (const name of fs.readdirSync(PUBLIC_B)) {
      if (!shipSet.has(name)) {
        fs.rmSync(path.join(PUBLIC_B, name), { recursive: true, force: true });
        pruned++;
      }
    }
  }

  let copied = 0;
  let patchCopied = 0;
  let jsonCopied = 0;
  for (const [batchId, configIds] of shipConfigs) {
    const bdir = path.join(root, batchId);
    const configsDir = path.join(bdir, "configs");
    if (fs.existsSync(configsDir)) {
      for (const configId of configIds) {
        const cdir = path.join(configsDir, configId);
        for (const vdir of VARIANT_DIRS) {
          const slotsDir = path.join(cdir, vdir, "slots");
          if (!fs.existsSync(slotsDir)) continue;
          for (const slot of fs.readdirSync(slotsDir)) {
            if (!/^\d+$/.test(slot)) continue;
            const srcSlot = path.join(slotsDir, slot);
            const dstSlot = path.join(PUBLIC_B, batchId, configId, vdir, slot);
            for (const asset of ASSET_FILES) {
              if (copyIfChanged(path.join(srcSlot, asset), path.join(dstSlot, asset))) copied++;
            }
            for (const asset of PATCH_FILES) {
              if (copyIfChanged(path.join(srcSlot, asset), path.join(dstSlot, asset))) patchCopied++;
            }
          }
        }
      }
    }

    // Similarity JSON (summary + per-pair details) for the client-side matrix
    // drawer + neighbors. Same URL scheme as on-disk.
    const simDir = path.join(bdir, "similarity");
    if (fs.existsSync(simDir)) {
      const dstSim = path.join(PUBLIC_B, batchId, "similarity");
      for (const name of fs.readdirSync(simDir)) {
        const src = path.join(simDir, name);
        let stat;
        try {
          stat = fs.statSync(src);
        } catch {
          continue;
        }
        if (stat.isDirectory()) {
          if (name !== "pairs") continue;
          const dstPairs = path.join(dstSim, "pairs");
          for (const pf of fs.readdirSync(src)) {
            if (!pf.endsWith(".json")) continue;
            if (copyIfChanged(path.join(src, pf), path.join(dstPairs, pf))) jsonCopied++;
          }
        } else if (name.endsWith(".json")) {
          if (copyIfChanged(src, path.join(dstSim, name))) jsonCopied++;
          // Also emit the compact matrix twin next to the verbose summary.
          const sm = name.match(/^summary--(.+)\.json$/);
          if (sm) {
            const dstCompact = path.join(dstSim, `summary-compact--${sm[1]}.json`);
            if (writeCompactSummary(src, dstCompact)) jsonCopied++;
          }
        }
      }
    }
  }
  // ---- prompts: mirror the verbatim task text the models were given --------
  let promptCopied = 0;
  for (const f of PROMPT_FILES) {
    const src = path.join(PROMPT_SRC_DIR, f);
    if (!fs.existsSync(src)) continue;
    if (copyIfChanged(src, path.join(PUBLIC_PROMPTS, f))) promptCopied++;
  }

  console.log(
    `[copy-assets] ${shipConfigs.size} shippable batch(es): ` +
      `+${copied} slot asset(s), +${patchCopied} patch file(s), +${jsonCopied} similarity json(s) copied; ` +
      `${pruned} stale batch dir(s) pruned → public/b/; ` +
      `+${promptCopied} prompt file(s) → public/prompts/`,
  );
}

main();
