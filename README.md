# PaperBot 🎭

> 基于 **NoneBot2** 的自定义人设 QQ 机器人 —— 插件化架构 + 四级权限 + WebUI 管理后台。

PaperBot 在 NoneBot2 之上自研了一层轻量「插件框架」，把「消息收发」交给 NoneBot2，
把「插件管控 / 权限 / 共享数据」交给自己的框架核心。任何一个功能插件都能被
**总控插件（`paperbot_console`）** 统一启停，并支持**全局 / 分群 / 分成员**三级作用域。

---

## ✨ 特性

- 🎭 **自定义人设对话**（LLM 驱动，Phase 2 规划中）
- 🧩 **自研插件系统**：每插件一个独立文件夹，继承 `Feature` 即自动纳管（加载即注册）
- ⚙️ **插件级配置项**：插件用 `config_schema` 声明字段，WebUI「插件管理」自动出表单、改完即时生效（值存 SQLite）
- 🔄 **插件热重载**：WebUI 一键重载（`importlib.reload`），不用重启进程、配置不丢
- 🪝 **生命周期钩子**：`on_load` / `on_unload` / `on_enable` / `on_disable`，支持同步 / 异步与三种签名
- 🏷️ **元信息与版本校验**：`author` / `repo` / `requires`，启动时校验框架版本，不兼容即拒绝加载
- 🧰 **插件脚手架**：`pixi run new-plugin <名字>` 一键生成骨架 + `docs/plugin-dev.md` 开发协议
- 🛡️ **四级权限**：黑名单 / 普通用户 / 群管理员 / 所有者
- 🔑 **命令权限可配置**：插件声明默认等级，实际等级由框架存库维护，WebUI 随时改、即时生效
- 🎚️ **三级门控**：成员规则 > 群规则 > 全局规则 > 插件默认开关（就近优先）
- 🔒 **消息输出守卫**：开发期锁死消息出口，仅允许发往白名单群，杜绝误发
- 🗄️ **共享数据库**：所有插件共用同一 SQLite（SQLAlchemy 2.0 async + aiosqlite），天然数据互通
- 🖥️ **WebUI 管理后台**：内嵌 FastAPI + 零构建单页前端，状态 / 配置 / 黑白名单 / 插件 / 数据库 / 命令 / 设置七大页面（`http://127.0.0.1:6188/`）
- 📝 **日志留档**：每次运行在 `logs/` 下生成一个独立日志文件，可在 WebUI 查看 / 清理；插件日志统一出口 `src.core.logger`（自动带插件名前缀）
- 🖼️ **文本渲染出图**：Markdown / 文本 → PNG（Playwright 无头浏览器 + 二次元风模板，支持表格 / 代码高亮 / 任务列表）
- 🔌 **协议端**：SnowLuma（OneBot v11）

---

## 🧱 项目结构

