# PaperBot 插件开发协议与规则

> 面向**功能插件作者**的规范文档。框架内部实现（门控 / 守卫 / 加载器细节）见 `AGENTS.md`。
> 本文与代码同步维护，改动框架对外接口时请顺手更新。

---

## 0. TL;DR（30 秒上手）

```powershell
# 1) 生成骨架（目录名 = 插件注册名）
pixi run new-plugin paperbot_weather --name 天气 --desc "示例：查天气"

# 2) 编辑 plugins/paperbot_weather/paperbot_weather.py 实现功能

# 3) 跑起来（或直接在 WebUI「插件管理」页点「热重载」）
pixi run bot
```

规则速记：

| 规则 | 说明 |
|---|---|
| **一插件一文件夹** | `plugins/<插件名>/<插件名>.py`，目录名即插件注册名 |
| **模块级 `Feature(...)`** | 必须建在模块顶层（热重载靠它重新注册） |
| **命令走 `feature.command()`** | 不要直接 `nonebot.on_command()`，否则绕过门控与总控 |
| **命令要填 `description=` / `level=`** | 供 `%help` 与 WebUI「命令管理」展示 |
| **不要自己 `Driver.on_startup`** | 用 `@feature.on_load` 等生命周期钩子 |
| **日志用 `feature.logger`** | 自动带 `[插件名]` 前缀 |
| **要发消息前想想守卫** | 群白名单 `BOT_ACTIVE_GROUPS` 之外的群、私聊一律被拦 |

---

## 1. 目录结构约定

```
plugins/
├── paperbot_console/               # 总控插件（protected，勿动）
│   └── paperbot_console.py
└── paperbot_weather/               # 你的插件（目录名 = 注册名）
    └── paperbot_weather.py         # 推荐：单文件，文件名与目录名一致
```

加载器兼容三种写法（`src/core/loader.py`）：

1. **目录内单个 `.py`**（推荐，如上面两例）——取目录名 + 文件名；
2. **目录内有 `__init__.py`**（标准包写法）——按包导入，适合插件代码要拆多个文件；
3. **单文件直接放 `plugins/` 根下**（`plugins/xxx.py`）——小型插件偷懒用。

> 目录名以 `.` 或 `_` 开头会被跳过（可用于放草稿）。

**命名规范**：插件名/命令名统一用 `[a-z][a-z0-9_]*`（脚手架会校验），
插件名建议带 `paperbot_` 前缀，避免与将来可能的第三方插件撞名。

### 加载时机

启动流程（`bot.py`）大致是：

```
nonebot.init() → 装消息输出守卫 → load_feature_plugins() → 注册启动/关闭钩子 → 挂载 WebUI
```

`load_feature_plugins()` 逐个 `import` 插件模块；模块 doimports 时创建 `Feature(...)`，
构造函数**自动登记**进内存注册表——**新增插件无需任何手工注册**。
导入完成后框架会校验插件声明的 `requires`（见 §8），不满足则拒绝加载该插件（不影响其它插件）。

随后 `on_startup` 钩子会：建表 → 同步插件元信息进 `plugin_info` 表 → 刷新插件配置缓存 → 触发 `on_load`。

---

## 2. `Feature`：插件实例

