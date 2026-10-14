"""聊天记录（原始流水层）。

用 NoneBot 的 `event_preprocessor` 全局钩子，在事件分发前把收到的消息
（群 + 私聊，含 bot 自己的）逐条写入 `chat_message` 表。这是**客观事实层**：

- 记忆提炼（个人 / 群）、群聊上下文注入、未来插件与 AI 工具调用都从这里取；
- 只存**纯文本 + 元信息**（群号 / QQ / 昵称 / 协议端消息 id / 是否 bot）；
- 与 `llm_message`（LLM 会话上下文）职责分离，二者互不依赖。

模块被 import 时即注册全局钩子（`bootstrap.on_startup` 里 import 本模块）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import nonebot
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent, MessageEvent
from nonebot.message import event_preprocessor
from sqlalchemy import select

from src.config import settings
from src.core.database import get_session
from src.core.models import CHAT_TYPE_GROUP, CHAT_TYPE_PRIVATE, ChatMessage

logger = nonebot.logger

CST = timezone(timedelta(hours=8))

# 最近见过的协议端消息 id（内存去重，避免 bot 自己的消息回显被记两次）
_seen_ids: set[str] = set()
_SEEN_MAX = 2000


def fmt_dt(value: Optional[datetime]) -> str:
    """库里的 UTC 时间 → 北京时间字符串（与 llm.fmt_dt 同规则）。"""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CST).isoformat(sep=" ", timespec="seconds")


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


__all__ = [
    "install_chatlog",
    "fmt_dt",
    "recent_messages",
    "messages_since",
    "messages_by_user",
    "delete_messages",
]
