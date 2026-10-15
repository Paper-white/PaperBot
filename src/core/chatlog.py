"""聊天记录（原始流水层）。

用 NoneBot 的 `event_preprocessor` 全局钩子，在事件分发前把收到的消息
（群 + 私聊，含 bot 自己的）逐条写入 `chat_message` 表。这是**客观事实层**：

- 记忆提炼（个人 / 群）、群聊上下文注入、未来插件与 AI 工具调用都从这里取；
- 只存**纯文本 + 元信息**（群号 / QQ / 昵称 / 协议端消息 id / 是否 bot）；
- 与 `llm_message`（LLM 会话上下文）职责分离，二者互不依赖。

模块被 import 时即注册全局钩子（`bootstrap.on_startup` 里 import 本模块）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import nonebot
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent, MessageEvent
from nonebot.message import event_preprocessor
from sqlalchemy import select

from src.config import settings
from src.core.database import get_session
from src.core.models import (
    CHAT_TYPE_GROUP,
    CHAT_TYPE_PRIVATE,
    ChatMessage,
    ChatMessageArchive,
)
from src.core.textutil import CST, fmt_dt  # noqa: F401 - fmt_dt 对外兼容导出

logger = nonebot.logger

# 最近见过的协议端消息 id（内存去重，避免 bot 自己的消息回显被记两次）
_seen_ids: set[str] = set()
_SEEN_MAX = 2000


def _row_dict(row: ChatMessage) -> dict:
    return {
        "id": row.id,
        "message_type": row.message_type,
        "group_id": row.group_id or "",
        "user_id": row.user_id or "",
        "nickname": row.nickname or "",
        "content": row.content or "",
        "is_bot": bool(row.is_bot),
        "created_at": fmt_dt(row.created_at),
    }


def _event_meta(event: MessageEvent) -> Optional[dict]:
    """从 OneBot 消息事件里提取聊天流水的元信息；非文本消息返回 None（跳过）。"""
    if not settings.chatlog_enable:
        return None
    text = (event.get_plaintext() or "").strip()
    if not text:
        return None  # 纯图片/表情/空消息不落流水（只存纯文本）

    sender = getattr(event, "sender", None)
    nickname = (
        getattr(sender, "nickname", "") or getattr(sender, "card", "") or ""
    )
    if isinstance(event, GroupMessageEvent):
        message_type = CHAT_TYPE_GROUP
        group_id = str(getattr(event, "group_id", "") or "")
    else:
        message_type = CHAT_TYPE_PRIVATE
        group_id = ""
    return {
        "message_type": message_type,
        "group_id": group_id,
        "user_id": str(event.user_id),
        "nickname": str(nickname),
        "content": text,
        "message_id": str(getattr(event, "message_id", "") or ""),
        "is_bot": bool(event.user_id == getattr(event, "self_id", None)),
    }


@event_preprocessor
async def _record_chat_message(event: Event) -> None:  # noqa: ANN001
    """全局钩子：把每条消息写入 chat_message（失败不影响事件处理）。"""
    if not isinstance(event, MessageEvent):
        return
    meta = _event_meta(event)
    if meta is None:
        return

    # 内存去重：同一协议端消息 id 只记一次（bot 自己发的会回显）
    mid = meta["message_id"]
    if mid and mid in _seen_ids:
        return

    try:
        async with get_session() as session:
            # 库里兜底去重：message_id 非空时查一次
            if mid:
                existed = (
                    await session.execute(
                        select(ChatMessage.id)
                        .where(
                            ChatMessage.message_id == mid,
                            ChatMessage.user_id == meta["user_id"],
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if existed is not None:
                    return
            session.add(ChatMessage(**meta))
    except Exception as exc:  # noqa: BLE001 - 记录失败不能影响消息处理
        logger.opt(exception=exc).warning("聊天记录写入失败（消息不受影响）")
        return

    if mid:
        _seen_ids.add(mid)
        if len(_seen_ids) > _SEEN_MAX:
            # 简单裁剪：清掉一半旧 id（集合无顺序，够用即可）
            _seen_ids.clear()


def install_chatlog() -> None:
    """注册全局消息记录钩子（bootstrap.on_startup 调用；幂等，重复调用安全）。"""
    event_preprocessor(_record_chat_message)


# --------------------------------------------------------------------------- #
# 查询 API（记忆提炼 / 群聊上下文 / 未来插件共用）
# --------------------------------------------------------------------------- #
async def recent_messages(
    group_id: str,
    *,
    limit: int = 10,
    before_id: Optional[int] = None,
) -> list[dict]:
    """取某群最近 `limit` 条消息（正序返回），`before_id` 可只取该 id 之前的。"""
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.group_id == str(group_id))
        .order_by(ChatMessage.id.desc())
        .limit(max(1, limit))
    )
    if before_id is not None:
        stmt = stmt.where(ChatMessage.id < before_id)
    async with get_session(readonly=True) as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [_row_dict(r) for r in reversed(rows)]


async def messages_since(
    *,
    watermark: int = 0,
    group_id: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 300,
) -> list[dict]:
    """取 `watermark`（chat_message.id）之后的消息增量。

    - `group_id` 给定时只取该群；`user_id` 给定时只取该人；
    - 返回按 id 升序，最多 `limit` 条（调用方据数量判断是否被截断）。
    """
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.id > int(watermark))
        .order_by(ChatMessage.id.asc())
        .limit(max(1, limit))
    )
    if group_id is not None:
        stmt = stmt.where(ChatMessage.group_id == str(group_id))
    if user_id is not None:
        stmt = stmt.where(ChatMessage.user_id == str(user_id))
    async with get_session(readonly=True) as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [_row_dict(r) for r in rows]


async def messages_by_user(user_id: str, *, limit: int = 100) -> list[dict]:
    """取某用户最近的发言（正序返回，跨所有群/私聊）。"""
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.user_id == str(user_id))
        .order_by(ChatMessage.id.desc())
        .limit(max(1, limit))
    )
    async with get_session(readonly=True) as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [_row_dict(r) for r in reversed(rows)]


async def delete_messages(*, group_id: Optional[str] = None, before_id: Optional[int] = None) -> int:
    """手动清理聊天流水：按群 / 按 id 之前的记录删除，返回删除条数。"""
    from sqlalchemy import delete

    stmt = delete(ChatMessage)
    if group_id is not None:
        stmt = stmt.where(ChatMessage.group_id == str(group_id))
    if before_id is not None:
        stmt = stmt.where(ChatMessage.id < int(before_id))
    async with get_session() as session:
        result = await session.execute(stmt)
        await session.commit()
    return int(result.rowcount or 0)


async def archive_messages(*, before_id: int, batch: int = 5000) -> dict:
    """把 `id < before_id` 的流水搬进 `chat_message_archive`（同事务：插入 + 删除）。

    机械搬移，不判断水位线（由 scheduler 负责算安全的 before_id）。幂等：
    归档表以原 id 做主键，插入前先查已存在的 id 跳过，重复跑不会产生重复行。
    返回 {"candidates", "archived", "deleted"}（archived = 本批真正新插入的条数）。
    """
    from sqlalchemy import delete

    before = int(before_id)
    limit = max(1, int(batch))
    async with get_session() as session:
        # 本批要搬的 ORM 行（升序取最旧的 limit 条）
        rows = (
            (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.id < before)
                    .order_by(ChatMessage.id.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return {"candidates": 0, "archived": 0, "deleted": 0}
        ids = [int(r.id) for r in rows]
        # 归档表里已存在的 id（跳过，避免主键冲突 → 等价 INSERT OR IGNORE）
        existed = set(
            (
                await session.execute(
                    select(ChatMessageArchive.id).where(ChatMessageArchive.id.in_(ids))
                )
            )
            .scalars()
            .all()
        )
        fresh = [r for r in rows if int(r.id) not in existed]
        session.add_all(
            ChatMessageArchive(
                id=r.id,
                platform=r.platform or "onebot11",
                message_type=r.message_type or CHAT_TYPE_GROUP,
                group_id=r.group_id or "",
                user_id=r.user_id or "",
                nickname=r.nickname or "",
                content=r.content or "",
                message_id=r.message_id or "",
                is_bot=bool(r.is_bot),
                created_at=r.created_at,
            )
            for r in fresh
        )
        deleted = (
            await session.execute(delete(ChatMessage).where(ChatMessage.id.in_(ids)))
        ).rowcount or 0
        await session.commit()
    return {"candidates": len(ids), "archived": len(fresh), "deleted": int(deleted)}


async def restore_messages(*, before_id: int, batch: int = 5000) -> int:
    """把归档表里 `id < before_id` 的记录搬回主表（幂等），返回真正恢复的条数。

    与 `archive_messages` 对称：先查主表已有 id 跳过（等价 INSERT OR IGNORE），
    再把归档行插回主表并从归档表删除，用于回滚 / 审计。
    """
    from sqlalchemy import delete

    before = int(before_id)
    limit = max(1, int(batch))
    async with get_session() as session:
        rows = (
            (
                await session.execute(
                    select(ChatMessageArchive)
                    .where(ChatMessageArchive.id < before)
                    .order_by(ChatMessageArchive.id.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return 0
        ids = [int(r.id) for r in rows]
        existed = set(
            (await session.execute(select(ChatMessage.id).where(ChatMessage.id.in_(ids))))
            .scalars()
            .all()
        )
        fresh = [r for r in rows if int(r.id) not in existed]
        session.add_all(
            ChatMessage(
                id=r.id,
                platform=r.platform or "onebot11",
                message_type=r.message_type or CHAT_TYPE_GROUP,
                group_id=r.group_id or "",
                user_id=r.user_id or "",
                nickname=r.nickname or "",
                content=r.content or "",
                message_id=r.message_id or "",
                is_bot=bool(r.is_bot),
                created_at=r.created_at,
            )
            for r in fresh
        )
        # 归档表里这些 id 都清掉（已恢复的 + 主表本就有的，后者属冗余）
        await session.execute(delete(ChatMessageArchive).where(ChatMessageArchive.id.in_(ids)))
        await session.commit()
    return len(fresh)


__all__ = [
    "install_chatlog",
    "fmt_dt",
    "recent_messages",
    "messages_since",
    "messages_by_user",
    "delete_messages",
    "archive_messages",
    "restore_messages",
]
