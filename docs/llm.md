# PaperBot LLM 使用与运维指南

> 本文写给**运维 / 使用者**：想配好模型、调教人设、管好记忆、看明白 WebUI 上的每一块。
> 想**在插件里写代码调大模型**？请移步开发者参考 [`llm-api.md`](llm-api.md)（`feature.llm` 接口 / 参数 / 异常 / 数据表 / REST）。
> 一句话：**任何 OpenAI 兼容服务都能接**，插件只要一行 `await feature.llm.ask("你好")`。

---

## 0. TL;DR

```powershell
# 打开 WebUI →「LLM 与记忆」页，配服务商 / 人设 / 看会话与记忆；或直接打 API：
curl http://127.0.0.1:6188/api/llm/status
```

三条铁律：

1. **DB 优先，`.env` 兜底**：WebUI 里加的服务商（`llm_provider` 表）优先；一个都没有时用 `.env` 的 `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL`。
2. **`env` 是虚拟服务商名**，代表「用 `.env` 兜底」，它不是数据库记录，不能删、不能改名成 `env`。
3. **密钥只出不进**：接口回显永远是掩码 `********`，提交掩码 = 不改动，提交空串 = 清空。

---

## 1. 配置总览

> 📌 **`.env` 全部键的唯一出处是** [`configuration.md`](configuration.md)（第 1 章）。
> 本文**不再复制整张键表**，只讲 LLM 相关的配置位置与生效方式。

### 1.1 两种配置位置

| 位置 | 谁在用 | 特点 |
|---|---|---|
| **数据库 `llm_provider` 表**（WebUI「服务商」卡片） | 推荐，可多套并存、随时切换默认 | 即时生效、不重启、可存多把 key、可点「测试」 |
| **`.env` 的 `LLM_*`** | 只有一个模型、懒得上 WebUI 时 | 需重启生效；当作「最后兜底」，名字固定为 `env` |

服务商解析顺序（`resolve_provider`）：

```
显式传的 provider 名 → DB 里 is_default=1 的服务商 → .env 兜底（"env"）
```

### 1.2 LLM 相关的 `.env` 键

> 📁 这里的 `.env` 指 **`.config/main/.env`**（整个 `.config/` 已 gitignore）。
> 根目录若还留着老的 `.env`，会被当作低优先级兜底读取（`.config/main/.env` 优先）。

LLM 相关的键分三组，**完整定义与默认值一律看** [`configuration.md`](configuration.md)：