```
PaperBot/
├── bot.py                  # 启动入口
├── plugins/                # 功能插件目录（每个插件一个独立文件夹）
│   ├── paperbot_console/           # 总控插件（命令 / 权限 / 门控）
│   │   └── paperbot_console.py     # ← 插件具体代码文件
│   └── paperbot_echo/              # 示例插件（复读）
│       └── paperbot_echo.py
├── src/
│   ├── config.py           # 项目配置（pydantic-settings）
│   └── core/               # 自研框架核心
│       ├── models.py       # 共享 ORM Base + 公共表
│       ├── database.py     # 共享 async engine / get_session() / 启动自动补列
│       ├── loader.py       # 插件加载器（扫描 plugins/{名}/{代码}.py，导入 + requires 校验）
│       ├── registry.py     # 内存插件注册表
│       ├── feature.py      # 功能插件基类 Feature（命令 / 配置项 / 钩子 / logger）
│       ├── pluginconf.py   # 插件级配置项（schema 描述 + SQLite 存储 + 缺省值合并）
│       ├── hooks.py        # 生命周期钩子分发（load / unload / enable / disable）
│       ├── version.py      # 框架版本 + 插件 requires 校验
│       ├── reload.py       # 插件热重载（importlib.reload + matcher / 前缀清理）
│       ├── logger.py       # 统一日志出口（自动带 [插件名] 前缀）
│       ├── gate.py         # 门控 Rule
│       ├── guard.py        # 消息输出守卫
│       ├── permission.py   # 四级权限解析
│       ├── cmdperm.py      # 命令权限（Permission 子类：查 command_rule 表判定等级）
│       ├── service.py      # 门控 / 权限 / 命令权限业务逻辑（命令 & WebUI 共用）
│       ├── logging.py      # 日志文件输出（logs/ 下每次运行一个文件）
│       ├── render.py       # 渲染核心：文本 / Markdown → PNG（Playwright）
│       ├── templates/      # 渲染模板（markdown.html + markdown.css）
│       └── bootstrap.py    # 启动钩子（建表 + 同步插件元信息 + 灌配置 + 触发 on_load）
├── scripts/                # 开发脚本（new-plugin 脚手架生成器）
├── docs/                   # 开发文档（plugin-dev.md = 插件开发协议）
├── assets/                 # 必要素材（被代码/配置引用才入库）
│   └── Q-OC.png            # 渲染卡片背景贴图（RENDER_BG_IMAGE 引用）
├── webui/                  # WebUI 管理后台
│   ├── api.py              # 挂载入口：/api 路由 + 页面 + /assets 静态资源
│   ├── deps.py             # 鉴权、在线 Bot 访问、通用小工具
│   ├── envfile.py          # .env 读写（按行替换，保留注释与顺序）
│   ├── routes/             # 各页面接口：status / config / permissions / groups / plugins / commands / database
│   └── static/             # 零构建前端（index.html + assets/app.js + assets/style.css）
├── data/                   # 运行时数据（SQLite，不提交）
├── logs/                   # 运行日志（每次运行一个文件，不提交）
└── pyproject.toml          # pixi 工作区配置
```

---

## 🚀 快速开始（Windows + pixi）

### 1. 安装环境

```powershell
# 在项目根目录执行
$env:PIXI_HOME="D:\pixi"
D:\pixi\bin\pixi.exe install
```

### 2. 配置 `.env`

复制 `.env.example` 为 `.env`，按需填写：

```powershell
Copy-Item .env.example .env
```

关键项：

| 变量 | 说明 |
|---|---|
| `SUPERUSERS` | **所有者 QQ 列表**（JSON 数组），拥有全部管理权限 |
| `COMMAND_START` | 命令前缀（本项目用 `%`） |
| `ONEBOT_WS_URLS` | OneBot 正向 WS 地址，如 `["ws://<服务器IP>:3001"]` |
| `ONEBOT_ACCESS_TOKEN` | OneBot 访问令牌（从协议端获取） |
| `BOT_ACTIVE_GROUPS` | **Bot 允许活跃/说话的白名单群**（JSON 数组）——门控默认策略与消息输出守卫**共用同一份**；留空 = 不向任何群发消息 |
| `GATE_DEFAULT_ENABLED` | 框架级门控默认策略：`true` = 未配置规则时跟随插件默认开关；`false` = 全局默认禁用，只有白名单群才跟随插件默认值 |
| `OUTPUT_GUARD_ENABLED` | 是否启用消息输出守卫（开发期建议 `true`；私聊一律拦截） |

### 3. 启动机器人

```powershell
# 方式一：pixi 任务
$env:PIXI_HOME="D:\pixi"
D:\pixi\bin\pixi.exe run bot

# 方式二：直接用环境内的 Python
.\.pixi\envs\default\python.exe bot.py
```

启动成功后日志会出现：

```
[SUCCESS] src | 🛡️ 消息输出守卫已启用，仅允许发往群：...
[SUCCESS] nonebot | OneBot V11 | Bot <QQ号> connected
[INFO] uvicorn | Uvicorn running on http://127.0.0.1:6188
```

健康检查：

```powershell
curl http://127.0.0.1:6188/api/health
```

---

## 🎮 命令一览

> 命令前缀为 `%`（由 `.env` 的 `COMMAND_START` 决定）。标注【所有者】的命令仅 `SUPERUSERS` 可用。

