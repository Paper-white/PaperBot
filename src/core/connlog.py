"""连接日志降噪（NoneBot OneBot 适配器「连不上协议端」的刷屏治理）。

## 问题

OneBot v11 适配器在**连不上协议端**时会每 3 秒重连一次，每次都往日志里打一整段
ERROR + 完整 traceback：

    OneBot V11 | Error while setup websocket to ws://10.21.0.203:3001/.
    Trying to reconnect...
    Traceback (most recent call last):
      ...
    OSError: [WinError 121] 信号灯超时时间已到

结果就是**日志被同一条报错刷屏**，既难读、又盖住了真正有用的信息。

## 处理

本模块给 loguru 挂一个**全局 patcher**（对所有 sink 生效，包括 NoneBot 已配置的），
只针对这条特定日志做三件事：

1. **可读化**：把原始英文 + traceback 换成一句中文提示，并从异常里提取「人话」原因，
   顺带给出排查建议；
2. **降级**：ERROR → WARNING（连接失败可自动重连，不是致命错误）；
3. **节流**：同一地址首次完整提示，之后在冷却窗口内**只计数不刷屏**，
   窗口结束再汇总打一条「已重试 N 次」。

> 只匹配「连接建立失败 / 处理数据失败」这种重连日志，其它日志一律不动。
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

import nonebot

logger = nonebot.logger

# 匹配的原文特征（适配器里写死的文案）
_RECONNECT_MARKERS = (
    "Error while setup websocket to",
    "Error while process data from websocket",
)

# 从地址里抠出主机:端口（日志里是 ws://host:port/）
_URL_RE = re.compile(r"wss?://[\w\.\-:]+")

# 同一条连接的聚合 key = 地址；冷却窗口内不重复刷屏
_COOLDOWN_SECONDS = 60.0

# 常见底层异常 → 人话原因
_REASON_HINTS = (
    (("WinError 121", "信号灯超时", "timed out"), "连接超时（目标不可达 / 被防火墙丢弃）"),
    (("WinError 1225", "拒绝", "refused", "actively refused"), "目标端口拒绝连接（协议端未启动？）"),
    (("WinError 10061", "refused"), "目标端口拒绝连接（协议端未启动？）"),
    (("WinError 10060",), "连接超时（目标不可达 / 网络不通）"),
    (("Name or service not known", "getaddrinfo", "nodename nor servname"), "域名解析失败"),
    (("403", "401", "unauthorized", "Authorization"), "鉴权失败（Access Token 不匹配？）"),
    (("WebSocketClosed", "closed"), "连接被对端关闭"),
)

# 运行状态（模块级，节流用）
_state: dict[str, Any] = {
    "installed": False,
    # { url: {"last_log": 秒, "count": 累计重试次数} }
    "tracked": {},
}


def _looks_like_reconnect(record: dict) -> bool:
    """判断这条日志是否为「连不上协议端」的重连报错。

    ⚠️ 只看**消息内容特征**，不依赖 `record["name"]`：适配器经由
    `nonebot.utils.logger_wrapper` 打日志，其 record name 会随调用点变化，
    硬匹配 name 容易漏判（实测 name 可能是 `nonebot` / `__main__` 等）。
    """
    message = str(record.get("message", ""))
    if not message:
        return False
    return any(m in message for m in _RECONNECT_MARKERS)


def _extract_reason(record: dict) -> str:
    """从异常里提取「人话」原因。"""
    exc = record.get("exception")
    text = ""
    if exc is not None:
        # loguru 记录里 exception 是 RecordException（有 .value / .type）
        value = getattr(exc, "value", None)
        text = str(value) if value is not None else str(exc)
    else:
        text = str(record.get("message", ""))
    for needles, hint in _REASON_HINTS:
        for needle in needles:
            if needle in text:
                return hint
    # 抠出末行的异常信息当兜底
    last = text.strip().splitlines()[-1] if text.strip() else "未知错误"
    return last[:120]


def _extract_url(record: dict) -> str:
    match = _URL_RE.search(str(record.get("message", "")))
    return match.group(0) if match else "协议端"


def _handle_record(record: dict) -> None:
    """patcher：命中则改写记录（降级 + 可读化 + 节流）。

    时序（loguru 内部）：patcher 在 `handler.emit()` **之前**运行，
    而 `emit()` 开头就用 `record["level"].no` 做过滤 —— 因此把 level 改成 TRACE
    即可让这条记录被所有 handler 跳过（真正的「静默」，不产生任何输出行）。
    """
    if not _looks_like_reconnect(record):
        return

    url = _extract_url(record)
    reason = _extract_reason(record)
    now = _now()
    entry = _state["tracked"].get(url)

    if entry is None or (now - entry["last_log"]) >= _COOLDOWN_SECONDS:
        # 首次，或冷却窗口已过：输出一条可读警告（去掉刷屏 traceback）
        suppressed = 0 if entry is None else entry["count"]
        _state["tracked"][url] = {"last_log": now, "count": 0}
        _downgrade_to_warning(record)
        record["message"] = _render(url, reason, suppressed)
        record["exception"] = None
        return

    # 冷却窗口内：只累计，静默丢弃（TRACE 低于任何 handler 的等级 → 不输出）
    entry["count"] += 1
    _silence(record)
    record["message"] = ""
    record["exception"] = None


def _render(url: str, reason: str, suppressed: int) -> str:
    """生成可读的提示文案。"""
    tip = (
        "请检查协议端是否已启动、ONEBOT_WS_URLS 地址与令牌是否正确、网络是否可达"
    )
    if suppressed > 0:
        return (
            f"⚠️ 仍无法连接协议端 {url}：{reason}（上一窗口内已静默 {suppressed} 次重试）。"
            f"{tip}"
        )
    return f"⚠️ 无法连接协议端 {url}：{reason}。将每 3 秒自动重连 —— {tip}"


# --------------------------------------------------------------------------- #
# loguru 级别对象 / 时间（延迟获取，避免 import 顺序问题）
# --------------------------------------------------------------------------- #
def _now() -> float:
    return time.monotonic()


def _downgrade_to_warning(record: dict) -> None:
    """把记录降级为 WARNING（原地改 RecordLevel，保留类型以便 `{level}` 正常渲染）。"""
    from loguru import logger as _l

    level = _l.level("WARNING")
    record["level"].name = level.name
    record["level"].no = level.no
    record["level"].icon = level.icon


def _silence(record: dict) -> None:
    """把记录降为 TRACE（低于任何 handler 的阈值 → 不产生任何输出行）。"""
    from loguru import logger as _l

    level = _l.level("TRACE")
    record["level"].name = level.name
    record["level"].no = level.no
    record["level"].icon = level.icon


def install_connection_log_filter() -> None:
    """安装连接日志降噪 patcher（幂等）。

    通过 `logger.configure(patcher=...)` 挂到**所有** sink（NoneBot 已配置的也包含在内）。
    """
    if _state["installed"]:
        return
    try:
        logger.configure(patcher=_handle_record)
    except Exception as exc:  # noqa: BLE001 - 降噪失败不该拦启动
        logger.warning(f"连接日志降噪未启用：{exc}")
        return
    _state["installed"] = True
    logger.debug("已启用连接日志降噪（协议端重连不再刷屏）")


__all__ = ["install_connection_log_filter"]
