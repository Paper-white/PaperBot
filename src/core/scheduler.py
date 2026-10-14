"""定时任务：记忆增量总结 + 上下文压缩。

一个后台 asyncio 循环（`on_startup` 启动、`on_shutdown` 停止），每
`LLM_SUMMARY_INTERVAL` 秒跑一轮：

1. **个人记忆**：遍历 `llm_user_profile` 里出现过的用户，从 `chat_message`
   取该用户 `last_msg_id` 之后的新发言 → 提炼印象/关键信息；
2. **群记忆**：遍历 `chat_message` 里出现过的群，从 `chat_message` 取该群
   `last_msg_id` 之后的新消息 → 提炼群共同记忆；
3. **上下文压缩**：遍历 `llm_session`，超过 `LLM_COMPRESS_THRESHOLD` 的
   把旧消息压进 `summary`。

设计要点：
- 全部后台运行，绝不 await 在对话链路里；
- 用独立便宜模型（`LLM_SUMMARY_PROVIDER` / `LLM_SUMMARY_MODEL`），
  未配置时回落默认服务商；
- 失败只记日志、不推进水位线（下一轮自动重试）。
"""

from __future__ import annotations

import asyncio
from typing import Optional

import nonebot
from sqlalchemy import select

from src.config import settings
from src.core import llm
from src.core.database import get_session
from src.core.models import (
    CHAT_TYPE_GROUP,
    ChatMessage,
    LlmSession,
    LlmUserProfile,
)

logger = nonebot.logger

_task: Optional[asyncio.Task] = None


async def _distinct_user_ids() -> list[str]:
    """聊天流水里出现过的所有用户（含 bot 自己的？排除 bot 的）。"""
    from sqlalchemy import func

    async with get_session(readonly=True) as session:
        rows = (
            await session.execute(
                select(ChatMessage.user_id, func.max(ChatMessage.id))
                .where(ChatMessage.is_bot.is_(False))
                .group_by(ChatMessage.user_id)
            )
        ).all()
    return [str(r[0]) for r in rows if r[0]]


async def _distinct_group_ids() -> list[str]:
    """聊天流水里出现过的所有群。"""
    from sqlalchemy import func

    async with get_session(readonly=True) as session:
        rows = (
            await session.execute(
                select(ChatMessage.group_id, func.max(ChatMessage.id))
                .where(
                    ChatMessage.message_type == CHAT_TYPE_GROUP,
                    ChatMessage.group_id != "",
                )
                .group_by(ChatMessage.group_id)
            )
        ).all()
    return [str(r[0]) for r in rows if r[0]]


async def _all_session_ids() -> list[int]:
    async with get_session(readonly=True) as session:
        rows = (await session.execute(select(LlmSession.id))).scalars().all()
    return [int(r) for r in rows]


async def run_summary_cycle() -> dict:
    """跑一轮总结 + 压缩，返回统计。"""
    stat = {"users": 0, "groups": 0, "compressed": 0, "errors": 0}
    if not settings.llm_summary_enable:
        return stat

    # 1) 个人记忆
    for uid in await _distinct_user_ids():
        try:
            result = await llm.summarize_user(uid)
            if result.get("ok"):
                stat["users"] += 1
            elif result.get("error"):
                stat["errors"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"总结用户 {uid} 异常：{exc}")
            stat["errors"] += 1

    # 2) 群记忆
    for gid in await _distinct_group_ids():
        try:
            result = await llm.summarize_group(gid)
            if result.get("ok"):
                stat["groups"] += 1
            elif result.get("error"):
                stat["errors"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"总结群 {gid} 异常：{exc}")
            stat["errors"] += 1

    # 3) 上下文压缩
    for sid in await _all_session_ids():
        try:
            result = await llm.compress_session(sid)
            if result.get("ok") and result.get("compressed"):
                stat["compressed"] += int(result["compressed"])
            elif result.get("error"):
                stat["errors"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"压缩会话 {sid} 异常：{exc}")
            stat["errors"] += 1

    if stat["users"] or stat["groups"] or stat["compressed"]:
        logger.info(
            f"记忆总结一轮完成：用户 {stat['users']}、群 {stat['groups']}、"
            f"压缩 {stat['compressed']} 条、异常 {stat['errors']}"
        )
    return stat


async def _loop() -> None:
    """后台主循环：等首轮延迟后，周期执行总结。"""
    interval = max(60, int(settings.llm_summary_interval or 1800))
    try:
        await asyncio.sleep(10)  # 启动后稍等，避开初始化高峰
        while True:
            await run_summary_cycle()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("记忆总结调度器已停止")
        raise


async def start_scheduler() -> None:
    """启动后台定时任务（幂等）。"""
    global _task
    if _task is not None and not _task.done():
        return
    if not settings.llm_summary_enable:
        logger.info("定时记忆总结已关闭（LLM_SUMMARY_ENABLE=false）")
        return
    _task = asyncio.create_task(_loop())
    logger.info(
        f"记忆总结调度器已启动（每 {settings.llm_summary_interval}s 一轮）"
    )


async def stop_scheduler() -> None:
    """停止后台定时任务。"""
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None


__all__ = ["run_summary_cycle", "start_scheduler", "stop_scheduler"]