| 分组 | 含哪些键 | 去哪看 |
|---|---|---|
| **LLM 兜底与运行参数** | `LLM_ENABLE` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_TIMEOUT` / `LLM_PROXY` / `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` / `LLM_DEFAULT_PRESET` / `LLM_STREAM_ENABLE` / `LLM_MEMORY_ENABLE` / `LLM_CONTEXT_ROUNDS` / `LLM_MAX_FACTS` | `configuration.md` |
| **聊天记录** | `CHATLOG_ENABLE` | `configuration.md` |
| **定时总结与压缩** | `LLM_SUMMARY_ENABLE` / `LLM_SUMMARY_INTERVAL` / `LLM_SUMMARY_PROVIDER` / `LLM_SUMMARY_MODEL` / `LLM_SUMMARY_MAX_MSGS` / `LLM_COMPRESS_THRESHOLD` | `configuration.md` |

> ⚠️ `.env` 里的 LLM 相关键也能在 WebUI **「LLM 与记忆」页 → 「运行参数」分组**里改；
> 这类键属**可热更**，改完点「系统配置」页的 **「热重载配置」**（或 QQ `%reload`）即生效，无需重启。
> ⚠️ `LLM_IMPRESSION_EVERY` 已**停用**（仅兼容旧 `.env` 保留），记忆自动生成改由**定时总结**负责（见第 4 章）。

### 1.3 首次配置三选一

**A. 懒人版（`.env`）**：填 `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`，重启即可。
**B. 推荐版（WebUI）**：打开 `/llm` 页 → 「服务商」卡片 → 填名称/地址/密钥 → 点「获取可用模型」→ 选模型加入列表（最多 3 个）→ 保存 → 点该行「设为默认」→ 点「测试」看到回复即成功。
**C. 多模型版**：同一个服务商里配最多 3 个模型（优先 + 备用），首选失败自动切下一个；也可以加多条服务商，一条设为默认，某插件想用别的就调用时传 `provider="名字"`。

### 1.4 多模型优先级（优先 + 备用）

- WebUI「服务商」卡片里可以**拉取中转站可用模型列表**（`GET /models`），把想要的模型加入列表、拖动排序，最多 **3 个**；
- 排在最前的是**首选**，其余是**备用**；调用失败（限流 / 超时 / 报错）时自动按顺序切换重试；
- 手动优先级字段 `priority` 数字小者靠前（WebUI 暂以列表顺序为准）；会话里显式指定的模型永远提到最前；
- `.env` 兜底模型只有一个，不参与多模型切换（想用多模型请走 WebUI 服务商）。

---

## 2. WebUI「LLM 与记忆」页

入口：`http://127.0.0.1:6188/llm`（左侧菜单「LLM 与记忆」）。
页面主体由**六块**卡片组成：**服务商 / 试聊 / 人设 / 会话 / 用户长期记忆 / 群记忆**，另有顶部**总览卡**与底部**「运行参数」分组**。

### 2.1 总览（顶部卡片）

- 第一组：`LLM（已启用/禁用 · 调用可用） / 当前服务商 / 人设 / 会话 / 用户记忆 / 群记忆`；
- 第二组：`流式 / 上下文轮数 / 定时总结间隔（LLM_SUMMARY_INTERVAL）`。
- 当 `LLM` 卡片的副文案显示「未配置密钥 / 地址」时，说明既没有启用的 DB 服务商，`.env` 也没填 —— 页面会给出提示。

### 2.2 服务商（Providers）

表格列：名称 / 状态 / API 地址 / 首选模型 / 模型数 / 密钥（`********` 或「未设置」）/ 操作。

- **新增/编辑**：填 `名称` / `API 地址` / `API Key` / `温度` / `设为默认`。
- **密钥字段**：留空 = 不改动已有密钥；显示 `********` 时原样提交也不改动；想清空请用「删除服务商」或直接填新 key。
- **设为默认**：同表唯一（框架自动保证只有一个 `is_default=1`）。
- **测试**：发一条 `ping` 给该服务商，返回耗时 / 模型 / 用量 —— 排查「key 对不对、地址通不通」的最快手段。
- **`env` 行**：来源列显示「兜底」，只能测试，不能改删（接口返回 400）。

### 2.3 试聊（Chat）

选一个人设（可留空走默认）+ 输入内容 → 发送，页面展示回复。
这是**无状态**试聊（不带上下文、不落库），适合验证人设文风与模型是否听话。

### 2.4 人设（Presets）

表格列：名字 / 显示名 / 来源（内置 / 导入 / 人工）/ 状态 / 提示词字数 / 更新时间 / 操作。

- **新建/编辑**：名字（唯一键）、显示名、简介、状态、`系统提示词（正文）`。
  通过 WebUI 保存后来源自动变成**「人工」**，之后**再导入同名文件不会覆盖它**（要覆盖得勾选 `force`）。
- **导入**：粘贴人设文本（Markdown + front-matter 或 JSON），可勾选「同名已存在 → 强制覆盖」。
- **导出**：下载 `.md` 文件（可直接当模板改）。
- **删除**：内置人设删掉后，下次重启会被重新导入。

文件格式与覆盖规则见本文第 3 章。

### 2.5 会话（Sessions）

列出「作用域 + ID + 插件 + 人设 + 服务商 + 消息数 + 更新时间」：

