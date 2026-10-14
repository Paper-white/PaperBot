# 框架版本控制（一键拉取云端版本）

- 状态：已完成
- 开始：2026-10-14
- 相关文件：`src/core/vcs.py`、`webui/routes/version.py`、`_tests/vcs_check.py`、`src/config.py`
- 相关文档：[`docs/api.md`](../api.md)、[`docs/deploy.md`](../deploy.md)、[`docs/configuration.md`](../configuration.md)

## 目标

给**框架本体**加「版本控制」能力，核心场景：

> **本地开发机 push → 服务器生产环境在 WebUI 点一下，就拉到最新代码，且配置文件与数据毫发无损。**

验收标准：

- 设置页能看到：当前 commit、分支、工作区是否干净、与 `origin/main` 的领先 / 落后数
- 「检查更新」拉取 `origin/main` 并列出提交历史，可选中**任意 commit** 切换（回滚）
- 「更新到最新」一键对齐；「重启 Bot」让新代码生效
- **全程不碰** `.config/` `data/` `logs/`（均已 gitignore，且绝不执行 `git clean`）
- 额外显示**守护服务（systemd / Windows 服务）是否正常运行**

## 方案与决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 入口 | **并入「设置」页**新增卡片 | 不新增左侧菜单，和「版本信息」卡片相邻，语义一致 |
| 生效 | 更新后给**独立「重启 Bot」按钮** | 框架代码无法热重载，必须重启；把决定权交给使用者 |
| 版本粒度 | 列 `origin/main` 提交历史，**可切任意 commit** | 既能更新到最新，也能回滚到任意历史版本 |
| 未提交改动 | **拒绝更新**（409） | 生产环境代码不该有手改，拦住比自动 stash 更安全、心智更简单 |
| 远程 / 鉴权 | 公开仓库 **HTTPS 免鉴权**，只追 `origin/main` | 只拉不推，公开仓库无需 token |
| git 实现 | **纯 subprocess**，零新依赖 | 和现有脚本风格一致，避免引入 GitPython |
| 重启方式 | **spawn 与 exit(守护) 都做**，配置项切换 | spawn 开箱即用；exit 配合 systemd/NSSM 实现"崩溃自愈" |

### 安全要点

- commit 入参用正则 `^[0-9a-fA-F]{7,40}$` **强校验**（防命令注入）
- 所有 git 命令走 `asyncio.create_subprocess_exec`（列表参数、**不走 shell**）
- fetch 的代理通过 `git -c http.proxy=` **临时注入**，不污染仓库 `.git/config`
- **绝不** `push`、**绝不**切换远程、**绝不** `git clean`、**绝不**自动装依赖
- 所有接口受 `WEBUI_TOKEN` 保护

### 重启的两种模式

| 模式 | 谁负责拉起新进程 | 崩溃能自愈 | 适用 |
|---|---|---|---|
| `spawn` | Bot 自己起 detached「小助手」，延迟 3s 拉起 | ❌ 只有点按钮才重启 | 未配守护 / 手动启动 |
| `exit` | systemd / NSSM 等守护进程（`Restart=always`） | ✅ 任何挂掉都自动拉 | 长期常驻生产环境 |

> 两者不冲突：配了守护进程后把 `VCS_RESTART_MODE` 改成 `exit` 即可。

## 落地清单

- [x] `src/config.py` 新增 `VCS_*` 配置项 + `.env.example` 模板
- [x] `src/core/vcs.py`：状态查询 / fetch / update / 提交历史 / 守护服务检测 / 重启
- [x] `webui/routes/version.py`：`GET /version`、`POST /version/fetch|update|restart`
- [x] `webui/api.py` 注册 `version` router
- [x] `webui/static/assets/app.js`：设置页「版本控制」卡片 + 交互
- [x] `_tests/vcs_check.py` 自检脚本（pixi task `check-vcs`），**27/27 通过**
- [x] 文档：本文件 + `plans/README.md` + `roadmap.md` + `api.md` + `deploy.md` + `configuration.md` + `README.md`

## 实现要点

### `src/core/vcs.py`

- 底层 `_run()` 统一执行 git 命令（超时 / 代理 / `GIT_TERMINAL_PROMPT=0` 禁交互）
- `VcsError.code` 为语义错误码，路由层映射 HTTP 状态码
- 工作区干净校验在 `update()` 内，脏 → `code="dirty"`
- `reset --hard` 天然不动被忽略的 `.config/` `data/` `logs/`（自检里有专项验证）

### `webui/routes/version.py`

| 错误码 | HTTP |
|---|---|
| `no_repo` / `no_git` / `bad_commit` / `unknown_commit` / `no_branch` | 400 |
| `no_fetch` / `dirty` | 409 |
| `timeout` | 504 |
| `git_failed` | 502 |

- `/version/restart`：**先返回响应**，再由后台任务延迟执行退出（前端请求中断属正常）

## 遗留 / 后续

- 依赖变化（`pyproject.toml` / `pixi.lock`）时只**提示**手动 `pixi install`，不自动装
- 更新失败不做**自动回滚**（可手动切回历史 commit 兜底）
- 只做**框架本体**，不做插件级独立版本控制
- 只追 `origin/main`，不做多分支管理
- git 命令超时时间写死（fetch 60s / 其它 15s），暂未做成配置项
