# 长期记忆与召回优化（调研 + 规划）

- 状态：主体已完成（P0/P1/P2 + P3 归档；剩 3 项增强待做）
- 开始：2026-10-15
- 相关文件：`src/core/models.py`、`src/core/llm.py`、`src/core/database.py`、`src/core/chatlog.py`、`src/core/scheduler.py`、`src/core/textutil.py`、`plugins/paperbot_chat/paperbot_chat.py`、`docs/llm.md`、`docs/configuration.md`
- 相关文档：对标仓库
  - `menglimi/astrbot_plugin_private_companion`（陪伴核心）+ 姊妹仓库 `menglimi/astrbot_plugin_memory_companion`（记忆核心）
  - `lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory`

## 目标

对标两个 AstrBot 记忆插件的实现，找出 PaperBot 当前长期记忆（提炼/存储/召回/注入）的空白，
沉淀成可落地的改进清单。**验收标准**：明确哪些直接照抄、哪些按需借鉴、哪些明确不学，
并排出优先级与落地点。

> 本文件是**调研结论 + 规划 + 落地记录**。主体已于 2026-10-15 落地（见 §4）；
> 剩余 3 项增强待做（WebUI 档位/改口展示、记忆整合、向量检索）。

## 1. 两个对标项目速览

| | **livingmemory** | **private_companion（+ memory_companion）** |
|---|---|---|
| 定位 | 独立长期记忆插件 | 陪伴插件与记忆核心**双仓库拆分**，可独立安装 |
| 存储 | SQLite + FTS5 + FAISS 向量 | SQLite + FTS5 + **进程内 numpy 精确扫描**（无 ANN） |
| 检索 | 双路四模式（文档路/图谱路 × 关键词/向量）+ RRF | **六路候选**（fts/keyword/vector/time/current/priority）+ RRF + Rerank + MMR |
| 记忆单元 | memory + **memory_atom**（带类型/TTL） | memory（40+ 字段）+ 知识图谱 + 断言(assertion) |
| 注入方式 | **add-only 临时段**（`mark_as_temp`） | XML 记忆包 + 临时段 + **分槽 + 用法权限** |
| 代表亮点 | 原子化 TTL 衰减、可恢复写日志 | 代码闸门、`mention_policy`、分槽保底、复述加固 |

> ⚠️ 重要发现：`private_companion` **本身不实现完整长期记忆链路**（只保留本地轻量记忆 +
> 只读投影适配器）；真正的存储/写入/召回/生命周期在 `astrbot_plugin_memory_companion`，上表已合并两者。

## 2. PaperBot 当前实现与局限（基线）

数据流：`chat_message`（消息流水，全局钩子）→ 每 30min 后台调度总结 →
`llm_user_profile` / `llm_user_fact` / `llm_group_profile` / `llm_session.summary` →
请求时在 `llm.py::_build_request` 拼进 system。

已具备（保留）：流水层与上下文层职责分离、后台异步不阻塞、人工记忆不被自动覆盖
（`source=manual`）、总结用独立便宜模型、注入措辞已做防暴露设计。

**主要局限（截至 2026-10-15，已修复的用删除线标注）**：

1. **无向量/语义检索**：仍靠 SQL scope 过滤 + 分槽保底 + 衰减打分选注入（非全量塞了，但也不是语义召回）。
2. ~~记忆是"自由文本 + KV"、无时间衰减、无重要性打分~~ → **已改善**：加了半衰期软衰减、`weight/access_count`、
   受控 key 词表 + 单值/多值区分；仍无独立 importance 打分（用 source+scope+访问频次近似）。
3. ~~`chat_message` 永久增长、`facts` 库内无上限~~ → **已修复**：`chat_message` 冷区归档（`CHATLOG_RETENTION_DAYS`）、
   `facts` 单 scope 上限（`LLM_MAX_FACTS_STORED`）；`summary` 仍会累加（低风险）。
