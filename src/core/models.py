"""共享数据模型层。

约定：**所有插件**的数据表都必须继承这里的 `Base`，这样：
1. 启动时 `Base.metadata.create_all` 会一次性建好全部表；
2. 插件之间可以用 ORM 关系 / 外键互相引用，实现数据联动。

本文件只放「框架级」的公共表（插件注册表 + 插件门控规则），
业务插件的表请放到各自插件目录里自行定义（同样继承 Base 即可）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---- 作用域常量 ----
SCOPE_GLOBAL = "global"  # 全局
SCOPE_GROUP = "group"  # 分群
SCOPE_MEMBER = "member"  # 分成员（黑白名单）

SCOPE_TYPES = (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_MEMBER)

# ---- 用户权限等级 ----
# 0 黑名单：不回应任何消息
# 1 默认权限用户（普通群友）
# 2 群管理员（群角色自动识别 / 所有者手动授权）
# 3 所有者（SUPERUSERS）
LEVEL_BLACKLIST = 0
LEVEL_USER = 1
LEVEL_ADMIN = 2
LEVEL_OWNER = 3

LEVEL_NAMES = {
    LEVEL_BLACKLIST: "黑名单",
    LEVEL_USER: "普通用户",
    LEVEL_ADMIN: "群管理员",
    LEVEL_OWNER: "所有者",
}


def level_name(level: int) -> str:
    """权限等级 → 中文名（未知等级回落成数字字符串）。"""
    return LEVEL_NAMES.get(int(level), str(level))


class Base(DeclarativeBase):
    """所有 ORM 模型的公共基类。"""


class PluginInfo(Base):
    """插件元信息镜像表。

    代码里的 `Feature` 才是唯一事实来源，这张表只是启动时同步过来的副本，
    方便 WebUI 列出插件（包括当前被禁用的），也方便做审计。
    """

    __tablename__ = "plugin_info"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(String(512), default="")
    version: Mapped[str] = mapped_column(String(32), default="0.1.0")
    author: Mapped[str] = mapped_column(String(64), default="")
    # repo / requires 是可选的补充元信息：声明为 nullable，
    # 这样老库能用 `ALTER TABLE ADD COLUMN` 平滑补列（见 database._add_missing_columns）。
    repo: Mapped[Optional[str]] = mapped_column(String(256), default="")
    requires: Mapped[Optional[str]] = mapped_column(String(64), default="")
    default_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# 插件级配置项**不再存库**：已迁到 `.config/<插件名>/config.json`
# （见 `src/core/pluginconf.py`）。旧的 `plugin_config` 表在启动时会被一次性
# 搬进 JSON 文件后改名归档，这里刻意不再声明 ORM 类。


class PluginRule(Base):
    """插件门控规则（三级作用域）。

    同 (plugin_name, scope_type, scope_id) 唯一。
    - global 作用域：scope_id 固定为 ""
    - group  作用域：scope_id 为群号
    - member 作用域：scope_id 为 QQ 号

    `enabled=True` 表示放行（白名单），`False` 表示拦截（黑名单）。
    """

    __tablename__ = "plugin_rule"
    __table_args__ = (
        UniqueConstraint("plugin_name", "scope_type", "scope_id", name="uq_plugin_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plugin_name: Mapped[str] = mapped_column(String(64), index=True)
    scope_type: Mapped[str] = mapped_column(String(16), index=True)
    scope_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<PluginRule {self.plugin_name} {self.scope_type}:{self.scope_id} "
            f"enabled={self.enabled}>"
        )


class CommandRule(Base):
    """命令权限覆盖表。

    - **默认权限**由插件在代码里声明（`Feature.command(..., level=1)`）；
    - **实际权限**由本表覆盖维护，可在 WebUI 命令管理页随时改；
    - 删除记录即「恢复默认」。

    同 (plugin_name, command_name) 唯一；`command_name` 用主命令名（不含前缀）。
    """

    __tablename__ = "command_rule"
    __table_args__ = (
        UniqueConstraint("plugin_name", "command_name", name="uq_command_rule"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plugin_name: Mapped[str] = mapped_column(String(64), index=True)
    command_name: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[int] = mapped_column(Integer, default=LEVEL_USER)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<CommandRule {self.plugin_name}.{self.command_name} level={self.level}>"
        )


class UserPermission(Base):
    """用户权限授予表（所有者手动授权 / 黑名单）。

    同 (user_id, scope_type, scope_id) 唯一。
    - group  作用域：scope_id=群号，level=2 表示「该群的管理员」
    - global 作用域：scope_id=""，level=0 表示「全局黑名单」

    未在此表命中的用户，按 QQ 群角色自动判定（群主/管理员=2，普通成员=1）。
    """

    __tablename__ = "user_permission"
    __table_args__ = (
        UniqueConstraint("user_id", "scope_type", "scope_id", name="uq_user_permission"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    scope_type: Mapped[str] = mapped_column(String(16), index=True)
    scope_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    level: Mapped[int] = mapped_column(Integer, default=LEVEL_USER)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<UserPermission {self.user_id} {self.scope_type}:{self.scope_id} "
            f"level={self.level}>"
        )

# ============================ LLM（统一大模型服务） ============================
# 说明：以下表由框架 src/core/llm.py 维护，插件**只调用接口**、不必直接读写；
# WebUI「LLM」页可管理服务商 / 人设 / 用户印象与关键信息 / 会话。

# 会话作用域（对话上下文按「个人 / 群聊」两种粒度隔离）
LLM_SCOPE_USER = "user"
LLM_SCOPE_GROUP = "group"
LLM_SCOPES = (LLM_SCOPE_USER, LLM_SCOPE_GROUP)

# 人设来源（内置示例 / 文件导入 / 面板手写）
LLM_PRESET_BUILTIN = "builtin"
LLM_PRESET_IMPORT = "import"
LLM_PRESET_MANUAL = "manual"

# 关键信息来源（LLM 自动提炼 / 人工维护）
LLM_FACT_AUTO = "auto"
LLM_FACT_MANUAL = "manual"

# LLM 记忆来源作用域（决定该条事实在哪些窗口可见）
FACT_SCOPE_GLOBAL = "global"    # 通用：任何窗口可见（人工手写默认）
FACT_SCOPE_GROUP = "group"      # 仅指定群可见（scope_id = 群号）
FACT_SCOPE_PRIVATE = "private"  # 仅私聊可见（scope_id 恒为 ""）
FACT_SCOPES = (FACT_SCOPE_GLOBAL, FACT_SCOPE_GROUP, FACT_SCOPE_PRIVATE)

# 消息类型（聊天流水表）
CHAT_TYPE_GROUP = "group"
CHAT_TYPE_PRIVATE = "private"
CHAT_TYPES = (CHAT_TYPE_GROUP, CHAT_TYPE_PRIVATE)


class ChatMessage(Base):
    """聊天记录（原始流水层，独立于 LLM 会话上下文）。

    由 `src/core/chatlog.py` 的全局事件钩子写入，客观记录「谁、在哪个群/私聊、
    什么时候、说了什么」。记忆提炼 / 群聊上下文注入 / 未来插件与 AI 工具调用
    都从这里取素材。只存**纯文本 + 元信息**（原始 CQ 消息不入库）。
    """

    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16), default="onebot11")
    message_type: Mapped[str] = mapped_column(String(16), default=CHAT_TYPE_GROUP, index=True)
    group_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    nickname: Mapped[str] = mapped_column(String(128), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    # 协议端消息 id（用于去重）
    message_id: Mapped[str] = mapped_column(String(64), default="")
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        where = self.group_id if self.message_type == CHAT_TYPE_GROUP else "private"
        who = "bot" if self.is_bot else self.user_id
        return f"<ChatMessage {where} {who}: {self.content[:20]!r}>"


class LlmProvider(Base):
    """LLM 服务商配置（OpenAI 兼容，可存多套 + 指定默认）。

    `extra` 是 JSON 文本，会原样透传给请求体（不必改代码就能传供应商特有参数）。
    """

    __tablename__ = "llm_provider"
    __table_args__ = (UniqueConstraint("name", name="uq_llm_provider_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    base_url: Mapped[str] = mapped_column(String(256), default="")
    api_key: Mapped[str] = mapped_column(String(256), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    # 多模型（优先 + 备用，最多 3 个）：JSON 数组，如 ["gpt-4o", "gpt-4o-mini"]
    models: Mapped[Optional[str]] = mapped_column(String(512), default="")
    # 模型优先级：越小越优先；0 表示无优先级（按 models 数组顺序）
    priority: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    temperature: Mapped[float] = mapped_column(Float, default=0.8)
    max_tokens: Mapped[int] = mapped_column(Integer, default=0)
    timeout: Mapped[float] = mapped_column(Float, default=60.0)
    extra: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<LlmProvider {self.name} model={self.model} default={self.is_default}>"


class LlmPreset(Base):
    """人设（角色卡）：`system_prompt` + 参数覆盖。

    同名唯一，`source` 记录来源（内置 / 导入 / 手写），手改过的人设不会被导入覆盖。
    """

    __tablename__ = "llm_preset"
    __table_args__ = (UniqueConstraint("name", name="uq_llm_preset_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(String(512), default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default=LLM_PRESET_MANUAL)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<LlmPreset {self.name} source={self.source}>"


class LlmSession(Base):
    """对话会话（上下文容器）。

    唯一键 `(scope_type, scope_id, plugin_name)`：
    - `scope_type=user`  → scope_id 为 QQ 号（个人上下文）
    - `scope_type=group` → scope_id 为群号（群聊上下文）
    - `plugin_name` 用于「同一个人的上下文按插件隔离」，留空 = 各插件共享。
    """

    __tablename__ = "llm_session"
    __table_args__ = (
        UniqueConstraint(
            "scope_type", "scope_id", "plugin_name", name="uq_llm_session"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(16), default=LLM_SCOPE_USER, index=True)
    scope_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    plugin_name: Mapped[str] = mapped_column(String(64), default="", index=True)
    preset_name: Mapped[str] = mapped_column(String(64), default="")
    provider_name: Mapped[str] = mapped_column(String(64), default="")
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    user_message_count: Mapped[int] = mapped_column(Integer, default=0)
    # 上下文压缩后的历史摘要（超出保留窗口的旧消息被压成这里的一段文本）
    summary: Mapped[Optional[str]] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<LlmSession {self.scope_type}:{self.scope_id} plugin={self.plugin_name!r} "
            f"msgs={self.message_count}>"
        )


class LlmMessage(Base):
    """会话消息（只存对话原文，role ∈ system/user/assistant/tool）。"""

    __tablename__ = "llm_message"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(16), default="user")
    content: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<LlmMessage s{self.session_id} {self.role} {self.content[:20]!r}>"


class LlmUserProfile(Base):
    """用户印象（每个用户一条，由 LLM 从对话中提炼，可人工改写）。"""

    __tablename__ = "llm_user_profile"
    __table_args__ = (UniqueConstraint("user_id", name="uq_llm_profile_user"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    impression: Mapped[str] = mapped_column(Text, default="")
    # 提炼时该用户累计的（个人上下文）消息数，用于判断「有没有新料」
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    # 个人记忆水位线：只总结 id 大于该值的消息（跨私聊/群聊，全局递增）
    last_msg_id: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    impression_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<LlmUserProfile {self.user_id} {self.impression[:20]!r}>"


class LlmGroupProfile(Base):
    """群记忆（每个群一条）：由 LLM 从群聊增量里提炼的共同话题/群梗/正在进行的事。"""

    __tablename__ = "llm_group_profile"
    __table_args__ = (UniqueConstraint("group_id", name="uq_llm_group_profile"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(64), index=True)
    memory: Mapped[str] = mapped_column(Text, default="")
    # 群记忆水位线：只总结 id 大于该值的群消息
    last_msg_id: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<LlmGroupProfile {self.group_id} {self.memory[:20]!r}>"


class LlmUserFact(Base):
    """用户关键信息（可枚举的事实条目，例：昵称 / 喜好 / 忌口）。

    `(user_id, fact_key, scope_type, scope_id)` 唯一；`source=auto` 表示由 LLM
    自动提炼，`source=manual` 表示人工手写（自动提炼不会覆盖同 scope 的人工条目）。
    `scope_type/scope_id` 决定该条事实在哪些窗口可见（见 FACT_SCOPE_* 常量）。

    唯一键用**具名唯一索引**而非 `UniqueConstraint`：SQLite 下命名 UniqueConstraint
    会退化成 `sqlite_autoindex_*`（名字不可反射），导致迁移无法按名探测；用同名
    unique Index 则名字可见，唯一性语义完全等价。
    """

    __tablename__ = "llm_user_fact"
    __table_args__ = (
        Index(
            "uq_llm_fact_scope",
            "user_id",
            "fact_key",
            "scope_type",
            "scope_id",
            unique=True,
        ),
        Index("ix_llm_user_fact_user_scope", "user_id", "scope_type", "scope_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    fact_key: Mapped[str] = mapped_column(String(64))
    fact_value: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default=LLM_FACT_AUTO)
    scope_type: Mapped[str] = mapped_column(
        String(16), default=FACT_SCOPE_PRIVATE, server_default=text("'private'")
    )
    scope_id: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<LlmUserFact {self.user_id} {self.fact_key}={self.fact_value[:20]!r} "
            f"scope={self.scope_type}:{self.scope_id}>"
        )
