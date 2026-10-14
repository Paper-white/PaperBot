# PaperBot 🎭

> 基于 **NoneBot2** 的自定义人设 QQ 机器人 —— 插件化架构 + 四级权限 + WebUI 管理后台。

PaperBot 把「消息收发」交给 NoneBot2，把「插件管控 / 权限 / 共享数据 / LLM」交给自研的框架核心。
每个功能都是一个**独立插件**，由总控插件统一启停，支持**全局 / 分群 / 分成员**三级作用域，
并带一个内嵌的 **WebUI 管理后台**（八页），插件开关、权限、LLM 人设与记忆都能在浏览器里改。

---

## 1. 技术栈与简介

### 1.1 技术栈

| 层 | 选型 |
|---|---|
| 机器人框架 | **NoneBot2 v2**（异步） |
| 协议 | **OneBot v11**（`nonebot-adapter-onebot`，正向 WebSocket） |
| 语言 / 运行时 | **Python 3.11**，环境用 **pixi** 管理 |
| WebUI 后端 | 内嵌 **FastAPI**（复用 NoneBot 的 driver app） |
| WebUI 前端 | **零构建单页**（原生 JS + CSS，无打包工具） |
| 数据库 | **SQLite**（SQLAlchemy 2.0 async + `aiosqlite`），所有插件共享 |
| 大模型 | **`openai` SDK**（任何 OpenAI 兼容服务，支持多家共存 + 流式） |
| 出图 | **Playwright** 无头浏览器（复用系统 Edge / Chromium）+ Markdown + Pygments |
| 配置 | `.env`（框架，pydantic-settings）+ JSON（插件） |

### 1.2 它是个什么项目

- **自研轻量插件框架**：在 NoneBot2 之上加了一层「插件管控」，包含门控、权限、配置、钩子、热重载、
  统一日志、统一 LLM —— 业务插件只需要关心自己的功能
- **插件化**：一插件一文件夹，继承 `Feature` 即自动纳管；可以按需启停、热重载、单独配置
- **人设对话**：LLM 驱动，支持多服务商、Markdown 人设导入导出、多轮上下文与长期记忆
- **管理后台**：不用改配置文件，浏览器里就能改插件开关、权限、命令等级、LLM 配置
- **定位**：**个人自用**的机器人框架（不追求通用平台化）

---

## 2. 快速部署

### 2.1 安装环境

```powershell
# 在项目根目录执行
$env:PIXI_HOME="D:\pixi"          # 改成你的 pixi 安装位置
D:\pixi\bin\pixi.exe install
```

### 2.2 准备配置

```powershell
New-Item -ItemType Directory -Force .config\main | Out-Null
Copy-Item .env.example .config\main\.env
```

至少要填这几项（详细键表见 [`docs/configuration.md`](docs/configuration.md)）：

| 键 | 说明 |
|---|---|
| `SUPERUSERS` | 你的 QQ 号（JSON 数组），即「所有者」 |
| `ONEBOT_WS_URLS` | 协议端的正向 WS 地址，如 `["ws://127.0.0.1:3001"]` |
| `ONEBOT_ACCESS_TOKEN` | 协议端令牌（如果启用了） |
| `BOT_ACTIVE_GROUPS` | **允许 Bot 说话的群**（JSON 数组）；**留空 = 不向任何群发消息** |
| `WEBUI_TOKEN` | 管理后台令牌；仅本机访问可留空 |

### 2.3 启动

```powershell
$env:PIXI_HOME="D:\pixi"
D:\pixi\bin\pixi.exe run bot
# 或：.\.pixi\envs\default\python.exe bot.py
```

启动后打开 **`http://127.0.0.1:6188/`** 就是管理后台；健康检查：

```powershell
curl http://127.0.0.1:6188/api/health
```

> ⚠️ 必须在**项目根目录**启动（代码按相对路径找 `.config/`、`data/`、`logs/`）。
> 首次部署建议保持 `OUTPUT_GUARD_ENABLED=true` 并只填一个测试群，确认没问题再放开。

### 2.4 部署文档

常驻运行（systemd / 任务计划）、反向代理、备份、排错 → **[`docs/deploy.md`](docs/deploy.md)**

---

## 3. 项目文件目录索引

