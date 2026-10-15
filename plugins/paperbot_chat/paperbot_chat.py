"""人设对话插件（paperbot_chat）。

演示框架的**统一 LLM 服务**（`src/core/llm.py`）怎么用——插件侧只需要 `feature.llm`：

个人对话（命令）::

    %ai <内容>          与 AI 对话（个人上下文 + 长期记忆）
    %ai 群 <内容>       群聊作用域对话（群共享上下文，不注入个人记忆）
    %ai 流式 <内容>     流式生成演示（会告诉你流了几片）
    %ai 印象            看看 AI 眼中的我（印象 + 关键信息）
    %ai 记住 <键> <值>  手工记一条关键信息（不会被自动提炼覆盖）
    %ai 忘记 [键]       删掉一条 / 全部关键信息
    %ai 重置            清空我的对话上下文
    %ai 人设 [名字]     查看 / 切换我的人设
    %ai 状态            LLM 可用性 / 服务商 / 人设 / 上下文条数

群聊「对话模式」（艾特触发，之后免艾特自动回复）::

    @白纸 在吗？        艾特触发一次，开启该群的对话模式（默认 5 分钟超时）
    （之后群里随便说话，bot 都会自动接话，带冷却时间）
    %ai 停              手动关闭本群对话模式
    %ai 群状态          查看本群对话模式状态

顺带演示：插件级配置项（`config_schema` → WebUI 自动出表单）、
统一 logger、`on_load / on_unload` 钩子、以及渲染核心（`render=true` 时把回复画成图）。
"""

from __future__ import annotations

import asyncio
import time

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import Rule

from src.core.feature import Feature
from src.core.llm import LLMError
from src.core.models import LEVEL_USER

feature = Feature(
    "paperbot_chat",
    display_name="AI 对话",
    description="基于统一 LLM 服务的人设对话（个人/群上下文、长期记忆、艾特触发群聊模式）",
    author="paper_white",
    repo="https://github.com/paper-white/PaperBot",
    version="0.2.0",
    requires=">=1.0,<2.0",
    config_schema=[
        {
            "key": "preset",
            "label": "默认人设名",
            "type": "string",
            "default": "paper_white",
            "hint": "留空则用全局默认人设；可用内置 paper_white，也可填自己导入的人设名",
        },
        {
            "key": "render",
            "label": "回复渲染成图片",
            "type": "bool",
            "default": False,
            "hint": "开启后用渲染核心把回复画成卡片图（渲染失败自动退回纯文本）",
        },
        {
            "key": "max_chars",
            "label": "回复最大字数",
            "type": "int",
            "default": 1200,
            "hint": "超长回复截断，0 = 不限制",
        },
        {
            "key": "cooldown",
            "label": "个人命令冷却（秒）",
            "type": "int",
            "default": 3,
            "hint": "防刷屏；0 = 不限制",
        },
        {
            "key": "group_mode",
            "label": "群聊对话模式",
            "type": "bool",
            "default": True,
            "hint": "开启后：群里艾特一次，之后免艾特自动回复（关闭则只回应艾特）",
        },
        {
            "key": "group_timeout",
            "label": "群对话模式超时（秒）",
            "type": "int",
            "default": 300,
            "hint": "对话模式开启后，这么久没人说话就自动关闭；0 = 不超时",
        },
        {
            "key": "group_cooldown",
            "label": "群回复合并窗口（秒）",
            "type": "int",
            "default": 3,
            "hint": "窗口内到达的多条群消息会合并成一次回复（不再丢消息）；0 = 不合并、每条立即回复",
        },
        {
            "key": "group_history",
            "label": "注入的群聊历史条数",
            "type": "int",
            "default": 10,
            "hint": "把群里最近的 N 条消息拼进上下文，让回复更懂氛围；0 = 不注入",
        },
        {
            "key": "bot_names",
            "label": "bot 昵称（逗号分隔，可多个）",
            "type": "text",
            "default": "小白纸",
            "hint": "多个昵称用英文逗号「,」或中文逗号「，」分隔；用于「昵称触发」与「捂嘴」识别",
        },
        {
            "key": "mute_words",
            "label": "捂嘴触发词（逗号分隔）",
            "type": "text",
            "default": "闭嘴,安静,别说了,shut up",
            "hint": "多个词用英文逗号「,」或中文逗号「，」分隔；昵称紧挨触发词（如「小白纸闭嘴」）会关闭本群对话模式",
        },
        {
            "key": "nickname_trigger",
            "label": "昵称也能触发对话模式",
            "type": "bool",
            "default": True,
            "hint": "开启后在群里提到 bot 昵称即可触发对话模式；关闭后只有艾特能触发",
        },
    ],
)