```python
from src.core.feature import Feature

feature = Feature(
    "paperbot_weather",                  # 注册名（唯一，= 目录名）
    display_name="天气",                  # 中文显示名
    description="查询天气（示例）",         # 简介，WebUI 与 %help 会展示
    author="paper_white",                # 作者
    repo="https://github.com/...",       # 仓库（可选）
    version="0.1.0",                     # 版本
    requires=">=1.0,<2.0",               # 兼容的框架插件协议版本（默认此值）
    default_enabled=True,                # 默认是否启用（三级门控的兜底值）
    protected=False,                     # 受保护插件：永远放行、不可被 %off、WebUI 不可改
    config_schema=[...],                 # 插件级配置项，见 §6
)
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 注册名，须与目录名一致；命令权限、门控规则、配置项都按它索引 |
| `display_name` | | 中文名，默认同 `name` |
| `description` | | 简介 |
| `author` / `repo` / `version` | | 元信息，写进 `plugin_info` 表并在 WebUI 展示 |
| `requires` | | 依赖的**框架插件协议版本**范围（默认 `>=1.0,<2.0`） |
| `default_enabled` | | 门控兜底值（默认 `True`） |
| `protected` | | `True` = 永远放行、禁止禁用/热重载（总控插件用） |
| `config_schema` | | 插件配置项声明，见 §6 |

---

## 3. 注册事件处理器

`Feature` 提供 5 个方法，**只有经它们注册的 matcher 才会被门控管控**：

```python
cmd = feature.command("weather", aliases={"天气"}, priority=50, level=LEVEL_USER,
                      description="查询天气")
msg = feature.message(rule=..., priority=90)      # 任意消息
notice = feature.notice(...)                      # 通知事件
req = feature.request(...)                        # 请求事件（加好友/加群）
m = feature.on("message", rule=...)               # 通用出口，等价于 nonebot.on()
```

`command()` 专有参数：

| 参数 | 说明 |
|---|---|
| `cmd` / `aliases` | 主命令名 / 别名集合（前缀由 `COMMAND_START` 决定，本项目为 `%`） |
| `rule` | 额外 Rule，会与**门控 Rule 做 AND**（即：插件被禁用则整条 matcher 静默跳过） |
| `permission` | 一般**不用传**！不传时框架自动挂「命令权限」校验（等级可在 WebUI 改，即时生效）。传自定义 `Permission` 则框架不插手（WebUI 标 `editable=false`） |
| `description` | 命令说明（`%help <插件名>` 与 WebUI 展示） |
| `level` | **默认权限等级**：`1` 普通 / `2` 群管 / `3` 所有者 |
| `priority` | NoneBot 优先级（数字越小越先响应，默认 1；业务命令建议 50+） |

处理器里可以正常用 NoneBot 的 `xxx.finish()` / `xxx.send()`，参数注入（`CommandArg`、`Bot`、`Event`…）照旧。

```python
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg

@cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    await cmd.finish(f"收到：{text or '（空）'}")
```

> ⚠️ 别写 `nonebot.on_command(...)` —— 那样拿不到门控，被 `%off` 了还会响。

---

## 4. 命令权限：写「默认值」，实际值由框架维护

```python
from src.core.models import LEVEL_USER, LEVEL_ADMIN, LEVEL_OWNER

cmd = feature.command("weather", level=LEVEL_USER, description="查询天气")
```

- 插件声明的 `level` 只是**默认等级**；真正生效的等级存在 `command_rule` 表里；
- **没有记录 = 跟随默认值**（表保持干净）；
- 判定每次消息都查库 → 在 WebUI「命令管理」页改完**即时生效**，无需重启；
- 插件显式传了自定义 `permission=` → 该命令由插件自管，WebUI 不可改（`editable=false`）。

等级含义：`3` 所有者（`SUPERUSERS`）→ `2` 群管理员/授权用户 → `1` 普通用户 → `0` 黑名单（不回应）。

---

## 5. 门控：我理不理你

**三级门控（就近优先，`src/core/service.py`）**：

```
成员规则存在 → 听成员（True=白名单放行 / False=拦截）
  否则 群规则存在 → 听群
    否则 全局规则存在 → 听全局
      否则 用框架级默认策略（GATE_DEFAULT_ENABLED）或插件 default_enabled
