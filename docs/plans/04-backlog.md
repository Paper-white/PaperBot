# 待办与想法池

- 状态：进行中
- 说明：**零散的待办 / 想法**先记在这里；攒够分量再升级成 `NN-xxx.md` 独立计划

## 已完成（存档）

- [x] 群内实测：`%help` / `%ai` 系列 / `%off` / `%on` / `%admin`，以及 WebUI 改命令权限、改人设/服务商即时生效
- [x] 真实 LLM 连通：WebUI 配服务商（含中转站拉模型、试聊、流式、记忆触发）
- [x] 时间显示口径统一（三处改用 `llm.fmt_dt()`，UTC → 北京时间）
- [x] `pagePlugins` 前端兜底（详情缺 `global_enabled` 回落列表数据）
- [x] 聊天记录原始流水层 + 定时记忆总结（个人/群双层）+ 上下文压缩
- [x] echo 插件改为在线状态测试插件（`paperbot_ping`，`%ping` → `pong`）

## 待办（下一步候选）

### 1. 记忆 / 压缩真机验证

- [ ] 重启后确认 `paperbot_ping` 正常加载（旧实例还是 `paperbot_echo`）
- [ ] 群里聊一阵 → 查 `chat_message` 有 sender_id / is_bot
- [ ] 等一轮 scheduler → 查 `llm_user_profile` / `llm_group_profile` 落库
- [ ] 群聊回复注入「群记忆 + 发言者个人记忆」的效果
- [ ] 会话消息超 `LLM_COMPRESS_THRESHOLD` 后 `llm_session.summary` 被写入

## 想法池（未定，先记着）

- 记忆的**向量检索**（现在是全塞进 prompt，条数一多就不划算）
- 群成员昵称/头像获取，让人设对话更自然（需注意权限与隐私）
- 渲染：支持更多模板（输出表格 / 排行榜 / 天气卡片）
- 定时任务框架（插件里有周期性任务时，现在靠各插件自己起 asyncio task；scheduler 目前是记忆专用的雏形）
- **AI 工具调用（function calling）**：让 AI 能自己调用工具查看群聊上下文 / 用户画像，
  做「按需深挖」而非全量注入。依赖 `chat_message` 按维度查询能力（已具备）