# 命令实际权限由框架维护（command_rule 表），可在 WebUI「命令管理」里改，改完即时生效。
ai = feature.command(
    "ai",
    aliases={"AI", "聊"},
    priority=40,
    level=LEVEL_USER,
    description="与 AI 人设对话（个人上下文 + 长期记忆；群聊模式：%ai 停 / %ai 群状态）",
)

# 冷却记录：{用户ID: 上次调用时间}
_COOLDOWN: dict[str, float] = {}

# 群聊对话模式：{群号: {"active": 最后活跃时间, "reply": 上次回复时间}}
_GROUP_MODE: dict[str, dict[str, float]] = {}

# 群自动回复「合并窗口」：{群号: {"items": [(uid, name, text)], "task": asyncio.Task|None}}
_GROUP_BUFFER: dict[str, dict] = {}


def _split_words(raw: str) -> list[str]:
    """把配置里的逗号分隔字符串拆成去空、去重的词列表。

    支持英文逗号 `,`、中文逗号 `，`、顿号 `、`、分号 `;`/`；`、换行等分隔符。
    空串返回 `[]`；保持出现顺序去重。
    """
    text = (raw or "").replace(",", "\n").replace("，", "\n")
    for sep in ("、", "；", ";", "\r", "\n"):
        text = text.replace(sep, "\n")
    words: list[str] = []
    seen: set[str] = set()
    for piece in text.split("\n"):
        word = piece.strip()
        if word and word not in seen:
            seen.add(word)
            words.append(word)
    return words


def _bot_names() -> list[str]:
    """bot 昵称列表（配置里拆出来，空则回退默认「小白纸」）。"""
    names = _split_words(feature.cfg("bot_names", "小白纸") or "小白纸")
    return names or ["小白纸"]


def _mute_words() -> list[str]:
    """捂嘴触发词列表（默认词串见 config_schema.mute_words）。"""
    return _split_words(feature.cfg("mute_words", "") or "闭嘴,安静,别说了,shut up")


def _command_starts() -> tuple[str, ...]:
    """命令前缀（NoneBot 全局配置，默认 %）。"""
    try:
        starts = get_driver().config.command_start
        if starts:
            return tuple(str(s) for s in starts)
    except Exception:  # noqa: BLE001 - driver 未就绪
        pass
    return ("%",)


def _is_command(text: str) -> bool:
    t = (text or "").lstrip()
    return any(t.startswith(s) for s in _command_starts())


def _need_id(event: MessageEvent) -> str:
    return str(event.get_user_id())


def _group_id(event: MessageEvent) -> str:
    return str(getattr(event, "group_id", "") or "")


def _nickname(event: MessageEvent) -> str:
    """取发言者昵称（群名片优先，其次昵称，最后回落 QQ 号）。"""
    sender = getattr(event, "sender", None)
    if sender is not None:
        nick = getattr(sender, "card", "") or getattr(sender, "nickname", "") or ""
        if nick:
            return str(nick)
    return _need_id(event)