4. **压缩仍是朴素字符串拼接**（未变）。
5. ~~**user-scope 压缩摘要"写了却从不注入"**~~ → **已修复（2026-10-15）**：user 分支已补读 `llm_session.summary`。
6. ~~**群分支的「更早的对话摘要」是死代码**~~ → **已处置**：群上下文改走 `chat_message` 流水，该分支恒空但不影响功能。
7. ~~**私聊内容可能经群聊泄露**~~ → **已修复（2026-10-15）**：事实按来源 scope 隔离，群聊只注入「通用 + 本群」，印象降级为私聊专属。
8. ~~**无引用校验/代码闸门**，幻觉事实可直接入库~~ → **已修复（2026-10-15 第二批）**：`memory_gate.py` 引用校验 + 四类拒绝。
9. ~~两套提炼入口并存（`refresh_profile` 旧 vs `summarize_user` 新）~~ → **已清理**：`refresh_profile` 已删除。
10. **`message_count` 语义混用**（未变，低风险）。
11. ~~`summarize_user` 水位线恒 0~~ → **已修复（2026-10-15）**：`get_profile()` 补返回 `last_msg_id`。

## 3. 共识度最高、最该抄的设计

### 1. 写入与注入解耦：「原文 ≠ 记忆」两段式
两者一致：原始消息先落流水（不可信数据），长期记忆由阶段性 LLM 总结沉淀，且**原文只在该批总结成功后才允许清理**。
- private_companion：`timeline` + `summary_batches` 账本，失败挂起该批、绝不丢原文。
- livingmemory：滑动窗口增量总结 + `last_summarized_index` 水位线 + 失败重试补偿（最多 3 次）。

→ PaperBot 有 `chat_message` ✅，但缺少"总结成功才清理"的强约束（现在直接物理删 `llm_message`）。

### 2. 代码闸门 > prompt 兜底（private_companion 最强项）
`claim_is_personal_fact()` 对着**被引用的原文**（而非模型改写后的断言）判定，用 marker 词表 +
`difflib` 长匹配拒绝四类"字面有据但错"的事实：
- 转述（他说/据说）、角色扮演（扮演/台词）、临时状态（今天有点/就这一次）、未兑现打算（打算/计划）

→ PaperBot 完全依赖 prompt、无引用校验，**幻觉极易入库**。**最高价值借鉴点**。

### 3. 受控 predicate 词表 + 单值/多值维度区分
单值维度（生日/职业/住址）改口 → 旧值标 `superseded` + `supersedes_id` 链接且**保留旧值**
（支持"你以前说过…"）；多值维度（爱好/承诺）绝不互相取代。别名归一化（"住址"→residence）
防止模型自创槽位。

→ PaperBot 有 `llm_user_fact(user_id, fact_key)` + manual/auto 保护 ✅，但无受控词表、无改口版本链、
无单值/多值区分 → 模型可自造 key 无限膨胀。

### 4. 重要性打分 + 访问强化衰减（认知规律建模）
- livingmemory：`importance *= (1-rate)^days`，`access_factor` 越大衰减越慢。
- private_companion：半衰期制 `decay = exp(-ln2·age/half_life)` +
  `rehearsal_strength = reinforcement·exp(-ln2·idle/60)`（被召回过就更难忘）。
- 两者都强调：**软衰减只降权、绝不静默删除**，删除一律走「冷区/归档 + 可回滚审计」。

→ PaperBot **零衰减、零重要性、零遗忘**。

### 5. 分槽 + 每槽保底（解决 RAG 陪聊经典痛点）
private_companion `search_by_slots`：`open_loop → self_timeline → user_profile → current_window →
summary → stable_memory`，**每个非空槽先保底 1 条**再按预算填充，避免"日程/近期消息占满 top_k"。

→ PaperBot 是全量读 + 硬截断，无分槽、无相关性排序。

### 6. 注入安全：包围标记 + 冲突优先级声明 + 用法权限分离
- livingmemory：`BEGIN/END HISTORICAL MEMORY` 模板 + "与当前消息冲突时以当前为准"。
- private_companion：**mention / tone / uncertain** 三档，`tone` 类只给语气提示、**不塞原文**
  （省 token 且守隐私）；记忆可带 `usage` 字段。

→ PaperBot 有防暴露措辞 ✅，但无冲突优先级声明、无用法档位，且存在跨窗口泄露路径。

### 7. 时间归一化（两者都做）
写入时把"今天/明天/下周"按**每条消息的发送时间**换算成绝对日期，显著提升记忆可查性。