- 能看每条会话的**最近消息**（点「消息」）；
- 能**换人设 / 换服务商**（点「换人设」；清空则回落默认，人设或服务商不存在 → 400）；
- 能**清空单条**（连带消息，长期记忆不受影响）。

> 💡 会话只对**个人作用域**（`scope="user"`）存在；群聊不建会话（原因见 4.1）。

### 2.6 用户长期记忆（Users）

列出「用户 / 印象 / 关键信息条数 / 消息数 / 更新时间」：

- 能看某个用户的**印象 + 全部关键信息**；
- 能**手写印象**、**手加/删除关键信息**（人工记录不会被定时总结覆盖）；
- 能**清空某个用户的全部长期记忆**（印象 + 关键信息）。

### 2.7 群记忆（Groups）

列出「群 / 共同记忆 / 累计消息 / 更新时间」：

- 群记忆由后台**定时总结**从群聊流水增量生成（共同话题 / 群梗 / 正在聊的事）；
- 可人工改写某群的群记忆；
- 原始流水可在「数据库管理」页翻 `chat_message` 表。

### 2.8 运行参数

「运行参数」分组把 `.env` 兜底 + 定时总结的一批键搬到本页（点摘要卡弹浮窗编辑），
写回 `.config/main/.env`，改完点「系统配置」页的「热重载配置」即生效（可热更项）。**键表见** [`configuration.md`](configuration.md)。

> 💡 关于作用域：`scope="user"` 的会话按「同一个人 + 同一个插件」隔离（不同插件各自记各自的历史与记忆）；
> `scope="group"` 则**根本不建 `llm_session`**，群上下文由聊天流水（`chat_message`）提供，
> 因此群聊会话级操作（换人设 / 清空）在群作用域下不适用；群聊回复注入的是群记忆 + 当前发言者个人记忆。

---

## 3. 人设（Preset）

人设 = **一段 system prompt + 几个采样参数 + 一个名字**。框架启动时会自动把 `src/core/presets/*.md`
导成内置人设（同名已被「人工/导入」占用的会跳过）。

> 在插件代码里指定人设（`preset=` / `use_preset()`）见 [`llm-api.md`](llm-api.md)。

### 3.1 文件格式（Markdown + front-matter）

```markdown
---
name: paper_white          # 必填：注册名（字母数字 _-.，最长 64）
display_name: 白纸姐姐      # 可选：展示名，默认 = name
description: 本体是白纸，说话软软的  # 可选：一句话介绍
temperature: 0.9           # 可选：直接写采样参数
enabled: true              # 可选：默认 true
---

（front-matter 之后的所有内容 = system prompt 正文，**不能为空**）
你是一只叫「白纸」的猫娘助手……
```

- 也支持**纯 JSON**：`{"name": "...", "system_prompt": "...", ...}`（键名 `prompt` 亦可）。
- 采样参数可写在顶层（`temperature` / `max_tokens` / `top_p` / `seed` …），也可写进 `params: {...}`。
- `name` 不合法或正文为空 → 导入失败（400），不会落库。
- front-matter 解析优先用 **PyYAML**；没装也能工作（退化成逐行 `key: value`，此时嵌套 `params` 请写成 JSON 或顶层平铺）。

### 3.2 覆盖规则（`import_preset_text`）

| 库中已有人设来源 | 新导入来源 | 结果 |
|---|---|---|
| `manual`（WebUI 手改/新建） | 任意 | **跳过**（人工编辑优先） |
| `import`（导入的） | `builtin`（`src/core/presets/` 里的） | **跳过**（内置不盖用户的） |
| `import` / `builtin` | 其它 | **覆盖更新** |
| 任意 | 任意 + `force=True` | **覆盖更新** |

> 结论：**WebUI 改过的不会被内置文件或再次导入悄悄覆盖**；重复导入同一个文件 = 正常「更新」。

### 3.3 自建内置人设（可选）

