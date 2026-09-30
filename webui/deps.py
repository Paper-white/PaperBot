"""WebUI 公共依赖：鉴权、在线 Bot 访问、通用小工具。"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import nonebot
from fastapi import Header, HTTPException

from src.config import settings

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
def iso_local(ts: float) -> str:
    """时间戳 → 北京时间 ISO 字符串。"""
    return datetime.fromtimestamp(ts, CST).isoformat(timespec="seconds")


def human_duration(seconds: float) -> str:
    """秒数 → 「1 天 2 小时 3 分 4 秒」。"""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} 天")
    if hours or days:
        parts.append(f"{hours} 小时")
    if minutes or hours or days:
        parts.append(f"{minutes} 分")
    parts.append(f"{secs} 秒")
    return " ".join(parts)


def human_size(num_bytes: float) -> str:
    """字节数 → 人类可读。"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def mask_secret(value: Optional[str]) -> dict[str, Any]:
    """把敏感值转成「已配置 + 长度」的安全描述。"""
    text = "" if value is None else str(value)
    return {
        "value": SECRET_PLACEHOLDER if text else "",
        "configured": bool(text),
        "length": len(text),
    }
