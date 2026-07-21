// ===========================================================================
// lib/github.ts — push a landed community batch to a REVIEW branch (spec §3.3).
//
// Anonymous web input must never reach the public default branch without review:
// commits go to `community/<jobId>` and are pushed to THAT branch via
// WCB_GITHUB_TOKEN — NEVER `main`. PR creation is optional (`gh` if present); a
// human merges. No-op with a clear status when the token is unset.
//
// Only ever called by the contribute queue worker (flag-gated), after gates
// pass. Everything here is best-effort and never throws into the request path.
// ===========================================================================

import { execFileSync } from "node:child_process";
import path from "node:path";

export interface CommitResult {
  ok: boolean;
  pushed: boolean;
  branch: string;
  prUrl?: string;
  reason?: string;
}

function git(repoRoot: string, args: string[], env?: NodeJS.ProcessEnv): string {
  return execFileSync("git", ["-C", repoRoot, ...args], {
    encoding: "utf8",
    env: env ?? process.env,
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
}

function repoRootOf(batchDir: string): string {
  try {
    return git(path.dirname(batchDir), ["rev-parse", "--show-toplevel"]);
  } catch {
    // fall back to two levels up (data root's parent is typically the repo)
    return path.resolve(batchDir, "..", "..");
  }
}

/**
 * Commit `batchDir` onto branch `community/<jobId>` and push that branch.
 * Never touches main. Returns a status object; never rejects.
 */
export function commitBatch(batchDir: string, jobId: string, message: string): CommitResult {
  const branch = `community/${jobId}`;
  const token = process.env.WCB_GITHUB_TOKEN;
  const repoRoot = repoRootOf(batchDir);

  try {
    // Create/switch to the review branch (never main).
    try {
      git(repoRoot, ["checkout", "-B", branch]);
    } catch {
      git(repoRoot, ["switch", "-c", branch]);
    }
    const rel = path.relative(repoRoot, batchDir) || ".";
    git(repoRoot, ["add", "--", rel]);
    // Only commit if there is something staged.
    try {
      git(repoRoot, ["diff", "--cached", "--quiet"]);
      // exit 0 → nothing staged
      return { ok: true, pushed: false, branch, reason: "nothing-to-commit" };
    } catch {
      /* non-zero → there are staged changes, continue */
    }
    git(repoRoot, ["commit", "-m", message]);

    if (!token) {
      return { ok: true, pushed: false, branch, reason: "no-token" };
    }

    // Push the review branch only. Use an ephemeral tokenised remote URL.
    let remote = "";
    try {
      remote = git(repoRoot, ["remote", "get-url", "origin"]);
    } catch {
      return { ok: true, pushed: false, branch, reason: "no-origin" };
    }
    const pushUrl = tokenizeRemote(remote, token);
    git(repoRoot, ["push", pushUrl, `HEAD:refs/heads/${branch}`]);

    // Optional PR via gh (best-effort; a human still merges).
    let prUrl: string | undefined;
    try {
      prUrl = execFileSync(
        "gh",
        ["pr", "create", "--head", branch, "--base", "main", "--title", message, "--body", "Automated community contribution — review before merge."],
        { encoding: "utf8", cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"] },
      ).trim();
    } catch {
      /* gh not present or PR exists — fine */
    }

    return { ok: true, pushed: true, branch, prUrl };
  } catch (e) {
    return { ok: false, pushed: false, branch, reason: (e as Error).message };
  }
}

/** Inject the token into an https origin URL; leave ssh/other URLs untouched. */
function tokenizeRemote(url: string, token: string): string {
  if (url.startsWith("https://")) {
    return url.replace("https://", `https://x-access-token:${token}@`);
  }
  return url;
}