## 4. 落地清单（按 ROI 排序）

### 已完成（2026-10-15）
- [x] **修复「user-scope 压缩摘要静默丢失」**：`_build_request` 的 user 分支补读 `llm_session.summary`
- [x] **注入加「冲突优先级声明 + 包围标记」**：`--- 记忆开始/结束 ---` + 「冲突以当前为准」
- [x] **跨窗口隔离（私聊/各群事实分离）**：`llm_user_fact` 加 `scope_type` + `scope_id`，唯一键改四元组；
  `summarize_user` 按来源分批提炼；群聊只注入 `global + 本群`、印象降级为私聊专属（`LLM_MEMORY_SCOPE_ISOLATION`）
- [x] **`summarize_user` 水位线恒 0 修复（B1）**：`get_profile()` 补返回 `last_msg_id`
- [x] **QQ 身份锚**：群聊 system 钉死「当前发言人（QQ）」，判人以 QQ 号为准
- [x] **群聊回复合并窗口**：`paperbot_chat` 冷却由「丢消息」改「窗口合并成一轮」
- [x] **P0 事实抽取代码闸门**：`src/core/memory_gate.py`（引用校验 + 转述/角色扮演/临时态/未兑现四类拒绝）
- [x] **P1 压缩窗口对齐**：保留窗口改 `2 × LLM_CONTEXT_ROUNDS`
- [x] **P1 facts 库内条数上限**：`LLM_MAX_FACTS_STORED=200` + `_prune_facts`（只淘汰 auto）
- [x] **P1 分槽保底注入**：`select_facts_by_slots`（通用/窗口两槽各保底 5 条），预留 `_fact_score`
- [x] **P1 受控 key 词表**：`src/core/fact_keys.py`（14 条规范 key + 别名归一 + 单值/多值），
  auto 未命中即丢弃（`LLM_MEMORY_KEY_EXACT`），manual 只归一不丢弃
- [x] **P2 时间归一化**：相对日期词按消息发送时间内联标注成绝对日期，prompt 与闸门同源
- [x] **P2 用法档**：`llm_user_fact.usage`（mention/tone/uncertain），槽内分档渲染（`LLM_MEMORY_USAGE_ENABLE`）
- [x] **P2 半衰期软衰减 + 复述加固**：`weight`/`access_count`/`last_used_at`；
  `_fact_score = weight × 0.5^(age/half_life)`（manual 不衰减）；注入即回血 + 节流写库；**只降权不删**
- [x] **P1 单值改口版本链（`supersedes_id`）**：`llm_user_fact` 加 `status`（active/superseded）+ `supersedes_id`；
  唯一键改 **partial unique index**（`WHERE status='active'`，含 `fact_value` 以支持多值并存）；
  单值改口保留旧值、多值并存；`get_profile` / `_prune_facts` / `fact_count` 均只计 active；
  开关 `LLM_MEMORY_SUPERSEDE_ENABLE`；迁移 `_migrate_llm_fact_status`（换索引、不整表重建）
- [x] **P3 `chat_message` 归档 / 保留期**：`chat_message_archive` 冷区表 + 每日归档
  （只归档「时间到期且已过水位线」的消息）；`CHATLOG_ARCHIVE_ENABLE` / `CHATLOG_RETENTION_DAYS=90`
- [x] 清理废弃 `refresh_profile`；清理死配置 `LLM_GROUP_CONTEXT_MSGS`；`_migrate_llm_fact_scope` 幂等迁移

### 待办
- [ ] **WebUI 手写档位下拉 / 改口历史展示**（P3）：用户详情页给 fact 加 usage 选择、按 `supersedes_id` 展示旧值
- [ ] 记忆整合/压缩：低价值旧记忆聚合成高层摘要（`consolidation` 任务）
- [ ] **向量检索**：目前全塞 prompt（backlog 已记）→ 建议上 **pgvector + HNSW/IVFFlat**

### 改口版本链设计结论（oracle 评审，**已实施**，2026-10-15）
- 字段：`llm_user_fact` 加 `status`（`active`/`superseded`，默认 active）+ `supersedes_id`（可空）；
- 唯一键：**partial unique index** `uq_llm_fact_active`，`(user_id,fact_key,scope_type,scope_id,fact_value) WHERE status='active'`
  （含 `fact_value` 以支持多值并存；SQLite 支持 partial index，可 DROP+CREATE、无需整表重建）；
