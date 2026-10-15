# PaperBot LLM API 参考

> 面向**插件作者 / 二次开发**：在代码里怎么调 `feature.llm`、有哪些参数与异常、涉及哪些表和接口。
> 面向**使用与运维**（配模型 / 调人设 / 管记忆 / 看 WebUI）请看 [`llm.md`](llm.md)。
> 框架实现见 `src/core/llm.py`，WebUI 路由见 `webui/routes/llm.py`。

---

## 0. TL;DR

```python
# 插件里最小用法：不用自己读配置、不用自己建 client、不用自己拼记忆
reply = await feature.llm.ask("你好呀")                                              # 一句话
reply = await feature.llm.ask("你好呀", scope="user", scope_id=str(event.user_id))   # 带上下文 + 长期记忆
async for chunk in feature.llm.stream("写首诗"):                                      # 流式（全局关了也安全，退化成一片）
    if chunk.done:
        await matcher.finish(chunk.text)
```

三条约定（配置侧细节见 [`llm.md`](llm.md)）：

1. **DB 优先，`.env` 兜底**：WebUI 里加的服务商（`llm_provider` 表）优先；一个都没有时用 `.env` 的 `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL`。
2. **`env` 是虚拟服务商名**，代表「用 `.env` 兜底」，不是数据库记录。
3. **密钥只出不进**：掩码 `********` = 不改动。

---

## 1. `feature.llm` API 一览

`Feature` 自带懒加载属性 `feature.llm`（`src/core/llm.py::PluginLLM`），**自动带上插件名**做上下文隔离，
所以插件永远不需要自己读配置、自己建 client、自己拼记忆。

### 1.1 调用

| 方法 | 说明 |
|---|---|
| `await feature.llm.available()` | 是否已配置可用（插件应先判断） |
| `await feature.llm.ask(text, **kw)` | 返回字符串，最常用 |
| `await feature.llm.complete(text, **kw)` | `ask` 的别名（想读起来像「补全」时用） |
| `await feature.llm.chat(messages, **kw)` | 传完整消息列表，返回 `LLMResult`（含 `text / model / usage / latency_ms / provider / preset`） |
| `feature.llm.stream(source, **kw)` | 异步生成器，`source` 可是字符串或消息列表；`async for chunk in ...`，`chunk.delta` 增量、`chunk.done=True` 的那片带完整 `chunk.text` 与 `chunk.result` |

### 1.2 个人长期记忆

| 方法 | 说明 |
|---|---|
| `await feature.llm.remember(user_id, key, value)` | 人工记一条关键信息（不会被自动总结覆盖） |
| `await feature.llm.forget(user_id, key=None)` | 删一条 / 全删（`key=None`）关键信息，返回删除条数 |
| `await feature.llm.profile(user_id)` | 读某个用户的「印象 + 关键信息」 |
| `await feature.llm.set_impression(user_id, text)` | 手写印象 |
| `await feature.llm.refresh_profile(user_id, **kw)` | 立刻让模型总结一次印象（一般不用手动调；注意这是旧入口，定时任务走 `summarize_user`） |
| `await feature.llm.clear_memory(user_id)` | 清空该用户长期记忆 |

### 1.3 上下文（会话）

| 方法 | 说明 |
|---|---|
| `await feature.llm.context(scope, scope_id)` | 查本插件在某作用域的会话（`None` = 还没有） |
| `await feature.llm.use_preset(scope, scope_id, 名字)` | 给某会话换人设（`None`/`""` = 回落默认） |
| `await feature.llm.use_provider(scope, scope_id, 名字)` | 给某会话换服务商（同上） |
| `await feature.llm.clear_context(scope, scope_id)` | 清掉某作用域的历史，返回删除条数 |

> ⚠️ 群作用域（`scope="group"`）**不建 `llm_session`**（见 [`llm.md`](llm.md) 的「上下文」一节），
> 因此上述会话级方法只对 `scope="user"` 有意义；群上下文由插件从 `chat_message` 流水自行读取。

### 1.4 群记忆

| 方法 | 说明 |
|---|---|
| `await feature.llm.group_profile(group_id)` | 读某群的共同记忆 |
| `await feature.llm.set_group_memory(group_id, memory)` | 人工改写某群的共同记忆 |

### 1.5 定时总结 / 压缩（一般由 `scheduler` 调用）

| 方法 | 说明 |
|---|---|
| `await feature.llm.summarize_user(user_id)` | 从聊天流水增量总结某用户的个人记忆 |
| `await feature.llm.summarize_group(group_id)` | 从聊天流水增量总结某群的共同记忆 |
| `await feature.llm.compress_session(session_id)` | 把某会话超窗的旧消息压进 `llm_session.summary`（⚠️ 摘要回注目前有缺，见下） |

> ⚠️ **上下文压缩当前有已知缺陷**：`summary` 的读取只写在群分支（死代码），
> 个人会话的摘要写了却不注入 → 超窗旧原文被物理删除后静默丢失；群上下文压缩整体缺失。
> 详见 [`plans/07-memory-optimization.md`](plans/07-memory-optimization.md) 的 P0，以及 [`llm.md`](llm.md) 第 4.4 节。

### 1.6 元信息

