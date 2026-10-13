"""示例插件：chat（AI 人设对话）。

演示框架的**统一 LLM 服务**（`src/core/llm.py`）怎么用——插件侧只需要 `feature.llm`：

```
%ai <内容>          与 AI 对话（个人上下文 + 长期记忆）
%ai 群 <内容>       群聊作用域对话（群共享上下文，不注入个人记忆）
%ai 流式 <内容>     流式生成演示（会告诉你流了几片）
%ai 印象            看看 AI 眼中的我（印象 + 关键信息）
%ai 记住 <键> <值>  手工记一条关键信息（不会被自动提炼覆盖）
%ai 忘记 [键]       删掉一条 / 全部关键信息
%ai 重置            清空我的对话上下文
%ai 人设 [名字]     查看 / 切换我的人设
%ai 状态            LLM 可用性 / 服务商 / 人设 / 上下文条数
```

顺带演示：插件级配置项（`config_schema` → WebUI 自动出表单）、
统一 logger、`on_load / on_unload` 钩子、以及渲染核心（`render=true` 时把回复画成图）。
"""

from __future__ import annotations

import time

from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg

from src.core.feature import Feature
from src.core.llm import LLMError
from src.core.models import LEVEL_USER

feature = Feature(
    "paperbot_chat",
    display_name="AI 对话",
    description="基于统一 LLM 服务的人设对话（个人/群上下文、长期记忆、流式）",
    author="paper_white",
    repo="https://github.com/paper-white/PaperBot",
    version="0.1.0",
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
            "label": "每人冷却（秒）",
            "type": "int",
            "default": 3,
            "hint": "防刷屏；0 = 不限制",
        },
    ],
)

# 命令实际权限由框架维护（command_rule 表），可在 WebUI「命令管理」里改，改完即时生效。
ai = feature.command(
    "ai",
    aliases={"AI", "聊"},
    priority=40,
    level=LEVEL_USER,
    description="与 AI 人设对话（个人上下文 + 长期记忆）",
)

# 冷却记录：{用户ID: 上次调用时间}
_COOLDOWN: dict[str, float] = {}


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


async def _preset_for(scope: str, scope_id: str) -> str:
    """会话里存的人设优先 → 插件配置 → 空（= 全局默认）。"""
    info = await feature.llm.context(scope, scope_id)
    if info and (info.get("preset_name") or "").strip():
        return str(info["preset_name"]).strip()
    return str(feature.cfg("preset", "") or "").strip()


async def _reply(text: str) -> None:
    """按配置回复（截断 / 渲染成图），最后必定 finish。"""
    text = (text or "").strip() or "（AI 这次什么都没说…）"
    limit = int(feature.cfg("max_chars", 1200) or 0)
    if limit > 0 and len(text) > limit:
        text = text[:limit] + "…"
    if feature.cfg("render", False):
        try:
            from src.core.render import render_text

            png = await render_text(text, header=feature.display_name)
            await ai.finish(MessageSegment.image(png))
        except Exception as exc:  # noqa: BLE001 - 渲染失败不该把回复吞了
            feature.logger.opt(exception=exc).warning("渲染失败，退回纯文本发送")
    await ai.finish(text)


@ai.handle()
async def _handle(event: MessageEvent, args: Message = CommandArg()) -> None:
    raw = args.extract_plain_text().strip()
    user_id = _need_id(event)
    group_id = _group_id(event)

    if not raw:
        await ai.finish(
            "用法：%ai <内容> ｜ %ai 群 <内容> ｜ %ai 流式 <内容>\n"
            "　　　%ai 印象 ｜ %ai 记住 <键> <值> ｜ %ai 忘记 [键]\n"
            "　　　%ai 重置 ｜ %ai 人设 [名字] ｜ %ai 状态"
        )

    head, _, rest = raw.partition(" ")
    sub = head.strip().lower()
    rest = rest.strip()

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
        f"渲染={feature.cfg('render')}，冷却={feature.cfg('cooldown')}s）"
    )


@feature.on_unload
async def _on_unload() -> None:
    _COOLDOWN.clear()
    feature.logger.info("对话插件已卸载")