```
PaperBot/
├── bot.py                      # 启动入口
├── pyproject.toml              # pixi 工作区 + 依赖 + 任务（bot / new-plugin / check-*）
├── .env.example                # 配置模板（复制到 .config/main/.env）
├── README.md                   # 本文件（面向使用者）
│
├── .config/                    # ★ 本地配置（整个目录不提交）
│   ├── main/.env               #   框架配置
│   └── <插件名>/config.json     #   各插件配置 + 插件自己的文件
│
├── plugins/                    # ★ 功能插件（一插件一文件夹，目录名 = 插件注册名）
│   ├── paperbot_console/       #   总控插件：启停 / 权限 / 黑名单
│   ├── paperbot_chat/          #   人设对话插件（LLM + 记忆）
│   └── paperbot_ping/          #   在线状态测试插件（%ping → pong）
│
├── src/
│   ├── config.py               # 项目配置（pydantic-settings）
│   └── core/                   # ★ 自研框架核心
│       ├── feature.py          #   插件基类 Feature（命令 / 配置 / 钩子 / logger / LLM 门面）
│       ├── loader.py           #   插件加载器（扫描 + 版本校验）
│       ├── registry.py         #   内存插件注册表
│       ├── gate.py             #   三级门控（谁能用插件）
│       ├── guard.py            #   消息输出守卫（能往哪发消息）
│       ├── permission.py       #   四级权限解析
│       ├── cmdperm.py          #   命令权限（可在 WebUI 改）
│       ├── service.py          #   业务逻辑（QQ 命令与 WebUI 共用）
│       ├── pluginconf.py       #   插件配置项（JSON 文件读写）
│       ├── hooks.py            #   生命周期钩子
│       ├── version.py          #   框架版本 + requires 校验
│       ├── reload.py           #   插件热重载
│       ├── logger.py           #   统一日志出口
│       ├── logging.py          #   日志文件输出
│       ├── llm.py              #   统一 LLM 服务（服务商 / 人设 / 会话 / 记忆）
│       ├── presets/            #   内置人设（*.md）
│       ├── render.py           #   渲染核心（文本 / Markdown → PNG）
│       ├── templates/          #   渲染模板
│       ├── models.py           #   共享 ORM 表定义
│       ├── database.py         #   共享数据库连接
│       └── bootstrap.py        #   启动 / 关闭流程
│
├── webui/                      # ★ WebUI 管理后台
│   ├── api.py                  #   挂载入口（/api + 页面 + 静态资源）
│   ├── routes/                 #   各页面接口
│   └── static/                 #   零构建前端
│
├── scripts/                    # 开发工具
│   └── new_plugin.py           #   插件脚手架（pixi run new-plugin）
│
├── _tests/                     # 自检脚本（pixi run check-*）
│   ├── config_check.py         #   配置体系自检（pixi run check-config）
│   ├── llm_check.py            #   LLM 服务自检（pixi run check-llm）
│   └── vcs_check.py            #   版本控制自检（pixi run check-vcs）
│
├── docs/                       # ★ 文档（入口：docs/index.md）
│   ├── index.md                #   文档导航
│   ├── commands.md             #   命令手册
│   ├── architecture.md         #   架构与实现
│   ├── configuration.md        #   配置总览
│   ├── deploy.md               #   部署与排错
│   ├── api.md                  #   WebUI / REST API
│   ├── development.md          #   开发指南与踩坑
│   ├── plugin-dev.md           #   插件开发协议
│   ├── llm.md                  #   LLM 使用指南
│   └── plans/                  #   功能与开发计划（一个功能一个文件）
│
├── assets/                     # 必要素材（被代码引用的图片等）
├── data/                       # 运行时数据（SQLite，不提交）
└── logs/                       # 运行日志（不提交）
```

---

## 4. 功能简介

### 4.1 插件系统

- **一插件一文件夹**：放进 `plugins/` 即被加载，模块顶层创建 `Feature(...)` 就自动纳管
- **总控启停**：`%on` / `%off` / `%plugin` / `%reset`，支持**全局 / 分群 / 分成员**三级作用域，就近优先
- **插件配置项**：插件声明字段 → WebUI 自动出表单，改完即时生效（值存 `.config/<插件名>/config.json`）
- **热重载**：改完代码不用重启，WebUI 点一下即可（配置不丢）
- **生命周期钩子**：`on_load` / `on_unload` / `on_enable` / `on_disable`
- **元信息与版本校验**：`author` / `repo` / `requires`，不兼容框架版本会拒绝加载并给出提示
- **脚手架**：`pixi run new-plugin <名字>` 一键生成骨架 → 详见 [`docs/plugin-dev.md`](docs/plugin-dev.md)

