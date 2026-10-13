# PaperBot 文档

来对了地方 👋 这里是所有开发与使用文档的入口。

## 我想……

| 我想… | 看这里 |
|---|---|
| 先把机器人跑起来 | [`../README.md`](../README.md#-快速部署)（README 的「快速部署」） |
| 知道有哪些功能 / 命令怎么用 | [`commands.md`](commands.md) |
| 搞懂整体架构与设计取舍 | [`architecture.md`](architecture.md) |
| 配置机器人（框架 / 插件） | [`configuration.md`](configuration.md) |
| 部署到服务器 / 长期运行 | [`deploy.md`](deploy.md) |
| 写一个插件 | [`plugin-dev.md`](plugin-dev.md) |
| 用 LLM（人设 / 记忆 / 会话） | [`llm.md`](llm.md) |
| 调 WebUI 接口 | [`api.md`](api.md) |
| 参与开发 / 跑自检 / 看踩坑 | [`development.md`](development.md) |
| 看开发计划与进度 | [`plans/roadmap.md`](plans/roadmap.md) |

## 文档地图

```
docs/
├── index.md            ← 你在这里（导航）
├── commands.md          命令手册（总控 / 对话 / 示例插件）
├── architecture.md      架构与实现（技术决策 / 模块 / 机制 / 数据表）
├── configuration.md     配置总览（.config/ 布局 + 全部配置项）
├── deploy.md            部署（本地 / 服务器 / 反向代理 / 排错）
├── api.md               WebUI 与 REST API 一览
├── development.md       开发指南（环境 / 自检 / 规范 / 踩坑备忘）
├── plugin-dev.md        插件开发协议（写给插件作者）
├── llm.md               LLM 使用与开发指南
└── plans/               功能与开发计划（一个功能一个文件）
    ├── README.md        计划写法约定 + 目录
    ├── roadmap.md       总路线图（Phase 1~4）
    ├── 01-plugin-dev-protocol.md
    ├── 02-llm-service.md
    ├── 03-config-consolidation.md
    └── 04-backlog.md    待办与想法池
```

## 约定

- 文档用**中文**；面向使用者的（本目录与 `README.md`）**不写本地环境信息**（服务器地址、令牌、群号、密钥）
- 「规范与指南」放 `docs/`，「一个功能的规划与进度」放 `docs/plans/`
- 改框架对外接口时，**顺手更新对应文档**（`plugin-dev.md` / `llm.md` / `api.md` 最容易过期）
