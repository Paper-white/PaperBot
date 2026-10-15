# PaperBot 插件开发指南

> 面向**写插件的人**的指南：从生成骨架、注册命令，到生命周期、热重载与提交前检查的完整流程。
> 每个 API 的写法速查（`Feature` 参数 / 事件注册 / `config_schema` / `feature.llm`…）见配套的
> [`plugin-api.md`](plugin-api.md)（API 参考）；框架内部实现见 [`architecture.md`](architecture.md)；
> 文档总入口见 [`index.md`](index.md)。
> 本文与代码同步维护，改动框架对外接口时请顺手更新（同时检查 [`plugin-api.md`](plugin-api.md)）。

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
| **配置走 `config_schema`** | 值存 `.config/<插件名>/config.json`（已 gitignore），别自己写根目录文件 |
| **自己的文件放 `feature.config_dir`** | 非配置类的持久化文件也统一扔进这个目录 |

> 想知道每条 API 怎么调？翻 [`plugin-api.md`](plugin-api.md)。

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

### 1.1 加载时机

启动流程（`bot.py`）大致是：

```
nonebot.init() → 装消息输出守卫 → load_feature_plugins() → 注册启动/关闭钩子 → 挂载 WebUI
```

`load_feature_plugins()` 逐个 `import` 插件模块；模块导入时会创建 `Feature(...)`，
构造函数**自动登记**进内存注册表——**新增插件无需任何手工注册**。
导入完成后框架会校验插件声明的 `requires`（见 §3），不满足则拒绝加载该插件（不影响其它插件）。

随后 `on_startup` 钩子会：建表 → 同步插件元信息进 `plugin_info` 表 → 刷新插件配置缓存 → 触发 `on_load`。

---

## 2. 生命周期钩子

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
| `on_load` | Bot 启动、插件配置缓存就绪之后；热重载后也会触发 |
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

## 3. 元信息与版本校验（`requires`）

- `requires` 描述**兼容的框架插件协议版本范围**，语法：`>= > <= < == != ^ ~`，
  逗号或空格分隔（如 `>=1.0,<2.0`），`^1.2` = `>=1.2,<2.0`；
- 空串 = 不限制；**写错的那一条会被忽略**（不因为元信息手滑就拒绝插件）；
- 启动时校验：不满足则**拒绝加载该插件**（销毁其 matcher + 移出注册表 + 打 ERROR 日志），
  其它插件照常运行；
- 当前框架版本见 `src/core/version.py::FRAMEWORK_VERSION`；
- 声明位置与其它元信息字段（`Feature(...)` 的参数）见 [`plugin-api.md`](plugin-api.md)。

---

## 4. 热重载

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

## 5. 提交前检查清单

- [ ] 目录/文件名 = 插件注册名，`Feature(...)` 在模块顶层
- [ ] 每条命令都有 `description=`，并按需填 `level=`
- [ ] 没有直接调用 `nonebot.on_command / on_message`
- [ ] 可调参数写进 `config_schema`，而不是硬编码
- [ ] 日志走 `feature.logger`（无 `print`）
- [ ] 生命周期钩子签名正确（0/1/2 参皆可），`on_unload` 释放了自己申请的资源
- [ ] 随手确认：插件被 `%off` 后，命令**完全无响应**（门控生效）
- [ ] 发消息的目标群在 `BOT_ACTIVE_GROUPS` 里（否则会被守卫拦掉）

---

## 6. 常见坑

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

## 7. 参考

- 插件 API 速查：[`plugin-api.md`](plugin-api.md)
- 框架内部实现与设计取舍：[`architecture.md`](architecture.md)
- 写 LLM 相关功能：[`llm-api.md`](llm-api.md)（开发参考）、[`llm.md`](llm.md)（使用指南）
- 开发环境 / 自检 / 踩坑备忘：[`development.md`](development.md)
- 配置项总表：[`configuration.md`](configuration.md)
- NoneBot2 文档：<https://nonebot.dev>
- OneBot v11 标准：<https://github.com/botuniverse/onebot-11>
- 示例插件：`plugins/paperbot_ping/paperbot_ping.py`（含钩子 / 日志完整示例）
