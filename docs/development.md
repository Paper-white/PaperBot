# 开发指南

- 相关文件：`pyproject.toml`、`_tests/`、`src/core/*`
- 相关文档：[`architecture.md`](architecture.md)、[`plugin-dev.md`](plugin-dev.md)、[`plans/`](plans/)

## 1. 环境

| 项 | 值 |
|---|---|
| 语言 | Python **3.11**（`requires-python = ">=3.11,<3.12"`） |
| 环境管理 | **pixi**（`pixi.toml` 配在 `pyproject.toml` 的 `[tool.pixi]` 段） |
| 环境路径 | 项目根下 `.pixi/envs/default/` |
| 主要依赖 | nonebot2[fastapi,httpx,websockets] · nonebot-adapter-onebot · sqlalchemy(async) + aiosqlite · openai · pydantic-settings · playwright · markdown / pygments / jinja2 / pymdown-extensions |

```powershell
# 安装 / 同步环境
$env:PIXI_HOME="D:\pixi"
D:\pixi\bin\pixi.exe install

# 加依赖
D:\pixi\bin\pixi.exe add --pypi <package>
```

> pixi 环境默认不带 pip，需要时 `pixi add --pypi pip`。

## 2. 常用命令（pixi tasks）

| 命令 | 作用 |
|---|---|
| `pixi run bot` | 启动机器人（等价 `.\.pixi\envs\default\python.exe bot.py`） |
| `pixi run new-plugin <名字>` | 生成插件骨架（`--desc` / `--command` / `--author` / `--force`） |
| `pixi run check-config` | **配置体系自检**（92 项：schema / JSON 读写 / 目录穿越 / 接口 / env 迁移） |
| `pixi run check-llm` | **LLM 服务自检**（86 项，含真实 `openai` SDK 打本地假服务） |
| `pixi run check-vcs` | **版本控制自检**（27 项：临时 git 仓库演练 fetch / update / 脏拒绝 / 回滚 / 接口） |

⚠️ **必须从项目根目录启动**：`bot.py` 用相对路径找 `.config/main/.env`、`data/`、`logs/`。

## 3. 自检脚本怎么写

长期复用的自检脚本放 `_tests/`（并在 `pyproject.toml` 注册 task）；插件脚手架等开发工具放 `scripts/`。套路：

```python
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # 让脚本能直接跑

import nonebot

tmp = Path(tempfile.mkdtemp(prefix="xxx_check_"))
os.environ["DRIVER"] = "~none"
from src.config import settings
settings.database_url = f"sqlite+aiosqlite:///{(tmp / 'check.db').as_posix()}"   # ← 别碰真实库！
nonebot.init(driver="~none")

# 要测 WebUI 接口：直接挂 router，不用起服务
import httpx
from fastapi import FastAPI
from webui.routes import plugins as plugins_routes
app = FastAPI(); app.include_router(plugins_routes.router)
transport = httpx.ASGITransport(app=app)

async def main():
    from src.core import database
    await database.init_db()
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/plugins/paperbot_echo/config")
        ...
    await database.dispose_db()
```

要点：

- ⚠️ **别对真实的 `data/paperbot.db` 下手**：把 `settings.database_url` 指到 `%TEMP%`，收尾 `dispose_db()`
- 要把**配置目录**也隔离，得 monkeypatch（模块级引用）：
  `pluginconf.config_dir = lambda: tmp_config`、`envfile._ENV_PATH = tmp / ".env"`
- 输出风格：`[OK]/[FAIL] + 名称 + 明细`，结尾打印 `结果：x/y 通过`，失败返回非 0
- 临时脚本放系统临时目录即可（**会被清理**，别把它当长期入口）

## 4. 代码约定

- **中文注释 / 中文回复**；错误信息要**给人看**（避免把 SDK 的英文异常直接抛给用户）
- 插件**不要**直接 `nonebot.on_command`，走 `feature.command()`（否则绕过门控与总控）
- 插件**不要**自己 `Driver.on_startup`，用 `@feature.on_load` 等生命周期钩子
- 日志统一走 `feature.logger` / `get_logger(name)`（自动带 `[插件名] ` 前缀）
- 配置只放 `.config/`（见 [`configuration.md`](configuration.md)）
- 数据表用共享 `Base`，读写走 `get_session()`；**纯读路径用 `get_session(readonly=True)`**
- 时间：库里 `func.now()` 存的是 **UTC**，对外展示统一走 `llm.fmt_dt()` 转北京时间
- 命令记得填 `description=` 和 `level=`（供 `%help` 与 WebUI 命令管理页展示）

## 5. 调试技巧

```bash
# 健康检查
curl http://127.0.0.1:6188/api/health

# 看最新一次运行的日志文件
ls -t logs/*.log | head -1

# 单独跑渲染（输出 PNG）
./.pixi/envs/default/python.exe -m src.core.render out.png
```

