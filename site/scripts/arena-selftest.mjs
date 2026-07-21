// ===========================================================================
// arena-selftest.mjs — direct (non-HTTP) exercise of the arena vote loop
// against a throwaway SQLite DB. Verifies the integrity-critical mechanics A3
// owns: pair issuance, atomic single-use consume (token reuse -> 410), server
// dwell floor (<2.5s -> 410), vote provenance rows, BT/stats aggregation, share
// round-trip, and the rate limiter.
//
// Run:  node scripts/arena-selftest.mjs
// (this file installs the TS resolve hook via module.register before importing
// any .ts module; Node v26 strips the TypeScript types natively.)
// ===========================================================================
import { register } from "node:module";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";

// Install the resolve hook BEFORE importing any .ts module.
register(new URL("./_ts-resolve-hook.mjs", import.meta.url));

// Throwaway DB + deterministic secret.
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "wcb-arena-"));
process.env.WCB_DB_PATH = path.join(tmpDir, "app.db");
process.env.WCB_ARENA_SECRET = "selftest-fixed-secret";

let failures = 0;
function check(name, cond, extra) {
  const ok = !!cond;
  if (!ok) failures++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  — " + extra : ""}`);
}

const arena = await import("../lib/arena.ts");
const { getDb } = await import("../lib/db.ts");
const rl = await import("../lib/ratelimit.ts");

// Synthetic qualified pool: 3 distinct configs, one valid slot each.
const POOL = [
  { configId: "cfg-alpha", batchId: "b1", slots: [{ index: 0, sha256: "a".repeat(64) }] },
  { configId: "cfg-bravo", batchId: "b1", slots: [{ index: 0, sha256: "b".repeat(64) }] },
  { configId: "cfg-charlie", batchId: "b1", slots: [{ index: 0, sha256: "c".repeat(64) }] },
];
arena.setPoolProvider(() => POOL);

function backdate(token, ms) {
  getDb()
    .prepare("UPDATE pairs SET ts=? WHERE id=?")
    .run(new Date(Date.now() - ms).toISOString(), token);
}

// ---- 1. issue a pair -------------------------------------------------------
const pair = await arena.issuePair({ angle: "overall", source: "arena" });
check("issuePair returns opaque 64-hex token", /^[0-9a-f]{64}$/.test(pair.token), pair.token?.slice(0, 12) + "…");
check("issuePair returns 2 cards", Array.isArray(pair.cards) && pair.cards.length === 2);
check("issuePair variant is P-q", pair.variant === "P-q", pair.variant);

// ---- 2. dwell floor: an instant vote is rejected 410 too-fast --------------
const fast = await arena.castVote({ token: pair.token, choice: "a", voterId: "v-fast", ipHash: "iphash-fast" });
check("instant vote -> 410 too-fast", fast.ok === false && fast.status === 410 && fast.code === "too-fast", JSON.stringify(fast));

// ---- 3. a properly-dwelt vote lands ---------------------------------------
backdate(pair.token, 5000);
const good = await arena.castVote({ token: pair.token, choice: "a", voterId: "v-1", ipHash: "iphash-1", latencyMs: 4200, locale: "en" });
check("dwelt vote ok=true", good.ok === true, JSON.stringify(good).slice(0, 80));
check("vote reveal has 2 identities", good.ok && Array.isArray(good.reveal) && good.reveal.length === 2);

// ---- 4. token reuse is dead by construction -> 410 -------------------------
const reuse = await arena.castVote({ token: pair.token, choice: "b", voterId: "v-1", ipHash: "iphash-1" });
check("token reuse -> 410 used-or-expired", reuse.ok === false && reuse.status === 410 && reuse.code === "used-or-expired", JSON.stringify(reuse));

// ---- 5. vote row persisted with full provenance ---------------------------
const row = getDb().prepare("SELECT * FROM votes WHERE pair_id=?").get(pair.token);
check("vote row exists", !!row);
check("vote row carries config/batch/sha per side", row && row.config_a && row.batch_a && /^[0-9a-f]{64}$/.test(row.sha_a));
check("vote row source recorded", row && row.source === "arena", row && row.source);
check("vote row patched_served audit column is 0", row && row.patched_served === 0);
check("vote row dwell_ms >= 2500", row && row.dwell_ms >= 2500, row && row.dwell_ms);

// ---- 6. stats aggregation renders (few votes -> insufficient) --------------
const stats = arena.stats("overall", "P-q");
check("stats has method url", stats.methodUrl === "/methodology#voting");
check("stats totalVotes counts the landed vote", stats.totalVotes === 1, String(stats.totalVotes));
check("stats items present for participants", stats.items.length === 2, String(stats.items.length));
check("insufficient items carry null prefRate/ci/strength", stats.items.every((it) => it.insufficient && it.prefRate === null && it.ci === null && it.strength === null));

// stats with zero data for a different angle must not crash
const empty = arena.stats("visual", "P-q");
check("stats empty angle -> 0 votes, [] items", empty.totalVotes === 0 && empty.items.length === 0);

// ---- 7. BT math sanity on a synthetic decisive record ----------------------
const bt = arena.bradleyTerry([
  { config_a: "x", config_b: "y", choice: "a" },
  { config_a: "x", config_b: "y", choice: "a" },
  { config_a: "x", config_b: "y", choice: "a" },
]);
check("BT: dominant winner has higher strength", (bt.get("x") ?? 0) > (bt.get("y") ?? 0), `x=${bt.get("x")?.toFixed(3)} y=${bt.get("y")?.toFixed(3)}`);

// ---- 8. share round-trip ---------------------------------------------------
const share = arena.createShare(pair.token);
check("createShare returns id + url", share && /^[0-9a-f]{24}$/.test(share.shareId) && share.url === `/arena/m/${share.shareId}`);
const shared = await arena.issueFromShare(share.shareId, "overall");
check("issueFromShare issues a fresh distinct token", shared.token !== pair.token && /^[0-9a-f]{64}$/.test(shared.token));
check("shared pair source is 'share'", getDb().prepare("SELECT source FROM pairs WHERE id=?").get(shared.token)?.source === "share");

// ---- 9. rate limiter: 30 then block ---------------------------------------
let blockedAt = -1;
for (let i = 0; i < 32; i++) {
  const r = rl.consumeRate(["ip:rltest", "voter:rltest"]);
  if (!r.ok && blockedAt < 0) blockedAt = i;
}
check("rate limiter blocks after ~30", blockedAt === 30, `blockedAt=${blockedAt}`);

// ---- 10. CSV export well-formed -------------------------------------------
const csv = arena.exportCsv();
check("CSV has gameability disclaimer header", csv.startsWith("# unauthenticated"));
check("CSV has header row + at least one data row", csv.split("\n").filter(Boolean).length >= 3);

// cleanup
try {
  fs.rmSync(tmpDir, { recursive: true, force: true });
} catch {}

console.log(`\n${failures === 0 ? "ALL PASS" : failures + " FAILED"}`);
process.exit(failures === 0 ? 0 : 1);
