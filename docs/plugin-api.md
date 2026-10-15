# PaperBot 插件 API 参考

> 面向**写插件的人**的 API 速查：`Feature` 实例、事件注册、命令权限、门控、配置项、日志、
> 数据表、出图、大模型调用与消息守卫。
> 上手流程 / 目录约定 / 生命周期 / 热重载 / 检查清单见配套的 [`plugin-dev.md`](plugin-dev.md)（开发指南）；
> 框架内部实现见 [`architecture.md`](architecture.md)；文档总入口见 [`index.md`](index.md)。
> 本文与代码同步维护，改动框架对外接口时请顺手更新（同时检查 [`plugin-dev.md`](plugin-dev.md)）。

---

## 1. `Feature`：插件实例

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
    config_schema=[...],                 # 插件级配置项，见 §5
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
| `config_schema` | | 插件配置项声明，见 §5 |

---

## 2. 注册事件处理器

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

## 3. 命令权限：写「默认值」，实际值由框架维护

```python
from src.core.models import LEVEL_USER, LEVEL_ADMIN, LEVEL_OWNER

cmd = feature.command("weather", level=LEVEL_USER, description="查询天气")
```

- 插件声明的 `level` 只是**默认等级**；真正生效的等级存在 `command_rule` 表里；
- **没有记录 = 跟随默认值**（表保持干净）；
- 判定每次消息都查库 → 在 WebUI「命令管理」页改完**即时生效**，无需重启；
- 插件显式传了自定义 `permission=` → 该命令由插件自管，WebUI 不可改（`editable=false`）。

等级含义：`3` 所有者（`SUPERUSERS`）→ `2` 群管理员/授权用户 → `1` 普通用户 → `0` 黑名单（不回应）。
细节见 [`commands.md`](commands.md)。

---

## 4. 门控：我理不理你

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

> 门控（入口）与消息输出守卫（出口）的分工见 [`architecture.md`](architecture.md#门控-vs-守卫不能互相替代)。

---

## 5. 插件级配置项（`config_schema`）

对标 AstrBot 的 `_conf_schema.json`：插件声明字段，框架负责**校验 + 存 JSON 文件 + WebUI 自动渲染表单**。

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

### 5.1 字段定义

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

### 5.2 存储与生效

- 值存 **`.config/<插件名>/config.json`**（UTF-8、无 BOM、可直接手改），
  **只写显式改过的键** = 没写的键实时回落默认值；
- 文件里 `_` 开头的键是框架元信息（`_plugin` / `_updated_by` / `_updated_at`），
  插件**不要**用 `_` 开头当配置键；
- WebUI「插件管理」页每个插件下方自动出现「插件配置项」卡片，含「保存配置 / 恢复默认」；
- 保存后框架会**立即刷新插件缓存**（`await feature.load_config()`），所以 `feature.cfg(...)` 立刻拿到新值；
- 直接在插件代码里也可以读写：

  ```python
  from src.core import pluginconf
  await pluginconf.write_config("paperbot_weather", {"city": "杭州"}, updated_by="plugin")
  ```

  但**普通场景推荐把可调参数都放进 `config_schema`**，让管理员在 WebUI 改。

> 配置目录布局与全量配置项见 [`configuration.md`](configuration.md)。

### 5.3 插件自己的文件放哪（`feature.config_dir`）

`config_schema` 只管声明式的配置项；插件如果需要额外的持久化文件（名单、缓存、导出数据…），
统一放到自己的配置目录里：

```python
path = feature.config_dir / "watchlist.json"   # → .config/paperbot_xxx/watchlist.json
path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
```

- 目录不存在会自动创建；整个 `.config/` **已 gitignore**，不会污染版本库；
- 目录名 = 插件注册名（只允许字母/数字/`_`/`-`/`.`，`..`、`/`、`.` 开头会被拒绝）；
- 手改 `config.json` 后，下次读配置（重启 / 热重载 / WebUI 保存）就会生效；
  类型写错了不会炸，会**回落默认值**。

### 5.4 首个读配置的时机

`feature.cfg()` 在 `on_load` 之后才是最终值（`on_startup` 会先刷新缓存）。
模块 import 顶层**不要**读配置，放到 handler 或 `on_load` 里读。

---

## 6. 统一日志出口

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

## 7. 数据表：继承 `Base` 即自动建表

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
- ⚠️ 热重载时若模块里定义了 ORM 表，框架会自动把旧表从 metadata 摘掉再重试一次（见 [`plugin-dev.md`](plugin-dev.md) 的「热重载」）。

> 框架自带表与插件表的完整清单、字段与唯一键见 [`architecture.md`](architecture.md#7-数据表)。

---

## 8. 出图：文本 / Markdown → PNG

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

## 9. 调用大模型（`feature.llm`）

框架内置统一 LLM 服务（`src/core/llm.py`），插件**不用**自己读配置、自己建 `openai` client、自己拼记忆：

```python
@cmd.handle()
async def _(event: MessageEvent, args=CommandArg()):
    text = args.extract_plain_text().strip() or "你好"
    if not await feature.llm.available():
        await cmd.finish("还没配置大模型哦（.env 或 WebUI「LLM 与记忆」页）")
    try:
        reply = await feature.llm.ask(text)
    except LLMError as exc:            # from src.core.llm import LLMError
        feature.logger.warning("LLM 调用失败：{}", exc)
        await cmd.finish("模型开小差了，稍后再试……")
    await cmd.finish(reply)
```

- **上下文自动按「作用域 + 谁 + 插件名」三元组隔离**，用 `feature.llm` 会自动带上插件名，不会串到别的插件；
- 传了 `scope` 就必须给 `scope_id`，指定的人设若不存在会直接抛 `LLMError`（不静默用错人设）；
- 所有失败统一抛 `LLMError`（已包装成人话），插件只需 `try/except LLMError`；
- 记忆类方法（`remember` / `profile` …）只读写数据库，`LLM_ENABLE=false` 时也能用。

**完整 API 一览、关键字参数、异常处理与记忆机制见 [`llm-api.md`](llm-api.md)**
（`feature.llm` 开发参考）；LLM 的配置 / 人设 / 记忆使用指南见 [`llm.md`](llm.md)。本文不重复。

---

## 10. 消息输出守卫（必须知道）

框架在 `Bot.call_api` 层锁死了消息出口（`src/core/guard.py`）：

- **群消息只放行 `BOT_ACTIVE_GROUPS` 里的群**，其它群一律拦截（记 WARNING 后静默丢弃）；
- **私聊一律拦截**；
- 其它 API（查信息、上传文件等）不受影响。

对插件作者的意义：

1. 别写「定时广播到所有群」这种逻辑，会被逐条拦掉（日志里能看到 WARNING）；
2. 本地开发时想测试发消息，请把目标群加进 `BOT_ACTIVE_GROUPS`；
3. **不要**试图绕过 `call_api`（如自己起 HTTP 请求发消息）——这是开发期防误发的兜底闸门。

---

## 11. 参考

- 开发指南（上手流程 / 生命周期 / 热重载 / 检查清单）：[`plugin-dev.md`](plugin-dev.md)
- 框架内部实现与设计取舍：[`architecture.md`](architecture.md)
- 命令与权限：[`commands.md`](commands.md)
- 配置项总表：[`configuration.md`](configuration.md)
- 用 LLM（服务商 / 人设 / 记忆）：[`llm.md`](llm.md)
- NoneBot2 文档：<https://nonebot.dev>
- OneBot v11 标准：<https://github.com/botuniverse/onebot-11>
