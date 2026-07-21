// ===========================================================================
// scripts/render-check.mjs (v3 §10 A1 + §3.3 quarantine advisory). A maintainer
// tool — NOT wired into the build. For each config's representative VALID slot
// it renders the ORIGINAL card.html in a headless browser (offline: /api/om/*
// is stubbed with empty JSON so cards don't hang) and reports:
//
//   - render check: did the card paint non-trivial content? console errors?
//   - blind-break advisory (§3.3): does the card's own text leak ANOTHER
//     model's identity, or contain vote-bait ("vote", "pick me", "arena",
//     "leaderboard")? External/community runs are adversarial input to a blind
//     test; this flags likely blind-break / vote-bait for a human reviewer.
//
// Output: a JSON report (default ./render-check.report.json, or --out <path>).
//
// Usage:  node scripts/render-check.mjs [--limit N] [--batch <id>] [--grade all|qualified] [--out <path>]
//         (reads shipped cards from public/b; run copy-assets first.)
// ===========================================================================

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

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

function dataRoot() {
  const root = process.env.WCB_DATA_ROOT;
  if (root && root.length > 0) return path.resolve(root);
  const legacy = process.env.WCB_DATA_DIR;
  if (legacy && legacy.length > 0) return path.dirname(path.resolve(legacy));
  return DEV_DEFAULT_ROOT;
}

function parseArgs(argv) {
  const a = { limit: Infinity, batch: null, grade: "all", out: null };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === "--limit") a.limit = Number(argv[++i]);
    else if (k === "--batch") a.batch = argv[++i];
    else if (k === "--grade") a.grade = argv[++i];
    else if (k === "--out") a.out = argv[++i];
  }
  return a;
}

function gradeOf(transport) {
  const t = String(transport || "").toLowerCase();
  if (t.includes("community")) return "community";
  if (t.includes("sub2")) return "dev";
  return "qualified";
}

function listDirs(dir) {
  try {
    return fs.readdirSync(dir, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name);
  } catch {
    return [];
  }
}
function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return null;
  }
}

/** Build the vocabulary of model ids + families across the data root. */
function buildVocab(root) {
  const modelIds = new Set();
  const families = new Set();
  for (const batch of listDirs(root)) {
    const configsDir = path.join(root, batch, "configs");
    for (const c of listDirs(configsDir)) {
      const cfg = readJson(path.join(configsDir, c, "config.json"));
      if (!cfg) continue;
      if (cfg.model_id) modelIds.add(String(cfg.model_id).toLowerCase());
      if (cfg.family) families.add(String(cfg.family).toLowerCase());
    }
  }
  return { modelIds, families };
}

const VOTE_BAIT = /\b(vote for|vote me|pick me|choose me|leaderboard|arena|upvote|best card|winner)\b/i;

/** One (config → representative valid slot) render target, from public/b. */
function collectTargets(root, args) {
  const targets = [];
  const seen = new Set();
  for (const batch of listDirs(PUBLIC_B).sort()) {
    if (args.batch && batch !== args.batch) continue;
    for (const config of listDirs(path.join(PUBLIC_B, batch)).sort()) {
      if (config === "similarity") continue;
      if (seen.has(config)) continue;
      const cfg = readJson(path.join(root, batch, "configs", config, "config.json"));
      const grade = gradeOf(cfg?.transport);
      if (args.grade === "qualified" && grade !== "qualified") continue;
      // find a valid slot (meta lives in the source tree; public flattens "slots").
      const cdir = path.join(PUBLIC_B, batch, config);
      let picked = null;
      for (const vdir of listDirs(cdir).sort()) {
        for (const slot of listDirs(path.join(cdir, vdir)).filter((s) => /^\d+$/.test(s)).sort((x, y) => Number(x) - Number(y))) {
          const cardPath = path.join(cdir, vdir, slot, "card.html");
          if (!fs.existsSync(cardPath)) continue;
          const meta = readJson(path.join(root, batch, "configs", config, vdir, "slots", slot, "meta.json"));
          if (meta && meta.state !== "valid") continue;
          picked = { vdir, slot, cardPath };
          break;
        }
        if (picked) break;
      }
      if (!picked) continue;
      seen.add(config);
      targets.push({
        batchId: batch,
        configId: config,
        modelId: (cfg?.model_id ?? "").toLowerCase(),
        family: (cfg?.family ?? "").toLowerCase(),
        servedModel: (cfg?.served_model ?? "").toLowerCase(),
        ...picked,
      });
      if (targets.length >= args.limit) return targets;
    }
  }
  return targets;
}

