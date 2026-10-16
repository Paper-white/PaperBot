# PaperBot 文档

来对了地方 👋 这里是所有开发与使用文档的入口。

## 我想……

| 我想… | 看这里 |
|---|---|
| 先把机器人跑起来 | [`../README.md`](../README.md#2-快速部署)（README 的「快速部署」） |
| 知道有哪些功能 / 命令怎么用 | [`commands.md`](commands.md) |
| 搞懂整体架构与设计取舍 | [`architecture.md`](architecture.md) |
| 配置机器人（框架 / 插件） | [`configuration.md`](configuration.md) |
| 部署到服务器 / 长期运行 | [`deploy.md`](deploy.md) |
| 写一个插件（上手指南） | [`plugin-dev.md`](plugin-dev.md) |
| 写插件的 API 参考 | [`plugin-api.md`](plugin-api.md) |
| 用 LLM（人设 / 记忆 / 会话） | [`llm.md`](llm.md) |
| LLM 开发参考 | [`llm-api.md`](llm-api.md) |
| 调 WebUI 接口 | [`api.md`](api.md) |
| 参与开发 / 跑自检 / 看踩坑 | [`development.md`](development.md) |
| 看开发计划与进度 | [`plans/roadmap.md`](plans/roadmap.md) |

## 文档地图

```
docs/
├── index.md            ← 你在这里（导航）
├── architecture.md     架构与实现（技术决策 / 模块 / 机制 / 数据表 / 文件树）
├── configuration.md    配置总览（.config/ 布局 + 全部配置项）
├── commands.md         命令手册（总控 / 对话 / 示例插件）
├── api.md              WebUI 与 REST API 一览
├── deploy.md           部署（本地 / 服务器 / 反向代理 / 排错）
├── development.md      开发指南（环境 / 自检 / 规范 / 踩坑备忘）
├── plugin-dev.md       插件开发指南（上手 / 目录 / 生命周期 / 热重载）
├── plugin-api.md       插件开发 API 参考（接口 / 装饰器 / 数据结构）
├── llm.md              LLM 使用与开发指南（配置 / 人设 / 记忆）
├── llm-api.md          LLM 开发 API 参考（接口 / 数据结构）
└── plans/              功能与开发计划（一个功能一个文件）
    ├── README.md        计划写法约定 + 目录
    ├── roadmap.md       总路线图（Phase 1~4）
    ├── 01-plugin-dev-protocol.md
    ├── 02-llm-service.md
    ├── 03-config-consolidation.md
    ├── 04-backlog.md    待办与想法池
    ├── 05-version-control.md
    ├── 06-webui-config-and-mute.md
    ├── 07-memory-optimization.md  长期记忆与召回优化（调研 + 规划）
    └── 08-customskinloader-log.md  CustomSkinLoader 日志解析插件
```

> 模块 / 数据表 / 目录结构的**明细**统一维护在 [`architecture.md`](architecture.md)，
> 这里只列「文档入口」，避免两处漂移。

## 约定

- 文档用**中文**；面向使用者的（本目录与 `README.md`）**不写本地环境信息**（服务器地址、令牌、群号、密钥）
- 「规范与指南」放 `docs/`，「一个功能的规划与进度」放 `docs/plans/`
- **单一信息源**：数据表 / 文件树 → `architecture.md`；`.env` 配置 → `configuration.md`；
  WebUI 页面 → `api.md`；命令 → `commands.md`（别在别处重复维护）
- 改框架对外接口时，**顺手更新对应文档**（`plugin-dev.md` / `plugin-api.md` / `llm.md` / `llm-api.md` / `api.md` 最容易过期）
