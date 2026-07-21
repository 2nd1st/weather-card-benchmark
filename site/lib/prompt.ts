// The verbatim task text every card was generated from.
//
// Provenance matters more than convenience here: each manifest already pins
// prompt_min_sha256 / prompt_q_sha256, so the text shown to a reader must be the
// same bytes that were hashed. copy-assets.mjs mirrors the two source files into
// public/prompts/ at build time; this module reads them from there and hashes
// what it read, so a mismatch surfaces instead of silently showing stale copy.
//
// Server-only (node:fs). Never import from a "use client" module.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export type PromptVariant = "P-min" | "P-q";

export interface PromptDoc {
  variant: PromptVariant;
  /** public URL of the verbatim file (downloadable). */
  href: string;
  text: string;
  /** sha256 of the bytes actually read — compare against the manifest. */
  sha256: string;
}

const FILES: Record<PromptVariant, string> = {
  "P-min": "prompt-v5-min.txt",
  "P-q": "prompt-v5-q.txt",
};

function promptsDir(): string {
  return path.join(process.cwd(), "public", "prompts");
}

/** Read one prompt; null when the mirror is absent (copy-assets not run). */
export function readPrompt(variant: PromptVariant): PromptDoc | null {
  const file = FILES[variant];
  try {
    const buf = fs.readFileSync(path.join(promptsDir(), file));
    return {
      variant,
      href: `/prompts/${file}`,
      text: buf.toString("utf8"),
      sha256: crypto.createHash("sha256").update(buf).digest("hex"),
    };
  } catch {
    return null;
  }
}

/** Both prompts, in display order; absent mirrors are dropped. */
export function readPrompts(): PromptDoc[] {
  return (["P-min", "P-q"] as PromptVariant[])
    .map(readPrompt)
    .filter((p): p is PromptDoc => p !== null);
}
