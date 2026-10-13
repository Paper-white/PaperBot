# PaperBot LLM 服务使用与开发指南

> 本文写给两类人：**运维/使用者**（想配好模型、调教人设、管记忆）与**插件作者**（想在插件里调大模型）。
> 框架侧实现见 `src/core/llm.py`，WebUI 接口见 `webui/routes/llm.py`。
> 一句话：**任何 OpenAI 兼容服务都能接**，插件只要一行 `await feature.llm.ask("你好")`。

---

## 0. TL;DR

```python
# 插件里最小用法（无需自己读配置、自己建 client）
reply = await feature.llm.ask("你好呀")                      # 一句话
reply = await feature.llm.ask("你好呀", scope="user", scope_id=str(event.user_id))  # 带上下文 + 长期记忆
async for chunk in feature.llm.stream("写首诗"):              # 流式（全局关了也安全，退化成一片）
    if chunk.done:
        await matcher.finish(chunk.text)
```

```powershell
# 起服务后用 WebUI「LLM 与记忆」页管理服务商 / 人设 / 会话 / 用户记忆；或直接打 API：
curl http://127.0.0.1:6188/api/llm/status
```

三条铁律：

1. **DB 优先，`.env` 兜底**：WebUI 里加的服务商（`llm_provider` 表）优先；一个都没有时用 `.env` 的 `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL`。
2. **`env` 是虚拟服务商名**，代表「用 `.env` 兜底」，它不是数据库记录，不能删、不能改名成 `env`。
3. **密钥只出不进**：接口回显永远是掩码 `********`，提交掩码 = 不改动，提交空串 = 清空。

---

## 1. 配置总览

### 1.1 两种配置位置

| 位置 | 谁在用 | 特点 |
|---|---|---|
| **数据库 `llm_provider` 表**（WebUI「服务商」卡片） | 推荐，可多套并存、随时切换默认 | 即时生效、不重启、可存多把 key、可点「测试」 |
| **`.env` 的 `LLM_*`** | 只有一个模型、懒得上 WebUI 时 | 需重启生效；当作「最后兜底」，名字固定为 `env` |

服务商解析顺序（`resolve_provider`）：

```
显式传的 provider 名 → DB 里 is_default=1 的服务商 → .env 兜底（"env"）
```

### 1.2 `.env` 全部 LLM 键

> 📁 这里的 `.env` 指 **`.config/main/.env`**（整个 `.config/` 已 gitignore）。
> 根目录若还留着老的 `.env`，会被当作低优先级兜底读取（`.config/main/.env` 优先）。

| 键 | 默认 | 说明 |
|---|---|---|
| `LLM_ENABLE` | `true` | 总开关，关掉后所有插件调用都会抛可读错误 |
| `LLM_BASE_URL` | 空 | 兜底服务商地址（OpenAI 兼容，通常以 `/v1` 结尾） |
| `LLM_API_KEY` | 空 | 兜底密钥（勿提交） |
| `LLM_MODEL` | `gpt-4o-mini` | 兜底模型名 |
| `LLM_TIMEOUT` | `60` | 请求超时（秒） |
| `LLM_PROXY` | 空 | 代理，本机 Clash 例 `http://127.0.0.1:2080`；留空直连 |
| `LLM_TEMPERATURE` | `0.8` | 采样温度 |
| `LLM_MAX_TOKENS` | `0` | `0` = 不传，交给服务端默认 |
| `LLM_DEFAULT_PRESET` | 空 | 默认人设名（未指定时用它），内置示例 `paper_white` |
| `LLM_STREAM_ENABLE` | `false` | 流式总开关（插件显式传 `use_stream` 时以插件为准） |
| `LLM_MEMORY_ENABLE` | `true` | 是否注入「用户印象 + 关键信息」 |
| `LLM_CONTEXT_ROUNDS` | `10` | 上下文轮数（1 轮 = 用户 + 助手） |
| `LLM_IMPRESSION_EVERY` | `10` | 每多出 N 条用户消息，自动提炼一次印象（`<=0` 关闭） |
| `LLM_MAX_FACTS` | `30` | 最多往提示词里塞几条关键信息 |

> ⚠️ `.env` 里的 LLM 相关键（含 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 等）也能在 WebUI
> **「系统配置」页 → LLM 兜底与行为** 分组里改，改完需重启。

### 1.3 首次配置三选一

