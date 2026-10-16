# 配置总览

> 📍 本文件是 **`.config/main/.env` 全量键**的唯一信息源（与 `src/config.py` + `webui/routes/config.py`
> 的字段定义保持一致）；其它文档如需配置键请引用此处。

PaperBot 有**两类配置**，都在 `.config/` 目录下（整个目录已 gitignore，含密钥不进版本库）：

```
.config/
├── main/.env                  ← 框架自身配置（驱动 / 端口 / 权限 / 守卫 / 渲染 / LLM 兜底…）
└── <插件名>/config.json        ← 每个插件的配置项（+ 插件自己放的文件）
```

> 💡 根目录若还留着老的 `.env`，它会被当作**低优先级兜底**读取（`.config/main/.env` 优先），
> 迁移完可以删掉。

---

## 1. 框架配置（`.config/main/.env`）

从一个模板开始：

```powershell
New-Item -ItemType Directory -Force .config\main | Out-Null
Copy-Item .env.example .config\main\.env
```

### 1.1 运行与协议端

| 键 | 说明 |
|---|---|
| `DRIVER` | NoneBot 驱动，本项目用 `~fastapi+~httpx+~websockets`（**正向 WS 必须带 `~websockets`**） |
| `HOST` / `PORT` | WebUI 监听地址 / 端口（默认 `127.0.0.1:6188`） |
| `ONEBOT_WS_URLS` | OneBot v11 正向 WS 地址（JSON 数组） |
| `ONEBOT_ACCESS_TOKEN` | 协议端令牌（**勿提交**） |
| `SUPERUSERS` | **所有者 QQ 列表**（JSON 数组），拥有全部管理权限 |
| `COMMAND_START` | 命令前缀（本项目用 `["%"]`） |
| `DATABASE_URL` | 共享数据库连接串（默认 `sqlite+aiosqlite:///data/paperbot.db`）；**改动需重启**（engine 已建好） |

### 1.2 WebUI

| 键 | 说明 |
|---|---|
| `WEBUI_ENABLE` | 是否启用 WebUI |
| `WEBUI_PREFIX` | API 前缀（默认 `/api`） |
| `WEBUI_TOKEN` | 访问令牌；**留空 = 本机免鉴权**（非回环地址且留空时启动会打 WARNING） |

### 1.3 群白名单与消息安全

| 键 | 说明 |
|---|---|
| `BOT_ACTIVE_GROUPS` | **唯一群白名单**（JSON 数组）：门控默认策略 + 消息输出守卫**共用同一份**；留空 = 不向任何群发消息。WebUI「系统配置 → 消息安全」里可点**「拉取 Bot 已加入的群」**一键填入（需 Bot 在线） |
| `GATE_DEFAULT_ENABLED` | 框架级门控默认策略：`true` = 未配置规则时跟随插件默认开关；`false` = **全局默认禁用 + 白名单群放行** |
| `OUTPUT_GUARD_ENABLED` | 是否启用消息输出守卫（开发期建议 `true`；私聊一律拦截） |

> `OUTPUT_GUARD_GROUPS` 已废弃，请改用 `BOT_ACTIVE_GROUPS`。

### 1.4 日志

| 键 | 默认 | 说明 |
|---|---|---|
| `LOG_LEVEL` | `INFO` | 终端日志级别 |
| `LOG_FILE_ENABLE` | `true` | 是否写文件日志（每次运行一个文件） |
| `LOG_FILE_LEVEL` | `DEBUG` | 文件日志级别（建议 `DEBUG`） |
| `LOG_DIR` | `logs` | 日志目录 |
| `LOG_KEEP_DAYS` | `7` | 启动时清理超过 N 天的旧日志（`<=0` 不清理） |

### 1.5 渲染（文本 / Markdown → 图片）