- 热重载：改完插件代码，WebUI「插件管理」页点「热重载」即可（受保护插件不提供）
- 插件配置：`.config/<插件名>/config.json` 可直接手改，下次读配置即生效
- WebUI 页面若整体「请求失败」：先怀疑 `index.html` 的前缀占位符被写坏（见下方踩坑）

## 6. 踩坑备忘（省下几小时的那种）

### 框架 / NoneBot

- ⚠️ **正向 WS 必须用 `~websockets` 驱动**，否则报 `does not support websocket client connections!`
- ℹ️ **协议端连不上的刷屏日志已被降噪**（`src/core/connlog.py`，启动时自动安装）：
  适配器每 3 秒重连会打一整段 ERROR + traceback，现在被改写为**一条可读中文 WARNING**
  （含原因与排查建议），60 秒冷却窗口内**静默**、只在窗口结束时汇总一次。
  - 实现：`logger.configure(patcher=...)` 挂全局 patcher，命中则原地改 `record["level"]`
    （WARNING 降级 / TRACE 静默）并清掉 `record["exception"]`
  - ⚠️ **判据只看消息内容**（`Error while setup/process data from websocket`），
    **不要**依赖 `record["name"]`：适配器经 `logger_wrapper` 打日志，name 会随调用点变化
  - ⚠️ 改 level 必须改 `record["level"]` 的 `.name/.no/.icon`（它是 `RecordLevel`），
    **不能**整体替换成 `logger.level("WARNING")`（那是 `Level` 类，`{level}` 会渲染成 repr）

- ⚠️ **自定义 `Permission` 子类必须调 `super().__init__(...)`**：基类带 `__slots__=("checkers",)`，
  不调就没有 `checkers` 属性 → `Feature.command()` 里 `permission | ...` 抛 `AttributeError` →
  **插件整个 import 失败**（表现为「插件加载不出来」）。多 checker 的 `Permission` 是 **OR** 语义
- ⚠️ **命令前缀是类级全局单例**：`TrieRule.prefix` 挂在类上，`Matcher.destroy()` **不会**清掉它 →
  热重载 / 拒绝加载后残留 `%cmd`，下次注册只打 `Duplicated prefix rule` WARNING。
  统一用 `feature.drop_command_prefixes()` 手动 `pop`
- ⚠️ **`create_all()` 不给已有表补列**：老库遇到新列会 `no such column`。框架自带表靠
  `database._add_missing_columns()` 兜底；**加列时给新列可空或带 `server_default`**，
  否则 SQLite 的 `ALTER TABLE ADD COLUMN` 会失败
- ℹ️ **`parse_version()` 解析不出数字会抛 `ValueError`**；`satisfies()` 逐条 try/except，
  **写错的条件被忽略而不是拒绝插件**（别改成「非法即拒绝」，会误伤元信息手滑）
- ℹ️ loguru 的 `{}` 是格式化占位符，要显示字面量大括号得写 `{{}}`

### WebUI

- ⚠️ **页面全部「请求失败」的元凶通常是占位符重名**：`index.html` 里的占位符必须是 `%WEBUI_PREFIX%`；
  写成 `__WEBUI_PREFIX__` 会把 `window.__WEBUI_PREFIX__` 这个**变量名本身**也替换掉 →
  返回的 HTML 变成 `window./api = "/api";` → 内联脚本 SyntaxError → 前缀为空 → 所有接口 404
- ⚠️ **没有 `GET /api/llm/providers/{name}`**（单条只有 PUT/DELETE/test）：请求它返回 **405 而非 404**，
  写测试时别拿 404 当「不存在」的判据
- ℹ️ 本机环境**没有 psutil**，状态页只报进程内信息（PID / 线程 / Python 版本），不报 CPU / 内存

### 数据库

- ⚠️ **`get_session()` 出错会 rollback 再抛**：「校验失败不落库」是自动成立的，
  但校验必须写在事务内（`session.add()` 之后的赋值段）才会回滚
- ℹ️ 门控判定复用同一个只读 session 跑「权限 + 开关」两次查询；`plugin_overview()` 等
  单次查询后在内存解析（避免 N+1）

### 渲染

- ⚠️ **Edge CLI headless 不支持整页 / 自适应截图**（只截视口），渲染**必须走 Playwright 元素级截图**
- ⚠️ **Pygments 的 `<pre>` 外层还有 `.codehilite` / `.highlight` 包裹层**：内边距只加在 `pre`，
  包裹层仅做圆角裁切，否则双重内边距
- ℹ️ 未启用 `pymdownx.tilde`：`~~删除线~~` 不生效（避免把 `~/.config` 误解析成下标）
- ℹ️ `python -m src.core.render` 会打印一条 `RuntimeWarning: ... found in sys.modules`，**无副作用**