### 总控插件（`paperbot_console`）

| 命令 | 说明 | 权限 |
|---|---|---|
| `%help` | 列出**当前已启用**的插件 | 所有用户 |
| `%help <插件名>` | 查看某插件的**命令与介绍** | 所有用户 |
| `%on <插件名> [目标]` | 在对应作用域**启用**插件（省略=本群；`all`=全局；`用户:<QQ>`=指定成员） | 【所有者】 |
| `%off <插件名> [目标]` | 在对应作用域**禁用**插件（省略=本群；`all`=全局；`用户:<QQ>`=指定成员） | 【所有者】 |
| `%plugin <插件名>` | 查看该插件在**全局 / 各群**的开关状态 | 【所有者】 |
| `%admin add <QQ> [群号]` | 授予该用户 **2 级管理员**（仅对应群） | 【所有者】 |
| `%admin del <QQ> [群号]` | 移除手动授予的管理员 | 【所有者】 |
| `%admin black <QQ>` | 加入**全局黑名单**（0 级） | 【所有者】 |
| `%admin unblack <QQ>` | 移出全局黑名单 | 【所有者】 |
| `%reset <插件名> [作用域]` | 清除某作用域规则（额外提供） | 【所有者】 |

> `%on` / `%off` / `%reset` 的目标/作用域写法：`全局`(`all`) / `本群` / `群:<群号>` / `用户:<QQ>`。

### 示例插件（`paperbot_echo`）

| 命令 | 说明 | 默认权限 |
|---|---|---|
| `%echo <内容>`（或 `%复读 <内容>`） | 复读内容（用于验证门控与输出守卫） | 普通用户 |

> **命令权限是「框架维护 + WebUI 可改」的**：插件只在注册命令时声明一个**默认等级**
> （`feature.command("echo", level=1)`），实际等级存在 `command_rule` 表里，
> 可在 WebUI「命令管理」页随时下拉修改 / 恢复默认，**改完即时生效、无需重启**；
> 未改动时永远跟随插件声明的默认值（表里不留记录）。
> 受保护插件（如总控）的命令**锁定不可改**（接口返回 409）。

---

## 🛡️ 权限等级

| 等级 | 名称 | 说明 |
|:---:|---|---|
| `0` | 黑名单 | **不回应任何消息**（一票否决，连受保护插件也不回应） |
| `1` | 普通用户 | 默认权限（普通群友） |
| `2` | 群管理员 | QQ 群角色**自动识别**（群主/管理员），或所有者**手动授权**（仅对应群） |
| `3` | 所有者 | `SUPERUSERS`，拥有全部管理权限 |

解析优先级（就近优先）：**所有者 → 本群手动授权 → 全局手动授权 → 群角色自动判定 → 默认普通用户**。

---

## 🎚️ 三级门控（插件启停）

```
成员规则存在 → 听成员（True=白名单放行 / False=黑名单拦截）
  否则 群规则存在 → 听群
    否则 全局规则存在 → 听全局
      否则 用插件 default_enabled（默认 True）
```

- 受保护插件（`protected=True`，如总控插件自身）永远放行，不能被禁用。
- 规则存表 `plugin_rule`，唯一键 `(plugin_name, scope_type, scope_id)`。

**框架级默认门控策略（可选）**：把 `GATE_DEFAULT_ENABLED=false` 打开后，规则链的最后一环
（「插件默认开关」）会被替换成「**全局默认禁用 + 白名单群放行**」——
即未显式配置规则的群/用户一律不理，只有 `BOT_ACTIVE_GROUPS` 里的群才跟随插件默认开关。
好处是**一条配置对所有插件生效**，不用逐个插件去禁群；与消息输出守卫**共用同一份群白名单**，
避免两处维护。

> 🛡️ **出口保险**：门控管「进」、守卫管「出」，二者职责不同，不能互相替代——
> 门控无法拦截**主动发送**（定时推送 / 广播 / 变量拼错群号）与跨群误发，守卫则能（它在 `Bot.call_api` 层兜底，连受保护插件也拦）。

---

## 🌐 WebUI 管理后台