把人设 `.md` 丢进 `src/core/presets/`，重启即生效（属可提交素材）。
想强刷：`await llm.load_builtin_presets(force=True)`。

---

## 4. 上下文与长期记忆

> 记忆与上下文相关的 `.env` 键（`LLM_CONTEXT_ROUNDS` / `LLM_MEMORY_ENABLE` / `LLM_MAX_FACTS` /
> `LLM_SUMMARY_*` / `LLM_COMPRESS_THRESHOLD`）定义见 [`configuration.md`](configuration.md)。

### 4.1 上下文（短期）

- 只有传了 `scope` + `scope_id` 才有上下文；按「**作用域 + 谁 + 插件名**」三元组建会话，各插件互不干扰。
- **个人对话**（`scope="user"`）：每轮自动带最近 `LLM_CONTEXT_ROUNDS` 轮（1 轮 = 用户 + 助手）历史；
- **群聊**（`scope="group"`）：**不建 `llm_session`**，群上下文由聊天流水（`chat_message` 表）
  提供 —— 插件从流水读最近 N 条群消息注入，避免一份历史存两处；
- 超出轮数的老消息仍留在库里（WebUI 可见），只是不再进提示词；超阈值会被**压缩**（见 4.4，仅个人会话）；
- 想清空：WebUI 侧在「会话」卡片点清空；插件内用 `clear_context()`。

### 4.2 群聊「对话模式」（`paperbot_chat` 插件层实现）

`paperbot_chat` 在「群聊上下文」之上做了一层**艾特触发、免艾特续聊**：

- 群里**艾特一次** bot → 开启该群的对话模式；之后**不用再艾特**，普通消息 bot 也会自动接话（带冷却防刷屏）；
- 默认 **300 秒**没人说话自动关闭（`group_timeout` 可改，0 = 不超时）；`%ai 停` 手动关闭；
- **回复合并窗口**：窗口内（`group_cooldown`，默认 3 秒）到达的多条消息会**合并成一轮回复**，
  不再逐条丢弃也不刷屏——5 条消息 = 1 次 LLM 调用、1 条综合回复；
- 自动接话时从**聊天流水**（`chat_message`）读最近 N 条群消息（`group_history` 配置项）拼进上下文；
- 群聊回复注入：**群记忆 + 当前发言者的「本群可见」个人记忆**（QQ 号为准的身份锚 + 跨窗口隔离，见 4.3）。

### 4.3 长期记忆（印象 + 关键信息 + 群记忆）

**个人记忆**（`scope="user"`，按 QQ 区分）：

1. **印象（impression）**：一段 80 字以内的用户画像。**语义上属「私聊专属」**——群聊不注入印象。
2. **关键信息（facts）**：`key: value` 列表（称呼、偏好、身份、习惯、禁忌……），**带来源作用域**：
   - `global`：通用（WebUI 手写默认），任何窗口可见；
   - `group:<群号>`：仅该群可见；
   - `private`：仅私聊可见。

**群记忆**（`scope="group"`，按群号区分）：一段共同话题/群梗/正在聊的事，存 `llm_group_profile`。

**跨窗口隔离**（`LLM_MEMORY_SCOPE_ISOLATION`，默认 `true`）：

- 提炼：`summarize_user` 把该用户的新消息**按来源拆批**——私聊批次更新印象 + `private` 事实，群批次只写 `group:<群号>` 事实（不写印象）；
- 注入：**私聊**只看到 `global + private`；**群聊**只看到 `global + 本群`（`global + group:<当前群号>`）；
- 效果：bot 在群里**不会**说出该用户在私聊或别群说过的话（曾经的「乱认人/乱说话」根因）；
- 关闭该开关 = 退回旧行为（全量注入，不推荐）。

