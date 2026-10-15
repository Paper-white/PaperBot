# WebUI 配置修复 + 配置热重载 + 捂嘴命令

- 状态：进行中
- 开始：2026-10-15
- 相关文件：`webui/static/assets/app.js`、`webui/routes/config.py`、`webui/routes/llm.py`、
  `webui/routes/settings.py`、`src/config.py`、`plugins/paperbot_chat/paperbot_chat.py`、
  `plugins/paperbot_console/paperbot_console.py`
- 相关文档：[`docs/configuration.md`](../configuration.md)、[`docs/commands.md`](../commands.md)、
  [`docs/llm.md`](../llm.md)、[`docs/api.md`](../api.md)

## 目标

修掉 2026-10-14 真机测试暴露的 4 个问题，并新增「捂嘴」能力：

1. **捂嘴命令**：群里说「小白纸闭嘴」能强制结束该群 LLM 自动回复（对话模式）；
   顺便把「bot 昵称」做成可配置，并且昵称本身也能像艾特一样触发对话模式。
2. **配置分组归位**：WebUI「系统配置」页里的「LLM 兜底」「定时记忆总结」两组
   移到「LLM 与记忆」页；系统配置页不再出现这两组。
3. **配置热重载**：改完 `.config/main/.env` 后，能在不重启的前提下让**可热更**的项生效
   （WebUI 按钮 + QQ `%reload`）。
4. **修 WebUI 保存失效**：系统配置浮窗点「保存本节」无法保存（Bug）。

验收标准：

- 群里发「小白纸，闭嘴」→ 该群对话模式关闭，bot 回一句确认；`%ai 停` 仍可用
- 群里发「小白纸」→ 视同艾特，开启对话模式（可配置关闭该行为）
- LLM 页底部有「运行参数（.env 兜底 + 定时总结）」分组摘要卡，点开可编辑并保存
- 系统配置页不再有 LLM/总结分组
- 系统配置浮窗改一个值点保存 → 写入 `.config/main/.env` 且 toast 提示成功
- WebUI 设置页点「热重载配置」/ 群里发 `%reload` → 返回「已热更 / 需重启」清单

## 方案与决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 捂嘴触发 | 消息包含**固定搭配** `<昵称><触发词>`（中间不加任何字符），如「小白纸闭嘴」 | 精确，群里别人互说「闭嘴」不会误伤 |
| 捂嘴权限 | **任何人可用** | 白纸姐姐的需求：顺手 |
| 捂嘴范围 | 仅关闭**本群**对话模式；仅对话模式开启时响应 | 与 `%ai 停` 语义一致，避免无谓回复 |
| bot 昵称 | 插件配置 `bot_names`（**可多个**，默认 `["小白纸"]`） | 与人设解耦、可随时改；默认「小白纸」避开「白纸姐姐」误命中 |
| 昵称触发 | `nickname_trigger`（默认开）：消息含任一昵称 → 视同艾特开启/续期对话模式 | 复用艾特链路，少打一个 @ |
| 归一化 | 匹配用原文（不做去空格/去标点） | 「中间加字符不生效」是明确的硬要求，简单即可靠 |
| LLM/总结配置归位 | 后端把两组抽成 `LLM_SECTIONS`，`_FIELD_INDEX` **仍保留全部键** | 否则 `PUT /api/config` 会以「不支持的配置项」拒写 |
| LLM 页呈现 | 复用全站「摘要卡 + 浮窗」（`configBlock`/`openConfigModal`） | 风格统一，标签/chips 等交互零成本复用 |
| 数据库 / 渲染配置卡 | **从系统配置页移除**，字段定义保留（`HIDDEN_SECTIONS`）以兼容 `PUT` 校验 | 数据库页已展示连接串路径（重复）；渲染模板已定稿不需 UI 配置 |
| 热重载实现 | `reload_settings()` 重建 `Settings()` 后**逐字段原地改写旧对象** | `from src.config import settings` 是对象引用，原地改则所有模块立即可见 |
| 热重载入口 | **右上角全局「重载」按钮** `POST /api/reload`（= 重读 .env + 重载所有插件）+ QQ `%reload` | 全局功能放全局位置；插件详情里的单独热重载按钮移除，界面更干净 |
| 插件配置生效 | 保存即**自动热更新**（`pluginconf.write_config` → `feature.load_config()`），无需热重载 | 热重载只解决「插件代码 `.py` 改动」，与配置无关 |
| NoneBot 驱动键 | 单独比对 `nonebot.get_driver().config`：`SUPERUSERS` 可热更，其余归「需重启」 | `HOST/PORT/DRIVER/COMMAND_START/ONEBOT_*` 不在 `Settings` 里，只比 `Settings` 会漏判 |

