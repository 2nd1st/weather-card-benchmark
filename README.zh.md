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

构建数据集过程中的描述性测量 —— 是信号,不是定论(单一 prompt 家族、小 N)。每一条都能在站点上回溯到产生它的原始数据。相似度相关的发现都基于**代码习惯指纹**([见下](#从矩阵里才看得到的)),描述的是*代码怎么写*,从不推断模型从哪来。

- **Claude 2025 年的写法,比 Claude 自己活得久。** Claude Sonnet 4 / 4.5、Opus 4.5、Haiku 4.5 写得像 MiniMax M2.x、豆包 Seed 2.1 Pro、Grok 4.3、DeepSeek V3.2、Qwen3-Coder-Next 和 Gemini 3 Flash —— 却不再像 Claude Opus 4.8 / Claude 5。
- **高 effort 下 Claude 和 GPT 开始写得一样 —— 而 DeepSeek V4.1 本来就这么写**,几乎任何 effort 档都是。
- **有的厂商换代就换了一手写法,GPT 从来没换。** DeepSeek V4.1 和 Kimi K3 和自家前代断开了;GPT-6 写起来仍像 GPT-5。
- **harness 改变模型画多少,不改变它怎么写。** 同一个模型换一个 harness,代码习惯不变;变的是外观,以及最主要的 —— 体量。
- **effort 能把输出体积翻好几倍 —— 某个模型上高达 14×。** 更多"思考"不是多写一点代码,可能是多一个数量级。
- **在有些模型上,effort 旋钮测不出任何区别。** 参数被接受,输出纹丝不动。
- **effort 梯子随代际变长。** GPT 的 `max` 档从 gpt-5.6 起才有 —— 更早的 GPT 模型一律不接受,更新的都有。
- **官方 API 的输出比同一模型走 proxy 通道大 5–8×。**
- **kimi-k3 在 qualitative prompt 变体上花的"思考"约是 minimal 变体的 10×。**

## 这张地图

<div align="center">
<img src="docs/matrix.png" alt="config 对 config 代码习惯指纹热力图(585 config 集的 407 config harness 视图)" width="82%">
</div>

每个被测 configuration 对上其余每一个,用的是代码习惯指纹。亮 = 相似,暗 = 分化。对角线上的亮块是各**模型家族**在自我聚类;那条细亮对角线是每个 config 的**自一致性**(一个模型和它*自己*重跑之间有多像)。这里的相似度是**测量,不是质量分** —— 它只刻画收敛,别无其他。

### 从矩阵里才看得到的

> **这个数是什么。** 每一对 configuration 在十条代码通道上比较 —— token shingle、winnowing 指纹、特征集合、JS 语法树、CSS 属性与取值、布局、命名、颜色字面量、语义标记。每条通道先扣掉各个 config 自己的基线(有些卡跟*谁*都有点像,那不算像某一个具体的谁),再按"这条通道多能区分模型"学出来的权重合并,最后显示为**在全部跨配对中的百分位**:0.50 是两个不同厂商的一对典型配对,同一模型的两个 config 大约在 0.97。像素是单独的**外观**视角,不进这个分数。百分位相对于当前集合,集合变了就重新拟合。
>
> 为什么看代码不看像素:在按模型留出的交叉验证里,指纹认出同一模型的 **AUC 是 0.91**,认出同一家族 **0.74**,跨 effort 档认出同一模型 **0.90**;外观视角只有 **0.76 / 0.59**,旧的全通道等权合并是 **0.87 / 0.68**。卡片*长什么样*,各家越来越趋同;*怎么写*,则不然。

下面的数字都是 585 个 configuration 集上的百分位,给两个数时是 `P-min` / `P-q`。是信号不是证明:单一 prompt 家族,而且有些模型 config 很少 —— 这些都标出来了。

- **家族是真实的分组。** 一个 config 和自己重跑最像(`P-min` 上 **0.958**),和自家家族次之(**0.699**),和别家最不像(**0.441**)。
- **Claude 2025 年的写法,比 Claude 自己活得久。** Claude Sonnet 4 / 4.5、Opus 4.5、Haiku 4.5 和 MiniMax M2.1–M2.7、豆包 Seed 2.1 Pro、Grok 4.3、DeepSeek V3.2、Qwen3-Coder-Next、Gemini 3 Flash / 3.1 Pro 组成一个紧密的簇:跨厂商配对在两个 prompt 上都是 **0.96–1.00** —— 和同一模型的两个 config 一样近 —— 跨 harness、跨 effort、十条代码通道全部一致。Claude 自己却走开了:Claude 4.5 对 Claude Opus 4.8 / Claude 5 只有 **0.44 / 0.23**,和两个不相干的厂商一样远。一行标志性代码 `const today = new Date().toISOString().split('T')[0];` 出现在 63% 的 Claude Sonnet 4/4.5 卡、34% 的其他成员卡里,而 571 张 Claude Opus 4.8+ 卡一张都没有。同时期的 GPT 从未加入。
- **高 effort 下 Claude 和 GPT 开始写得一样。** Claude Opus 5 / 5.5 和 GPT Sol 在 none–medium 档相距很远(**0.40 / 0.41**),到 xhigh 及以上却汇合(**0.90 / 0.78**),五个 harness 上都成立。普通跨家族配对从高 effort 里几乎得不到什么(0.37 → 0.43),所以这不是"长答案都相像"。共同的代码是防御型工程写法 —— `prefers-reduced-motion` 处理、用 `AbortController` 中止过期请求、`finally` 收尾 —— 低档 Sol 的卡几乎从不出现(0–2%)。这也解释了为什么**满档 Claude 5.5 离别家比离自家还近**(`claude-opus-5-5 @max`:`P-min` 上对 Claude **0.33**,对其余所有人 **0.56**)。
- **DeepSeek V4.1 写在这个前沿区里。** 1017 构建对高档 Claude/GPT 这组是 **0.86 / 0.91** —— 其他厂商 49 个模型里最近的 —— 对低档 GPT Sol 只有 **0.54 / 0.37**,官方 API 和 DeepSeek 自家 dsh harness 上都成立。和 Claude、GPT 不同,它几乎任何 effort 档都到得了。0910 构建已经很近(**0.71 / 0.85**)。DeepSeek 的 dsh harness 比 API 更常出现这些防御写法(reduced-motion 88% 对 25% 的卡);指纹结论在两边都成立。
- **有的厂商换代就换了一手写法,GPT 从来没换。** GPT-6 对 GPT-5.x 仍是 **0.86 / 0.81**(3,300 个 config 对)。DeepSeek V4.1 对所有更早的 DeepSeek:**0.56 / 0.49** —— 相当于不相干的一对 —— 而它自己的两个构建之间是 **0.82 / 0.89**。Kimi K3 对 Kimi K2.x:**0.56 / 0.55**;K3 反而更靠近 Claude Fable 5 / Opus 4.8(**0.84 / 0.82**,500+ 个 config 对)。
- **代码是模型写的,不是 harness 写的。** 同一模型同一 effort 换一个 harness,是 **0.99 / 0.98** —— 和它自己重跑一样 —— 两个不同家族的模型共用一个 harness 也只多 1–2 个点。外观视角确实会动(跨 harness **0.88–0.89**,重跑是 **0.92–0.94**),体量动得最多。
- **有些模型和自己都不像。** `kimi-k3 @high`(**0.51 / 0.61**)和 `deepseek-v4-pro-0812 @high`(`P-min` 上 **0.68**)自一致性最低 —— 多数重跑画得都不一样。全集有 105 个 config 拿得到读数:它需要同一 config 在同一变体下有好几次重跑。

**值得一提的配对** —— 信号强,但样本小:

- **Qwen3.8-Max-Preview 写得像 Claude Opus 4.6**(**1.00 / 1.00**,比像任何 Qwen 都近),连 HTML 转义 helper `var d = document.createElement('div')` 都一样 —— 64% 的 Opus 4.6 卡有它,别处几乎没有。只有一个 config、4 张卡。MiMo-V2.5-Pro 也有同样的拉力(**0.97 / 0.96**,66 个 config 对)。
- **Cursor 的 Composer 2.5 写得像 Grok 4.5**(**0.98 / 0.94**,跨四个 Grok harness),CSS 词汇一模一样(`--bg-deep`、`--card-bg: rgba(255,255,255,0.07)`);`--bg-deep` 在其余 2,436 张卡里只出现 2 次。只有一个 config、8 张卡。

**厂商在固定型号名下换掉了模型 —— 几把尺子对此意见不一。** DeepSeek 于 2026-07-31 对 `deepseek-v4-flash` 重做了 post-training,API 型号名不变(官方原话:架构与参数量不变,"only re-post-trained")。我们手上有一份 **2026-07-19** 的采集,正好在更新之前,于是两个纪元并排留在集合里。

在**代码指纹**上,这次变化在一个 effort 档看得见,换一个档就消失:`high` 档两个纪元互相是 **0.78 / 0.83**,而各自重跑在 **0.90–0.99**;`max` 档互相是 **0.87 / 0.93**,重跑是 **0.88–0.95** —— 落在噪声里。

但在**输出体积**这条轴上,同一个变化明显到无法忽视。**同一个 effort 档位**下,新纪元的 completion token 涨了 **2–4 倍** —— `high`/`P-min` 中位数 6,546 → 25,972(**4.0×**),`high`/`P-q` 12,376 → 52,332(**4.2×**)—— 而且四格里有三格两个纪元的 token 区间**完全不相交**(逐 slot 两两比较,P(新 > 旧) = **0.961**)。它那两个失败的 slot 正是同一效应的尾巴:写到撑破厂商 65,536 的输出上限还没收尾。

这条的教训是关于 benchmark 本身,而不是关于模型:**一个"设计相似度"指标和一个"体积"指标,可以对"到底有没有发生变化"给出不同的答案。** 旧卡从未重跑、也未被覆盖;新纪元是独立席位;失败的 slot 保留为失败而不重试 —— 因为只重试失败会对成功条件化,恰好把上面这件事抹掉。

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
| **Matrix** | 完整的 config × config 热力图(即上图)—— 指纹、外观或任意单条通道 |
| **Compare** | 任意一组模型并排,同 prompt、同数据 |
| **Arena** | 盲选 A/B —— 挑你更想看的那张,身份投票前隐藏 |
| **Methodology** | 22 条通道、代码习惯指纹,以及一张卡如何算合格 |

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

完整测量集很大(500+ configurations × 多个 slot × 截图),所以仓库只带一份**旗舰子集** —— 每个前沿实验室一个 canonical configuration —— 放在 [`data/batches/`](data/batches/)。站点开箱即用地渲染它。

**完整集**可在线浏览,也能作为单个 pack 下载:

- **完整数据集** → <https://weathercard.secondfirst.ai/downloads/wcb-full-dataset-2026-10-18.tar.gz>
  (~1.7 GB,sha256 `8edf43eb…48e3ef`)。解压出 `2026-07-19--unified/` + `index.json`;把 `WCB_DATA_ROOT` 指向解压目录即可在本地跑整套。

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
