# 总路线图

- 状态：进行中
- 开始：2026-09-30
- 相关文件：全部（这是概览）

> 每个阶段的细节看对应计划文件；这里只维护**「我们在哪、下一步去哪」**。

## 阶段总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| **Phase 1** | 框架骨架：插件系统 / 门控 / 权限 / WebUI 后端 / 渲染 / 日志 | ✅ 已完成 |
| **Phase 1.5** | 插件开发协议（对标 AstrBot 六项差距）→ [`01-plugin-dev-protocol.md`](01-plugin-dev-protocol.md) | ✅ 已完成 |
| **Phase 2** | 统一 LLM 服务（多服务商 / 人设 / 会话 / 记忆 / 流式）→ [`02-llm-service.md`](02-llm-service.md) | ✅ 已完成 |
| **Phase 2.5** | 配置集中化（`.config/` + 插件配置改 JSON）→ [`03-config-consolidation.md`](03-config-consolidation.md) | ✅ 已完成 |
| **Phase 2.6** | 框架版本控制（WebUI 一键拉取云端版本 + 重启）→ [`05-version-control.md`](05-version-control.md) | ✅ 已完成 |
| **Phase 3** | WebUI 打磨（第一轮已完成：自动刷新 / 浮窗 / 输入控件 / 命令大表 / 日志页；后续按需继续） | 🚧 进行中 |
| **Phase 3（06 号）** | WebUI 配置修复 + 配置热重载 + 捂嘴命令 → [`06-webui-config-and-mute.md`](06-webui-config-and-mute.md) | 🚧 进行中 |

## Phase 1 已完成清单（存档）

- pixi 工作区 + `pyproject.toml`
- `bot.py` 入口 + `.env.example`
- 自研框架核心 `src/core`（Feature / 门控 / 守卫 / 权限 / 共享 DB / service / loader）
- 总控插件 `paperbot_console` + 在线状态测试插件 `paperbot_ping`
- 四级权限系统（0 黑名单 / 1 普通 / 2 群管 / 3 所有者）+ 黑名单门控
- 消息输出守卫（开发期锁死消息出口，私聊一律拦截）
- WebUI 后端全量 API + 零构建单页前端
- 渲染核心 `src/core/render.py`（Playwright + 系统 Edge）+ 模板
- 日志文件输出 `src/core/logging.py`（每次运行一个文件 + WebUI 查看/清理）
- **命令权限可配置**（`command_rule` 表 + WebUI 命令管理页，改完即时生效）
- **框架级默认门控策略**（`GATE_DEFAULT_ENABLED=false` = 全局默认禁用 + 白名单群放行）
- **群白名单合并**（`BOT_ACTIVE_GROUPS`：门控默认策略 + 输出守卫共用）

## 下一步（Phase 3 候选）

按优先级，具体拆解见 [`04-backlog.md`](04-backlog.md)：

1. **记忆 / 压缩真机验证**：重启确认 `paperbot_ping` 加载，群里聊一阵后查
   `chat_message` / `llm_user_profile` / `llm_group_profile` / `llm_session.summary`
2. **WebUI 打磨**：前端交互细节、更多 API、按需补设置项
3. **AI 工具调用（function calling）**：让 AI 自己查群聊上下文 / 用户画像（想法池）
4. **长期记忆与召回优化**：对标两 AstrBot 记忆插件（代码闸门 / 分槽保底 / 软衰减 / 向量检索等）
   → [`07-memory-optimization.md`](07-memory-optimization.md)（规划中）

## 维护约定

- 新开一个大功能 → 在 `docs/plans/` 建一个新文件（取当前最大序号 +1），并在本文档的阶段表里占一行
- 阶段完成后把状态改成 ✅，把「下一步」往下推
- **详细进度与待办写在本目录**；`AGENTS.md` 的「当前状态」小节只留一句摘要 + 指到这里的指针
