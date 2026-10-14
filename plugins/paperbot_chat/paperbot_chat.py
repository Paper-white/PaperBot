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
            "label": "群自动回复冷却（秒）",
            "type": "int",
            "default": 3,
            "hint": "对话模式下连续回复的最小间隔，防刷屏",
        },
        {
            "key": "group_history",
            "label": "注入的群聊历史条数",
            "type": "int",
            "default": 10,
            "hint": "把群里最近的 N 条消息拼进上下文，让回复更懂氛围；0 = 不注入",
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
# 群聊历史缓存：{群号: [最近的群消息文本, ...]}
_GROUP_HISTORY: dict[str, list[str]] = {}


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


def _group_cooldown_left(gid: str) -> int:
    """群对话模式的自动回复冷却。"""
    wait = int(feature.cfg("group_cooldown", 3) or 0)
    mode = _GROUP_MODE.setdefault(gid, {"active": 0.0, "reply": 0.0})
    if wait <= 0:
        mode["reply"] = time.monotonic()
        return 0
    now = time.monotonic()
    left = int(mode["reply"] + wait - now) + 1
    if left > 0 and now < mode["reply"] + wait:
        return left
    mode["reply"] = now
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
        _GROUP_HISTORY.pop(gid, None)
        return False
    return True


def _touch_group(gid: str) -> None:
    """刷新群对话模式的活跃时间（进入 / 有人说话时调用）。"""
    mode = _GROUP_MODE.setdefault(gid, {"active": 0.0, "reply": 0.0})
    mode["active"] = time.monotonic()


def _push_group_history(gid: str, text: str) -> None:
    limit = int(feature.cfg("group_history", 10) or 0)
    if limit <= 0:
        return
    if not text:
        return
    hist = _GROUP_HISTORY.setdefault(gid, [])
    hist.append(text)
    if len(hist) > limit:
        del hist[: len(hist) - limit]


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
            await matcher.finish(MessageSegment.image(png))
        except Exception as exc:  # noqa: BLE001 - 渲染失败不该把回复吞了
            feature.logger.opt(exception=exc).warning("渲染失败，退回纯文本发送")
    await matcher.finish(text)


def _group_history_block(gid: str) -> str:
    """把缓存里的群聊历史拼成一段提示（给 LLM 感知群聊氛围）。"""
    hist = _GROUP_HISTORY.get(gid, [])
    if not hist:
        return ""
    return "【最近的群聊记录（供你感知氛围，不要逐条复述）】\n" + "\n".join(f"- {h}" for h in hist)


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

    # ① 艾特触发：开启（或续期）本群对话模式
    if to_me:
        _touch_group(gid)
        _push_group_history(gid, _format_speaker(event, text) if text else "")
        feature.logger.info(f"群 {gid} 的对话模式已开启/续期（@触发）")
        left = _group_cooldown_left(gid)
        if left:
            await group_chat.finish(f"在啦在啦～（{left} 秒后再找我哦）")
        await _group_talk(event, gid, text, triggered=True)
        return

    # ② 未艾特：只有对话模式有效期内才自动接话
    if not _group_mode_active(gid):
        return
    _touch_group(gid)
    _push_group_history(gid, _format_speaker(event, text) if text else "")
    left = _group_cooldown_left(gid)
    if left:
        return  # 冷却中，不刷屏
    await _group_talk(event, gid, text, triggered=False)


def _format_speaker(event: GroupMessageEvent, text: str) -> str:
    """群消息格式化为「昵称: 内容」。"""
    sender = getattr(event, "sender", None)
    nick = ""
    if sender is not None:
        nick = getattr(sender, "nickname", "") or getattr(sender, "card", "") or ""
    who = nick or _need_id(event)
    return f"{who}: {text}"


async def _group_talk(event: GroupMessageEvent, gid: str, text: str, *, triggered: bool) -> None:
    """群聊对话：群共享上下文 + 注入最近群聊历史。"""
    if not text:
        if triggered:
            await group_chat.finish("在的呀，想聊什么？")
        return
    preset = await _preset_for("group", gid)
    hist_block = _group_history_block(gid)
    content = f"{hist_block}\n\n【当前消息】{_format_speaker(event, text)}" if hist_block else text
    try:
        reply = await feature.llm.ask(
            content, scope="group", scope_id=gid, preset=preset
        )
    except LLMError as exc:
        feature.logger.warning(f"群 {gid} 对话失败：{exc}")
        await group_chat.finish(f"LLM 暂时用不了：{exc}")
        return  # pragma: no cover - finish 不会返回，这里只为了静态分析
    await _reply(reply, matcher=group_chat)


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
        _GROUP_HISTORY.pop(group_id, None)
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
    _GROUP_HISTORY.clear()
    feature.logger.info("对话插件已卸载")
