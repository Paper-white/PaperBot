# 架构与实现

- 相关文件：`bot.py`、`src/config.py`、`src/core/*`、`webui/*`
- 相关文档：[`plugin-dev.md`](plugin-dev.md)（插件作者视角）、[`llm.md`](llm.md)（LLM 细节）、[`plans/`](plans/)（决策由来）

## 1. 总体思路

用 **NoneBot2** 负责「消息收发 + 事件分发」，在其之上自研一层**框架核心 `src/core`**，
把「插件管控 / 权限 / 共享数据 / 渲染 / LLM」从业务插件里抽出来：

```
                    ┌─────────────── OneBot v11 协议端 ───────────────┐
                    │                 （正向 WS）                     │
                    └───────────────────────┬────────────────────────┘
                                            │
   ┌────────────────────────────────────────▼────────────────────────────────────┐
   │                            NoneBot2（消息收发 + 事件分发）                    │
   │                            适配器注册 · 命令解析 · matcher 调度               │
   └────────────────────────────────────────┬────────────────────────────────────┘
                                            │
   ┌────────────────────────────────────────▼────────────────────────────────────┐
   │                       框架核心 src/core（本项目自研）                          │
   │  门控 gate · 守卫 guard · 权限 permission/cmdperm · 服务 service · 加载器      │
   │  Feature 基类 · 配置 pluginconf · 钩子 hooks · 热重载 reload · 版本 version    │
   │  渲染 render · 日志 logging · 统一 LLM llm                                     │
   └───────┬──────────────────────┬───────────────────────┬──────────────────────┘
           │                      │                       │
   ┌───────▼───────┐    ┌─────────▼─────────┐   ┌─────────▼─────────┐
   │ 功能插件       │    │ 共享 SQLite        │   │ WebUI（内嵌       │
   │ plugins/*     │    │ data/paperbot.db  │   │ FastAPI + 单页）   │
   └───────────────┘    └───────────────────┘   └───────────────────┘
```

## 2. 模块职责

| 模块 | 职责 |
|---|---|
| `src/config.py` | 项目配置（pydantic-settings，读 `.config/main/.env`，根 `.env` 兜底）+ `config_dir()` / `MAIN_ENV_PATH` |
| `src/core/models.py` | 共享 ORM `Base` + 框架公共表 + 6 张 `llm_*` 表 |
| `src/core/database.py` | 全局单例 async engine + `get_session()`；建表 + 自动补列 |
| `src/core/registry.py` | 内存插件注册表 |
| `src/core/feature.py` | **功能插件基类 `Feature`**（命令 / 配置项 / 钩子 / logger / `config_dir` / `.llm` 门面） |
| `src/core/loader.py` | 插件加载器（扫描 `plugins/*/` 并 import + `requires` 校验） |
| `src/core/gate.py` | 门控 Rule（进 handler 前判断放行 + 黑名单一票否决） |
| `src/core/guard.py` | **消息输出守卫**（锁死消息出口到白名单群，防误发） |
| `src/core/permission.py` | 四级权限解析 |
| `src/core/cmdperm.py` | 命令权限（`Permission` 子类，查 `command_rule` 表判定） |
| `src/core/service.py` | 门控 / 权限 / 命令权限 / 插件配置业务逻辑（QQ 命令与 WebUI 共用） |
| `src/core/pluginconf.py` | 插件级配置项（schema 规范化 / 类型转换 / `.config/<插件名>/config.json` 读写） |
| `src/core/hooks.py` | 生命周期钩子分发（三种签名兼容） |
| `src/core/version.py` | 框架版本 + `requires` 校验 |
| `src/core/vcs.py` | 框架版本控制（git 拉取 / 更新 / 重启 / 守护服务检测） |
| `src/core/reload.py` | 插件热重载（`importlib.reload` + matcher / 命令前缀清理 + 失败回滚） |
| `src/core/logger.py` | 统一日志出口（`PrefixedLogger` 自动加 `[插件名] ` 前缀） |
| `src/core/logging.py` | 日志文件输出（每次运行一个文件 + 查看 / 清理） |
| `src/core/connlog.py` | 连接日志降噪（协议端重连报错：可读化 + 降级 + 节流） |
| `src/core/textutil.py` | 共享小工具（`human_size` / `fmt_dt` / `now_text` / `human_duration`，各模块唯一来源） |
| `src/core/chatlog.py` | **聊天记录原始流水层**（全局钩子写 `chat_message` + 查询 API） |
| `src/core/llm.py` | 统一 LLM 服务（服务商 / 人设 / 会话 / 长期记忆 / 群记忆 / 压缩 / 流式） |
| `src/core/scheduler.py` | 后台定时任务（记忆增量总结 + 上下文压缩） |
| `src/core/presets/` | 内置人设（`*.md`，YAML front-matter + 正文 = system prompt） |
| `src/core/render.py` + `templates/` | 渲染核心：文本 / Markdown → PNG |
| `src/core/bootstrap.py` | 启动 / 关闭钩子（建表 → 迁移旧配置 → 同步元信息 → 灌配置 → 导入人设 → 触发钩子） |