| 方法 | 说明 |
|---|---|
| `await feature.llm.providers()` | 读服务商列表（做管理命令时用） |
| `await feature.llm.presets()` | 读人设列表 |

### 1.7 典型用法

```python
@cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if not await feature.llm.available():
        await cmd.finish("还没配置大模型哦（.env 或 WebUI「LLM 与记忆」页）")
    reply = await feature.llm.ask(args.extract_plain_text().strip() or "你好")
    await cmd.finish(reply)
```

---

## 2. 常用关键字参数（`**kw`）

`ask` / `chat` / `stream` 共用下面这组关键字参数：

| 参数 | 说明 |
|---|---|
| `scope="user"` + `scope_id=QQ` | 个人上下文 + 长期记忆（最常用于私聊 / 群里针对某个人） |
| `scope="group"` + `scope_id=群号` | 群聊上下文（**不建会话**，注入群记忆 + 当前发言者个人记忆）；群上下文由插件从流水自行拼 |
| 都不传 | 无状态单次问答，**不落库**（适合翻译、总结这类工具型调用） |
| `preset="人设名"` | 指定人设；不传用 `LLM_DEFAULT_PRESET` |
| `provider="服务商名"` | 指定服务商；不传用默认服务商（再不行 `.env` 兜底） |
| `model=` / `temperature=` / `max_tokens=` / `extra={...}` | 临时覆盖参数（`extra` 会并进请求体，用于 `top_p` 等各家私有参数） |
| `memory=False` | 这次不注入长期记忆（上下文照常） |
| `use_stream=True/False` | 临时强制开/关流式（默认跟随 `LLM_STREAM_ENABLE`） |

> ⚠️ 传了 `scope` 就**必须**给 `scope_id`，否则抛 `LLMError`（防止写串库）。
> ⚠️ 指定的人设名如果**不存在**会直接报错（不会静默用错人设）；只有「默认人设名」查不到才会安静回落到无人设。
> ℹ️ 群聊注入当前发言者记忆时，插件通过 `extra={"_sender_id": ...}` 传内部信息；
> `_` 开头的键属框架内部约定，**不会**透传给模型（见 `_build_request`）。

---

## 3. 异常处理

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

## 4. 数据表

LLM 相关表都建在共享 SQLite（`data/paperbot.db`）里：

- `chat_message`（聊天流水，全局钩子写）
- `llm_provider`（服务商）
- `llm_preset`（人设）
- `llm_session`（会话，仅 `scope="user"` 会建）
- `llm_message`（会话消息）
- `llm_user_profile`（用户印象）
- `llm_user_fact`（用户关键信息）
- `llm_group_profile`（群记忆）

> **字段与唯一键的权威清单**见 [`architecture.md`](architecture.md)（第 7 章「数据表」），本文件不再复制。

开发时注意：

- 密钥是**明文存库**（与 `.env` 一致，本项目定位是自用机器人）；请勿把 `data/paperbot.db` 传出去。
- `source` 取值：人设 `builtin/import/manual`；关键信息 `auto/manual`。
- 时间列一律**按 UTC 存**（SQLite `func.now()` 就是 UTC），API / WebUI 展示时统一换算成
  **北京时间**（形如 `2026-09-30 18:11:45+08:00`，`llm.fmt_dt()` 负责转换）。

---

## 5. REST API

本模块的 WebUI 接口都挂在 `/api/llm/*` 前缀下（服务商 / 人设 / 会话 / 用户记忆 / 群记忆 / 试聊等）。

> **完整端点清单与错误码**见 [`api.md`](api.md)（「接口清单」一节），本文件不再复制。

配置类接口（`GET /api/llm/status` 里已经打包了「LLM 与记忆」页所需的运行参数分组）与
`.env` 写回、热重载相关的接口，同样以 [`api.md`](api.md) 为准。

---

## 6. 自检脚本（开发视角）

```powershell
# 项目根目录执行；项数以实际输出为准
pixi run check-llm
```

脚本是 `_tests/llm_check.py`，覆盖三层：

1. **WebUI 接口**（`webui/routes/llm.py`）：用 `httpx.ASGITransport` 直接打 router（同一个事件循环，避开 SQLite 跨 loop 的坑）；
2. **业务层**（`src/core/llm.py`）：会话写库、记忆注入、人设解析；
3. **真实 SDK 链路**：起一个本地「假 OpenAI 兼容服务」，用真正的 `OpenAIChatClient`（openai SDK）跑通 `complete()` 与 `stream()`。

写临时自检时：

- 传输层可替换：`llm.set_client_factory(...)` 换成假客户端，无需真网络；用完 `reset_client_factory()` 复原。
- **别碰真实 `data/paperbot.db`**：把 `settings.database_url` 指到临时库再测。
- 收尾记得 `await database.dispose_db()`。
- 自检脚本套路详见 [`development.md`](development.md)。

---

*相关文档：使用 / 运维指南见 [`llm.md`](llm.md)；插件开发指南见 [`plugin-dev.md`](plugin-dev.md)、API 参考 [`plugin-api.md`](plugin-api.md)；
架构与数据表见 [`architecture.md`](architecture.md)；WebUI 接口见 [`api.md`](api.md)。*