**A. 懒人版（`.env`）**：填 `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`，重启即可。
**B. 推荐版（WebUI）**：打开 `/llm` 页 → 「服务商」卡片 → 填名称/地址/密钥/模型 → 保存 → 点该行「设为默认」→ 点「测试」看到回复即成功。
**C. 多模型版**：WebUI 加多条服务商，一条设为默认；某个插件想用别的，调用时传 `provider="名字"`。

---

## 2. WebUI「LLM 与记忆」页

入口：`http://127.0.0.1:6188/llm`（左侧菜单「LLM 与记忆」）。页面分五块：

### 2.1 总览（顶部卡片）

`LLM 总开关 / 是否可用 / 流式 / 记忆 / 上下文轮数 / 提炼间隔 / 默认人设 / 兜底服务商 / 服务商数 / 人设数 / 会话数 / 用户数`。
**「是否可用」为否**时，说明既没有启用的 DB 服务商，`.env` 也没填 —— 页面会给出提示。

### 2.2 服务商（Providers）

表格列：名称 / 地址 / 模型 / 密钥（`********` 或「未设置」）/ 温度 / 超时 / 默认 / 启用 / 来源 / 操作。

- **新增/编辑**：右侧表单填 `名称`（必填，字母数字`_-.`，最长 64）/ `地址` / `密钥` / `模型` / `温度` / `超时` / `额外参数(JSON)`。
- **密钥字段**：留空 = 不改动已有密钥；显示 `********` 时原样提交也不改动；想清空请用「删除服务商」或直接填新 key。
- **设为默认**：同表唯一（框架自动保证只有一个 `is_default=1`）。
- **测试**：发一条 `你好` 给该服务商，返回耗时 / 模型 / 用量 —— 排查「key 对不对、地址通不通」的最快手段。
- **`env` 行**：来源列显示「兜底」，只能测试，不能改删（接口返回 400）。

### 2.3 试聊（Chat）

选一个服务商（可留空走默认）+ 选一个人设（可留空走默认）+ 输入内容 → 回车，页面展示回复与耗时。
这是**无状态**试聊（不带上下文、不落库），适合验证人设文风与模型是否听话。

### 2.4 人设（Presets）

表格列：名称 / 显示名 / 来源（内置 / 导入 / 人工）/ 启用 / 正文长度 / 更新时间 / 操作。

- **新建/编辑**：显示名、描述、`system_prompt`（正文）、`params`（温度/最大 tokens 等，JSON）。
  通过 WebUI 保存后来源自动变成**「人工」**，之后**再导入同名文件不会覆盖它**（要覆盖得勾选 `force`）。
- **导入**：粘贴人设文本（Markdown + front-matter 或 JSON），可勾选「覆盖同名（force）」。
- **导出**：下载 `.md` 文件（可直接当模板改）。
- **删除**：内置人设删掉后，下次重启会被重新导入。

### 2.5 会话（Sessions）

列出「作用域 + 谁 + 哪个插件 + 人设 + 服务商 + 消息数 + 更新时间」：

- 能看每条会话的**最近消息**（点「查看消息」）；
- 能给某条会话**换人设 / 换服务商**（清空则回落默认；人设或服务商不存在 → 400）；
- 能**删除单条**或**按作用域批量清空**（如清掉某个群的全部上下文）。

### 2.6 用户记忆（Users）

列出「用户 / 印象 / 关键信息条数 / 消息数 / 印象更新时间」：

- 能看某个用户的**印象 + 全部关键信息**；
- 能**手写印象**、**手加/删除关键信息**（人工记录不会被自动提炼覆盖）；
- 能**清空某个用户的全部长期记忆**（印象 + 关键信息）。

> 💡 关于作用域：`scope="user"` 的会话按「同一个人 + 同一个插件」隔离（不同插件各自记各自的历史与记忆）；
> `scope="group"` 的会话按「群 + 插件」隔离，**不注入个人长期记忆**（群里不该知道私聊细节）。

---

## 3. 插件开发：`feature.llm`

`Feature` 自带懒加载属性 `feature.llm`（`src/core/llm.py::PluginLLM`），**自动带上插件名**做上下文隔离，
所以插件永远不需要自己读配置、自己建 client、自己拼记忆。

```python
@cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if not await feature.llm.available():
        await cmd.finish("还没配置大模型哦（.env 或 WebUI「LLM 与记忆」页）")
    reply = await feature.llm.ask(args.extract_plain_text().strip() or "你好")
    await cmd.finish(reply)
```

