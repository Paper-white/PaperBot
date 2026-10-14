# 待办与想法池

- 状态：进行中
- 说明：**零散的待办 / 想法**先记在这里；攒够分量再升级成 `NN-xxx.md` 独立计划

## 待办（已确认要做）

### 1. 群内实测（Phase 3 第一步）

- [ ] `%help` / `%help paperbot_chat` / `%help paperbot_echo`
- [ ] `%ai` 系列（`%ai <内容>` / `%ai 群` / `%ai 流式` / `%ai 印象` / `%ai 记住` / `%ai 人设` / `%ai 状态`）
- [ ] `%off <插件>` / `%on <插件>` / `%admin add <QQ>`
- [ ] WebUI「命令管理」改命令权限 → 验证**即时生效**
- [ ] WebUI「LLM 与记忆」页改人设 / 服务商 → 验证即时生效

### 2. 真实 LLM 连通

- [ ] 配一把真实 key（WebUI 加服务商或 `.env` 兜底）
- [ ] 群内跑通 `%ai`（普通 + 流式）
- [ ] 触发记忆：连续聊 ≥ `LLM_IMPRESSION_EVERY` 条 → 检查 `llm_user_profile` / `llm_user_fact`

### 3. 历史遗留：时间显示口径不统一

- [x] `webui/routes/commands.py` 用 `iso_local(rule.updated_at.timestamp())` 显示时间，
      而库里是 UTC（`func.now()`）→ 显示**比北京时间少 8 小时**
- [x] `webui/routes/plugins.py` / `permissions.py` 是裸 `isoformat()`（同样是 UTC 钟点）
- [x] 统一方案：对外统一用 `llm.fmt_dt()`（那是目前唯一正确的转换）
- [x] 改的时候要**连带回归**「命令管理 / 插件管理 / 黑白名单」三个页面

### 4. 小加固

- [x] `webui/static/assets/app.js::pagePlugins` 加兜底：详情缺 `global_enabled` 时回落列表数据
      （后端已返回该字段，纯前端加固）

## 想法池（未定，先记着）

- 插件市场 / 插件包（`.zip` 上传安装）——参考 AstrBot 的做法，但本项目是自用，优先级低
- 会话「总结压缩」：上下文超长时用 LLM 压缩成一段摘要再塞回去（省 token）
- 记忆的**向量检索**（现在是全塞进 prompt，条数一多就不划算）
- 群成员昵称/头像获取，让人设对话更自然（需注意权限与隐私）
- 渲染：支持更多模板（输出表格 / 排行榜 / 天气卡片）与深色模式（当前**刻意不做**深色）
- 定时任务框架（插件里有周期性任务时，现在靠各插件自己起 asyncio task）

## 已知不做（避免反复讨论）

- ❌ 不基于 Pallas-Bot 开发（复读/语料/牛格用不到，LLM 链路绑死牛格，架构过重）
- ❌ 不支持多平台（只做 OneBot v11）
- ❌ 插件配置不存 SQLite（已迁到 `.config/<插件名>/config.json`，见 03 号计划）
- ❌ 不做深色模式渲染
