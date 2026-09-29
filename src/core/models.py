"""共享数据模型层。

约定：**所有插件**的数据表都必须继承这里的 `Base`，这样：
1. 启动时 `Base.metadata.create_all` 会一次性建好全部表；
2. 插件之间可以用 ORM 关系 / 外键互相引用，实现数据联动。

本文件只放「框架级」的公共表（插件注册表 + 插件门控规则），
业务插件的表请放到各自插件目录里自行定义（同样继承 Base 即可）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---- 作用域常量 ----
SCOPE_GLOBAL = "global"  # 全局
SCOPE_GROUP = "group"  # 分群
SCOPE_MEMBER = "member"  # 分成员（黑白名单）

SCOPE_TYPES = (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_MEMBER)


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
    default_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


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
