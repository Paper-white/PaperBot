# PaperBot 🎭

> 基于 **NoneBot2** 的自定义人设 QQ 机器人 —— 插件化架构 + 四级权限 + WebUI 管理后台。

PaperBot 在 NoneBot2 之上自研了一层轻量「插件框架」，把「消息收发」交给 NoneBot2，
把「插件管控 / 权限 / 共享数据」交给自己的框架核心。任何一个功能插件都能被
**总控插件（`Paperbot_CONSOLE`）** 统一启停，并支持**全局 / 分群 / 分成员**三级作用域。

---

## ✨ 特性

- 🎭 **自定义人设对话**（LLM 驱动，Phase 2 规划中）
- 🧩 **自研插件系统**：每插件一个独立文件，继承 `Feature` 即自动纳管
- 🛡️ **四级权限**：黑名单 / 普通用户 / 群管理员 / 所有者
- 🎚️ **三级门控**：成员规则 > 群规则 > 全局规则 > 插件默认开关（就近优先）
- 🔒 **消息输出守卫**：开发期锁死消息出口，仅允许发往白名单群，杜绝误发
- 🗄️ **共享数据库**：所有插件共用同一 SQLite（SQLAlchemy 2.0 async + aiosqlite），天然数据互通
- 🖥️ **WebUI 后端 API**：内嵌 FastAPI，挂载于 `/api`
- 🔌 **协议端**：SnowLuma（OneBot v11）

---

## 🧱 项目结构

```
PaperBot/
├── bot.py                  # 启动入口
├── plugins/                # 功能插件目录（每插件一个独立 .py）
│   ├── paperbot_console.py # 总控插件（命令 / 权限 / 门控）
│   └── echo.py             # 示例插件（复读）
├── src/
│   ├── config.py           # 项目配置（pydantic-settings）
│   └── core/               # 自研框架核心
│       ├── models.py       # 共享 ORM Base + 公共表
│       ├── database.py     # 共享 async engine / get_session()
│       ├── registry.py     # 内存插件注册表
│       ├── feature.py      # 功能插件基类 Feature
│       ├── gate.py         # 门控 Rule
│       ├── guard.py        # 消息输出守卫
│       ├── permission.py   # 四级权限解析
│       ├── service.py      # 门控 / 权限业务逻辑（命令 & WebUI 共用）
│       └── bootstrap.py    # 启动钩子（建表 + 同步插件元信息）
├── webui/api.py            # WebUI 后端路由（/api）
├── data/                   # 运行时数据（SQLite，不提交）
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
| `OUTPUT_GUARD_ENABLED` | 是否启用消息输出守卫（开发期建议 `true`） |
| `OUTPUT_GUARD_GROUPS` | 守卫白名单群（JSON 数组），私聊一律拦截 |

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
[INFO] uvicorn | Uvicorn running on http://127.0.0.1:8080
```

健康检查：

```powershell
curl http://127.0.0.1:8080/api/health
```

---

## 🎮 命令一览

> 命令前缀为 `%`（由 `.env` 的 `COMMAND_START` 决定）。标注【所有者】的命令仅 `SUPERUSERS` 可用。

### 总控插件（`Paperbot_CONSOLE`）

| 命令 | 说明 | 权限 |
|---|---|---|
| `%help` | 列出**当前已启用**的插件 | 所有用户 |
| `%help <插件名>` | 查看某插件的**命令与介绍** | 所有用户 |
| `%on <插件名> [群号]` | 在对应群**启用**插件（省略=本群；`all`=全局） | 【所有者】 |
| `%off <插件名> [群号]` | 在对应群**禁用**插件（省略=本群；`all`=全局） | 【所有者】 |
| `%plugin <插件名>` | 查看该插件在**全局 / 各群**的开关状态 | 【所有者】 |
| `%admin add <QQ> [群号]` | 授予该用户 **2 级管理员**（仅对应群） | 【所有者】 |
| `%admin del <QQ> [群号]` | 移除手动授予的管理员 | 【所有者】 |
| `%admin black <QQ>` | 加入**全局黑名单**（0 级） | 【所有者】 |
| `%admin unblack <QQ>` | 移出全局黑名单 | 【所有者】 |
| `%reset <插件名> [作用域]` | 清除某作用域规则（额外提供） | 【所有者】 |

> `%reset` 的作用域写法：`全局` / `本群` / `群:<群号>` / `用户:<QQ>`。

### 示例插件（`echo`）

| 命令 | 说明 |
|---|---|
| `%echo <内容>`（或 `%复读 <内容>`） | 复读内容（用于验证门控与输出守卫） |

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

---

## 🌐 WebUI 后端 API

挂载在 `/api`（前缀由 `WEBUI_PREFIX` 配置）：

```
GET    /api/health                                       健康检查
GET    /api/plugins                                      插件总览
GET    /api/plugins/{name}                               插件详情 + 规则列表
PUT    /api/plugins/{name}/rule                          设置规则 {scope_type, scope_id, enabled}
DELETE /api/plugins/{name}/rule?scope_type=&scope_id=    删除规则
```

若 `.env` 设置了 `WEBUI_TOKEN`，请求需带 `Authorization: Bearer <token>`。

---

## 🧩 开发一个插件

在 `plugins/` 下新建一个 `.py` 文件，用 `Feature` 注册（**不要**直接调用 `nonebot.on_command`）：

```python
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg
from src.core.feature import Feature

feature = Feature("demo", display_name="示例", description="一个示例插件")

cmd = feature.command("demo", aliases={"示例"}, priority=50)

@cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    await cmd.finish("hello~")
```

- 需要存数据？继承 `src.core.models.Base` 定义自己的表，启动时会自动建表。
- 读写数据库用 `from src.core.database import get_session`，天然与其它插件共享。

---

## 📄 License

仅供个人学习与自用。
