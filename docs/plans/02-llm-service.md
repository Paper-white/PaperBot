# Phase 2：统一 LLM 服务

- 状态：已完成
- 开始：2026-09-30
- 相关文件：`src/core/llm.py`、`src/core/presets/`、`webui/routes/llm.py`、`plugins/paperbot_chat/`、`_tests/llm_check.py`
- 相关文档：[`../llm.md`](../llm.md)（使用与开发指南，长期维护）

## 目标

插件**不直接 `import openai`**，统一走 `feature.llm`，拿到「多服务商 + 人设 + 会话上下文 + 长期记忆」一条龙。

验收标准：

- 一套代码支持多个服务商（可指定默认），`.env` 作为兜底
- 人设可导入导出，支持「内置 / 导入 / 人工」三种来源
- 上下文按「个人 / 群聊」分作用域，互不串味
- 长期记忆：用户印象 + 关键信息，可人工覆盖
- 流式输出：全局开关 + 插件逐次覆盖
- WebUI 能管理以上全部，且**改动即时生效**

## 方案与决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 服务商存储 | **DB 表 `llm_provider` 优先，`.env` 兜底** | WebUI 改动即时生效；单模型用户可以只填 `.env` 不折腾 |
| `.env` 兜底的名字 | 虚拟服务商名 `env` | 前端要能在列表里显示「当前在用 .env」，但它不是 DB 记录（不能删/改） |
| 人设存储 | DB 表 `llm_preset`，正文即 system_prompt | 便于 WebUI 增删改 + 导入导出；内置的从 `src/core/presets/*.md` 启动导入 |
| 上下文作用域 | `scope_type`（`user` / `group`）× `scope_id` × `plugin_name` | 私聊记忆与群聊记忆分开；`plugin_name` 让不同插件默认不共享会话 |
| 记忆提炼 | 每 N 条用户消息触发一次（`LLM_IMPRESSION_EVERY`） | 省 token：不是每轮都提炼 |
| 记忆覆盖 | `manual` 不被自动提炼覆盖 | 人工写的信息不该被模型冲掉 |
| 密钥回显 | 掩码 `********` / `••••••••` | 前端不出现明文；提交掩码 = 不改动 |
| 客户端 | `openai` SDK（可换实现） | 兼容面最广；自检里用假 client 打本地假 OpenAI 服务 |

## 落地清单

- [x] 服务商 CRUD + `is_default` + 连通性测试 + 密钥掩码
- [x] 人设：内置导入（覆盖规则见下）+ WebUI 增删改 + 文本/文件导入 + 导出 md
- [x] 会话：`ensure_session` / `load_history` / `record_messages` / 清空（单个 + 按作用域批量）
- [x] 记忆：`get_profile` / `set_impression` / `set_fact` / `delete_fact` / `clear_profile` / `build_memory_block`
- [x] 核心调用：`ask`（单问）/ `chat`（消息列表）/ `stream`（流式）
- [x] 门面 `PluginLLM`（`feature.llm.ask(...)`），统一异常 `LLMError`
- [x] WebUI 第 8 页「LLM 与记忆」（5 张卡片 + 试聊）
- [x] 演示插件 `paperbot_chat`（`%ai` 系列命令）
- [x] 自检 `_tests/llm_check.py`（`pixi run check-llm`，86 项，含真实 SDK 链路）

## 人设覆盖规则（容易忘）

`import_preset_text(force=...)`：

- `force=True` → **一律覆盖**
- 已有 `manual` → 跳过（人工改过的不动）
- 已有 `import` 而新的是 `builtin` → 跳过
- **`import` → `import` 视为正常更新**（不是跳过）
- 内置人设启动时导入，所以**改内置 md 后重启就会同步**（除非被标成 manual）

## 踩坑记录

- ⚠️ **`llm.chat()` 收的是消息列表**（`[{"role":"user","content":"..."}]`），传字符串会炸；单问用 `ask()`
- ⚠️ **`set_fact()` 返回 `get_profile()`**，返回值里**没有 `source` 字段**，要看来源得查列表/详情
- ⚠️ **没有 `GET /api/llm/providers/{name}`**：单条只有 PUT/DELETE/test，请求它返回 **405 而不是 404**
- ⚠️ **时间列一律 UTC 存、北京时间展示**：DB 用 `func.now()`（UTC），对外统一走 `llm.fmt_dt()`；
  新加时间字段时别裸 `isoformat()`（会少 8 小时）
- ⚠️ **非 ASCII 的 `api_key` / `model` / `base_url`** 会让 openai/httpx 吐
  `'ascii' codec can't encode characters...` 这种天书 → 已由 `_validate_provider_fields()` 拦成中文提示；
  漏写 `http://` 同理。校验失败**不会落库**
- ℹ️ `set_provider` 里 `api_key == SECRET_MASK` 视为「不改动」，空串 `""` 才是「清空」
- ℹ️ 会话的 `plugin_name` 为空 = 各插件共享同一会话（默认按插件隔离）

## 遗留 / 后续

- **未做真机连通**：还没用真实 LLM key 在群里跑过 `%ai`（含流式与记忆触发）→ 见 `04-backlog.md`
- 试聊接口是 `POST /api/llm/chat`（**不是**早期设想的 `/llm/test`）
