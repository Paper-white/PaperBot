# 开发计划 / 功能文档

> 这里放**功能规划与开发计划**：一个功能（或一轮开发计划）一个文件，方便单独跟踪、
> 也方便以后写「类似 LLM 那样的大功能」时有个固定去处。

## 和其它文档的分工

| 位置 | 放什么 | 例子 |
|---|---|---|
| `docs/plans/`（**本目录**） | 一个功能的**规划 / 决策 / 落地记录 / 遗留问题** | 本目录下的各文件 |
| `docs/` | 面向使用者的**规范与指南**（长期有效、成体系），入口是 [`../index.md`](../index.md) | `architecture.md`、`configuration.md`、`commands.md`、`deploy.md`、`api.md`、`development.md`、`plugin-dev.md`、`plugin-api.md`、`llm.md`、`llm-api.md` |
| `README.md` | 面向项目外的**公开介绍**（技术栈 / 部署 / 目录索引 / 功能 / 致谢） | 根目录 |
| `AGENTS.md` | 交接给新会话的**开发知识**（含本地环境信息）→ **不提交** | §2 本地环境、§3 硬核易错点 |

> ⚠️ 本目录的文件**是可以提交的**：写的时候**不要**包含服务器地址、令牌、群号、密钥等本地信息
> （那些留在 `.config/` 或 `AGENTS.md` 里）。

## 命名与状态

- 文件名：`NN-简短英文名.md`（`NN` 是两位序号，按开工顺序递增；新计划取当前最大值 +1）
- 计划改主意了、被废弃了：**不要删文件**，把状态改成 `已废弃` 并写清原因（保留决策痕迹）
- **每一步都要落文档**：每完成一步，就在**该功能已有的那个计划文件**里写清楚「这一步干了什么」
  （改了什么 / 为什么 / 验证结论 / 留下的坑）；阶段性收尾时顺手更新 `roadmap.md` 的「已完成」段。
  ❌ 不要为了记流水账新增一堆文件。
- 每个文件开头用这个骨架：

```markdown
# <功能名>

- 状态：规划中 / 进行中 / 已完成 / 已废弃
- 开始：YYYY-MM-DD
- 相关文件：`src/xxx.py`、`docs/xxx.md`
- 相关文档：#<issue / 讨论链接>（可选）

## 目标
（要解决什么问题、验收标准是什么）

## 方案与决策
（为什么这么做、放弃了哪些替代方案）

## 落地清单
- [x] 已完成项
- [ ] 未完成项

## 遗留 / 后续
（已知问题、待办、下次接着做的地方）
```

## 目录

| 文件 | 内容 | 状态 |
|---|---|---|
| [`roadmap.md`](roadmap.md) | **总路线图**（Phase 1~4 与当前进度）——先看这个 | 进行中 |
| [`01-plugin-dev-protocol.md`](01-plugin-dev-protocol.md) | 插件开发协议（对标 AstrBot 的六项差距） | 已完成 |
| [`02-llm-service.md`](02-llm-service.md) | 统一 LLM 服务（多服务商 / 人设 / 会话 / 记忆 / 流式） | 已完成 |
| [`03-config-consolidation.md`](03-config-consolidation.md) | 配置集中化（`.config/` + 插件配置改 JSON） | 已完成 |
| [`04-backlog.md`](04-backlog.md) | 待办与想法池（零散的、还没立成计划的） | 进行中 |
| [`05-version-control.md`](05-version-control.md) | 框架版本控制（WebUI 一键拉取云端版本 + 重启） | 已完成 |
| [`06-webui-config-and-mute.md`](06-webui-config-and-mute.md) | WebUI 配置修复 + 配置热重载 + 捂嘴命令 | 进行中 |
| [`07-memory-optimization.md`](07-memory-optimization.md) | 长期记忆与召回优化（对标两 AstrBot 记忆插件的调研 + 规划） | 规划中 |