| 键 | 默认 | 说明 |
|---|---|---|
| `RENDER_WIDTH` | `720` | 卡片宽度（像素） |
| `RENDER_SCALE` | `2` | 截图缩放倍数（输出宽 = 宽 × 倍数） |
| `RENDER_BROWSER_CHANNEL` | `msedge` | 复用系统 Edge；换环境可改 `chromium` |
| `RENDER_BROWSER_PATH` | 空 | 手动指定浏览器可执行文件 |
| `RENDER_BG_IMAGE` | 空 | 背景贴图路径（相对项目根或绝对）；留空不叠图 |
| `RENDER_BG_IMAGE_OPACITY` | `0.22` | 贴图透明度 |
| `RENDER_BG_IMAGE_SIZE` | `58%` | 贴图大小 |
| `RENDER_BG_IMAGE_POSITION` | `bottom right` | 贴图位置 |
| `RENDER_TIMEOUT_MS` | `30000` | 单次渲染超时（毫秒） |

### 1.6 LLM（兜底）

> 推荐用 WebUI「**LLM 与记忆**」页管理服务商（**即时生效**）；这一组只是「DB 里没服务商时」的兜底。

| 键 | 默认 | 说明 |
|---|---|---|
| `LLM_ENABLE` | `true` | LLM 总开关（关掉后插件调用会抛可读错误） |
| `LLM_BASE_URL` | 空 | 兜底服务商地址（OpenAI 兼容，一般以 `/v1` 结尾） |
| `LLM_API_KEY` | 空 | 兜底密钥（**勿提交**） |
| `LLM_MODEL` | `gpt-4o-mini` | 兜底模型名 |
| `LLM_TIMEOUT` | `60` | 请求超时（秒） |
| `LLM_PROXY` | 空 | 代理（例 `http://127.0.0.1:2080`），留空直连 |
| `LLM_TEMPERATURE` | `0.8` | 采样温度 |
| `LLM_MAX_TOKENS` | `0` | 最大输出（`0` = 不传该参数） |
| `LLM_DEFAULT_PRESET` | 空 | 默认人设名（留空 = 不注入人设；内置示例 `paper_white`） |
| `LLM_STREAM_ENABLE` | `false` | 全局流式开关（插件可逐次覆盖） |
| `LLM_MEMORY_ENABLE` | `true` | 是否启用长期记忆（印象 + 关键信息 + 群记忆） |
| `LLM_CONTEXT_ROUNDS` | `10` | 上下文保留轮数（`0` = 不带历史） |
| `LLM_MAX_FACTS` | `30` | 注入提示词的关键信息条数上限 |
| `LLM_MEMORY_SCOPE_ISOLATION` | `true` | 记忆跨窗口隔离：群聊只注入「通用 + 本群」信息，不再看到私聊/别群内容；`false` = 退回旧的全量注入 |
| `LLM_MEMORY_SLOTS_ENABLE` | `true` | 记忆注入分槽保底：通用画像/窗口事实分槽，各槽至少保底若干条，避免近期事实挤掉长期画像 |
| `LLM_MEMORY_KEY_EXACT` | `true` | 受控 key 词表：自动提炼时未命中词表的自造 key 直接丢弃（`false` = 放行原样） |
| `LLM_MEMORY_USAGE_ENABLE` | `true` | 记忆用法档：注入时按 mention/tone/uncertain 分组渲染（`false` = 忽略档位、单列表渲染） |
| `LLM_MEMORY_DECAY_ENABLE` | `true` | 半衰期软衰减 + 复述加固（只降权不删；`false` = 不衰减） |
| `LLM_MEMORY_DECAY_HALF_LIFE_GLOBAL` | `60` | 通用事实半衰期（天） |
| `LLM_MEMORY_DECAY_HALF_LIFE_WINDOW` | `30` | 窗口事实（本群/私聊）半衰期（天） |
| `LLM_MEMORY_DECAY_REINFORCE` | `0.2` | 每次被注入（复述）的权重回血量 |
| `LLM_MEMORY_DECAY_TOUCH_INTERVAL` | `21600` | 复述加固写库节流（秒，默认 6 小时） |
| `LLM_MEMORY_SUPERSEDE_ENABLE` | `true` | 单值事实改口版本链：改口时保留旧值（标 superseded + supersedes_id）；`false` = 原地覆盖 |
| `LLM_MAX_FACTS_STORED` | `200` | 单用户单 scope 的事实库内条数上限（超出按最旧 auto 淘汰，manual 永不淘汰） |

