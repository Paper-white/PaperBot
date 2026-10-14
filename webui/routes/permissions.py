"""权限（黑白名单）接口：`/api/permissions`。

数据源是 `user_permission` 表：
- `scope_type=global, level=0` → 全局黑名单
- `scope_type=group,  level=2` → 某群的手动管理员
未命中记录的用户按 QQ 群角色自动判定（群主/管理员 = 2 级）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from src.core import permission
from src.core import llm
from src.core.models import (
    LEVEL_ADMIN,
    LEVEL_BLACKLIST,
    LEVEL_OWNER,
    LEVEL_USER,
    SCOPE_GLOBAL,
    SCOPE_GROUP,
    level_name as _level_name,
)
from webui.deps import require_token

router = APIRouter(dependencies=[Depends(require_token)], tags=["permissions"])

VALID_LEVELS = (LEVEL_BLACKLIST, LEVEL_USER, LEVEL_ADMIN, LEVEL_OWNER)


def _serialize(grant) -> dict[str, Any]:
    return {
        "id": grant.id,
        "user_id": grant.user_id,
        "scope_type": grant.scope_type,
        "scope_id": grant.scope_id,
        "level": grant.level,
        "level_name": _level_name(grant.level),
        "updated_by": grant.updated_by,
        "updated_at": llm.fmt_dt(grant.updated_at) if grant.updated_at else None,
    }


@router.get("/permissions")
async def list_permissions() -> dict:
    """全部手动授权记录 + 分类视图（全局黑名单 / 分群管理员 / 其它）。"""
    grants = [_serialize(g) for g in await permission.list_grants()]
    blacklist = [
        g
        for g in grants
        if g["scope_type"] == SCOPE_GLOBAL and int(g["level"]) == LEVEL_BLACKLIST
    ]
    group_admins = [
        g for g in grants if g["scope_type"] == SCOPE_GROUP and int(g["level"]) == LEVEL_ADMIN
    ]
    others = [
        g for g in grants if g not in blacklist and g not in group_admins
    ]
    return {
        "count": len(grants),
        "items": grants,
        "blacklist": blacklist,
        "group_admins": group_admins,
        "others": others,
        "owners": sorted(permission.owner_ids()),
        "levels": [
            {"value": value, "name": _level_name(value)} for value in VALID_LEVELS
        ],
    }


@router.put("/permissions")
async def set_permission(payload: dict = Body(...)) -> dict:
    """写入/更新一条授权：{user_id, level, group_id?}（group_id 省略 = 全局）。"""
    user_id = str(payload.get("user_id", "") or "").strip()
    if not user_id.isdigit():
        raise HTTPException(status_code=400, detail="user_id 必须是 QQ 号")

    try:
        level = int(payload.get("level"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="level 必须是整数") from None
    if level not in VALID_LEVELS:
        raise HTTPException(
            status_code=400, detail=f"level 必须是 {list(VALID_LEVELS)} 之一"
        )

    raw_group = payload.get("group_id")
    group_id = None if raw_group in (None, "", "global") else str(raw_group).strip()
    if group_id is not None and not group_id.isdigit():
        raise HTTPException(status_code=400, detail="group_id 必须是群号或留空")

    if level == LEVEL_OWNER:
        raise HTTPException(
            status_code=400, detail="所有者只能通过 .env 的 SUPERUSERS 配置"
        )

    grant = await permission.set_level(
        user_id,
        level,
        group_id=group_id,
        updated_by=str(payload.get("updated_by", "webui")),
    )
    return {
        "ok": True,
        "item": _serialize(grant),
        "scope_label": "全局" if group_id is None else f"群[{group_id}]",
    }


@router.delete("/permissions")
async def delete_permission(
    user_id: str,
    group_id: str = "",
) -> dict:
    """删除一条授权（group_id 留空 = 全局）。"""
    if not user_id.isdigit():
        raise HTTPException(status_code=400, detail="user_id 必须是 QQ 号")
    scope = group_id.strip() or None
    removed = await permission.clear_level(user_id, group_id=scope)
    if not removed:
        raise HTTPException(status_code=404, detail="该授权记录不存在")
    return {"ok": True, "user_id": user_id, "group_id": scope or "global"}
