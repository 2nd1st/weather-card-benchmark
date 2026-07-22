<div align="center">

<img src="docs/gallery.png" alt="众多前沿模型从同一个 prompt 和同一份数据生成的 weather card" width="100%">

# weather-card-benchmark

**同一道题,喂给每一个前沿模型 —— 它们造出来的东西差多少?**

[**▶ 打开在线站点**](https://weathercard.secondfirst.ai) &nbsp;·&nbsp;
[我们发现了什么](#我们发现了什么) &nbsp;·&nbsp;
[自己跑一遍](#自己跑一遍) &nbsp;·&nbsp;
[数据](#数据) &nbsp;·&nbsp;
MIT

[English](README.md) &nbsp;·&nbsp; **中文**

<a href="https://buymeacoffee.com/2nd1st"><img src="https://img.shields.io/badge/Buy_me_a_coffee-2nd1st-FFDD00?logo=buymeacoffee&logoColor=black" alt="Buy me a coffee"></a>

</div>

---

每个模型拿到*完全相同*的 prompt 和*完全相同*的冻结天气数据,只被要求做一件事:一张自包含的 HTML **weather card**。每张卡在**完全一致**的条件下 headless 渲染并截图,然后每一对结果在 **22 条视觉与结构相似度通道**上打分。

结果是一张地图 —— 今天最强的模型们在一道明确定义的、one-shot 前端任务上,哪里**收敛**、哪里**分化**;以及*同一个*模型跨不同 coding harness/CLI、不同 reasoning-effort 档位时,会怎么变。

上面那些是真实的卡:同一份 Berlin 天气,设计天差地别。

## 我们发现了什么

构建数据集过程中的描述性测量 —— 是信号,不是定论(单一 prompt 家族、小 N)。每一条都能在站点上回溯到产生它的原始数据。

- **同一个模型,经不同 harness 会画出不同的卡。** harness 本身是一条独立的能力轴,不是中立的管道。
- **effort 能把输出体积翻好几倍 —— 某个模型上高达 14×。** 更多"思考"不是多写一点代码,可能是多一个数量级。
- **在有些模型上,effort 旋钮测不出任何区别。** 参数被接受,输出纹丝不动。
- **官方 GPT API 上根本没有 `minimal` 档** —— 而 **`max` 是 gpt-5.6 家族独有的**。effort 梯子每家不同,且到处是洞。
- **官方 API 的输出比同一模型走 proxy 通道大 5–8×。**
- **kimi-k3 在 qualitative prompt 变体上花的"思考"约是 minimal 变体的 10×。**

## 这张地图

<div align="center">
<img src="docs/matrix.png" alt="118×118 config 对 config 相似度热力图,merged 合并通道" width="82%">
</div>

每个被测 configuration 对上其余每一个,跨所有通道合并。亮 = 相似,暗 = 分化。对角线上的亮块是各**模型家族**在自我聚类;那条细亮对角线是每个 config 的**自一致性**(一个模型和它*自己*重跑之间有多像)。这里的相似度是**测量,不是质量分** —— 它只刻画收敛,别无其他。

### 从矩阵里才看得到的

有些东西只在这里现形。下面的数字都是 `P-min` 集上 merged、非诊断通道(**20 条正式通道**)的相似度 —— **是信号,不是证明**(单一 prompt 家族、小 N),而且有两个混淆项贯穿始终:低 effort 的卡会向一个通用基线收敛,共享的 harness(opencode / Kiro / Qoder)本身也会带进脚手架。请据此理解。*(这些是加入 `x-*` 代码特征通道族后的 20 条通道;那次扩展把每个数字抬高了约 0.07,但更早的 15 通道 merged 呈现**同样的结构** —— 这些发现对通道集是稳健的。)*

- **家族是真实但"弱"的分组。** 一个 config 和自己重跑最像(自一致性 ≈ **0.72**),和自家家族次之(**0.63**),和别家最不像(**0.59**)—— 但差距很小。一个模型低 effort 的卡,可能比它自己高 effort 的卡更像别家。
- **满血 frontier Claude 是全局离群点。** `claude-fable-5 @max`(**0.573**)和 `claude-opus-4.8 @max`(**0.589**)离*其余 Claude* 比 Claude 家族自身的平均(**0.630**)还要远 —— 离别家也更远。最强的模型开到满 effort,去了一个连它自家同门都不跟去的地方。
- **高 effort 把老 Opus 推离家族。** `opus-4.6` 和 `opus-4.7` 在 **high / xhigh** 档漂离 Claude 簇(≈ **0.62**),而在 medium / low / max 档更贴近(高至 **0.68**)—— effort 改变的是模型*落在设计空间的哪里*,不只是写多少。
- **grok 谁都像一点;GPT 谁都不像。** 在样本充足的家族里,grok 的跨家族触达最高(**0.602**),GPT 最低(**0.578**)—— GPT 的卡是全场最我行我素的。
- **有些模型和自己都不像。** `kimi-k3`(**0.66**)和 `gemini-pro`(**0.64**)自一致性最低 —— 多数重跑画得都不一样;别的模型确定性强得多。

## 这是什么

**任务。** 两个 prompt 变体 —— `P-min`(极简)和 `P-q`(充分)—— 都喂同一份冻结天气快照,所以结果不依赖于何时何地跑。逐字 prompt 在 [`prompts/`](prompts/)。每个模型每个变体跑 `N` 次;一张卡必须渲染出非平凡、合规的内容才计入。

**三条轴。** 每个 configuration 是三条轴上的一个点:

- **model** —— 前沿模型(Claude、GPT、Gemini、Qwen、GLM、Kimi、DeepSeek、Grok、Doubao、MiMo、MiniMax …)
- **harness** —— 怎么驱动它:官方 API,或一个 coding CLI/harness(Claude Code、Codex、Qoder、opencode、Kiro、grok-cli …)
- **effort** —— reasoning-effort / thinking 档位,如果模型暴露这个旋钮

## 在线探索

在 **[weathercard.secondfirst.ai](https://weathercard.secondfirst.ai):**

| | |
|---|---|
| **Gallery** | 每一张卡,可筛选 —— 原始输出 |
| **Matrix** | 完整的 config × config 相似度热力图(即上图) |
| **Compare** | 任意一组模型并排,同 prompt、同数据 |
| **Arena** | 盲选 A/B —— 挑你更想看的那张,身份投票前隐藏 |
| **Methodology** | 22 条通道,以及一张卡如何算合格 |

## 自己跑一遍

**Runner**(Python 3.11+)—— sample → render → similarity:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install requests playwright pytest pyyaml jsonschema rfc8785
.venv/bin/python -m playwright install chromium

cp runner/.env.example runner/.env   # 填 key + WCB_API_BASE
cd runner && ../.venv/bin/python -m pytest -q
```

`runner/.env` 存你的 key,已 gitignore —— 绝不能提交。config YAML 里的 `credential_ref` 是环境变量*名字*,永远不是密钥值。

**Site**(Node)—— gallery / matrix / compare / methodology:

```bash
cd site && npm install && npm run dev   # http://localhost:3000
```

站点从 `WCB_DATA_ROOT` 解析数据根(默认用仓库内的 `data/batches`)。部署自己的 fork 时,把 `NEXT_PUBLIC_SITE_URL` 设成你自己的域名。

## 数据

完整测量集很大(190+ configurations × 多个 slot × 截图),所以仓库只带一份**旗舰子集** —— 每个前沿实验室一个 canonical configuration —— 放在 [`data/batches/`](data/batches/)。站点开箱即用地渲染它。

**完整集**可在线浏览,也能作为单个 pack 下载:

- **完整数据集** → <https://weathercard.secondfirst.ai/downloads/wcb-full-dataset-2026-07-19.tar.gz>
  (~385 MB)。解压出 `2026-07-19--unified/` + `index.json`;把 `WCB_DATA_ROOT` 指向解压目录即可在本地跑整套。

## 目录

```
runner/     Python 流水线:adapters(各厂 API/CLI)· render · similarity · tests
site/       Next.js 站点:gallery / matrix / compare / methodology
data/       SCHEMA(冻结 JSON Schema)· batches(旗舰子集)
prompts/    两份逐字任务 prompt
```

## 帮忙扩大覆盖

这是一个人持续在跑的 sweep,而前沿每周都在动。可以这样帮忙:

- **贡献一次 run。** 站点的 **Contribute** 流程会接收你在本地生成的一张卡,用同样的方式渲染、打分,并并入数据集。
- **指出一个缺口。** 缺某个 model、某个 harness、某个 effort 档?开一个 [issue](https://github.com/2nd1st/weather-card-benchmark/issues) —— 站点上的 coverage 视图记录了已覆盖和计划中的内容。
- **借出访问权。** 如果你手上有尚未覆盖的 model 或 harness 的账号 / API 访问权,并且愿意让它跑这个 benchmark —— 开一个 issue,我们私下约一个渠道对接。**绝不要在公开处(issue、PR、评论,任何地方)贴出 key、token 或 cookie**,我也永远不会这样问你要。密钥只走你自己掌控的私密渠道。
- **赞助一次 run。** 每个 configuration 都是真金白银的 API 和订阅开销。一杯咖啡 [buymeacoffee.com/2nd1st](https://buymeacoffee.com/2nd1st) 会直接变成更多模型、更勤的重跑。

## 许可

MIT —— 见 [LICENSE](LICENSE)。
