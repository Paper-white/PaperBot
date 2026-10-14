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
from src.core import registry, service
from src.core.database import get_session
from src.core.models import SCOPE_GROUP, PluginRule, UserPermission
from webui.deps import (
    STARTED_AT,
    human_duration,
    human_size,
    iso_local,
    online_bots,
    require_token,
    sqlite_db_size,
)

router = APIRouter(dependencies=[Depends(require_token)], tags=["status"])

# 预热 CPU 采样基线：`cpu_percent(None)` 首次调用恒返回 0，先垫一次，
# 之后每次请求统计的是「距上次请求」区间的使用率（非阻塞）。
try:
    import psutil as _psutil  # noqa: PLC0415

    _psutil.cpu_percent(interval=None)
except Exception:  # noqa: BLE001
    pass


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


def _host_metrics() -> dict:
    """主机资源：CPU / 内存 / 本进程占用（psutil，失败则降级为空值）。"""
    data: dict = {"available": False}
    try:
        import psutil  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - 未装 psutil 时优雅降级
        return data

    try:
        vm = psutil.virtual_memory()
        proc = psutil.Process()
        mem = proc.memory_info()
        try:
            load1, load5, load15 = psutil.getloadavg()
        except (AttributeError, OSError):
            load1 = load5 = load15 = 0.0
        data = {
            "available": True,
            "cpu_percent": psutil.cpu_percent(interval=None),
            "cpu_count": psutil.cpu_count() or 0,
            "load1": round(load1, 2),
            "load5": round(load5, 2),
            "load15": round(load15, 2),
            "mem_percent": vm.percent,
            "mem_total_text": human_size(vm.total),
            "mem_used_text": human_size(vm.used),
            "mem_free_text": human_size(vm.available),
            "proc_rss_bytes": int(mem.rss),
            "proc_rss_text": human_size(mem.rss),
            "proc_threads": proc.num_threads(),
        }
    except Exception:  # noqa: BLE001 - 取数失败不影响整个接口
        return {"available": False}
    return data


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
    db_size, db_size_text = sqlite_db_size(db_path)

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
            "started_at_ts": int(STARTED_AT),
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
            "plugins_enabled": sum(
                1 for p in await service.plugin_overview() if p.get("global_enabled")
            ),
            "commands": _command_count(),
            "groups": counts["known_groups"],
            "rules": counts["rules"],
            "grants": counts["grants"],
        },
        "host": _host_metrics(),
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
            "size_text": db_size_text,
            "exists": os.path.exists(db_path),
        },
    }
