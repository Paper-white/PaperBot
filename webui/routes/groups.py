"""群与成员接口：`/api/groups`、`/api/groups/{id}/members`。

在线时数据取自协议端（OneBot `get_group_list` / `get_group_member_list`），
离线时回退到数据库里出现过的群号（规则 / 授权记录推导）。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.core import permission
from src.core.database import get_session
from src.core.models import (
    LEVEL_NAMES,
    SCOPE_GLOBAL,
    SCOPE_GROUP,
    PluginRule,
    UserPermission,
)
from webui.deps import call_api, online_bots, require_token

router = APIRouter(dependencies=[Depends(require_token)], tags=["groups"])


def _level_name(level: int) -> str:
    return LEVEL_NAMES.get(int(level), str(level))


async def _db_group_index() -> dict[str, dict[str, list]]:
    """一次查询取出「按群分组」的规则与授权。"""
    index: dict[str, dict[str, list]] = {}
    async with get_session(readonly=True) as session:
        rules = (
            await session.execute(
                select(PluginRule).where(PluginRule.scope_type == SCOPE_GROUP)
            )
        ).scalars().all()
        grants = (
            await session.execute(
                select(UserPermission).where(UserPermission.scope_type == SCOPE_GROUP)
            )
        ).scalars().all()

    for rule in rules:
        index.setdefault(rule.scope_id, {"rules": [], "grants": []})["rules"].append(
            {"plugin": rule.plugin_name, "enabled": bool(rule.enabled)}
        )
    for grant in grants:
        index.setdefault(grant.scope_id, {"rules": [], "grants": []})["grants"].append(
            {
                "user_id": grant.user_id,
                "level": grant.level,
                "level_name": _level_name(grant.level),
                "updated_by": grant.updated_by,
            }
        )
    return index


async def _online_group_list() -> Optional[list[dict]]:
    """在线时取协议端群列表；离线返回 None。"""
    if not online_bots():
        return None
    data = await call_api("get_group_list")
    items = []
    for raw in data or []:
        items.append(
            {
                "group_id": str(raw.get("group_id", "")),
                "group_name": str(raw.get("group_name", "")),
                "member_count": raw.get("member_count"),
                "max_member_count": raw.get("max_member_count"),
            }
        )
    return items


@router.get("/groups")
async def list_groups() -> dict:
    """群列表（含该群的插件规则与手动授权）。"""
    db_index = await _db_group_index()
    online_items = await _online_group_list()

    merged: dict[str, dict[str, Any]] = {}
    if online_items is not None:
        for item in online_items:
            gid = item["group_id"]
            merged[gid] = {**item, "source": "onebot", "online": True}
    for gid in db_index:
        merged.setdefault(
            gid,
            {
                "group_id": gid,
                "group_name": "",
                "member_count": None,
                "max_member_count": None,
                "source": "db",
                "online": False,
            },
        )

    items = []
    for gid, base in merged.items():
        extra = db_index.get(gid, {"rules": [], "grants": []})
        items.append(
            {
                **base,
                "rules": sorted(extra["rules"], key=lambda x: x["plugin"]),
                "grants": sorted(extra["grants"], key=lambda x: x["user_id"]),
            }
        )
    items.sort(key=lambda x: x["group_id"])

    return {
        "online": online_items is not None,
        "count": len(items),
        "items": items,
    }


async def _member_levels(group_id: str, members: list[dict]) -> dict[str, Any]:
    """给一批群成员批量解析权限等级（复用同一个只读 session）。"""
    result: dict[str, Any] = {}
    async with get_session(readonly=True) as session:
        for member in members:
            uid = str(member.get("user_id", ""))
            result[uid] = await permission.resolve_level(
                uid, group_id=group_id, role=member.get("role"), session=session
            )
    return result


@router.get("/groups/{group_id}/members")
async def group_members(group_id: str) -> dict:
    """群成员列表 + 各自权限等级（需要 Bot 在线）。"""
    if not group_id.isdigit():
        raise HTTPException(status_code=400, detail="群号必须是数字")

    try:
        raw_members = await call_api("get_group_member_list", group_id=int(group_id))
    except HTTPException as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=f"{exc.detail}（群成员列表需要 Bot 在线）",
        ) from exc

    members = []
    for raw in raw_members or []:
        members.append(
            {
                "user_id": str(raw.get("user_id", "")),
                "nickname": str(raw.get("nickname", "")),
                "card": str(raw.get("card", "")),
                "role": str(raw.get("role", "member")),
            }
        )

    levels = await _member_levels(group_id, members)

    async with get_session(readonly=True) as session:
        grants = (
            await session.execute(
                select(UserPermission).where(
                    UserPermission.scope_type == SCOPE_GROUP,
                    UserPermission.scope_id == group_id,
                )
            )
        ).scalars().all()
        global_black = (
            await session.execute(
                select(UserPermission).where(
                    UserPermission.scope_type == SCOPE_GLOBAL,
                    UserPermission.scope_id == "",
                )
            )
        ).scalars().all()

    grant_map = {g.user_id: g for g in grants}
    black_set = {g.user_id for g in global_black if int(g.level) == 0}

    items = []
    for member in members:
        uid = member["user_id"]
        level = int(levels.get(uid, 1))
        grant = grant_map.get(uid)
        items.append(
            {
                **member,
                "level": level,
                "level_name": _level_name(level),
                "manual": {
                    "level": grant.level,
                    "level_name": _level_name(grant.level),
                    "updated_by": grant.updated_by,
                }
                if grant
                else None,
                "globally_banned": uid in black_set,
            }
        )

    # 角色排序：群主 > 管理员 > 成员，同级按 QQ 号
    role_order = {"owner": 0, "admin": 1, "member": 2}
    items.sort(key=lambda x: (role_order.get(x["role"], 9), x["user_id"]))

    return {"group_id": group_id, "count": len(items), "items": items}