## 3. 四级权限

| 等级 | 名称 | 说明 |
|:---:|---|---|
| `0` | 黑名单 | **不回应任何消息**（一票否决，连受保护插件也不回应） |
| `1` | 普通用户 | 默认权限 |
| `2` | 群管理员 | QQ 群角色**自动识别**（群主 / 管理员）或所有者**手动授权**（仅对应群） |
| `3` | 所有者 | `SUPERUSERS`，拥有全部管理权限 |

解析优先级（就近优先）：**所有者 → 本群手动授权 → 全局手动授权 → 群角色自动 → 默认普通**。
授权记录存 `user_permission`，唯一键 `(user_id, scope_type, scope_id)`。

## 4. 三级门控（就近优先）

```
成员规则存在 → 听成员（True=白名单放行 / False=黑名单拦截）
  否则 群规则存在 → 听群
    否则 全局规则存在 → 听全局
      否则 用插件 default_enabled（默认 True）
```

- 受保护插件（`protected=True`，如总控插件自身）**永远放行**，不能被禁用
- 规则存 `plugin_rule`，唯一键 `(plugin_name, scope_type, scope_id)`

**框架级默认策略**：把 `GATE_DEFAULT_ENABLED=false` 打开后，规则链最后一环换成
「**全局默认禁用 + 白名单群放行**」——未显式配置的群 / 用户一律不理，只有 `BOT_ACTIVE_GROUPS`
里的群才跟随插件默认开关。好处是**一条配置对所有插件生效**。

### 门控 vs 守卫（不能互相替代）

| | 门控 `gate` | 守卫 `guard` |
|---|---|---|
| 位置 | **入口**（消息进 handler 前） | **出口**（`Bot.call_api` 发送时） |
| 管什么 | 我理不理你 | 我能不能往这儿发 |
| 粒度 | 每插件 × 每作用域一条规则 | 一份白名单管全部发送 |
| 主动发送（定时 / 广播） | ❌ 管不了 | ✅ 拦得住 |
| 跨群 / 私聊误发 | ❌ 拦不住 | ✅ 拦得住 |
| 受保护插件 | ❌ 永远放行 | ✅ 照样拦 |

结论：**守卫必须保留**（唯一能兜住主动发送与误发的闸门），二者是纵深防御的互补关系。

## 5. 命令权限（框架维护 + WebUI 可改）

- **插件侧**：`feature.command(..., level=1|2|3)` 声明**默认等级**
  （不传 → 按 `permission` 推断：`SUPERUSER`→3，其它→1）
- **框架侧**：实际等级存 `command_rule`，唯一键 `(plugin_name, command_name)`；
  **没有记录 = 跟随默认值**（表保持干净）
- **判定**：每次消息都查库 → **改完即时生效**；插件显式传自定义 `permission=` 时框架不插手
- **WebUI**：命令管理页下拉修改 / 恢复默认；受保护插件命令 409；插件自管权限 400

## 6. WebUI 管理后台

- 后端：内嵌 **FastAPI**（复用 NoneBot 的 fastapi driver app），挂载在 `/api`；页面与静态资源由 `webui/api.py` 提供
- 前端：**零构建单页**（`webui/static/index.html` + `assets/app.js` + `assets/style.css`），hash 路由，九个菜单：
  **状态 / 系统配置 / 黑白名单 / 插件管理 / LLM 与记忆 / 数据库管理 / 命令管理 / 日志 / 设置**
- API 前缀通过服务端替换 `%WEBUI_PREFIX%` 占位符注入前端（页面用 `window.__WEBUI_PREFIX__` 读取）
- 除 `GET /` 外，9 个页面名也各自注册成 `GET /<name>`（直接敲 `/status`、`/llm`、`/logs` 也能用）
- 交互组件（全站共用）：通用浮窗 `openModal/closeModal`、chips 列表输入、可搜索长下拉、
  群号 `datalist`、`mutate()` 写操作助手、页面级定时器 `addPageTimer/clearPageTimers`
- 接口清单见 [`api.md`](api.md)

## 7. 数据表