**代码闸门（防幻觉入库）**：自动提炼的事实写库前先过 `src/core/memory_gate.py` ——
对着**本批原始消息**做引用校验（最长公共子串找支撑片段），并拒绝四类「字面有据但错」的事实：
**转述**（他说/据说）、**角色扮演**（扮演/台词）、**临时状态**（今天有点/就这一次）、**未兑现打算**（打算/计划）。
只拦自动提炼；WebUI 手写（`manual`）不经过闸门。宁缺勿滥，避免幻觉污染长期画像。

**分槽保底注入**（`LLM_MEMORY_SLOTS_ENABLE`，默认 `true`）：注入时把事实按「**通用画像**（`global`）」
与「**窗口事实**（`group:<群>` / `private`）」分槽渲染，每槽至少保底 5 条再按分数（时间/权重）填充，
避免近期活跃话题挤掉长期稳定画像（称呼 / 生日 / 忌口）。

**库内上限**（`LLM_MAX_FACTS_STORED`，默认 `200`）：单用户单 scope 的自动事实超过上限时，
按最旧淘汰（**manual 永不淘汰**）。

**受控 key 词表**（`src/core/fact_keys.py`）：自动提炼时的 key 必须命中规范词表
（称呼/姓名/性别/生日/年龄/职业/学校/所在地/家乡/爱好/擅长/忌口/过敏/承诺），
别名会自动归一（「住址/居住地/城市」→`所在地`）；未命中词表的自造 key 在
`LLM_MEMORY_KEY_EXACT=true`（默认）下**直接丢弃**，从根上压住 key 膨胀。
WebUI 手写（`manual`）只做别名归一、保留原 key 不丢弃。

**时间归一化**：提炼时把消息里的相对日期词（今天/明天/昨天/前天/后天/大后天）按
**该消息的发送时间**内联标注成绝对日期（`明天（2026-10-16）`），使记忆里的时间可查。
标注后的文本同时用于提炼 prompt 与代码闸门，保证二者看到同一份内容。

**用法档**（`LLM_MEMORY_USAGE_ENABLE`，默认 `true`）：每条事实带一个「怎么用」的档位
——`mention`（可信、可自然提及）/ `tone`（可信但敏感，只影响语气、勿说破）/
`uncertain`（推断/弱依据，谨慎参考）。提炼时由模型产出（漏给则由 key 兜底、默认 mention），
注入时**槽内按档分三个子块**渲染（`【可自然提及】` / `【只影响语气，勿说破】` / `【不太确定，谨慎参考】`）。

**定时总结**：由后台调度器（`src/core/scheduler.py`）**每 `LLM_SUMMARY_INTERVAL` 秒**（默认 1800）
增量跑一轮：

- 从 `chat_message`（聊天流水）取每个用户 / 每个群自水位线之后的新消息；
- 用「总结模型」（`LLM_SUMMARY_PROVIDER` / `LLM_SUMMARY_MODEL`，未配置则用默认服务商）生成记忆；
- 单次最多 `LLM_SUMMARY_MAX_MSGS` 条（超出只取最近，水位线直推最新）；
- 写回 `llm_user_profile` / `llm_group_profile` 并推进水位线；
- 全程后台运行，**不阻塞对话**；失败只记日志、不推进水位线（下一轮重试）。

定时总结出的关键信息带 `source=auto`，**不会覆盖人工记录**（`source=manual`）。

**注入格式**（拼在 system prompt 之后）：

```
以下是你与 ta 过往对话中积累的长期记忆，只在自然合适时使用，不要逐条复述、不要暴露你在「读取记忆」：
【你对 ta 的印象】喜欢猫的群友，说话直接
【已知关键信息】
- 称呼：白纸姐姐
- 爱好：撸猫
```

- 上限 `LLM_MAX_FACTS`（默认 30）条关键信息。
- 整体开关 `LLM_MEMORY_ENABLE`，单次可用 `memory=False` 关掉。
- 群聊注入的是「群记忆 + 当前发言者个人记忆」。

### 4.4 上下文压缩