浏览器打开 **`http://127.0.0.1:6188/`** 即可使用控制台（内嵌 FastAPI + 零构建单页前端，
  左侧七个菜单：状态 / 系统配置 / 黑白名单 / 插件管理 / 数据库管理 / 命令管理 / 设置）。

```
页面      /                                       控制台（webui/static/）
GET    /api/health                                       健康检查（不鉴权）
GET    /api/status                                       运行状态、在线时长、连接、统计
GET    /api/config                                       配置分组（密钥脱敏）
PUT    /api/config                                       写回 .env（留空=不改，自动备份 .env.bak）
GET    /api/plugins                                      插件总览
GET    /api/plugins/{name}                               插件详情 + 规则 + 命令 + 配置 schema + 钩子
PUT    /api/plugins/{name}/rule                          设置规则 {scope_type, scope_id, enabled}
DELETE /api/plugins/{name}/rule?scope_type=&scope_id=    删除规则
GET    /api/plugins/{name}/config                        读插件配置项（含 schema、当前值、脱敏）
PUT    /api/plugins/{name}/config                        写插件配置项 {values:{...}}（即时生效）
DELETE /api/plugins/{name}/config?key=                   清配置项（key 省略 = 全部清空，回落默认值）
POST   /api/plugins/{name}/reload                        热重载插件（受保护插件 409）
GET    /api/permissions                                  权限记录（黑名单 / 分群管理员 / 其它）
PUT    /api/permissions                                  写入授权 {user_id, level, group_id?}
DELETE /api/permissions?user_id=&group_id=               删除授权
GET    /api/groups                                       群列表（含规则与授权）
GET    /api/groups/{group_id}/members                    群成员 + 各人权限等级
GET    /api/commands                                     按插件分组的命令清单（含默认/实际权限等级）
PUT    /api/commands/{plugin}/{command}                  改命令权限 {level:1|2|3}（即时生效）
DELETE /api/commands/{plugin}/{command}                  恢复命令默认权限
GET    /api/database                                     数据库与表清单（只读）
GET    /api/database/tables/{name}?limit=&offset=        分页读取表数据（只读）
GET    /api/settings                                     版本信息 / 运行时 / 日志 / 安全设置 + 可改配置组
PUT    /api/settings/token                               修改 WebUI 访问令牌（写 .env，立即生效）
GET    /api/settings/logs/{name}?lines=                  读取日志文件尾部（默认 200 行）
DELETE /api/settings/logs                                清理全部日志
DELETE /api/settings/logs/{name}                         删除单个日志文件
```

- 页面除 `http://127.0.0.1:6188/` 外，也支持直接访问 `/<页面名>`（如 `/status`、`/settings`）。
- 「系统配置」页放运维参数（驱动 / 守卫 / 数据库 / 渲染）；
  **「设置」页放服务与日志参数**（`HOST` / `PORT` / `WEBUI_ENABLE` / `WEBUI_PREFIX` / `LOG_*`），
  外加：改 WebUI 访问令牌（**即时生效**，无需重启）、查看/清理日志、看版本信息。
- 「命令管理」页可改每条命令的实际权限等级（见上方「命令权限」说明）。
- 「插件管理」页除了开关 / 规则，还能**改插件自己的配置项**（插件用 `config_schema` 声明什么，页面就出什么表单）
  和**一键热重载**（受保护插件不提供热重载）。

- 若 `.env` 设置了 `WEBUI_TOKEN`，除 `/api/health` 与页面本身外的请求需带
  `Authorization: Bearer <token>`（定长比较）。
- 配置改动会写回 `.env` 并自动生成 `.env.bak`，**需重启 Bot 生效**；
  插件开关 / 规则 / 权限改动**即时生效**（与 QQ 命令共用同一套数据）。
- ⚠️ 绑定非回环地址且未设 `WEBUI_TOKEN` 时启动会打 WARNING（管理接口裸奔）。

---

## 📝 日志

