"""定时任务：记忆增量总结 + 上下文压缩 + 聊天流水归档。

一个后台 asyncio 循环（`on_startup` 启动、`on_shutdown` 停止），每
`LLM_SUMMARY_INTERVAL` 秒跑一轮：

1. **个人记忆**：遍历 `llm_user_profile` 里出现过的用户，从 `chat_message`
   取该用户 `last_msg_id` 之后的新发言 → 提炼印象/关键信息；
2. **群记忆**：遍历 `chat_message` 里出现过的群，从 `chat_message` 取该群
   `last_msg_id` 之后的新消息 → 提炼群共同记忆；
3. **上下文压缩**：遍历 `llm_session`，超过 `LLM_COMPRESS_THRESHOLD` 的
   把旧消息压进 `summary`；
4. **流水归档**（跨日一次）：把保留期外、且已过提炼水位线的老消息搬进
   `chat_message_archive`，主表只留热区。

设计要点：
- 全部后台运行，绝不 await 在对话链路里；
- 用独立便宜模型（`LLM_SUMMARY_PROVIDER` / `LLM_SUMMARY_MODEL`），
  未配置时回落默认服务商；
- 失败只记日志、不推进水位线（下一轮自动重试）。

⚠️ 归档**复用本循环**（不另开协程）：`_loop()` 只在 `LLM_SUMMARY_ENABLE=True`
时启动，故「总结关 = 归档关」——这是正确性保障：归档依赖提炼水位线，必须先有总结
把水位线推到位，才敢搬走老消息，否则会被「尚未提炼」的消息永久漏掉。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import nonebot
from sqlalchemy import func, select, text

from src.config import settings
from src.core import chatlog, llm
from src.core.database import get_session
from src.core.models import (
    CHAT_TYPE_GROUP,
    ChatMessage,
    LlmGroupProfile,
    LlmSession,
    LlmUserProfile,
)

logger = nonebot.logger

_task: Optional[asyncio.Task] = None

# 上次执行归档的日期（UTC，`%Y-%m-%d`），用于「跨日才跑一次」
_last_archive_day: Optional[str] = None


async def _distinct_user_ids() -> list[str]:
    """聊天流水里出现过的所有用户（含 bot 自己的？排除 bot 的）。"""
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


async def _safe_archive_before_id() -> int:
    """算出可安全归档的 id 上界（严格小于它才允许归档）。

    取以下四者的最小值（NULL 视为不限制）：
    1. 所有已存在用户画像水位线的最小值；
    2. 所有已存在群画像水位线的最小值；
    3. 「出现在流水里但还没画像」的用户的最早消息 id（否则归档后他从 _distinct_user_ids 消失、永不提炼）；
    4. 同理「还没画像」的群的最早消息 id。
    """
    async with get_session(readonly=True) as session:
        user_wm = (await session.execute(select(func.min(LlmUserProfile.last_msg_id)))).scalar()
        group_wm = (await session.execute(select(func.min(LlmGroupProfile.last_msg_id)))).scalar()
        pending_user = (
            await session.execute(
                select(func.min(ChatMessage.id))
                .select_from(ChatMessage)
                .outerjoin(LlmUserProfile, LlmUserProfile.user_id == ChatMessage.user_id)
                .where(LlmUserProfile.user_id.is_(None), ChatMessage.is_bot.is_(False))
            )
        ).scalar()
        pending_group = (
            await session.execute(
                select(func.min(ChatMessage.id))
                .select_from(ChatMessage)
                .outerjoin(LlmGroupProfile, LlmGroupProfile.group_id == ChatMessage.group_id)
                .where(
                    LlmGroupProfile.group_id.is_(None),
                    ChatMessage.message_type == CHAT_TYPE_GROUP,
                    ChatMessage.group_id != "",
                )
            )
        ).scalar()
    cands = [int(v) for v in (user_wm, group_wm, pending_user, pending_group) if v is not None]
    return min(cands) if cands else 0   # 0 = 没有任何水位线信息 → 不归档（保守）


async def run_archive_cycle() -> dict:
    """每日归档一轮：算「时间到期」与「水位线安全」的交集，批量搬移。"""
    stat = {"archived": 0, "errors": 0}
    if not settings.chatlog_archive_enable:
        return stat
    try:
        days = max(1, int(settings.chatlog_retention_days or 0))
        async with get_session(readonly=True) as session:
            # 到期的上界：晚于 cutoff 的消息都还「在保留期内」，只允许 < cutoff 的被归档
            cutoff = (
                await session.execute(
                    text("SELECT MIN(id) FROM chat_message WHERE created_at >= datetime('now', :d)"),
                    {"d": f"-{days} days"},
                )
            ).scalar()
        if cutoff is None:
            return stat  # 没有到期数据
        safe = await _safe_archive_before_id()
        # 无水印信息（safe==0）→ 保守不归档
        before = min(int(cutoff), safe) if safe > 0 else 0
        if before <= 0:
            return stat
        # 分小批循环，直到本批不足 batch（避免长事务）
        batch = max(1, int(settings.chatlog_archive_batch or 5000))
        while True:
            result = await chatlog.archive_messages(before_id=before, batch=batch)
            moved = int(result.get("archived") or 0)
            stat["archived"] += moved
            if moved < batch:
                break
        if stat["archived"]:
            logger.info(
                f"聊天流水归档一轮完成：{stat['archived']} 条（保留 {days} 天，before_id={before}）"
            )
    except Exception as exc:  # noqa: BLE001 - 归档失败不能影响总结
        logger.warning(f"聊天流水归档失败：{exc}")
        stat["errors"] += 1
    return stat


async def _maybe_run_archive_cycle() -> None:
    """跨日才执行一次归档（复用 _loop，天然随总结生命周期启停）。

    ⚠️ 之所以挂在同一循环：`_loop()` 只在 `LLM_SUMMARY_ENABLE=True` 时启动，
    从而保证「总结关 = 归档关」——先由总结把水位线推到位，归档才安全。
    """
    global _last_archive_day
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _last_archive_day == today:
        return
    _last_archive_day = today
    await run_archive_cycle()


async def _loop() -> None:
    """后台主循环：等首轮延迟后，周期执行总结（并在跨日时顺带归档流水）。"""
    interval = max(60, int(settings.llm_summary_interval or 1800))
    try:
        await asyncio.sleep(10)  # 启动后稍等，避开初始化高峰
        while True:
            await run_summary_cycle()
            # 先总结推水位线、再归档更安全（跨日才实际执行）
            await _maybe_run_archive_cycle()
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


__all__ = [
    "run_summary_cycle",
    "run_archive_cycle",
    "start_scheduler",
    "stop_scheduler",
]