### 可热更 vs 需重启（判定依据：调用时是否现读 `src.config.settings` / `driver.config`）

| 可热更 ✅ | 需重启 ❌ |
|---|---|
| `LLM_*`（每次调用现读） | `DRIVER` / `HOST` / `PORT`（NoneBot 驱动配置） |
| `RENDER_*`（渲染时现读） | `COMMAND_START` / `ONEBOT_*`（NoneBot 驱动配置） |
| `BOT_ACTIVE_GROUPS` / `OUTPUT_GUARD_ENABLED` / `GATE_DEFAULT_ENABLED` | `WEBUI_ENABLE` / `WEBUI_PREFIX`（路由已挂载） |
| `CHATLOG_ENABLE` / `VCS_*` | `DATABASE_URL`（engine 已建） |
| `SUPERUSERS`（NoneBot `driver.config`，权限每次现读） | `LOG_*`（sink / 终端级别已挂） |
| `LLM_SUMMARY_ENABLE`（`interval` 需下一轮或重启才变） | — |

> ⚠️ **真机反馈修正（2026-10-15）**：首版 `reload_settings()` 只遍历 `src.config.Settings` 的字段，
> 而 `SUPERUSERS` 等属 NoneBot 驱动配置 → 用户改了超管再点热重载，返回「无变动」。
> 追加 `_reload_nonebot_config()` 单独比对 `driver.config`（`SUPERUSERS` 原地 `setattr` 即生效）。
> 另：热重载按钮从「设置」页移到「系统配置」页（配置就在这改）。

### 问题 4 根因

`411d2f4` 把系统配置改成「摘要卡 + 浮窗」时，浮窗里 **`data-config-block` 只包住了字段，
`[data-config-save]` 按钮落在容器外**；保存逻辑 `btn.closest('[data-config-block]')` 返回 `null`，
`collectConfig(null)` 抛 `TypeError`，请求根本没发出（连 `.catch` 都没走到，表现为"点了没反应"）。
插件配置块 `data-plugin-config-block` 结构正确，故只有系统配置坏。

## 接口契约（各 lane 按此对齐）

- `POST /api/config/reload`（B）→ `{ok, changed:[...], hot:[...], restart:[...], message}`
- `POST /api/reload`（二轮，全局重载）→
  `{ok, config:{changed,hot,restart,message}, plugins:{reloaded:[],failed:[{name,error}],skipped:[]}, message}`
  （= 重读 `.env` + 重载所有可重载插件；受保护插件进 `skipped`）
- `GET /api/llm/status`（C）新增字段 `env_sections`：`[{id,name,note,fields:[...]}]`
  （= 旧 `llm` + `summary` 两组，字段结构同 `/config` 的 section）
- `src.config.reload_settings()`（B）同步函数，返回 `{changed, hot, restart, message}`

## 落地清单

- [x] A：`paperbot_chat` 新增 `bot_names` / `mute_words` / `nickname_trigger` 配置，昵称触发 + 捂嘴处理
- [x] B：`src/config.py` 新增 `reload_settings()`；`webui/routes/config.py` 拆 `LLM_SECTIONS` + `POST /config/reload`
- [x] C：`webui/static/assets/app.js` 修保存 Bug + LLM 页配置区 + 系统配置页热重载按钮；`webui/routes/llm.py` 返回 `env_sections`
- [x] D：`paperbot_console` 新增 `%reload`（调用 `reload_settings`）
- [x] 追加：`reload_settings()` 补 `_reload_nonebot_config()`（修 `SUPERUSERS` 误报「无变动」）
- [x] 追加：热重载按钮从「设置」页移到「系统配置」页
- [x] 自检：`check-config` / `check-llm` / `check-vcs` 全通过
- [x] 文档同步：本文件 + `plans/README.md` + `configuration.md` + `commands.md` + `llm.md` + `api.md`
- [ ] 真机验收：捂嘴 / 昵称触发 / LLM 页配置区 / 系统配置保存 / 热重载（待白纸姐姐实机确认）
- [ ] （可选）`GET /api/settings` 的 `reload_hint` 字段未实现（仅接口契约里的可选项，前端未用到）

## 遗留 / 后续

- 热重载暂不覆盖 NoneBot 驱动配置（需重启）
- 捂嘴只关本群对话模式，不打断**已生成/流式中**的那一条回复
