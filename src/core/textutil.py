"""共享小工具：人类可读格式化 + 时间格式化。

集中放在这里，避免同一段逻辑在 `webui/deps.py`、`src/core/logging.py`、
`src/core/llm.py`、`src/core/chatlog.py` 等处各写一份。

⚠️ 本模块**只依赖标准库**，不 import 任何项目的其它模块，确保任何地方都能安全引用
（包括 `bot.py` 早期 import 的 `logging` 链路），不会引入循环依赖。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# 北京时间（UTC+8）。库里 `func.now()` 存的是 UTC，对外展示统一换算到这里。
CST = timezone(timedelta(hours=8))


def now_local() -> datetime:
    """当前北京时间（带时区）。"""
    return datetime.now(CST)


def now_text() -> str:
    """当前北京时间字符串（形如 `2026-10-14 20:00:00`，无时区后缀）。"""
    return now_local().isoformat(sep=" ", timespec="seconds")


def fmt_dt(value: Optional[datetime]) -> str:
    """库里的 UTC 时间 → 北京时间字符串；空值返回空串。

    - naive datetime 按 UTC 解释（与 SQLite `func.now()` 一致）；
    - 带时区的按自身时区正确换算。
    """
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CST).isoformat(sep=" ", timespec="seconds")


def fmt_ts(ts: float) -> str:
    """时间戳（秒）→ 北京时间 ISO 字符串（带 +08:00 偏移）。"""
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
    """字节数 → 人类可读（B / KB / MB / GB）。"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


__all__ = [
    "CST",
    "now_local",
    "now_text",
    "fmt_dt",
    "fmt_ts",
    "human_duration",
    "human_size",
]