def _cooldown_left(key: str) -> int:
    """还剩几秒冷却（0 = 可以调用，并顺手刷新时间戳）。"""
    wait = int(feature.cfg("cooldown", 3) or 0)
    if wait <= 0:
        return 0
    now = time.monotonic()
    last = _COOLDOWN.get(key, 0.0)
    left = int(last + wait - now) + 1
    if left > 0 and now < last + wait:
        return left
    _COOLDOWN[key] = now
    return 0


def _group_mode_active(gid: str) -> bool:
    """该群对话模式是否仍在有效期内（超时自动关闭）。"""
    if not feature.cfg("group_mode", True):
        return False
    mode = _GROUP_MODE.get(gid)
    if mode is None:
        return False
    timeout = int(feature.cfg("group_timeout", 300) or 0)
    if timeout <= 0:
        return True
    if time.monotonic() - mode["active"] > timeout:
        _GROUP_MODE.pop(gid, None)
        return False
    return True


def _touch_group(gid: str) -> None:
    """刷新群对话模式的活跃时间（进入 / 有人说话时调用）。"""
    mode = _GROUP_MODE.setdefault(gid, {"active": 0.0})
    mode["active"] = time.monotonic()


# --------------------------------------------------------------------------- #
# 群自动回复「合并窗口」：窗口内到达的多条消息合并成一轮回复（不丢消息、不刷屏）
# --------------------------------------------------------------------------- #
def _buffer_add(gid: str, uid: str, name: str, text: str) -> None:
    """把一条待回复消息并入本群的合并窗口（未开窗则开窗）。"""
    buf = _GROUP_BUFFER.setdefault(gid, {"items": [], "task": None})
    buf["items"].append((uid, name, text))
    task = buf.get("task")
    if task is None or task.done():
        buf["task"] = asyncio.create_task(_flush_group_later(gid))


async def _flush_group_later(gid: str) -> None:
    """等合并窗口结束后统一回复本群这一批消息。"""
    window = int(feature.cfg("group_cooldown", 3) or 0)
    try:
        await asyncio.sleep(max(0, window))
        await _flush_group(gid)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 后台回复失败不能炸循环
        feature.logger.opt(exception=exc).warning(f"群 {gid} 合并回复失败")


async def _flush_group(gid: str) -> None:
    """把缓冲区里的多条消息合并成一轮，调用 LLM 并发送一条回复。"""
    buf = _GROUP_BUFFER.pop(gid, None)
    if not buf or not buf.get("items"):
        return
    items: list[tuple[str, str, str]] = list(buf["items"])
    last_uid, last_name, last_text = items[-1]
    preset = await _preset_for("group", gid)
    hist_block = await _chatlog_history_block(gid, last_uid, last_text)
    lines = [f"{name}（QQ {uid}）: {text}" for uid, name, text in items]
    merged = "\n".join(lines)
    content = f"{hist_block}\n\n【本轮要回复的消息】\n{merged}" if hist_block else merged
    try:
        reply = await feature.llm.ask(
            content,
            scope="group",
            scope_id=gid,
            preset=preset,
            extra={"_sender_id": last_uid, "_sender_name": last_name},
        )
    except LLMError as exc:
        feature.logger.warning(f"群 {gid} 合并回复失败：{exc}")
        return
    await _send_group_text(gid, reply)


async def _send_group_text(gid: str, text: str) -> None:
    """后台任务里往群里发文本（带截断/渲染），发送前走输出守卫。"""
    from nonebot import get_bot

    text = (text or "").strip() or "（AI 这次什么都没说…）"
    limit = int(feature.cfg("max_chars", 1200) or 0)
    if limit > 0 and len(text) > limit:
        text = text[:limit] + "…"
    try:
        bot = get_bot()
    except Exception as exc:  # noqa: BLE001 - bot 未就绪
        feature.logger.opt(exception=exc).warning(f"群 {gid} 发送失败：bot 未就绪")
        return
    if feature.cfg("render", False):
        try:
            from src.core.render import render_text

            png = await render_text(text, header=feature.display_name)
            await bot.send_group_msg(group_id=int(gid), message=MessageSegment.image(png))
            return
        except Exception as exc:  # noqa: BLE001 - 渲染失败退回纯文本
            feature.logger.opt(exception=exc).warning("渲染失败，退回纯文本发送")
    await bot.send_group_msg(group_id=int(gid), message=text)