- 终端日志级别由 `LOG_LEVEL` 控制；
- 开启 `LOG_FILE_ENABLE=true` 后，**每次运行**都会在 `LOG_DIR`（默认 `logs/`）下生成一个独立日志文件
  `paperbot-YYYYmmdd-HHMMSS.log`，框架 / 插件 / WebUI 的日志都会写进去（级别由 `LOG_FILE_LEVEL` 控制，建议 `DEBUG`）；
- 启动时自动清理超过 `LOG_KEEP_DAYS` 天的旧日志；
- 可以直接在 WebUI 的「设置」页里查看日志尾部、删除单个日志或一键清理。

```powershell
# 找最新一次运行的日志
Get-ChildItem logs\*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1
```

---

## 🧩 开发一个插件

> 📖 完整的插件开发协议见 **[`docs/plugin-dev.md`](docs/plugin-dev.md)**（配置项 / 钩子 / 日志 / 出图 / 热重载 / 检查清单）。

**一键生成骨架**（目录名 = 插件注册名）：

```powershell
$env:PIXI_HOME="D:\pixi"
D:\pixi\bin\pixi.exe run new-plugin paperbot_demo --desc "示例插件" --command demo
```

生成结果：

```
plugins/
└── paperbot_demo/
    └── paperbot_demo.py     # 插件具体代码文件
```

> 加载器 `src/core/loader.py` 会自动扫描 `plugins/` 下**所有子目录中的 `.py` 文件**并导入
> （也兼容目录内放 `__init__.py`、或直接把单文件放在 `plugins/` 根下），并顺带校验插件的 `requires`。
> **无需手工注册**：模块被 import 时创建 `Feature(...)` 即自动登记到框架注册表。

在手写的代码文件里用 `Feature` 注册（**不要**直接调用 `nonebot.on_command`）：

```python
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg
from src.core.feature import Feature
from src.core.models import LEVEL_USER

# 插件注册名建议 paperbot_ 前缀；display_name 是给用户看的中文名
feature = Feature(
    "paperbot_demo",
    display_name="示例",
    description="一个示例插件",
    author="paper_white",
    repo="https://github.com/paper-white/PaperBot",   # 显示在 WebUI 插件详情
    requires=">=1.0,<2.0",                            # 兼容的框架协议版本，不满足则拒绝加载
    config_schema=[                                   # 插件配置项 → WebUI 自动出表单
        {"key": "greet", "label": "问候语", "type": "string", "default": "hello~"},
        {"key": "times", "label": "次数", "type": "int", "default": 1},
        {"key": "loud", "label": "大写输出", "type": "bool", "default": False},
    ],
)

# level= 是「默认权限等级」：1 普通 / 2 群管 / 3 所有者（不写则按 permission 推断，默认 1）
cmd = feature.command(
    "demo",
    aliases={"示例"},
    priority=50,
    level=LEVEL_USER,
    description="演示命令（描述会出现在 %help <插件名> 里）",
)

@feature.on_load
async def _loaded(feature):
    feature.logger.info("插件已就绪")

@cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    await cmd.finish(feature.cfg("greet"))
```

- **默认启用**：新插件在没有任何规则时默认放行（`default_enabled=True`）；
  想默认关闭就传 `Feature(..., default_enabled=False)`。
- **命令权限**：`level=` 只是默认值，实际等级存在 `command_rule` 表、可在 WebUI「命令管理」改；
  想自己用 NoneBot 的 `Permission` 完全托管（不参与 WebUI 调整），传 `permission=` 自定义对象即可。
- **配置项**：用 `feature.cfg("key")` 读（永远有值，回落默认值），WebUI「插件管理」页可改，**即时生效**。
- **生命周期钩子**：`@feature.on_load / on_unload / on_enable / on_disable`（配套 `feature.logger`）。
- **日志**：`feature.logger.info(...)` 会自动带上 `[插件名] ` 前缀，也会进 `logs/` 文件日志。
- **热重载**：改完代码不用重启进程，WebUI 点「热重载」即可。
- 需要存数据？继承 `src.core.models.Base` 定义自己的表，启动时会自动建表。
- 读写数据库用 `from src.core.database import get_session`，天然与其它插件共享。

---

## 📄 License

仅供个人学习与自用。