> ⚠️ `LLM_IMPRESSION_EVERY` 已**停用**（仅兼容旧 `.env` 保留）：
> 记忆自动生成改由「定时总结调度器」负责（见下面 1.7 小节）。

### 1.7 聊天记录与定时总结

| 键 | 默认 | 说明 |
|---|---|---|
| `CHATLOG_ENABLE` | `true` | 全局钩子把收到的消息（群 + 私聊，含 bot）逐条写入 `chat_message` 表 |
| `CHATLOG_ARCHIVE_ENABLE` | `true` | 聊天流水归档总开关：把保留期外、且已过提炼水位线的老消息搬进 `chat_message_archive` |
| `CHATLOG_RETENTION_DAYS` | `90` | 主表保留最近 N 天（更老的进冷区；只归档已提炼的，绝不丢未提炼数据） |
| `CHATLOG_ARCHIVE_BATCH` | `5000` | 每日每批最多搬多少条（防长事务） |
| `LLM_SUMMARY_ENABLE` | `true` | 定时记忆总结 + 上下文压缩总开关 |
| `LLM_SUMMARY_INTERVAL` | `1800` | 总结周期（秒），默认 30 分钟 |
| `LLM_SUMMARY_PROVIDER` | 空 | 总结用服务商（留空 = 默认服务商） |
| `LLM_SUMMARY_MODEL` | 空 | 总结用模型（留空 = 服务商首选；建议填便宜模型） |
| `LLM_SUMMARY_MAX_MSGS` | `300` | 单次总结最多取多少条增量（超出只取最近，水位线直推） |
| `LLM_COMPRESS_THRESHOLD` | `20` | 会话消息超过该数时把旧消息压进 `llm_session.summary`（**保留窗口 = 2×`LLM_CONTEXT_ROUNDS`**，与注入窗口对齐） |

详见 [`llm.md`](llm.md)。

### 1.8 版本控制（一键拉取云端版本）

> 用途：本地开发机 push → 服务器生产环境在 WebUI「设置」页点一下拉取最新代码。
> 只拉不推，**不碰** `.config/` `data/` `logs/`。见 [`plans/05-version-control.md`](plans/05-version-control.md)。

| 键 | 默认 | 说明 |
|---|---|---|
| `VCS_ENABLE` | `true` | 是否启用版本控制（关闭则设置页不显示卡片） |
| `VCS_REMOTE` | `origin` | 远程名 |
| `VCS_BRANCH` | `main` | 跟踪分支 |
| `VCS_REMOTE_URL` | 空 | 远程地址；留空 = 用仓库已有 origin，首次部署可填公开仓库 HTTPS 地址 |
| `VCS_PROXY` | 空 | fetch 代理（如 `http://127.0.0.1:2080`），留空直连 |
| `VCS_LOG_LIMIT` | `30` | 提交历史展示条数 |
| `VCS_RESTART_MODE` | `spawn` | `spawn` = 自己拉起新进程；`exit` = 交给守护进程（systemd/NSSM） |
| `VCS_SERVICE_NAME` | 空 | 守护服务名（如 `paperbot.service` / `PaperBot`），留空则不检测守护状态 |

> ⚠️ 生产环境建议：配置 systemd（Linux）/ NSSM（Windows）守护服务并把
> `VCS_RESTART_MODE` 设为 `exit`，同时填上 `VCS_SERVICE_NAME` 以便 WebUI 显示守护状态。

---

## 2. 插件配置（`.config/<插件名>/config.json`）

插件用 `config_schema` 声明字段，WebUI「插件管理」页自动出表单；值存 JSON 文件，**改完即时生效**。