| 表 | 用途 | 关键字段 / 唯一键 |
|---|---|---|
| `plugin_info` | 插件元信息（同步自注册表） | `name` / `display_name` / `version` / `author` / `repo` / `requires` / `protected` |
| `plugin_rule` | 三级门控规则 | (`plugin_name`, `scope_type`, `scope_id`) |
| `command_rule` | 命令权限覆盖 | (`plugin_name`, `command_name`) |
| `user_permission` | 权限授权 / 黑名单 | (`user_id`, `scope_type`, `scope_id`) |
| `llm_provider` | LLM 服务商 | `name`；`is_default` |
| `llm_preset` | 人设 | `name`；`source` = builtin / import / manual |
| `llm_session` | 会话 | (`scope_type`, `scope_id`, `plugin_name`) |
| `llm_message` | 会话消息 | `session_id` |
| `llm_user_profile` | 用户印象 | `user_id` |
| `llm_user_fact` | 关键信息 | (`user_id`, `fact_key`)；`source` = auto / manual |

插件配置**不在数据库里**（已迁到 `.config/<插件名>/config.json`，见 [`configuration.md`](configuration.md)）。

- SQLite 连接自动启用 `journal_mode=WAL` / `busy_timeout=5000` / `foreign_keys=ON`
- `create_all()` **只建新表，不给已有表补列**：框架自带表靠 `database._add_missing_columns()`
  在启动时做极简 `ALTER TABLE ADD COLUMN`（新增列请**可空或带默认值**）
- 插件自己的表加字段**不会被自动补**（要么删库重来，要么自己在 `on_load` 里处理）

## 8. 启动 / 关闭流程

`on_startup()`：`init_db()`（建表 + 自动补列）→ 迁移旧 `plugin_config` 表 → `sync_plugin_info()`
（含清理失效记录）→ 逐个插件 `load_config()` → 导入内置人设 → 触发 `on_load` 钩子

`on_shutdown()`：触发 `on_unload` 钩子 → 释放渲染浏览器 → 关闭 LLM 连接 → 释放数据库 → 摘日志 sink
（日志最后摘，保证关闭过程也留痕）

## 9. 渲染核心

```
Markdown（python-markdown + pymdownx + Pygments）
  → Jinja2 注入模板（src/core/templates/markdown.html + markdown.css）
  → Playwright 元素级截图（只截 .markdown-body，device_scale_factor = RENDER_SCALE）
```

- 浏览器单例懒加载，**复用系统 Edge**（换环境改 `RENDER_BROWSER_CHANNEL`）
- 画布宽固定 `RENDER_WIDTH`，高自适应；输出宽 = `RENDER_WIDTH × RENDER_SCALE`
- Markdown 里的**本地图片会自动内联成 data URI**
- 命令行自测：`python -m src.core.render 输出.png [输入.md]`

## 10. 技术决策速查

| 项目 | 决定 | 备注 |
|---|---|---|
| 框架 | **NoneBot2 v2** | 明确**不基于 Pallas-Bot** 开发 |
| 协议适配 | `nonebot-adapter-onebot`（OneBot v11） | 正向 WS 驱动**必须带 `~websockets`** |
| WebUI 后端 | 内嵌 FastAPI（复用 NoneBot 的 driver app） | 挂载 `/api` |
| WebUI 前端 | 零构建单页（9 页） | 不引入打包工具 |
| 数据库 | SQLite（SQLAlchemy 2.0 async + aiosqlite） | 所有插件共享 |
| 配置位置 | **`.config/`**（已 gitignore）：`main/.env` + `<插件名>/config.json` | 见 [`configuration.md`](configuration.md) |
| 插件系统 | 复用 NoneBot2 加载 + 自研门控层 | 一插件一文件夹 |
| 插件管理 | QQ 命令 + WebUI API 双通道（共用 `service`） | |
| 框架版本协议 | `FRAMEWORK_VERSION`，插件用 `requires` 声明兼容范围 | 不满足则拒绝加载 |
| 环境管理 | pixi（Python 3.11，win-64 / linux-64） | |
| 部署 | 本地 / 服务器直跑（systemd / 任务计划） | 见 [`deploy.md`](deploy.md) |

> 不用 Pallas-Bot 的原因：复读 / 语料 / 牛格功能用不到；LLM 链路绑死牛格；WebUI 与功能强耦合；架构过重。
> 可以参考其相关代码，但不基于它开发。

## 11. 与同类项目的差距对比

与 **AstrBot** 插件规范的逐项对比记录在 [`plans/01-plugin-dev-protocol.md`](plans/01-plugin-dev-protocol.md)
（六项差距已全部落地）。本项目更强的地方：

- 命令权限**可配置 + 即时生效**（AstrBot 是代码里硬编码）
- **三级门控**比 AstrBot 的全局插件开关细
- **消息输出守卫**（AstrBot 没有这一层）
- 共享 SQLite（插件继承 `Base` 即自动建表）
- 插件是**模块级 `Feature` 实例 + 装饰器**，可跨文件拆分