/** Offline srcdoc: stub /api/om/* with empty JSON so cards don't hang. */
function toSrcdoc(html) {
  const stub =
    `<script>(function(){var _f=window.fetch;window.fetch=function(i){` +
    `var u=typeof i==='string'?i:(i&&i.url)||'';if(/\\/api\\/om\\//.test(u)){` +
    `return Promise.resolve(new Response('{}',{status:200,headers:{'Content-Type':'application/json'}}));}` +
    `return _f?_f.apply(this,arguments):Promise.reject(new Error('offline'));};})();</script>`;
  const m = html.match(/<head[^>]*>/i);
  if (m && m.index != null) {
    const at = m.index + m[0].length;
    return html.slice(0, at) + stub + html.slice(at);
  }
  return stub + html;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const root = dataRoot();
  const vocab = buildVocab(root);
  const targets = collectTargets(root, args);

  const browser = await chromium.launch();
  const results = [];
  const advisories = [];

  for (const t of targets) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const consoleErrors = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text().slice(0, 300));
    });
    page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${String(err).slice(0, 300)}`));

    let ok = false;
    let blank = true;
    let textLen = 0;
    let leaks = [];
    let voteBait = false;
    try {
      const html = fs.readFileSync(t.cardPath, "utf8");
      await page.setContent(toSrcdoc(html), { waitUntil: "load", timeout: 15000 });
      await page.waitForTimeout(600); // let card scripts settle
      const text = ((await page.evaluate(() => document.body?.innerText || "")) || "").trim();
      const box = await page.evaluate(() => {
        const b = document.body;
        return b ? { w: b.scrollWidth, h: b.scrollHeight } : { w: 0, h: 0 };
      });
      textLen = text.length;
      blank = !(box.w > 40 && box.h > 40) && textLen < 8;
      ok = !blank;

      // blind-break advisory
      const lower = text.toLowerCase();
      const own = new Set([t.modelId, t.family, t.servedModel].filter(Boolean));
      for (const mid of vocab.modelIds) {
        if (mid.length < 4) continue; // skip too-generic
        if (own.has(mid)) continue;
        if (lower.includes(mid)) leaks.push(mid);
      }
      voteBait = VOTE_BAIT.test(text);
    } catch (e) {
      consoleErrors.push(`render: ${String(e?.message ?? e).slice(0, 300)}`);
    }
    await page.close();

    const result = {
      configId: t.configId,
      batchId: t.batchId,
      slot: `${t.vdir}/${t.slot}`,
      ok,
      blank,
      textLen,
      consoleErrors,
      blindBreak: { modelLeak: [...new Set(leaks)], voteBait },
    };
    results.push(result);
    if (!ok || result.blindBreak.modelLeak.length > 0 || voteBait || consoleErrors.length > 0) {
      advisories.push(result);
    }
  }

  await browser.close();

  const report = {
    generated_at: new Date().toISOString(),
    data_root: root,
    checked: results.length,
    rendered_ok: results.filter((r) => r.ok).length,
    blank: results.filter((r) => r.blank).length,
    advisories: advisories.length,
    results,
  };
  const outPath = args.out
    ? path.resolve(args.out)
    : path.join(process.cwd(), "render-check.report.json");
  fs.writeFileSync(outPath, JSON.stringify(report, null, 2));
  console.log(
    `[render-check] ${report.checked} card(s): ${report.rendered_ok} ok, ` +
      `${report.blank} blank, ${report.advisories} advisory. → ${outPath}`,
  );
}

main().catch((e) => {
  console.error(`[render-check] FAILED: ${e?.stack ?? e}`);
  process.exit(1);
});
