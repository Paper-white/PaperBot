"""状态页接口：`/api/status`。"""

from __future__ import annotations

import os
import platform
import sys
import threading
import time

import nonebot
from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from src.config import active_groups, settings
from src.core import registry
from src.core.database import get_session
from src.core.models import SCOPE_GROUP, PluginRule, UserPermission
from webui.deps import (
    STARTED_AT,
    human_duration,
    human_size,
    iso_local,
    online_bots,
    require_token,
)

router = APIRouter(dependencies=[Depends(require_token)], tags=["status"])


async def _db_counts() -> dict[str, int]:
    async with get_session(readonly=True) as session:
        rules = (
            await session.execute(select(func.count()).select_from(PluginRule))
        ).scalar_one()
        grants = (
            await session.execute(select(func.count()).select_from(UserPermission))
        ).scalar_one()
        group_rules = (
            await session.execute(
                select(PluginRule.scope_id).where(PluginRule.scope_type == SCOPE_GROUP)
            )
        ).scalars().all()
        group_grants = (
            await session.execute(
                select(UserPermission.scope_id).where(
                    UserPermission.scope_type == SCOPE_GROUP
                )
            )
        ).scalars().all()
    groups = {g for g in set(group_rules) | set(group_grants) if g}
    return {"rules": int(rules), "grants": int(grants), "known_groups": len(groups)}


def _command_count() -> int:
    return sum(len(f.commands) for f in registry.all_features().values())


def _bot_list() -> list[dict]:
    items = []
    for self_id, bot in online_bots().items():
        adapter = getattr(getattr(bot, "adapter", None), "get_name", lambda: "")()
        items.append(
            {
                "self_id": str(self_id),
                "adapter": adapter or "unknown",
                "connected": bool(getattr(bot, "is_connected", False)),
            }
        )
    return items


@router.get("/status")
async def get_status() -> dict:
    """Bot 运行状态（在线时长 / 连接 / 统计 / 守卫）。"""
    now = time.time()
    uptime = now - STARTED_AT
    counts = await _db_counts()
    bots = _bot_list()

    db_path = settings.database_url.split("///", 1)[-1]
    db_size = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            db_size += os.path.getsize(db_path + suffix)
        except OSError:
            continue

    active = sorted(active_groups())
    try:
        host = str(nonebot.get_driver().config.host)
        port = int(nonebot.get_driver().config.port)
    except Exception:  # noqa: BLE001
        host, port = "127.0.0.1", 0

    return {
        "server": {
            "now": iso_local(now),
            "started_at": iso_local(STARTED_AT),
            "uptime_seconds": int(uptime),
            "uptime_text": human_duration(uptime),
        },
        "process": {
            "pid": os.getpid(),
            "python": sys.version.split()[0],
            "platform": f"{platform.system()} {platform.release()}",
            "threads": threading.active_count(),
        },
        "connection": {
            "online": any(b["connected"] for b in bots),
            "bots": bots,
        },
        "counts": {
            "plugins": len(registry.names()),
            "commands": _command_count(),
            "groups": counts["known_groups"],
            "rules": counts["rules"],
            "grants": counts["grants"],
        },
        "guard": {
            "enabled": settings.output_guard_enabled,
            "groups": active,
            "blocked_all": settings.output_guard_enabled and not active,
            "gate_default_enabled": settings.gate_default_enabled,
        },
        "webui": {
            "host": host,
            "port": port,
            "prefix": settings.webui_prefix,
            "token_required": bool(settings.webui_token),
            "enabled": settings.webui_enable,
        },
        "storage": {
            "database": settings.database_url,
            "size_bytes": db_size,
            "size_text": human_size(db_size),
            "exists": os.path.exists(db_path),
        },
    }