### 3.1 API 一览

| 方法 | 说明 |
|---|---|
| `await feature.llm.available()` | 是否已配置可用（插件应先判断） |
| `await feature.llm.ask(text, **kw)` | 返回字符串，最常用 |
| `await feature.llm.complete(text, **kw)` | `ask` 的别名（想读起来像「补全」时用） |
| `await feature.llm.chat(messages, **kw)` | 传完整消息列表，返回 `LLMResult`（含 `text / model / usage / latency_ms / provider / preset`） |
| `feature.llm.stream(source, **kw)` | 异步生成器，`source` 可是字符串或消息列表；`async for chunk in ...`，`chunk.delta` 增量、`chunk.done=True` 的那片带完整 `chunk.text` 与 `chunk.result` |
| `await feature.llm.remember(user_id, key, value)` | 人工记一条关键信息（不会被自动提炼覆盖） |
| `await feature.llm.forget(user_id, key=None)` | 删一条 / 全删（`key=None`）关键信息，返回删除条数 |
| `await feature.llm.profile(user_id)` | 读某个用户的「印象 + 关键信息」 |
| `await feature.llm.set_impression(user_id, text)` | 手写印象 |
| `await feature.llm.refresh_profile(user_id, **kw)` | 立刻让模型提炼一次印象（一般不用手动调） |
| `await feature.llm.clear_memory(user_id)` | 清空该用户长期记忆 |
| `await feature.llm.context(scope, scope_id)` | 查本插件在某作用域的会话（`None` = 还没有） |
| `await feature.llm.use_preset(scope, scope_id, 名字)` | 给某会话换人设（`None`/`""` = 回落默认） |
| `await feature.llm.use_provider(scope, scope_id, 名字)` | 给某会话换服务商（同上） |
| `await feature.llm.clear_context(scope, scope_id)` | 清掉某作用域的历史，返回删除条数 |
| `await feature.llm.providers()` / `.presets()` | 读服务商 / 人设列表（做管理命令时用） |

### 3.2 常用关键字参数（`**kw`）

| 参数 | 说明 |
|---|---|
| `scope="user"` + `scope_id=QQ` | 个人上下文 + 长期记忆（最常用于私聊 / 群里针对某个人） |
| `scope="group"` + `scope_id=群号` | 群聊上下文，**不注入个人记忆** |
| 都不传 | 无状态单次问答，**不落库**（适合翻译、总结这类工具型调用） |
| `preset="人设名"` | 指定人设；不传用 `LLM_DEFAULT_PRESET` |
| `provider="服务商名"` | 指定服务商；不传用默认服务商（再不行 `.env` 兜底） |
| `model=` / `temperature=` / `max_tokens=` / `extra={...}` | 临时覆盖参数（`extra` 会并进请求体，用于 `top_p` 等各家私有参数） |
| `memory=False` | 这次不注入长期记忆（上下文照常） |
| `use_stream=True/False` | 临时强制开/关流式（默认跟随 `LLM_STREAM_ENABLE`） |

> ⚠️ 传了 `scope` 就**必须**给 `scope_id`，否则抛 `LLMError`（防止写串库）。
> ⚠️ 指定的人设名如果**不存在**会直接报错（不会静默用错人设）；只有「默认人设名」查不到才会安静回落到无人设。

### 3.3 异常处理

所有失败都抛 `src.core.llm.LLMError`（已把第三方异常包装成人话，如
`LLM 请求失败（deepseek）：...`、`LLM 已全局禁用（LLM_ENABLE=false）`、`除 system 外至少要有一条消息`）。

```python
from src.core.llm import LLMError

try:
    reply = await feature.llm.ask(text)
except LLMError as exc:
    feature.logger.warning("调用失败：{}", exc)
    await cmd.finish("模型开小差了，稍后再试……")
```

> 记忆类方法（`remember` / `profile` 等）**即使 LLM 全局禁用也能用**（它们只读写数据库），
> 但 `LLM_ENABLE=false` 时 `available()` 为 `False`，`ask/chat/stream` 会报错。

---

## 4. 人设（Preset）

人设 = **一段 system prompt + 几个采样参数 + 一个名字**。框架启动时会自动把 `src/core/presets/*.md`
导成内置人设（同名已被「人工/导入」占用的会跳过）。

### 4.1 文件格式（Markdown + front-matter）

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

### 4.2 覆盖规则（`import_preset_text`）