```json
{
  "greet": "你好呀",
  "_plugin": "paperbot_xxx",
  "_updated_by": "webui",
  "_updated_at": "2026-10-13 22:42:39+08:00"
}
```

- **只写显式改过的键**：没写过的键实时回落 schema 默认值 → 文件永远干净（全空时**文件会被删掉**）
- `_` 开头是框架元信息（`_plugin` / `_updated_by` / `_updated_at`），插件**不要**用 `_` 当配置键
- **可以直接手改**：类型不对不会炸（按 schema 兜一层，错了回落默认值）；文件写坏当「没配过」且**不覆盖坏文件**
- 原子写：先写临时文件再替换，避免写坏
- 插件自己的其它文件（名单 / 缓存…）放同一目录（插件代码里用 `feature.config_dir`）

### 生效时机

| 改什么 | 怎么生效 |
|---|---|
| `.config/main/.env` 的**可热更项** | 保存后点右上角**「重载」按钮**（或 QQ `%reload`）**即时生效** |
| `.config/main/.env` 的**需重启项** | 必须重启（重载会列出来提醒） |
| `.config/<插件名>/config.json`（WebUI 或手改） | 保存即**自动生效**（无需重启/重载；pluginconf 写完自动刷新插件缓存） |
| 插件开关规则 / 命令权限 / 授权 | **即时生效**（存库，QQ 命令与 WebUI 共用） |

> **可热更**：`LLM_*`、`RENDER_*`、`BOT_ACTIVE_GROUPS`、`OUTPUT_GUARD_ENABLED`、
> `GATE_DEFAULT_ENABLED`、`CHATLOG_ENABLE`、`VCS_*`、`WEBUI_TOKEN`（设置页专属）、
> **`SUPERUSERS`**（所有者名单，权限每次现读）。
> **需重启**：`DRIVER` / `HOST` / `PORT`、`COMMAND_START`、`ONEBOT_*`、`WEBUI_ENABLE` /
> `WEBUI_PREFIX`、`DATABASE_URL`、`LOG_*`。
> ⚠️ 右上角「重载」= 重读 `.env`（可热更项生效）+ **重载所有插件代码**；不会重建监听 / 连接 /
> 数据库 engine。改**插件代码**（`.py`）后也用它，不用重启整个 Bot。

---

## 3. WebUI 上改配置

- **系统配置页**：运维参数（驱动 / 守卫 / 数据库 / 渲染 / LLM 兜底等），**点分组摘要卡 → 弹窗编辑**，保存后写回 `.config/main/.env`
- **日志页**：日志参数（`LOG_LEVEL` / `LOG_FILE_ENABLE` / `LOG_FILE_LEVEL` / `LOG_DIR` / `LOG_KEEP_DAYS`）
  + 实时日志滚动 + 历史日志查看 / 删除 / 清理
- **设置页**：服务与后台参数（`HOST` / `PORT` / `WEBUI_ENABLE` / `WEBUI_PREFIX`）、
  改 WebUI 访问令牌（**即时生效**）、版本信息与版本控制
- **插件管理页**：每个插件的配置项表单（保存 / 恢复默认）；配置**保存即生效**
- **右上角「重载」按钮**：重读 `.env`（可热更项立即生效）+ 重载所有插件代码（受保护插件跳过）
- **「LLM 与记忆」页**：底部的「运行参数」分组（原系统配置页的 LLM 兜底 / 定时总结）

> 💡 **列表类配置项**（`SUPERUSERS` / `BOT_ACTIVE_GROUPS` / `COMMAND_START` / `ONEBOT_WS_URLS` 等 `list` 类型）
> 用「标签」编辑：填一个值点「添加」（或回车），已添加项显示为标签、点 `×` 删除；
> 群号输入框带候选下拉（可选已有群，也可手填）；选项较多的下拉带筛选输入框。

写回 `.config/main/.env` 时会**按行替换、保留注释与顺序**，并先备份同目录 `.env.bak`；
新值与现有值完全等价时**完全不写盘**（连备份都不产生）。