### 4.2 权限与安全

- **四级权限**：`0` 黑名单（不回应）/ `1` 普通用户 / `2` 群管理员（群角色自动识别 + 手动授权）/ `3` 所有者
- **命令权限可配置**：插件只声明默认等级，实际等级在 WebUI「命令管理」页改，**即时生效**
- **消息输出守卫**：开发期可锁死消息出口，只允许发往白名单群（私聊一律拦截），防止误发

### 4.3 人设对话（LLM）

- **多服务商**：任何 OpenAI 兼容服务，可存多套并指定默认（`.env` 兜底），WebUI 里可测连通性
- **人设管理**：Markdown（front-matter + 正文），支持内置 / 导入 / 人工三种来源，可导入导出
- **上下文**：按「个人 / 群聊」分作用域，保留轮数可配
- **长期记忆**：自动提炼**用户印象**与**关键信息**，可人工覆盖，也可在 WebUI 里查看和编辑
- **流式输出**：全局开关 + 单次命令可覆盖
- **插件即用**：一行 `await feature.llm.ask("你好")` → 详见 [`docs/llm.md`](docs/llm.md)

### 4.4 WebUI 管理后台

浏览器打开 `http://127.0.0.1:6188/`，八个页面：

| 页面 | 能干什么 |
|---|---|
| **状态** | 运行时长、连接、插件统计、守卫与存储情况 |
| **系统配置** | 驱动 / 守卫 / 数据库 / 渲染 / LLM 兜底等运维参数 |
| **黑白名单** | 黑名单、分群管理员、所有者，查看与增删 |
| **插件管理** | 插件开关与规则、配置项表单、一键热重载 |
| **LLM 与记忆** | 服务商 / 人设 / 会话 / 用户印象与关键信息、在线试聊 |
| **数据库管理** | 浏览各数据表（只读） |
| **命令管理** | 改每条命令的权限等级（即时生效） |
| **设置** | 服务与日志参数、访问令牌、日志查看与清理、版本信息 |

### 4.5 其它

- **日志留档**：每次运行在 `logs/` 生成独立日志文件，可在 WebUI 查看 / 清理，自动清理过期日志
- **文本渲染出图**：Markdown / 文本 → PNG（表格、代码高亮、任务列表都支持），插件可一行调用
- **共享数据库**：所有插件共用一套 SQLite，天然数据互通；插件继承 `Base` 即自动建表
- **版本控制**：WebUI「设置」页可一键拉取云端最新代码并重启（本地 push → 生产环境一键更新，
  配置文件与数据不受影响），支持切到任意历史 commit 回滚

### 4.6 命令速查

> 前缀默认 `%`，标【所有者】的仅 `SUPERUSERS` 可用。完整手册见 [`docs/commands.md`](docs/commands.md)。

| 命令 | 说明 |
|---|---|
| `%help` | 列出当前已启用的插件 |
| `%help <插件名>` | 查看某插件的命令与介绍 |
| `%on` / `%off <插件名> [目标]` | 启用 / 禁用插件【所有者】 |
| `%plugin <插件名>` | 查看插件在各作用域的开关状态【所有者】 |
| `%admin add\|del\|black\|unblack <QQ>` | 管理员与黑名单管理【所有者】 |
| `%ai <内容>` | 与 AI 人设对话（`%ai 群` / `%ai 流式` / `%ai 印象` / `%ai 记住` / `%ai 重置` / `%ai 人设` / `%ai 状态`） |
| `%ping` | 在线状态测试（回复 pong） |

---

## 5. 参考项目与致谢

- [**NoneBot2**](https://nonebot.dev) —— 本项目的地基，负责消息收发与事件分发
- [**nonebot-adapter-onebot**](https://github.com/nonebot/adapter-onebot) —— OneBot v11 适配器
- [**AstrBot**](https://docs.astrbot.app) —— 插件规范与 WebUI 交互的重要参考
  （对比记录见 [`docs/plans/01-plugin-dev-protocol.md`](docs/plans/01-plugin-dev-protocol.md)）
- [**Pallas-Bot**](https://github.com/PallasBot/Pallas-Bot) —— 设计思路参考（本项目**不基于**它开发）
- `pixi` · `SQLAlchemy` · `FastAPI` · `Playwright` · `python-markdown` · `Pygments` · `openai` 等开源项目

感谢上述项目的作者与社区 🙏

---

## 📄 License

仅供个人学习与自用。
