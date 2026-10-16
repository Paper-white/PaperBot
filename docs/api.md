# WebUI 与 REST API

> 📍 本文件是 **WebUI 页面清单**与 **REST 接口一览**的唯一信息源，与 `webui/api.py`
> （菜单在 `webui/static/assets/app.js` 的 `MENU`）保持一致；其它文档如需页面/接口请引用此处。

浏览器打开 **`http://127.0.0.1:6188/`** 即是管理后台（内嵌 FastAPI + 零构建单页前端）。

- 左侧九个菜单（按菜单顺序）：**状态 / 系统配置 / 黑白名单 / 插件管理 / LLM 与记忆 / 数据库管理 / 命令管理 / 日志 / 设置**
- 页面路由：除 `GET /` 外，也可直接访问 `/<页面名>`（如 `/status`、`/llm`、`/logs`、`/settings`）
- API 前缀默认 `/api`（`WEBUI_PREFIX` 可改）；接口一览如下

> 💡 前端交互：**系统配置 / 日志配置** 的分组是「摘要卡」——点击弹浮窗编辑；
> **list 字段**（如 `SUPERUSERS`）用「标签（chips）」编辑（输入→添加，可删）；
> **群号**输入框带候选下拉（`datalist`，可选可填）；**长下拉**（如模型）带筛选框；
> **命令管理**是一张可筛选/排序/搜索的大表；**状态页**主机指标每 5 秒自动刷新（运行时长每秒本地自增）。

## 鉴权

若 `.config/main/.env` 设了 `WEBUI_TOKEN`，除 `GET /api/health` 与页面本身外，
所有请求都要带 `Authorization: Bearer <token>`（定长比较）。
**留空 = 免鉴权**；绑定非回环地址且留空时启动会打 WARNING（管理接口裸奔）。

## 接口清单

```
GET    /api/health                                       健康检查（不鉴权）
GET    /api/status                                       运行状态 / 在线时长 / 连接 / 统计 / 守卫 / 存储

# ---- 配置 ----
GET    /api/config                                       配置分组（适配器 + section，密钥脱敏；database/render 已隐藏）
PUT    /api/config                                       写回 .config/main/.env（留空或占位符=不改，自动 .env.bak）
POST   /api/config/reload                                重读 .env：可热更项立即生效，返回 {changed,hot,restart,message}
POST   /api/reload                                       一键重载：重读 .env + 重载所有插件
                                                         → {config:{...}, plugins:{reloaded,failed,skipped}, message}

# ---- 插件 ----
GET    /api/plugins                                      插件总览（含 global_enabled）
GET    /api/plugins/{name}                               插件详情 + 规则 + 命令 + 配置 schema + 钩子 + repo/requires
PUT    /api/plugins/{name}/rule                          设置规则 {scope_type, scope_id, enabled}
DELETE /api/plugins/{name}/rule?scope_type=&scope_id=    删除规则
GET    /api/plugins/{name}/config                        读插件配置项（schema + 当前值 + 文件路径 + secret 脱敏）
PUT    /api/plugins/{name}/config                        写插件配置项 {values:{...}}（schema 校验，即时生效）
DELETE /api/plugins/{name}/config?key=                   清配置项（key 省略 = 全清，回落默认值）
POST   /api/plugins/{name}/reload                        热重载插件（受保护插件 409，失败 400）

# ---- LLM ----
GET    /api/llm/status                                   LLM 状态（可用性 / 计数 / 作用域标签 / 密钥掩码占位）
GET    /api/llm/providers                                服务商列表（密钥掩码，含 .env 兜底的 env 行）
PUT    /api/llm/providers/{name}                         新增 / 更新服务商（密钥传掩码或省略 = 不改）
DELETE /api/llm/providers/{name}                         删除服务商（env → 400）
POST   /api/llm/providers/{name}/test                    试连一次，返回文本 / 模型 / 耗时 / 用量
POST   /api/llm/models/fetch                             拉取中转站可用模型 {base_url?, api_key?}（留空 = 用默认服务商）
POST   /api/llm/providers/{name}/models                  更新某服务商的多模型优先级 {models:[...], priority?}（最多 3 个）
GET    /api/llm/presets[?with_prompt=]                   人设列表（可带正文）
GET    /api/llm/presets/{name}                           人设详情（含导出文本）
PUT    /api/llm/presets/{name}                           新建 / 保存人设（来源标记为「人工」）
DELETE /api/llm/presets/{name}                           删除人设
POST   /api/llm/presets/import                           导入人设 {text, filename?, force?}
GET    /api/llm/presets/{name}/export                    导出人设为 .md
GET    /api/llm/sessions[?limit=]                        会话列表（作用域 / 谁 / 插件 / 消息数；带 scope_name 备注名）
GET    /api/llm/sessions/{id}/messages[?limit=]          某会话的消息
PUT    /api/llm/sessions/{id}                            给会话换人设 / 换服务商
DELETE /api/llm/sessions/{id}                            删除单条会话（连带消息）
DELETE /api/llm/sessions?scope_type=&scope_id=&plugin_name=  按作用域批量清空会话
GET    /api/llm/users[?limit=]                           有长期记忆的用户列表（带 nickname 备注名）
GET    /api/llm/users/{uid}                              用户印象 + 关键信息
PUT    /api/llm/users/{uid}/impression                   手写印象
PUT    /api/llm/users/{uid}/facts                        手加 / 更新关键信息（人工来源）
DELETE /api/llm/users/{uid}/facts?key=                   删单条（key 省略 = 全删）
DELETE /api/llm/users/{uid}                              清空该用户长期记忆
GET    /api/llm/groups[?limit=]                          有群记忆的群列表（`llm_group_profile`；带 group_name 备注名）
PUT    /api/llm/groups/{gid}/memory                      人工改写群记忆
POST   /api/llm/chat                                     无状态试聊 {prompt, preset?, provider?}

# ---- 权限 / 群 ----
GET    /api/permissions                                  权限记录（黑名单 / 分群管理员 / 其它 / owners / levels；带 user_name/scope_name 备注名）
PUT    /api/permissions                                  写入授权 {user_id, level, group_id?}（level 3 拒绝）
DELETE /api/permissions?user_id=&group_id=               删除授权
GET    /api/groups                                       群列表（含每群规则与授权；离线回退 DB 推导）
GET    /api/groups/{group_id}/members                    群成员 + 逐人权限等级（需 Bot 在线）

# ---- 命令 ----
GET    /api/commands                                     按插件分组的命令清单（含默认 / 实际权限等级）
PUT    /api/commands/{plugin}/{command}                  改命令权限 {level:1|2|3}（即时生效）
DELETE /api/commands/{plugin}/{command}                  恢复命令默认权限

# ---- 数据库 / 设置 ----
GET    /api/database                                     数据库与表清单（只读）
GET    /api/database/tables/{name}?limit=&offset=        分页读取表数据（只读，表名白名单）
GET    /api/settings                                     版本信息 / 运行时 / 日志 / 安全设置 + 可改配置组
PUT    /api/settings/token                               改 WEBUI_TOKEN（写 .config/main/.env + 内存即时生效）
GET    /api/settings/logs/{name}?lines=                  读日志尾部（1~500 行）
DELETE /api/settings/logs                                清理全部日志（删历史 + 清空当前）
DELETE /api/settings/logs/{name}                         删除单个日志（当前运行中的 → 409）

# ---- 日志（「日志」页实时 tail） ----
GET    /api/logs/tail?lines=                             读「本次运行」日志文件尾部（供实时面板轮询）

# ---- 版本控制（一键拉取云端版本） ----
GET    /api/version                                      仓库状态（commit / 分支 / 脏 / 落后领先 / 守护服务）
POST   /api/version/fetch                                拉取 origin/main（不改工作区），返回提交历史
POST   /api/version/update                               {commit?} 对齐工作区（空 = 最新；脏工作区 409）
POST   /api/version/restart                              重启 Bot（spawn 自拉起 / exit 交守护进程）
```

