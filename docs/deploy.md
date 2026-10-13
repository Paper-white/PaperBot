# 部署

## 1. 前置条件

| 项 | 要求 |
|---|---|
| 操作系统 | Windows（本机开发）/ Linux（服务器）均可 |
| Python | **3.11** |
| 环境管理 | **pixi**（推荐）；也可自行用 venv 装 `pyproject.toml` 里的依赖 |
| 协议端 | 支持 **OneBot v11** 正向 WebSocket 的实现（如 NapCat / Lagrange / SnowLuma 等） |
| 浏览器 | 渲染出图需要（Windows 复用系统 **Edge**；Linux 装 Chromium 或 `playwright install chromium`） |
| 网络 | 能访问 LLM 服务商（国内网络可能需要代理） |

## 2. 本地 Windows

### 2.1 装环境

```powershell
# 在项目根目录执行
$env:PIXI_HOME="D:\pixi"          # 按你的 pixi 安装位置改
D:\pixi\bin\pixi.exe install
```

### 2.2 写配置

```powershell
New-Item -ItemType Directory -Force .config\main | Out-Null
Copy-Item .env.example .config\main\.env
```

至少要填：

| 键 | 说明 |
|---|---|
| `SUPERUSERS` | 你的 QQ 号（JSON 数组），作为「所有者」 |
| `ONEBOT_WS_URLS` | 协议端的正向 WS 地址，如 `["ws://127.0.0.1:3001"]` |
| `ONEBOT_ACCESS_TOKEN` | 协议端令牌（如果开了） |
| `BOT_ACTIVE_GROUPS` | **允许 Bot 说话的群**（JSON 数组）；**留空 = 不向任何群发消息** |
| `WEBUI_TOKEN` | 管理后台令牌；仅本机用可留空 |

> 💡 第一次部署建议先打开 `OUTPUT_GUARD_ENABLED=true` 并只填一个测试群，
> 确认没问题再放开 —— 这是防止误发消息的保险。
> 全部键见 [`configuration.md`](configuration.md)。

### 2.3 启动

```powershell
$env:PIXI_HOME="D:\pixi"
D:\pixi\bin\pixi.exe run bot
# 或者：.\.pixi\envs\default\python.exe bot.py
```

⚠️ **必须从项目根目录启动**（代码用相对路径找 `.config/main/.env`、`data/`、`logs/`）。

启动成功时日志大致是：

```
[SUCCESS] src | 🛡️ 消息输出守卫已启用，仅允许发往群：...
[SUCCESS] webui | WebUI 已挂载：http://127.0.0.1:6188/api/health，页面 → http://127.0.0.1:6188/
[SUCCESS] nonebot | OneBot V11 | Bot <QQ号> connected
[INFO] uvicorn | Uvicorn running on http://127.0.0.1:6188
```

健康检查：

```powershell
curl http://127.0.0.1:6188/api/health
```

## 3. 常驻运行

### Windows（任务计划 / 服务）

- 简单做法：写个 `.bat` 用任务计划程序在开机时运行，或放进 `shell:startup`
- 想当服务跑可以用 [NSSM](https://nssm.cc/)：`nssm install PaperBot .\.pixi\envs\default\python.exe bot.py`

### Linux（systemd 示例）

```ini
[Unit]
Description=PaperBot
After=network-online.target

[Service]
Type=simple
User=paperbot
WorkingDirectory=/opt/PaperBot
Environment=PIXI_HOME=/opt/pixi
ExecStart=/opt/pixi/bin/pixi run bot
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> ⚠️ `WorkingDirectory` 必须是项目根目录（相对路径依赖）。
> 首次部署记得跑一次 `pixi install` 和（如需出图）`playwright install chromium`。

## 4. 反向代理 / 对外暴露

WebUI 默认只监听 `127.0.0.1:6188`。要远程访问：

1. **务必先设 `WEBUI_TOKEN`**（否则等于把管理接口裸奔到公网）
2. 用 Nginx / Caddy 反代，并建议加 HTTPS

```nginx
location / {
    proxy_pass http://127.0.0.1:6188;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

> 若把 `HOST` 直接改成 `0.0.0.0` 且 `WEBUI_TOKEN` 为空，启动时会打 WARNING —— 别无视它。

## 5. 数据与备份

| 内容 | 位置 | 说明 |
|---|---|---|
| 数据库 | `data/paperbot.db` | 所有插件共享；**含插件配置？不，配置在 `.config/`** |
| 配置 | `.config/` | 框架 `.config/main/.env` + 各插件 `.config/<插件名>/config.json`（**含密钥**） |
| 日志 | `logs/` | 每次运行一个文件，按 `LOG_KEEP_DAYS` 自动清理 |
| 人设 | 数据库 `llm_preset` + `src/core/presets/*.md`（内置） | 可从 WebUI 导出为 `.md` |

**备份建议**：`data/paperbot.db` + `.config/` 两个一起备（配置里有密钥，注意保管）。

## 6. Docker（规划中）

Docker 化部署属于 **Phase 4**，尚未实现。届时会提供 `Dockerfile` +
挂载 `.config/`、`data/`、`logs/` 三个目录的方案，进度见 [`plans/roadmap.md`](plans/roadmap.md)。

## 7. 排错

| 现象 | 排查方向 |
|---|---|
| 连不上协议端，日志有 `does not support websocket client connections` | `DRIVER` 少了 **`~websockets`**；确认 `ONEBOT_WS_URLS` 与令牌 |
| 启动报找不到 `.config/main/.env` | 必须**在项目根目录**启动；或先按 2.2 生成配置 |
| 消息发不出去（日志有 WARNING 说被拦截） | `BOT_ACTIVE_GROUPS` 没加目标群 / 私聊被守卫拦截（`OUTPUT_GUARD_ENABLED`） |
| 插件 `@` 不理人 | 门控：`%plugin <插件名>` 看状态；或 `GATE_DEFAULT_ENABLED=false` 且群不在白名单 |
| WebUI 页面「请求失败」 | 见 [`development.md`](development.md#webui) 的占位符坑 |
| 出图报浏览器相关错误 | `RENDER_BROWSER_CHANNEL` 设置不对；Linux 需 `playwright install chromium` |
| LLM 报「未配置」 | 在 WebUI「LLM 与记忆」页加一个服务商并设为默认，或填 `.env` 的 `LLM_*` |
| 时间显示不对 | 库里存 UTC、展示按北京时间；页面若少 8 小时属已知历史问题（见 [`plans/04-backlog.md`](plans/04-backlog.md)） |

更详细的开发期踩坑记录见 [`development.md`](development.md#6-踩坑备忘省下几小时的那种)。