def _mute_hit(text: str) -> bool:
    """「捂嘴」固定搭配判断：昵称紧挨触发词，中间不能夹任何字符。

    遍历「昵称 × 触发词」的全部组合，只要 `昵称+触发词` 相邻出现在原文里就算命中
    （如「小白纸闭嘴」命中，「小白纸，闭嘴」不算）。英文（如 shut up）忽略大小写：
    把原文与候选都转小写后比较，因此「Shut Up」「SHUT UP」也能识别。
    """
    if not text:
        return False
    lowered = text.lower()
    for name in _bot_names():
        for word in _mute_words():
            if name and word and f"{name.lower()}{word.lower()}" in lowered:
                return True
    return False


async def _preset_for(scope: str, scope_id: str) -> str:
    """会话里存的人设优先 → 插件配置 → 空（= 全局默认）。"""
    info = await feature.llm.context(scope, scope_id)
    if info and (info.get("preset_name") or "").strip():
        return str(info["preset_name"]).strip()
    return str(feature.cfg("preset", "") or "").strip()


async def _reply(text: str, matcher=ai) -> None:
    """按配置回复（截断 / 渲染成图），最后必定 finish 当前 matcher。"""
    text = (text or "").strip() or "（AI 这次什么都没说…）"
    limit = int(feature.cfg("max_chars", 1200) or 0)
    if limit > 0 and len(text) > limit:
        text = text[:limit] + "…"
    if feature.cfg("render", False):
        try:
            from src.core.render import render_text

            png = await render_text(text, header=feature.display_name)
        except Exception as exc:  # noqa: BLE001 - 渲染失败不该把回复吞了
            feature.logger.opt(exception=exc).warning("渲染失败，退回纯文本发送")
        else:
            # ⚠️ finish 必须在 try 之外：它抛 FinishedException（继承 Exception），
            # 若放在 try 里会被上面的 except 吞掉，导致图片和文本各发一次。
            await matcher.finish(MessageSegment.image(png))
    await matcher.finish(text)


# --------------------------------------------------------------------------- #
# 群聊对话模式 matcher：艾特触发 + 免艾特自动回复
# --------------------------------------------------------------------------- #
async def _group_chat_rule(event: Event) -> bool:  # noqa: ANN001
    """只在「群消息」上命中（艾特或对话模式内都交给 handler 细判）。"""
    if not isinstance(event, GroupMessageEvent):
        return False
    if not feature.cfg("group_mode", True):
        return False
    if event.user_id == event.self_id:
        return False
    if _is_command(event.get_plaintext()):
        return False
    return True


group_chat = feature.message(rule=Rule(_group_chat_rule), priority=99, block=False)


@group_chat.handle()
async def _handle_group_chat(event: GroupMessageEvent) -> None:
    gid = _group_id(event)
    if not gid:
        return
    text = event.get_plaintext().strip()
    to_me = bool(getattr(event, "to_me", False))
    hit_name = any(n and n in text for n in _bot_names())

    # ⓪ 捂嘴：昵称紧挨触发词（固定搭配），必须先于任何触发逻辑处理
    #    对话模式开着 → 关掉并回一句确认；没开 → 静默忽略（不打扰）
    if _mute_hit(text):
        if _group_mode_active(gid):
            _GROUP_MODE.pop(gid, None)
            feature.logger.info(f"群 {gid} 被「捂嘴」关闭对话模式（{text!r}）")
            await group_chat.finish("好，我不说了～（想再聊就叫我一声）")
        return

    # ① 触发（艾特 或 昵称触发）：开启（或续期）本群对话模式
    triggered = to_me or (feature.cfg("nickname_trigger", True) and hit_name)
    if triggered:
        _touch_group(gid)
        how = "艾特" if to_me else "昵称"
        feature.logger.info(f"群 {gid} 的对话模式已开启/续期（{how}触发）")
        _buffer_add(gid, _need_id(event), _nickname(event), text)
        return

    # ② 未触发：只有对话模式有效期内才自动接话
    if not _group_mode_active(gid):
        return
    _touch_group(gid)
    # 内容回复一律走「合并窗口」：窗口内的多条消息合并成一轮回复，绝不丢消息
    _buffer_add(gid, _need_id(event), _nickname(event), text)