| 库中已有人设来源 | 新导入来源 | 结果 |
|---|---|---|
| `manual`（WebUI 手改/新建） | 任意 | **跳过**（人工编辑优先） |
| `import`（导入的） | `builtin`（`src/core/presets/` 里的） | **跳过**（内置不盖用户的） |
| `import` / `builtin` | 其它 | **覆盖更新** |
| 任意 | 任意 + `force=True` | **覆盖更新** |

> 结论：**WebUI 改过的不会被内置文件或再次导入悄悄覆盖**；重复导入同一个文件 = 正常「更新」。

### 4.3 自建内置人设（可选）

把人设 `.md` 丢进 `src/core/presets/`，重启即生效（属可提交素材）。
想强刷：`await llm.load_builtin_presets(force=True)`。

---

## 5. 上下文与长期记忆

### 5.1 上下文（短期）

- 只有传了 `scope` + `scope_id` 才有上下文；按 `(作用域, 谁, 插件名)` 三元组建会话，各插件互不干扰。
- 每轮请求自动带上最近 `LLM_CONTEXT_ROUNDS` 轮（1 轮 = 用户 + 助手）历史。
- 超出轮数的老消息仍留在库里（WebUI 可见），只是不再进提示词。
- 想清空：QQ 侧由插件自己实现，WebUI 侧在「会话」卡片点删除 / 批量清空；插件内用 `clear_context()`。

### 5.2 长期记忆（印象 + 关键信息）

两样东西，都只对 `scope="user"` 生效：

1. **印象（impression）**：一段 80 字以内的用户画像。
2. **关键信息（facts）**：`key: value` 列表（称呼、偏好、身份、习惯、禁忌……）。

**自动提炼**：某用户的新增用户消息累计到 `LLM_IMPRESSION_EVERY` 条（默认 10，按
`用户消息数 - 上次提炼时的消息数` 计算）就后台跑一次 `refresh_profile()`，让模型输出严格 JSON：
`{"impression": "...", "facts": [{"key": "...", "value": "..."}]}`。
自动提炼出的信息带 `source=auto`，**不会覆盖人工记录**（`source=manual`）。

**注入格式**（`build_memory_block`，拼在 system prompt 之后）：

```
以下是你与 ta 过往对话中积累的长期记忆，只在自然合适时使用，不要逐条复述、不要暴露你在「读取记忆」：
【你对 ta 的印象】喜欢猫的群友，说话直接
【已知关键信息】
- 称呼：白纸姐姐
- 爱好：撸猫
```

- 上限 `LLM_MAX_FACTS`（默认 30）条关键信息。
- 整体开关 `LLM_MEMORY_ENABLE`，单次可用 `memory=False` 关掉。
- 群里（`scope="group"`）**不注入**个人记忆。

---

## 6. 数据表（共享 SQLite）

| 表 | 关键字段 | 唯一键 |
|---|---|---|
| `llm_provider` | `name` / `base_url` / `api_key` / `model` / `temperature` / `max_tokens` / `timeout` / `extra`(JSON) / `enabled` / `is_default` / `updated_by` / `updated_at` | `name` |
| `llm_preset` | `name` / `display_name` / `description` / `system_prompt` / `params`(JSON) / `enabled` / `source` / `updated_by` / `updated_at` | `name` |
| `llm_session` | `scope_type` / `scope_id` / `plugin_name` / `preset_name` / `provider_name` / `message_count` / `user_message_count` / `created_at` / `updated_at` | (`scope_type`,`scope_id`,`plugin_name`) |
| `llm_message` | `session_id` / `role` / `content` / `name` / `created_at` | — |
| `llm_user_profile` | `user_id` / `impression` / `message_count` / `impression_at` / `updated_at` | `user_id` |
| `llm_user_fact` | `user_id` / `fact_key` / `fact_value` / `source` / `updated_at` | (`user_id`,`fact_key`) |

- 密钥是**明文存库**（与 `.env` 一致，本项目定位是自用机器人）；请勿把 `data/paperbot.db` 传出去。
- `source` 取值：人设 `builtin/import/manual`；关键信息 `auto/manual`。
- 时间列一律**按 UTC 存**（SQLite `func.now()` 就是 UTC），API / WebUI 展示时统一换算成
  **北京时间**（形如 `2026-09-30 18:11:45+08:00`，`llm.fmt_dt()` 负责转换）。
- WebUI「数据库管理」页只读，可直接翻表排查。

---

