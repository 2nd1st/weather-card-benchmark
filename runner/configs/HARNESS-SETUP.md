# Harness(CLI coding agent)安装与认证清单 — M3 前置

原则(MODEL-MATRIX §4):CLI 生成**只能在本机**(auth 绑机器);自动化只走**官方 headless 模式**;每个 CLI × auth 档在启用前过 **ToS 核验**(订阅档自动化是否允许)+ **R8 探针**(effort 参数是否真到 API、served_model 核验)。

## ✅ 已就绪(美国四家)

| CLI | 版本 | headless 入口 | auth 现状 | M3 前要核 |
|---|---|---|---|---|
| Claude Code | 2.1.211 | `claude -p "<prompt>"` | Max 订阅 + API key 双通道可切 | 订阅档 headless 批量跑的 ToS |
| codex | 0.144.4 | `codex exec --skip-git-repo-check` | ChatGPT 订阅 + API key 双通道 | 同上;注意双通道 served_model 不同(5.5 vs 5.2-codex);历史有间歇网络层瘫痪,跑前 canary |
| gemini-cli | 0.47.0(最新 0.50.0) | `gemini -p` | ? | **Antigravity 迁移中**:个人 Pro/Ultra 档已停服(2026-06-18)——批次日确认还能不能用,不行就 CLI 列记 N/A |
| grok CLI | ~/.grok/bin(grok-guard 包装) | `GROK_GUARD_QUIET=1 grok -p "…" --always-approve` | SuperGrok 订阅(OAuth 直登) | Build 全开你已实测;headless 批量 ToS |

## 📦 待安装(中国系,装法以官方 doc 现查为准——下表是 sweep 线索,M3 安装时逐个核验)

| CLI | 厂商 | **对应模型(默认 backend,装时核 served_model)** | 安装线索 | auth | 备注 |
|---|---|---|---|---|---|
| **Qwen Code** | 阿里 | `qwen3.7-plus` + `qwen3-coder-plus/next`(**非 max!**) | npm(qwenlm.github.io/qwen-code-docs) | Bailian Coding Plan / API key | served_model 必核,跨臂对比按实际模型 |
| **Kimi Code** | 月之暗面 | `kimi-k3`(2026-07-16 GA,默认大概率已切 K3;装时 served_model 确认)/ `kimi-k2.7-code` 可选 | 官方 doc(原 Kimi CLI,6.4K stars) | Kimi 订阅 | K3:$3/$15,1M ctx,2.8T 开源 |
| **MiniMax Code** | MiniMax | `MiniMax-M3` | 官方 doc(v2.0,2026-07 重构) | token plan | 首个一方 agent 产品 |
| **MiMo Code** | 小米 | `mimo-v2.5-pro` | github.com/XiaomiMiMo/MiMo-Code(MIT 开源) | Token Plan | 全协议矩阵最顺 |
| **CodeBuddy Code** | 腾讯 | `hy3` —— **⚠️ 产品线历史混编过多模型(DeepSeek 等),必须核能否钉死 hy3**,钉不死按实际 served_model 记 config 或 N/A | 官方 doc(v2.0,ACP/sandbox/Skills) | 腾讯云 | |
| **trae-agent** | 字节 | `doubao-seed-2-1-pro` / `doubao-seed-2-0-code`(**工具本身 model-agnostic 开源,我们显式配到火山 Ark 的 Seed**) | github.com/bytedance/trae-agent(开源,Python) | 火山 Ark key | TRAE IDE 非 headless,CLI 用它 |
| **Mistral Vibe** | Mistral | `devstral-2512`(默认)/ `mistral-medium-2604`(remote agents) | github.com/mistralai/mistral-vibe(Apache 2.0) | Le Chat Pro / API key | 唯一欧洲 CLI |
| (Zulu CLI) | 百度 Comate | `ernie-5.1`(Comate 3.x) | comate.baidu.com | Comate 订阅 | 低优先 |

**通用原则**:harness 臂的 config 身份 = (harness, served_model, auth, billing) 四元组——CLI 只是壳。"CodeBuddy 对着什么模型"这类问题的最终答案永远以 **R8 探针实测的 served_model** 为准,默认 backend 只是出发点;探针核不出或与官方 model id 无法映射 → 该 config 以探针实测值如实标注。

## 借壳 harness(无官方 CLI 的家族跑 agent 协议)

DeepSeek / GLM / Step / LongCat / KAT 经各自 **Anthropic 兼容端点挂进 Claude Code**(套餐官方支持场景)——不需要装新东西,但 config 必须标注 `harness=claude-code` + system prompt hash,且 effort 探针必测(harness 吞参已有实证)。

## 安装时的统一动作(每个 CLI)

1. 装 + 记录精确版本(进 manifest env 描述)
2. 认证(订阅登录或 API key),记录 auth 通道
3. 跑一次 headless canary(30s 探针,codex 教训)
4. ToS 核验:该 auth 档的自动化/批量条款 → 不允许 → 降级 api-key auth 或砍
5. R8 探针:effort 是否透传、served_model 是什么
6. **STOCK 隔离(硬性)**:harness 身份必须是 **stock CLI**,不是"操作者个人配置过的 CLI"——user 级指令文件/skills/插件/MCP 一律隔离(可复现性 + 行为污染双重理由)。逐 CLI 找等价开关并验证(**验证法:对比 canary 的 system-prompt 体积/cache_creation tokens**)。已核:Claude Code = `--setting-sources "" --strict-mcp-config`(15.8K→5.0K 实证);codex/gemini/grok/中国系 CLI 的隔离开关 M3 逐个核。
7. **交付形态双路收集**:agentic CLI 可能 stdout 打印**或**沙箱写文件(Fable 5 两者皆现,随机)——adapter 必须两路收集(空目录沙箱 + 文件扫描兜底,trial collect_cli.py 语义)
