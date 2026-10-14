# 配置总览

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

### 1.2 WebUI

| 键 | 说明 |
|---|---|
| `WEBUI_ENABLE` | 是否启用 WebUI |
| `WEBUI_PREFIX` | API 前缀（默认 `/api`） |
| `WEBUI_TOKEN` | 访问令牌；**留空 = 本机免鉴权**（非回环地址且留空时启动会打 WARNING） |

### 1.3 群白名单与消息安全

| 键 | 说明 |
|---|---|
| `BOT_ACTIVE_GROUPS` | **唯一群白名单**（JSON 数组）：门控默认策略 + 消息输出守卫**共用同一份**；留空 = 不向任何群发消息 |
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

> ⚠️ `LLM_IMPRESSION_EVERY` 已**停用**（仅兼容旧 `.env` 保留）：
> 记忆提炼改由「定时总结调度器」负责（见下面 1.8 小节）。

### 1.8 聊天记录与定时总结

| 键 | 默认 | 说明 |
|---|---|---|
| `CHATLOG_ENABLE` | `true` | 全局钩子把收到的消息（群 + 私聊，含 bot）逐条写入 `chat_message` 表 |
| `LLM_SUMMARY_ENABLE` | `true` | 定时记忆总结 + 上下文压缩总开关 |
| `LLM_SUMMARY_INTERVAL` | `1800` | 总结周期（秒），默认 30 分钟 |
| `LLM_SUMMARY_PROVIDER` | 空 | 总结用服务商（留空 = 默认服务商） |
| `LLM_SUMMARY_MODEL` | 空 | 总结用模型（留空 = 服务商首选；建议填便宜模型） |
| `LLM_SUMMARY_MAX_MSGS` | `300` | 单次总结最多取多少条增量（超出只取最近，水位线直推） |
| `LLM_COMPRESS_THRESHOLD` | `40` | 会话消息超过该数时把旧消息压进 `llm_session.summary` |
| `LLM_GROUP_CONTEXT_MSGS` | `10` | 群聊回复时从聊天流水取最近多少条群消息 |

详见 [`llm.md`](llm.md)。

### 1.9 版本控制（一键拉取云端版本）

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
| `.config/main/.env` | **需重启** |
| `.config/<插件名>/config.json`（WebUI 或手改） | 重启 / 热重载 / WebUI 保存后即时生效 |
| 插件开关规则 / 命令权限 / 授权 | **即时生效**（存库，QQ 命令与 WebUI 共用） |

---

## 3. WebUI 上改配置

- **系统配置页**：运维参数（驱动 / 守卫 / 数据库 / 渲染 / LLM 兜底），保存后写回 `.config/main/.env`
- **设置页**：服务与日志参数（`HOST` / `PORT` / `WEBUI_ENABLE` / `WEBUI_PREFIX` / `LOG_*`），
  外加改 WebUI 访问令牌（**即时生效**）、查看 / 清理日志、版本信息
- **插件管理页**：每个插件的配置项表单（保存 / 恢复默认）+ 一键热重载

写回 `.config/main/.env` 时会**按行替换、保留注释与顺序**，并先备份同目录 `.env.bak`；
新值与现有值完全等价时**完全不写盘**（连备份都不产生）。