```

对插件作者的意义：**只需要正确声明 `default_enabled` 和 `protected`**，
剩下的开关粒度由管理员通过 `%on / %off / %reset` 或 WebUI 控制。你不需要在插件里写开关逻辑。

- `protected=True` 的插件永远放行（不能被禁用）；
- `GATE_DEFAULT_ENABLED=false` 时：未命中规则的群默认禁用，仅 `BOT_ACTIVE_GROUPS` 内的群放行，私聊一律不放行；
- 0 级黑名单用户**一票否决**（连受保护插件也不回应）。

---

## 6. 插件级配置项（`config_schema`）

对标 AstrBot 的 `_conf_schema.json`：插件声明字段，框架负责**校验 + 存库 + WebUI 自动渲染表单**。

```python
feature = Feature(
    "paperbot_weather",
    display_name="天气",
    config_schema=[
        {"key": "api_key", "label": "API Key", "type": "string", "secret": True},
        {"key": "city", "label": "默认城市", "type": "string", "default": "上海"},
        {"key": "mode", "label": "数据源", "type": "select",
         "choices": ["free", "pro"], "default": "free"},
        {"key": "timeout", "label": "超时（秒）", "type": "int", "default": 10},
        {"key": "enabled_emoji", "label": "带表情", "type": "bool", "default": True},
    ],
)

# 读取（永不返回 None，已合并默认值）
city = feature.cfg("city")                 # → "上海"
t = feature.cfg("timeout", 5)              # 带兜底值
all_conf = feature.config                  # → dict（全部字段）
await feature.load_config()                # 手动刷新缓存
```

### 字段定义

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | ✅ | 唯一键 |
| `label` | | 表单标签（默认用 `key`） |
| `type` | | `string`（默认）/ `text`（多行）/ `int` / `float` / `bool` / `select` |
| `default` | | 默认值；未声明时按类型给空值（`bool`→`False`，数字→`0`，其它→`""`） |
| `hint` | | 表单提示文案 |
| `choices` | | `type=select` 时**必填**，字符串列表 |
| `secret` | | `True` = 敏感字段，WebUI 不回显（只显示长度），留空 = 不修改 |

也可以偷懒写成字符串：`config_schema=["city", "api_key"]`（等价于只给 key）。

### 存储与生效

- 值存共享表 `plugin_config`（`plugin_name + key` 唯一，值以 JSON 文本存），**唯一键无记录 = 用默认值**；
- WebUI「插件管理」页每个插件下方自动出现「插件配置项」卡片，含「保存配置 / 恢复默认」；
- 保存后框架会**立即刷新插件缓存**（`await feature.load_config()`），所以 `feature.cfg(...)` 立刻拿到新值；
- 直接在插件代码里也可以读写：
  ```python
  from src.core import pluginconf
  await pluginconf.write_config("paperbot_weather", {"city": "杭州"}, updated_by="plugin")
  ```
  但**普通场景推荐把可调参数都放进 `config_schema`**，让管理员在 WebUI 改。

### 首个读配置的时机

`feature.cfg()` 在 `on_load` 之后才是最终值（`on_startup` 会先刷新缓存）。
模块 import 顶层**不要**读配置，放到 handler 或 `on_load` 里读。

---

## 7. 生命周期钩子

```python
@feature.on_load
async def _on_load() -> None: ...

@feature.on_enable
async def _on_enable(feature, context) -> None: ...

@feature.on_disable
async def _on_disable(feature) -> None: ...

