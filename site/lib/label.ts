// Unique, human-readable config labels. With effort/protocol expansion many
// configs share one model_id (8× "gpt-5.6-sol"), so bare model_id is ambiguous
// everywhere (wall headers, matrix rows, tooltips). One shared rule:
//   <model_id>[ @effort][ · arm]     arm ∈ api / codex / CC / go / …
// Pure string logic — client-safe, no fs.

export interface LabelInput {
  config_id: string;
  model_id: string;
  effort?: string | null;
  protocol?: string;
}

/** protocol/transport arm token derived from the config_id conventions. */
export function armOf(configId: string, protocol?: string): string {
  // NB: "--cli--codex-oauth" contains "--cli--codex" — longest match first.
  if (configId.includes("--cli--codex-oauth")) return "codex-oauth";
  if (configId.includes("--cli--codex")) return "codex";
  // ChatGPT app codex @ultra (app-only manual tier) — matches the harness-plan
  // "codex-app" cell; distinct from the CLI codex arms above.
  if (configId.includes("--codex-app")) return "codex-app";
  if (configId.includes("--cli--claude-code")) return "CC";
  if (configId.includes("--cli--grok")) return "grok-cli";
  // kiro-cli multi-family harness arm (AWS Kiro, GitHub-login credit plan).
  if (configId.includes("--cli--kiro")) return "kiro";
  // qoder-cli qwen harness arm (Alibaba Qoder, stock-login coding plan).
  if (configId.includes("--cli--qoder")) return "qoder";
  // opencode has two distinct arms: the CLI harness (--cli--opencode, protocol
  // "cli", harness group) and the go-gateway api arm (--opencode-go, protocol
  // "api", api group). They must resolve to different arm tokens so coverage
  // matching against harness-plan ("opencode" vs "go") is accurate — otherwise
  // the opencode harness arm falls through to the generic "cli" token and its
  // 22 landed configs never match the planned "opencode" cells.
  if (configId.includes("--cli--opencode")) return "opencode";
  if (configId.includes("--opencode-go")) return "go";
  if (protocol === "cli") return "cli";
  return "api";
}

/** Full unique label, e.g. "gpt-5.6-sol @medium · codex". */
export function configLabel(c: LabelInput): string {
  const arm = armOf(c.config_id, c.protocol);
  const eff = c.effort ? ` @${c.effort}` : "";
  return `${c.model_id}${eff} · ${arm}`;
}