def _format_speaker(event: GroupMessageEvent, text: str) -> str:
    """群消息格式化为「昵称: 内容」。"""
    sender = getattr(event, "sender", None)
    nick = ""
    if sender is not None:
        nick = getattr(sender, "nickname", "") or getattr(sender, "card", "") or ""
    who = nick or _need_id(event)
    return f"{who}: {text}"


async def _group_talk(event: GroupMessageEvent, gid: str, text: str, *, triggered: bool) -> None:
    """群聊对话：从聊天流水读最近群消息 + 注入群记忆/发言者个人记忆。"""
    if not text:
        if triggered:
            await group_chat.finish("在的呀，想聊什么？")
        return
    preset = await _preset_for("group", gid)
    sender_id = _need_id(event)
    sender_name = _nickname(event)
    # 群上下文直接从 chat_message（聊天流水）读，不再维护内存缓存
    hist_block = await _chatlog_history_block(gid, sender_id, text)
    # 当前消息显式带上「昵称（QQ）」：QQ 号是唯一身份标识，昵称仅作展示
    current = f"{sender_name}（QQ {sender_id}）: {text}"
    content = f"{hist_block}\n\n【当前消息】{current}" if hist_block else current
    # 通过 extra 内部键把「当前发言者 QQ / 昵称」传给框架（身份锚 + 个人档案注入）
    try:
        reply = await feature.llm.ask(
            content,
            scope="group",
            scope_id=gid,
            preset=preset,
            extra={"_sender_id": sender_id, "_sender_name": sender_name},
        )
    except LLMError as exc:
        feature.logger.warning(f"群 {gid} 对话失败：{exc}")
        await group_chat.finish(f"LLM 暂时用不了：{exc}")
        return  # pragma: no cover - finish 不会返回，这里只为了静态分析
    await _reply(reply, matcher=group_chat)


async def _chatlog_history_block(gid: str, current_user_id: str, current_text: str) -> str:
    """从聊天流水（chat_message）读最近 N 条群消息，拼成氛围提示。

    自动剔除「当前这条」（event_preprocessor 已把它写进流水，避免重复注入）；
    每行标注 QQ 号，帮助模型把内容和发言人对应起来。
    """
    from src.core import chatlog

    limit = int(feature.cfg("group_history", 10) or 0)
    if limit <= 0:
        return ""
    try:
        msgs = await chatlog.recent_messages(gid, limit=limit + 1)
    except Exception as exc:  # noqa: BLE001 - 读历史失败不影响回复
        feature.logger.opt(exception=exc).warning(f"读群 {gid} 聊天流水失败")
        return ""
    if not msgs:
        return ""
    # 剔除当前这条（按 QQ + 文本精确匹配末尾一条）
    last = msgs[-1]
    if (
        str(last.get("user_id") or "") == current_user_id
        and (last.get("content") or "").strip() == current_text.strip()
    ):
        msgs = msgs[:-1]
    msgs = msgs[-limit:]
    lines = [
        f"{m.get('nickname') or m.get('user_id')}（QQ {m.get('user_id')}）：{m.get('content')}"
        for m in msgs
        if not m.get("is_bot")
    ]
    if not lines:
        return ""
    return "【最近的群聊记录（供你感知氛围，不要逐条复述）】\n" + "\n".join(lines)