## 行为约定

- **生效时机**：`.config/main/.env` 的**可热更项**（`LLM_*` / `RENDER_*` / 白名单 / `SUPERUSERS` /
  `CHATLOG_ENABLE` 等）可用 `POST /api/config/reload`（系统配置页「热重载配置」按钮 / QQ `%reload`）
  **即时生效**；**需重启项**（`HOST`/`PORT`/`DRIVER`/`COMMAND_START`/`ONEBOT_*`/`WEBUI_ENABLE`/
  `WEBUI_PREFIX`/`DATABASE_URL`/`LOG_*`）必须重启（reload 会列出）；插件开关 / 规则 / 命令权限 /
  插件配置 / LLM 服务商与人设 **即时生效**
- **写回安全**：`.config/main/.env` 按行替换、保留注释与顺序，并先备份同目录 `.env.bak`；
  与现有值等价则**完全不写盘**（连备份都不产生）
- **错误码**：受保护插件改规则 / 热重载 → **409**；未知插件 → 404；非法参数 / 非法等级 → **400**；
  `limit` 超上限 → **422**
- ⚠️ **没有 `GET /api/llm/providers/{name}`** 这种单条读取接口（只有列表 + PUT/DELETE/test）：
  请求它会返回 **405 Method Not Allowed** 而不是 404 —— 写测试时别拿 404 当「不存在」的判据
- 密钥一律不回显真值：服务商密钥用掩码（提交掩码 = 不改动），敏感字段只显示长度
- **版本控制**（`/api/version*`，见 [`plans/05-version-control.md`](plans/05-version-control.md)）：
  工作区脏 → **409**（拒绝更新）；commit 非法 / 不存在 / 未 `fetch` → **400**；
  `VCS_ENABLE=false` 时 `fetch`/`update`/`restart` → **400**；git 超时 → **504**；
  **只拉不推**，只追 `origin/main`，绝不 `git clean`（`.config/` 等忽略目录不会被动）
