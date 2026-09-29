"""用户权限系统（四级）。

权限等级：
    0 黑名单   —— 不回应任何消息
    1 普通用户 —— 默认权限（普通群友）
    2 群管理员 —— 群角色自动识别，或由所有者手动授权（仅对应群生效）
    3 所有者   —— SUPERUSERS（.env 配置）

解析优先级（就近优先）：
    1. 若为所有者（SUPERUSERS）            → 3
    2. 查「本群」手动授权记录               → 命中则用该等级
    3. 查「全局」手动授权记录（如黑名单）    → 命中则用该等级
    4. 按 QQ 群角色自动判断（群主/管理=2）   → 2
    5. 否则                                 → 1

> 说明：手动记录优先于自动角色判定，因此可以把某群管理员手动拉黑（0 级）。
"""

from __future__ import annotations

from enum import IntEnum
from typing import List, Optional, Set

import nonebot
from sqlalchemy import select

from src.core.database import get_session
from src.core.models import (
    LEVEL_ADMIN,
    LEVEL_BLACKLIST,
    LEVEL_NAMES,
    LEVEL_OWNER,
    LEVEL_USER,
    SCOPE_GLOBAL,
    SCOPE_GROUP,
    UserPermission,
)


class PermLevel(IntEnum):
    """权限等级枚举。"""

    BLACKLIST = LEVEL_BLACKLIST
    USER = LEVEL_USER
    ADMIN = LEVEL_ADMIN
    OWNER = LEVEL_OWNER

    @property
    def label(self) -> str:
        return LEVEL_NAMES.get(int(self), str(int(self)))


def owner_ids() -> Set[str]:
    """从 NoneBot 配置读取所有者（SUPERUSERS）QQ 集合。"""
    try:
        supers = nonebot.get_driver().config.superusers
    except Exception:
        return set()
    return {str(x) for x in (supers or set())}


def is_owner(user_id: Optional[str]) -> bool:
    return user_id is not None and str(user_id) in owner_ids()


def role_level(role: Optional[str]) -> Optional[PermLevel]:
    """由 QQ 群角色推出等级；非群消息返回 None。"""
    if role in ("owner", "admin"):
        return PermLevel.ADMIN
    if role == "member":
        return PermLevel.USER
    return None


def event_role(event) -> Optional[str]:
    """从事件里安全取出 sender.role（仅群消息有）。"""
    sender = getattr(event, "sender", None)
    return getattr(sender, "role", None)


def _coerce_level(value) -> PermLevel:
    try:
        return PermLevel(int(value))
    except Exception:
        return PermLevel.USER


# --------------------------------------------------------------------------- #
# 授予记录 CRUD
# --------------------------------------------------------------------------- #
async def _get_grant(session, user_id: str, scope_type: str, scope_id: str):
    stmt = select(UserPermission).where(
        UserPermission.user_id == str(user_id),
        UserPermission.scope_type == scope_type,
        UserPermission.scope_id == scope_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_level(
    user_id: str,
    level: int,
    *,
    group_id: Optional[str] = None,
    updated_by: str = "",
) -> UserPermission:
    """写入/更新一条手动授权记录（group_id 为空则写全局作用域）。"""
    scope_type = SCOPE_GROUP if group_id is not None else SCOPE_GLOBAL
    scope_id = str(group_id) if group_id is not None else ""
    async with get_session() as session:
        grant = await _get_grant(session, str(user_id), scope_type, scope_id)
        if grant is None:
            grant = UserPermission(
                user_id=str(user_id), scope_type=scope_type, scope_id=scope_id
            )
            session.add(grant)
        grant.level = int(level)
        grant.updated_by = updated_by
        return grant


async def clear_level(user_id: str, *, group_id: Optional[str] = None) -> bool:
    """删除一条手动授权记录，返回是否真的删掉了。"""
    scope_type = SCOPE_GROUP if group_id is not None else SCOPE_GLOBAL
    scope_id = str(group_id) if group_id is not None else ""
    async with get_session() as session:
        grant = await _get_grant(session, str(user_id), scope_type, scope_id)
        if grant is None:
            return False
        await session.delete(grant)
        return True


async def list_grants() -> List[UserPermission]:
    async with get_session() as session:
        stmt = select(UserPermission).order_by(
            UserPermission.user_id, UserPermission.scope_type, UserPermission.scope_id
        )
        return list((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------- #
# 核心：解析某用户此刻的权限等级
# --------------------------------------------------------------------------- #
async def resolve_level(
    user_id: Optional[str],
    group_id: Optional[str] = None,
    role: Optional[str] = None,
) -> PermLevel:
    """解析用户在指定上下文下的权限等级。"""
    uid = str(user_id) if user_id is not None else ""

    # 1. 所有者最高优先
    if uid and uid in owner_ids():
        return PermLevel.OWNER

    # 2/3. 手动授权记录（群 > 全局）
    if uid:
        async with get_session() as session:
            if group_id is not None:
                grant = await _get_grant(session, uid, SCOPE_GROUP, str(group_id))
                if grant is not None:
                    return _coerce_level(grant.level)
            grant = await _get_grant(session, uid, SCOPE_GLOBAL, "")
            if grant is not None:
                return _coerce_level(grant.level)

    # 4. 群角色自动判定
    auto = role_level(role)
    if auto is not None:
        return auto

    # 5. 默认普通用户
    return PermLevel.USER