# --------------------------------------------------------------------------- #
# %ai 命令（个人对话 + 群聊模式控制）
# --------------------------------------------------------------------------- #
@ai.handle()
async def _handle(event: MessageEvent, args: Message = CommandArg()) -> None:
    raw = args.extract_plain_text().strip()
    user_id = _need_id(event)
    group_id = _group_id(event)

    if not raw:
        await ai.finish(
            "用法：%ai <内容> ｜ %ai 群 <内容> ｜ %ai 流式 <内容>\n"
            "　　　%ai 印象 ｜ %ai 记住 <键> <值> ｜ %ai 忘记 [键]\n"
            "　　　%ai 重置 ｜ %ai 人设 [名字] ｜ %ai 状态\n"
            "群聊模式：艾特我一次后免艾特自动回复；%ai 停 关闭"
        )

    head, _, rest = raw.partition(" ")
    sub = head.strip().lower()
    rest = rest.strip()

    # ---- 群聊模式控制 ----
    if sub in {"停", "关", "stop", "结束"}:
        if not group_id:
            await ai.finish("这个命令要在群里用哦～")
        _GROUP_MODE.pop(group_id, None)
        await ai.finish("好哦，本群的对话模式已关闭（想再聊艾特我一下就行）")
    if sub in {"群状态", "群模式", "groupmode"}:
        if not group_id:
            await ai.finish("这个命令要在群里用哦～")
        if _group_mode_active(group_id):
            timeout = int(feature.cfg("group_timeout", 300) or 0)
            await ai.finish(
                f"本群对话模式进行中：免艾特自动回复已开启，冷却 "
                f"{int(feature.cfg('group_cooldown', 3) or 0)} 秒"
                + (f"，{timeout} 秒没人说话自动关闭" if timeout else "")
            )
        await ai.finish("本群对话模式未开启（艾特我一下就能开启）")

    if sub in {"状态", "status", "s"}:
        await _show_status(user_id)
    if sub in {"印象", "profile", "备忘"}:
        await _show_profile(user_id)
    if sub in {"记住", "remember", "记"}:
        await _remember(user_id, rest)
    if sub in {"忘记", "forget"}:
        await _forget(user_id, rest)
    if sub in {"重置", "reset", "清空"}:
        count = await feature.llm.clear_context("user", user_id)
        await ai.finish(f"已清空我的上下文（{count} 个会话）")
    if sub in {"人设", "preset"}:
        await _preset(user_id, rest)
    if sub in {"流式", "stream"}:
        await _talk(user_id, rest, stream=True)
    if sub in {"群", "group", "群聊"}:
        if not group_id:
            await ai.finish("「%ai 群」要在群里用哦～")
        await _talk(user_id, rest, scope="group", scope_id=group_id)
    # 其余情况把整段原文当对话内容（这样空格不会被吃掉）
    await _talk(user_id, raw)


async def _talk(
    user_id: str,
    text: str,
    *,
    scope: str = "user",
    scope_id: str = "",
    stream: bool = False,
) -> None:
    """统一对话入口：个人 / 群聊作用域，普通 / 流式。"""
    if not text:
        await ai.finish("想聊点什么呀？把内容写在 %ai 后面就好～")
    scope_id = scope_id or user_id
    left = _cooldown_left(f"{scope}:{scope_id}")
    if left:
        await ai.finish(f"慢一点点啦，{left} 秒之后再聊～")
    preset = await _preset_for(scope, scope_id)
    try:
        if stream:
            pieces = 0
            reply = ""
            # 显式要求流式（use_stream=True）：不受全局默认开关影响
            async for chunk in feature.llm.stream(
                text, scope=scope, scope_id=scope_id, preset=preset, use_stream=True
            ):
                pieces += 1
                reply = chunk.text or reply
            tip = f"\n\n（这次流式输出了 {pieces} 片）" if pieces > 1 else ""
            await _reply(f"{reply}{tip}")
        reply = await feature.llm.ask(text, scope=scope, scope_id=scope_id, preset=preset)
    except LLMError as exc:
        await ai.finish(f"LLM 暂时用不了：{exc}")
        return  # pragma: no cover - finish 不会返回
    await _reply(reply)


