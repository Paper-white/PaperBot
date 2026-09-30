"""日志文件输出。

把**每次运行**的日志写进 `logs/` 下的独立文件（`paperbot-YYYYmmdd-HHMMSS.log`），
方便排查「跑起来之后到底发生了什么」——终端关了、或者只是把窗口最小化时也留痕。

- 文件 sink 挂在 NoneBot 的 loguru logger 上，所以框架 / 插件 / WebUI 的日志都会进文件；
- `.env` 里可用 `LOG_FILE_ENABLE` / `LOG_FILE_LEVEL` / `LOG_DIR` / `LOG_KEEP_DAYS` 控制；
- 启动时顺带清理超过保留天数的旧日志（当日与当前运行的日志一定保留）。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import nonebot

from src.config import BASE_DIR, settings

logger = nonebot.logger

# 文件里用的格式（不带颜色，带毫秒）
FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"

_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.log$")

_sink_id: int | None = None
_current_file: Path | None = None


# --------------------------------------------------------------------------- #
# 路径工具
# --------------------------------------------------------------------------- #
def log_dir() -> Path:
    """日志目录（相对路径按项目根目录解析）。"""
    raw = Path(settings.log_dir or "logs")
    return raw if raw.is_absolute() else (BASE_DIR / raw)


def current_log_file() -> Path | None:
    """本次运行的日志文件（未启用/未初始化时为 None）。"""
    return _current_file


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def human_size(num_bytes: float) -> str:
    """字节数 → 人类可读。"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _safe_file(name: str) -> Path:
    """把文件名解析成日志目录下的安全路径（防目录穿越）。"""
    if not _NAME_RE.match(name or ""):
        raise ValueError("非法的日志文件名")
    directory = log_dir().resolve()
    path = (directory / name).resolve()
    if path.parent != directory:
        raise ValueError("非法的日志文件名")
    return path


def _describe(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "size_bytes": stat.st_size,
        "size_text": human_size(stat.st_size),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "current": bool(_current_file and path.name == _current_file.name),
    }


# --------------------------------------------------------------------------- #
# 生命周期
# --------------------------------------------------------------------------- #
def _add_sink(path: Path) -> int:
    level = (settings.log_file_level or "DEBUG").upper()
    return logger.add(
        str(path),
        level=level,
        encoding="utf-8",
        enqueue=True,
        backtrace=False,
        diagnose=False,
        format=FILE_FORMAT,
    )


def setup_file_logging() -> Path | None:
    """挂上文件 sink 并返回本次运行的日志文件路径（未启用则返回 None）。"""
    global _sink_id, _current_file

    if not settings.log_file_enable:
        logger.info("日志文件输出已关闭（LOG_FILE_ENABLE=false）")
        return None
    if _sink_id is not None:  # 已初始化过，避免重复挂 sink
        return _current_file

    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"paperbot-{_stamp()}.log"
    _sink_id = _add_sink(path)
    _current_file = path

    removed = cleanup_expired()
    if removed:
        logger.debug(f"已清理 {removed} 个过期日志文件（> {settings.log_keep_days} 天）")
    return path


def shutdown_file_logging() -> None:
    """移除文件 sink（关闭前调用，保证日志落盘）。"""
    global _sink_id
    if _sink_id is not None:
        try:
            logger.remove(_sink_id)
        except ValueError:  # 已被移除
            pass
        _sink_id = None


# --------------------------------------------------------------------------- #
# 查询 / 清理
# --------------------------------------------------------------------------- #
def list_log_files() -> list[dict]:
    """列出日志文件（新的在前）。"""
    directory = log_dir()
    if not directory.is_dir():
        return []
    items = []
    for path in directory.glob("*.log"):
        if path.is_file():
            try:
                items.append(_describe(path))
            except OSError:
                continue
    items.sort(key=lambda x: x["modified_at"], reverse=True)
    return items


def cleanup_expired(days: int | None = None) -> int:
    """清理超过保留天数的日志（保留当前运行的文件），返回删除数量。"""
    keep_days = settings.log_keep_days if days is None else days
    if keep_days is None or keep_days <= 0:
        return 0
    directory = log_dir()
    if not directory.is_dir():
        return 0
    deadline = datetime.now() - timedelta(days=int(keep_days))
    removed = 0
    for path in directory.glob("*.log"):
        if _current_file and path.name == _current_file.name:
            continue
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) >= deadline:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def resolve_log_file(name: str) -> Path:
    """把文件名解析成日志目录下的安全路径（供 WebUI 读取/删除用）。"""
    return _safe_file(name)


def remove_log_file(name: str) -> int:
    """删除单个日志文件，返回释放的字节数；当前运行的日志不允许删除。"""
    path = _safe_file(name)
    if _current_file and path.name == _current_file.name:
        raise PermissionError("当前正在写入的日志不能单独删除，请用「清理全部日志」")
    if not path.is_file():
        raise FileNotFoundError("日志文件不存在")
    size = path.stat().st_size
    path.unlink()
    return size


def clear_log_files() -> dict:
    """清空日志：删除历史文件，并把本次运行的日志清成空文件。"""
    global _sink_id

    directory = log_dir()
    deleted = 0
    freed = 0
    if directory.is_dir():
        for path in directory.glob("*.log"):
            if _current_file and path.name == _current_file.name:
                continue
            try:
                freed += path.stat().st_size
                path.unlink()
                deleted += 1
            except OSError:
                continue

    truncated = False
    if _current_file is not None:
        # 先摘掉 sink 释放文件句柄，清空后再重新挂上，避免 Windows 文件占用
        was_running = _sink_id is not None
        shutdown_file_logging()
        try:
            freed += _current_file.stat().st_size
            _current_file.write_text("", encoding="utf-8")
            truncated = True
        except OSError:
            pass
        if was_running:
            _sink_id = _add_sink(_current_file)

    return {
        "deleted": deleted,
        "truncated": truncated,
        "freed_bytes": freed,
        "freed_text": human_size(freed),
    }