## 7. REST API 一览（前缀 `/api`，需 `Authorization: Bearer <WEBUI_TOKEN>`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/llm/status` | 总览（开关 / 是否可用 / 各计数 / 中文标签） |
| GET | `/llm/providers` | 服务商列表（密钥掩码，含虚拟 `env` 行） |
| PUT | `/llm/providers/{name}` | 新增或更新（`name=="env"` → 400；`api_key` 传掩码/省略 = 不改） |
| DELETE | `/llm/providers/{name}` | 删除（`env` → 400，不存在 → 404，删默认后自动补默认） |
| POST | `/llm/providers/{name}/test` | 试连一次，返回文本 / 模型 / 耗时 / 用量 |
| GET | `/llm/presets?with_prompt=1` | 人设列表（可带正文） |
| GET | `/llm/presets/{name}` | 人设详情（含导出文本 `text`） |
| PUT | `/llm/presets/{name}` | 新建 / 保存（来源变「人工」） |
| DELETE | `/llm/presets/{name}` | 删除 |
| POST | `/llm/presets/import` | 导入 `{text, filename?, force?}` → `{action: created/updated/skipped}` |
| GET | `/llm/presets/{name}/export` | 导出 `.md`（`Content-Disposition` 文件名） |
| GET | `/llm/sessions?limit=` | 会话列表（limit 1~500） |
| GET | `/llm/sessions/{id}/messages?limit=` | 某会话消息 |
| PUT | `/llm/sessions/{id}` | 换人设 / 换服务商（不存在 → 400） |
| DELETE | `/llm/sessions/{id}` | 删单条会话（连带消息） |
| DELETE | `/llm/sessions?scope_type=&scope_id=&plugin_name=` | 按作用域批量清空 |
| GET | `/llm/users?limit=` | 有记忆的用户列表 |
| GET | `/llm/users/{uid}` | 用户印象 + 关键信息 |
| PUT | `/llm/users/{uid}/impression` | 手写印象 |
| PUT | `/llm/users/{uid}/facts` | 手加/更新关键信息（人工来源） |
| DELETE | `/llm/users/{uid}/facts?key=` | 删单条（`key` 省略 = 全删） |
| DELETE | `/llm/users/{uid}` | 清空该用户长期记忆 |
| POST | `/llm/chat` | 试聊 `{prompt, preset?, provider?, scope?, scope_id?}`（无状态） |

---

## 8. 自检与常见问题

```powershell
# 全套自检（76 项：服务商 / 人设 / 会话 / 记忆 / 试聊 / 真实 SDK 链路）
D:\pixi\bin\pixi.exe run check-llm
```

**Q：插件调用报「还没配置大模型」？**
A：`await feature.llm.available()` 看一下；要么 `.env` 填 `LLM_BASE_URL/API_KEY/MODEL`，要么 WebUI 加一条服务商并「设为默认」。

**Q：WebUI 点「测试」报 401/连不上？**
A：401 是地址或 key 错；`Connection error` 看 `LLM_PROXY`（本机 Clash 填 `http://127.0.0.1:2080`）与 `LLM_BASE_URL` 是否以 `/v1` 结尾。

**Q：改了人设但机器人说话还是老样子？**
A：看该会话用了哪个人设（WebUI「会话」卡片）——会话上记着 `preset_name`，它优先于 `LLM_DEFAULT_PRESET`；用 `use_preset()` 或 WebUI 换掉。

**Q：为什么私聊历史跑到别的插件里了？**
A：不会。会话按「作用域 + 谁 + **插件名**」隔离；如果你发现串了，检查是否漏传 `plugin`（用 `feature.llm` 会自动带）。

**Q：印象一直不更新？**
A：默认每 10 条**用户消息**才提炼一次（`LLM_IMPRESSION_EVERY`），且要求 `scope="user"` + `LLM_MEMORY_ENABLE=true`；想立刻看效果用 WebUI 手写印象或调 `refresh_profile()`。

**Q：群里会不会知道我跟机器人私聊的内容？**
A：不会。`scope="group"` 的会话不注入个人长期记忆。

**Q：`.env` 里 `LLM_API_KEY` 会被 WebUI 回显吗？**
A：不会。所有接口只回显掩码；`GET /llm/providers` 里 `env` 行的密钥永远显示 `********`（设置了）或空。

---

*相关文档：插件开发协议见 `docs/plugin-dev.md`（第 12 章「调用大模型」）。*