async def _show_status(user_id: str) -> None:
    ok = await feature.llm.available()
    active = next((p for p in await feature.llm.providers() if p.get("is_default")), None)
    info = await feature.llm.context("user", user_id)
    preset = await _preset_for("user", user_id)
    lines = [
        f"LLM 可用：{'是' if ok else '否（还没配服务商）'}",
        "服务商：{}｜模型：{}".format(
            (active or {}).get("name") or "（无）", (active or {}).get("model") or "（默认）"
        ),
        f"人设：{preset or '（全局默认）'}",
        f"我的上下文：{(info or {}).get('message_count', 0)} 条消息",
    ]
    await ai.finish("\n".join(lines))


async def _show_profile(user_id: str) -> None:
    prof = await feature.llm.profile(user_id)
    lines = [f"【印象】{prof.get('impression') or '（还没有印象，多聊几句吧）'}"]
    facts = prof.get("facts") or []
    if facts:
        lines.append("【关键信息】")
        for item in facts:
            tag = "（手动）" if item.get("source") == "manual" else ""
            lines.append(f"- {item.get('key')}{tag}：{item.get('value')}")
    lines.append(f"（已累计 {prof.get('message_count') or 0} 条消息）")
    await ai.finish("\n".join(lines))


async def _remember(user_id: str, rest: str) -> None:
    key, _, value = rest.partition(" ")
    key, value = key.strip(), value.strip()
    if not key or not value:
        await ai.finish("用法：%ai 记住 <键> <值>，比如：%ai 记住 称呼 白纸姐姐")
    await feature.llm.remember(user_id, key, value)
    await ai.finish(f"记下啦：{key} = {value}（这条不会被自动提炼覆盖）")


async def _forget(user_id: str, rest: str) -> None:
    key = rest.strip()
    removed = await feature.llm.forget(user_id, key)
    if key:
        await ai.finish(f"「{key}」已删除 {removed} 条" if removed else f"没找到「{key}」这条记录")
    await ai.finish(f"已清空你的全部关键信息与印象（共 {removed} 条）")


async def _preset(user_id: str, rest: str) -> None:
    name = rest.strip()
    if not name:
        current = await _preset_for("user", user_id)
        names = [p.get("name") for p in await feature.llm.presets()]
        await ai.finish(
            f"我目前的人设：{current or '（全局默认）'}\n"
            f"可用人设：{'、'.join(n for n in names if n) or '（无）'}\n"
            "想换的话：%ai 人设 <名字>"
        )
    try:
        await feature.llm.use_preset("user", user_id, name)
    except LLMError as exc:
        names = [p.get("name") for p in await feature.llm.presets()]
        await ai.finish(f"换不了：{exc}\n可用人设：{'、'.join(n for n in names if n) or '（无）'}")
    await ai.finish(f"好啦，我现在的设定是「{name}」")


@feature.on_load
async def _on_load() -> None:
    ok = await feature.llm.available()
    feature.logger.info(
        f"对话插件已就绪（LLM 可用={'是' if ok else '否'}，人设={feature.cfg('preset')!r}，"
        f"渲染={feature.cfg('render')}，冷却={feature.cfg('cooldown')}s，"
        f"群聊模式={'开' if feature.cfg('group_mode', True) else '关'}）"
    )


@feature.on_unload
async def _on_unload() -> None:
    _COOLDOWN.clear()
    _GROUP_MODE.clear()
    # 取消所有还在计时的合并窗口，避免任务泄漏
    for buf in _GROUP_BUFFER.values():
        task = buf.get("task")
        if task is not None and not task.done():
            task.cancel()
    _GROUP_BUFFER.clear()
    feature.logger.info("对话插件已卸载")