- 改口判定：单值 key 值变化 → 插新行 + 旧行标 superseded + 新行 `supersedes_id=旧行.id`；多值 key 值变化 → 并存 + 值去重；
- 同步改：`_prune_facts` 只裁 active；`get_profile` 只返回 active；`list_profiles` 仅计 active；
  `_migrate_llm_fact_scope` 守卫改「`uq_llm_fact_scope` 或 `uq_llm_fact_active` 任一存在即已迁移」；
- 索引列集必须与 `models.__table_args__` **完全一致**（含 `fact_value`），否则迁移后旧库多值并存失效；
- 开关 `LLM_MEMORY_SUPERSEDE_ENABLE`；「以前说过」v1 仅保留在库、不注入（WebUI 展示留 P3）。

### 衰减设计结论（oracle 评审，**已实施**，2026-10-15）
- 字段：`llm_user_fact` 加 `weight`（默认 1.0）/ `access_count`（默认 0）/ `last_used_at`（可空），均自动补列；
- 公式：`score = weight × 0.5^(age_days/half_life)`，`manual` 不衰减、`global` 半衰期 60 天、`group/private` 30 天；
- 强化：被注入即 `weight=min(1.0, weight+0.2)`、`last_used_at=now`、`access_count+1`（节流 6h）；
- 时点：注入时实时算（不回写），「只降权不删」（`weight` 恒 ≥0.05、行不 DELETE）；
- 实现：`_fact_score` 升级为衰减打分；`_memory_fact_sections` 作为「渲染与复述加固」的唯一选数口径。

### 已废弃（不再需要）
- [x] ~~修复「群上下文压缩死代码」~~：群作用域改走 `chat_message` 流水，不再依赖群 `llm_session`；群摘要注入分支保留但当前恒空（无群会话），不影响功能

## 5. 明确「不要照搬」的坑

1. **别学 private_companion 的复杂度**：`store.py` 626KB/13757 行、`service.py` 647KB/13578 行，
   God Object + 71KB 配置 schema、几十个百分比阈值暴露给用户 → 维护地狱。保持现小文件结构。
2. **别用 metadata JSON 大水塘**：两者都把大量字段塞 JSON，导致 SQL 无法过滤排序、候选必须全量拉进
   Python（`materialize_limit=2000` 是召回天花板）。应把 `importance/status/session_id/validity`
   等**建独立列 + 索引**。
3. **别搞进程内精确向量扫描**：private_companion 用 64MB numpy 快照全量算余弦（O(N·d)），无 ANN。
   这正是 PaperBot 上 pgvector 的优势位。
4. **别默认关闭核心能力**：两个项目 Embedding/Rerank/整合都默认 false，开箱靠关键词兜底。
5. **图谱路存疑**：livingmemory 的"关系图谱"是固定模板边（`topic describes fact`）而非真实实体关系抽取，
   增益未验证，别为它付复杂度成本。

## 6. 一句话结论

最值得抄的是**工程范式**而非**功能规模**：private_companion 的
**代码闸门 + 分槽保底 + 用法权限 + 软衰减**，livingmemory 的
**原子化 TTL + 可恢复写日志 + 时间归一化**，这六点**均已落地**（见 §4 落地清单）；
而**向量检索**是 PaperBot 相对两者唯一能反超的方向（可上 pgvector，它俩都没做好），仍待立项。

## 遗留 / 后续

- **主体已落地（2026-10-15，共 9 个提交）**：P0/P1/P2 全部完成 + P3 归档，详见 §4 落地清单。
- **待办只剩 3 项**：WebUI 档位下拉/改口历史展示（P3，小）、记忆整合/压缩（P3）、向量检索（P3，大工程）。
- **建议顺序**：先做 WebUI 展示两项（体验、低风险）→ 再评估「记忆整合」→ 向量检索需先定存储方案（SQLite 现有栈 vs 引入 pgvector）单独立项。
- **先验前再动大工程**：三项待办均为增强、非缺陷；建议先在真机验证已落地部分（见 [`04-backlog.md`](04-backlog.md)）再决定是否投入大工程。