**个人会话**（`scope="user"`）的消息数超过 `LLM_COMPRESS_THRESHOLD`（默认 40）条时，
后台调度器把**超出保留窗口的旧消息**交给总结模型压成一段摘要，合并进 `llm_session.summary`，
并物理删除已压缩的旧原文。注入顺序：

```
人设 → 记忆（个人/群 + 发言者） → [更早的对话摘要] → 最近 N 轮原文
```

> ✅ **2026-10-15 已修复**：个人会话的 `summary` 现在会在 `_build_request` 的 user 分支被读回并注入
> （此前只在群分支读取、而群作用域又不建 `llm_session` → 摘要写了却从不进上下文、旧原文被删即静默丢失）。
>
> ⚠️ 仍存在的语义割裂：压缩保留窗口 `LLM_COMPRESS_THRESHOLD`（40 条）大于注入窗口
> `LLM_CONTEXT_ROUNDS`（10 轮 = 20 条），所以会话在 21~40 条区间时这些消息「不注入也不压缩」；
> 建议把 `LLM_COMPRESS_THRESHOLD` 对齐到 `2 × LLM_CONTEXT_ROUNDS`。见 [`plans/07-memory-optimization.md`](plans/07-memory-optimization.md) 的 P1。

---

## 5. 自检与常见问题

```powershell
# 项目根目录执行；具体项数以实际输出为准
D:\pixi\bin\pixi.exe run check-llm
```

> 💡 自检脚本覆盖哪些层、怎么写临时自检，见 [`llm-api.md`](llm-api.md) 第 6 章与 [`development.md`](development.md)。

**Q：插件调用报「还没配置大模型」？**
A：`await feature.llm.available()` 看一下；要么 `.env` 填 `LLM_BASE_URL/API_KEY/MODEL`，要么 WebUI 加一条服务商并「设为默认」。

**Q：WebUI 点「测试」报 401/连不上？**
A：401 是地址或 key 错；`Connection error` 看 `LLM_PROXY`（本机 Clash 填 `http://127.0.0.1:2080`）与 `LLM_BASE_URL` 是否以 `/v1` 结尾。

**Q：改了人设但机器人说话还是老样子？**
A：看该会话用了哪个人设（WebUI「会话」卡片）——会话上记着 `preset_name`，它优先于 `LLM_DEFAULT_PRESET`；用 `use_preset()` 或 WebUI 换掉。

**Q：为什么私聊历史跑到别的插件里了？**
A：不会。会话按「作用域 + 谁 + **插件名**」隔离；如果你发现串了，检查是否漏传 `plugin`（用 `feature.llm` 会自动带）。

**Q：印象一直不更新？**
A：印象由后台调度器**每 `LLM_SUMMARY_INTERVAL` 秒**（默认 1800）从聊天流水增量总结（要求 `LLM_SUMMARY_ENABLE=true` 且配置了可用服务商）；想立刻看效果可在 WebUI 手写印象，或调 `summarize_user()`。

**Q：群里会不会知道我跟机器人私聊的内容？**
A：默认不会。开启 `LLM_MEMORY_SCOPE_ISOLATION`（默认 `true`）后，群作用域只注入**群记忆 + 当前发言者
「通用 + 本群」**的个人信息，私聊/别群来源的事实与印象一律不进群聊。
（历史遗留数据在升级时统一归入 `private`，同样不会在群里出现。）

**Q：`.env` 里 `LLM_API_KEY` 会被 WebUI 回显吗？**
A：不会。所有接口只回显掩码；`GET /llm/providers` 里 `env` 行的密钥永远显示 `********`（设置了）或空。

---

*相关文档：开发者 API 参考 [`llm-api.md`](llm-api.md)；配置全表 [`configuration.md`](configuration.md)；
WebUI 接口一览 [`api.md`](api.md)；架构与数据表 [`architecture.md`](architecture.md)；
插件开发指南 [`plugin-dev.md`](plugin-dev.md) / API 参考 [`plugin-api.md`](plugin-api.md)。*