@feature.on_unload
async def _on_unload() -> None: ...
```

| 钩子 | 触发时机 |
|---|---|
| `on_load` | Bot 启动、插件配置缓存就绪之后；螺钉重载（热重载）后也会触发 |
| `on_enable` | 插件在**全局**作用域从「禁用」变为「启用」时（`%on` / WebUI 开关 / 写全局规则） |
| `on_disable` | 插件在**全局**作用域从「启用」变为「禁用」时 |
| `on_unload` | Bot 关闭时；热重载前也会触发（用于释放资源） |

- **签名灵活**：可以 0 参、1 参（`feature`）、2 参（`feature, context`），同步/异步都行；
- `context` 是 dict，至少含 `reason`（如 `startup` / `reload` / `shutdown` / `rule_change`）；
- 单个钩子报错只记日志、**不影响**其它钩子与主流程；
- ⚠️ `on_enable / on_disable` 只跟**全局**作用域联动（分群规则不触发），避免语义歧义。

典型用途：`on_load` 里启动定时任务（`nonebot.get_asyncio().create_task`）、预热缓存；
`on_unload` 里取消任务、落库。

---

## 8. 元信息与版本校验（`requires`）

- `requires` 描述**兼容的框架插件协议版本范围**，语法：`>= > <= < == != ^ ~`，逗号或空格分隔（如 `>=1.0,<2.0`），`^1.2` = `>=1.2,<2.0`；
- 空串 = 不限制；**写错的那一条会被忽略**（不因为元信息手滑就拒绝插件）；
- 启动时校验：不满足则**拒绝加载该插件**（销毁其 matcher + 移出注册表 + 打 ERROR 日志），其它插件照常运行；
- 当前框架版本见 `src/core/version.py::FRAMEWORK_VERSION`。

---

## 9. 统一日志出口

```python
@cmd.handle()
async def _(): 
    feature.logger.info("处理了一条天气查询")       # → [paperbot_weather] 处理了一条天气查询
    feature.logger.opt(exception=exc).error("查询失败")
```

或者：

```python
from src.core import get_logger
log = get_logger("paperbot_weather")   # 同样自动带 [paperbot_weather] 前缀
```

- 底层就是 NoneBot 的 loguru logger，支持 `{}` 格式化、`opt()`、`bind()`；
- 文件日志由框架统一挂到 `logs/paperbot-*.log`（`LOG_FILE_ENABLE=true`），你**不需要**自己配 sink；
- 别用 `print()`。

---

## 10. 数据表：继承 `Base` 即自动建表

插件的业务表只要继承框架共享的 `Base`，启动时**自动 `create_all`**（无需手写迁移）：

```python
from sqlalchemy.orm import Mapped, mapped_column
from src.core.models import Base

class WeatherCache(Base):
    __tablename__ = "weather_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    city: Mapped[str] = mapped_column(index=True)
    payload: Mapped[str] = mapped_column(default="")
```

读写统一走共享 session（与其它插件同库）：

```python
from src.core.database import get_session
from sqlalchemy import select

async with get_session() as session:          # 写路径（退出自动 commit）
    session.add(WeatherCache(city="上海", payload="..."))

async with get_session(readonly=True) as session:   # 纯读路径（不 commit）
    rows = (await session.execute(select(WeatherCache))).scalars().all()
```

- 表名建议带插件前缀（如 `weather_*`），避免撞名；
- SQLite 已开 WAL + `busy_timeout`，多个插件并发读写没问题；
- ⚠️ `create_all` **只建新表、不给已有表补列**：插件表后来加了字段，老库不会自动 ALTER
  （框架自带表有这样一条「启动自动补列」兜底逻辑，插件表请自己处理，例如直接删掉旧库重来）；
- ⚠️ 热重载时若模块里定义了 ORM 表，框架会自动把旧表从 metadata 摘掉再重试一次（见 §13）。

---

## 11. 出图：文本 / Markdown → PNG

```python
from src.core import render_markdown, render_text

png: bytes = await render_markdown("# 标题\n\n- 支持 **Markdown**\n- 本地图片路径会自动内联")
png2: bytes = await render_text("纯文本也行")

