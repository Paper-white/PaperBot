"""WebUI 公共依赖：鉴权、在线 Bot 访问、通用小工具。"""

from __future__ import annotations

import secrets
import time
from datetime import timedelta, timezone
from typing import Any, Optional

import nonebot
from fastapi import Header, HTTPException

from src.config import settings
from src.core.textutil import human_duration, human_size  # noqa: F401 - 对外转出兼容别名
from src.core.textutil import fmt_ts as iso_local

logger = nonebot.logger

# 本进程启动（≈ WebUI 启动）时间戳：bot.py 在启动阶段就 import 了本模块
STARTED_AT = time.time()

CST = timezone(timedelta(hours=8))

# 脱敏占位符：前端原样回传时视为「不修改」
SECRET_PLACEHOLDER = "••••••••"


# --------------------------------------------------------------------------- #
# 鉴权
# --------------------------------------------------------------------------- #
async def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    """可选的 Bearer Token 鉴权（定长比较，避免时序侧信道）。"""
    token = settings.webui_token
    if not token:
        return
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="unauthorized")


# --------------------------------------------------------------------------- #
# 在线 Bot
# --------------------------------------------------------------------------- #
def online_bots() -> dict[str, Any]:
    """当前已连接的 Bot（self_id -> Bot）；未连接时返回空字典。"""
    try:
        return dict(nonebot.get_bots())
    except Exception:  # noqa: BLE001 - driver 尚未就绪
        return {}


def first_online_bot():
    """取第一个可用的 Bot 实例，没有则 None。"""
    bots = online_bots()
    for bot in bots.values():
        if getattr(bot, "is_connected", False):
            return bot
    return next(iter(bots.values()), None)


async def call_api(action: str, **params: Any) -> Any:
    """调用协议端 API；不在线时抛 503，调用失败抛 502。"""
    bot = first_online_bot()
    if bot is None:
        raise HTTPException(
            status_code=503, detail="当前没有已连接的协议端（Bot 未在线）"
        )
    try:
        return await bot.call_api(action, **params)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"协议端接口调用失败：{exc}") from exc


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
# ℹ️ iso_local / human_duration / human_size 已抽到 `src.core.textutil`，
#    本模块在顶部重新导出，保持原有 `from webui.deps import iso_local` 可用。


def mask_secret(value: Optional[str]) -> dict[str, Any]:
    """把敏感值转成「已配置 + 长度」的安全描述。"""
    text = "" if value is None else str(value)
    return {
        "value": SECRET_PLACEHOLDER if text else "",
        "configured": bool(text),
        "length": len(text),
    }


def sqlite_db_size(db_path: str) -> tuple[int, str]:
    """SQLite 主库 + `-wal` + `-shm` 的总字节数与可读文本。"""
    import os

    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(db_path + suffix)
        except OSError:
            continue
    return total, human_size(total)
