"""统一日志出口（对标 AstrBot 的 `from astrbot.api import logger`）。

插件侧推荐两种用法：

1. 直接用 `Feature` 实例自带的 `logger`（**自动带插件名前缀**）::

       feature = Feature("paperbot_xxx", ...)
       feature.logger.info("插件启动啦")

2. 需要模块级 logger 时::

       from src.core.logger import get_logger
       logger = get_logger("paperbot_xxx")
       logger.warning("配置缺失")

输出形如 ``[paperbot_xxx] 插件启动啦``，便于在混合日志里一眼看出是哪个插件打的。
底层仍是 NoneBot 的 loguru logger（框架 / 插件 / WebUI 共用同一套 sink）。
"""

from __future__ import annotations

from typing import Any

import nonebot

# 框架通用 logger（不带前缀）
logger = nonebot.logger


class PrefixedLogger:
    """给日志消息统一加 `[名字] ` 前缀的轻量包装（不改变底层 logger）。"""

    __slots__ = ("_prefix",)

    def __init__(self, name: str) -> None:
        self._prefix = f"[{name}] "

    def _emit(self, level: str, message: Any, *args: Any, **kwargs: Any) -> None:
        getattr(nonebot.logger, level)(self._prefix + str(message), *args, **kwargs)

    # ---- 常用级别 ---- #
    def debug(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("debug", message, *args, **kwargs)

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("info", message, *args, **kwargs)

    def success(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("success", message, *args, **kwargs)

    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("warning", message, *args, **kwargs)

    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("error", message, *args, **kwargs)

    def exception(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("exception", message, *args, **kwargs)

    def critical(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._emit("critical", message, *args, **kwargs)

    def log(self, level: str, message: Any, *args: Any, **kwargs: Any) -> None:
        getattr(nonebot.logger, level)(self._prefix + str(message), *args, **kwargs)

    # ---- 高级用法透传（不带前缀，如 logger.opt(exception=e).error(...)） ---- #
    def opt(self, *args: Any, **kwargs: Any):
        return nonebot.logger.opt(*args, **kwargs)

    def bind(self, *args: Any, **kwargs: Any):
        return nonebot.logger.bind(*args, **kwargs)

    def __getattr__(self, item: str):  # pragma: no cover - 兜底透传
        return getattr(nonebot.logger, item)


def get_logger(name: str) -> PrefixedLogger:
    """获取带前缀的 logger。"""
    return PrefixedLogger(name)