await cmd.finish(MessageSegment.image(png))     # 发图
```

- 宽固定 720、高自适应，卡片样式由 `src/core/templates/markdown.{html,css}` 控制；
- 浏览器复用系统 Edge（`.env` 的 `RENDER_BROWSER_CHANNEL=msedge`），首次调用略慢，框架已做单例复用；
- 失败抛 `RenderError`，记得 try/except 后回退成文字发送。

---

## 12. 消息输出守卫（必须知道）

框架在 `Bot.call_api` 层锁死了消息出口（`src/core/guard.py`）：

- **群消息只放行 `BOT_ACTIVE_GROUPS` 里的群**，其它群一律拦截（记 WARNING 后静默丢弃）；
- **私聊一律拦截**；
- 其它 API（查信息、上传文件等）不受影响。

对插件作者的意义：

1. 别写「定时广播到所有群」这种逻辑，会被逐条拦掉（日志里能看到 WARNING）；
2. 本地开发时想测试发消息，请把目标群加进 `BOT_ACTIVE_GROUPS`；
3. **不要**试图绕过 `call_api`（如自己起 HTTP 请求发消息）——这是开发期防误发的兜底闸门。

---

## 13. 热重载

WebUI「插件管理」页有「热重载」按钮（`POST /api/plugins/{name}/reload`），流程：

1. 触发 `on_unload`；
2. 销毁该插件注册过的 matcher + 清理 NoneBot 全局命令前缀（`Feature.matchers` 记录了它们）；
3. 从注册表移除旧实例；
4. `importlib.reload` 重新执行插件模块（顶层重新建 `Feature` / matcher）；
5. 刷新配置缓存 → 触发 `on_load` → 同步数据库元信息。

已知限制：

- **受保护插件禁止热重载**（怕把管理入口弄没），返回 409；
- 插件模块里定义的 ORM 表会让 `reload` 撞「表已存在」，框架会自动摘表重试一次；
- 模块里 `from x import y` 已绑定的外部引用不会回溯更新（Python 固有行为，重启最干净）；
- 修改了**框架自身**（`src/core/*`）必须重启 Bot，热重载只对插件模块有效。

---

## 14. 提交前检查清单

- [ ] 目录/文件名 = 插件注册名，`Feature(...)` 在模块顶层
- [ ] 每条命令都有 `description=`，并按需填 `level=`
- [ ] 没有直接调用 `nonebot.on_command / on_message`
- [ ] 可调参数写进 `config_schema`，而不是硬编码
- [ ] 日志走 `feature.logger`（无 `print`）
- [ ] 生命周期钩子签名正确（0/1/2 参皆可），`on_unload` 释放了自己申请的资源
- [ ] 随手确认：插件被 `%off` 后，命令**完全无响应**（门控生效）
- [ ] 发消息的目标群在 `BOT_ACTIVE_GROUPS` 里（否则会被守卫拦掉）

---

## 15. 常见坑

| 现象 | 原因 / 对策 |
|---|---|
| 插件加载不出来 | 模块 import 时抛异常 → 看日志 `加载插件模块 xxx 失败`（多为语法错或依赖缺失） |
| 自定义 `Permission` 导致插件加载失败 | 子类必须调 `super().__init__(self._check)`（`Permission` 带 `__slots__`） |
| 命令被 `%off` 后还会响应 | 用了 `nonebot.on_command` 而不是 `feature.command` |
| 热重载后 `Duplicated prefix rule` 警告 | 框架已清理 Trie 前缀；若仍出现请检查是否自己在模块里注册了裸 matcher |
| 插件被拒绝加载、日志 `框架版本 1.0.0 不满足插件要求 ...` | `requires` 写太严，改成与 `FRAMEWORK_VERSION` 兼容的范围 |
| 改了配置但插件读到的还是旧值 | 用 `feature.cfg()` 读，别在模块顶层缓存到全局变量 |
| 群消息发不出去、日志有守卫 WARNING | 目标群不在 `BOT_ACTIVE_GROUPS`（或想发私聊，被一律拦截） |

---

## 16. 参考

- NoneBot2 文档：<https://nonebot.dev>
- OneBot v11 标准：<https://github.com/botuniverse/onebot-11>
- 框架内部实现与踩坑：`AGENTS.md`
- 示例插件：`plugins/paperbot_echo/paperbot_echo.py`（含配置项 / 钩子 / 日志完整示例）
