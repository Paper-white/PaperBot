"""白纸bot总控插件（Paperbot_CONSOLE）。

统一管控其它功能插件的启停 + 用户权限，支持三级作用域
（就近优先：成员 > 群 > 全局）与四级权限（0 黑名单 / 1 普通 / 2 群管 / 3 所有者）。

命令（前缀 `%`）::

    %help [插件名]            无参：列出当前已启用的插件；带参：看某插件的命令与介绍
    %on  <插件名> [群号]      在对应群启用插件（省略=本群；群号填 all 则全局启用）  【所有者】
    %off <插件名> [群号]      在对应群禁用插件（省略=本群；群号填 all 则全局禁用）  【所有者】
    %plugin <插件名>          查看该插件在全局 / 各群的开关状态                    【所有者】
    %admin add <QQ> [群号]    授予该用户 2 级管理员权限（仅在对应群生效）          【所有者】
    %admin del <QQ> [群号]    移除手动授予的管理员权限                            【所有者】
    %reset <插件名> [作用域]  清除某作用域规则（额外提供）                         【所有者】

「所有者」= `.env` 的 `SUPERUSERS`。
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from src.core import permission as perm
from src.core import registry, service
from src.core.feature import Feature
from src.core.models import (
    LEVEL_ADMIN,
    LEVEL_BLACKLIST,
    SCOPE_GLOBAL,
    SCOPE_GROUP,
    SCOPE_MEMBER,
)

# 本插件的注册信息（注册名 / 中文名）
feature = Feature(
    "Paperbot_CONSOLE",
    display_name="白纸bot总控插件",
    description="白纸bot 的总控台：管控插件启停（全局/分群/分成员）与用户权限（四级）",
    author="paper_white",
    version="0.2.0",
    protected=True,  # 总控台自己永远放行
)

# %help 对所有（非黑名单）用户开放；其余命令需所有者权限
help_cmd = feature.command(
    "help", aliases={"帮助", "菜单", "列表", "list"}, priority=1
)
on_cmd = feature.command(
    "on", aliases={"开", "启用"}, permission=SUPERUSER, priority=1
)
off_cmd = feature.command(
    "off", aliases={"关", "禁用"}, permission=SUPERUSER, priority=1
)
plugin_cmd = feature.command(
    "plugin", aliases={"插件", "状态", "status"}, permission=SUPERUSER, priority=1
)
admin_cmd = feature.command(
    "admin", aliases={"管理员", "权限"}, permission=SUPERUSER, priority=1
)
reset_cmd = feature.command(
    "reset", aliases={"重置"}, permission=SUPERUSER, priority=1
)

ADMIN_HELP = (
    "🛡️ 权限管理（需所有者）\n"
    "%admin add <QQ> [群号]    授予 2 级管理员（仅对应群）\n"
    "%admin del <QQ> [群号]    移除手动授予的管理员\n"
    "%admin black <QQ>         加入全局黑名单（0 级，额外）\n"
    "%admin unblack <QQ>       移出全局黑名单（额外）\n"
    "[群号] 省略则使用当前群"
)

_OK, _ERR = "✅", "❌"


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _scope_label(scope_type: str, scope_id: str) -> str:
    if scope_type == SCOPE_GLOBAL:
        return "全局"
    if scope_type == SCOPE_GROUP:
        return f"群[{scope_id}]"
    if scope_type == SCOPE_MEMBER:
        return f"成员[{scope_id}]"
    return f"{scope_type}:{scope_id}"


def _require_plugin(name: str) -> str:
    if not name:
        raise ValueError("请提供插件名")
    if not registry.has(name):
        raise ValueError(f"没有名为「{name}」的插件")
    return name


def _parse_target(token, event: MessageEvent, *, default_global: bool = False):
    """解析「群号 / all」目标 → (scope_type, scope_id)。"""
    gid = getattr(event, "group_id", None)
    t = (token or "").strip()

    if t in ("", "当前", "默认", "本群"):
        if gid is not None:
            return SCOPE_GROUP, str(gid)
        if default_global:
            return SCOPE_GLOBAL, ""
        raise ValueError("当前不是群聊，请指定群号，或用 all 表示全局")
    if t in ("all", "全部", "全局", "global", "g"):
        return SCOPE_GLOBAL, ""
    if t.startswith("群:"):
        t = t.split(":", 1)[1].strip()
    if t.isdigit():
        return SCOPE_GROUP, t
    raise ValueError(f"无法识别的目标：{token}（可用：省略=本群 / all=全局 / 群号）")


def _parse_scope(token, event: MessageEvent):
    """%reset 用：支持 全局 / 本群 / 群:号 / 用户:号。"""
    gid = getattr(event, "group_id", None)
    t = (token or "").strip()
    if t in ("", "当前", "默认"):
        if gid is not None:
            return SCOPE_GROUP, str(gid)
        return SCOPE_GLOBAL, ""
    if t in ("全局", "global", "g", "all"):
        return SCOPE_GLOBAL, ""
    if t in ("本群", "群", "group"):
        if gid is None:
            raise ValueError("当前不是群聊，无法使用「本群」")
        return SCOPE_GROUP, str(gid)
    if t.startswith("群:"):
        sid = t.split(":", 1)[1].strip()
        if not sid:
            raise ValueError("群号不能为空")
        return SCOPE_GROUP, sid
    if t.startswith("用户:"):
        sid = t.split(":", 1)[1].strip()
        if not sid:
            raise ValueError("用户 QQ 不能为空")
        return SCOPE_MEMBER, sid
    raise ValueError(f"无法识别的作用域：{token}（可用：全局 / 本群 / 群:号 / 用户:号）")


def _resolve_group(token: str, event: MessageEvent):
    """%admin 用：解析群号，省略则取当前群。"""
    if token:
        return token.strip()
    gid = getattr(event, "group_id", None)
    return str(gid) if gid is not None else None


async def _render_help_list(event: MessageEvent) -> str:
    user_id = event.get_user_id()
    gid = getattr(event, "group_id", None)
    names = await service.enabled_plugin_names(
        str(user_id), str(gid) if gid is not None else None
    )
    if not names:
        return "（当前没有已启用的插件）"
    lines = ["🧩 已启用的插件", "──────────────"]
    for n in names:
        feat = registry.get(n)
        lines.append(f"· {n}（{feat.display_name}）")
    lines.append("")
    lines.append("用「%help <插件名>」查看某插件的命令与介绍")
    return "\n".join(lines)


async def _render_plugin_help(name: str, event: MessageEvent) -> str:
    feat = registry.get(name)
    assert feat is not None
    lines = [f"🧩 {feat.display_name}（{name}） v{feat.version}"]
    if feat.description:
        lines.append(feat.description)
    lines.append("──────────────")
    if feat.commands:
        lines.append("命令：")
        for c in feat.commands:
            cmds = " / ".join(f"%{x}" for x in c["commands"])
            if c["aliases"]:
                alias = "（别名：" + "、".join(f"%{a}" for a in c["aliases"]) + "）"
            else:
                alias = ""
            lines.append(f"  {cmds}{alias}")
    else:
        lines.append("（该插件暂无可展示的命令）")

    if feat.protected:
        lines.append("\n🔒 受保护插件，永远启用")
    else:
        user_id = event.get_user_id()
        gid = getattr(event, "group_id", None)
        allowed = await service.resolve_enabled(
            name,
            user_id=str(user_id),
            group_id=str(gid) if gid is not None else None,
            default=feat.default_enabled,
        )
        lines.append(
            f"\n当前会话：{'🟢 已启用' if allowed else '🔴 已禁用'}"
            f"｜默认：{'开' if feat.default_enabled else '关'}"
        )
    return "\n".join(lines)


async def _render_plugin_status(name: str) -> str:
    feat = registry.get(name)
    assert feat is not None
    rules = await service.list_rules(name)
    lines = [
        f"🧩 {name}（{feat.display_name}）",
        f"默认开关：{'开' if feat.default_enabled else '关'}",
    ]
    if feat.protected:
        lines.append("🔒 受保护插件，永远放行")
    global_rule = next((r for r in rules if r.scope_type == SCOPE_GLOBAL), None)
    if global_rule is not None:
        lines.append(f"全局：{'🟢 启用' if global_rule.enabled else '🔴 禁用'}")
    else:
        lines.append(f"全局：（默认：{'开' if feat.default_enabled else '关'}）")

    group_rules = sorted(
        (r for r in rules if r.scope_type == SCOPE_GROUP), key=lambda x: x.scope_id
    )
    if group_rules:
        lines.append("分群规则：")
        for r in group_rules:
            lines.append(f"  群[{r.scope_id}]：{'🟢 启用' if r.enabled else '🔴 禁用'}")
    else:
        lines.append("分群规则：（无）")

    member_rules = [r for r in rules if r.scope_type == SCOPE_MEMBER]
    if member_rules:
        lines.append("分成员规则：")
        for r in member_rules:
            lines.append(
                f"  成员[{r.scope_id}]：{'🟢 白名单' if r.enabled else '🔴 黑名单'}"
            )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 命令处理
# --------------------------------------------------------------------------- #
@help_cmd.handle()
async def _handle_help(event: MessageEvent, args: Message = CommandArg()) -> None:
    name = args.extract_plain_text().strip()
    if not name:
        await help_cmd.finish(await _render_help_list(event))
    if not registry.has(name):
        await help_cmd.finish(f"{_ERR} 没有名为「{name}」的插件")
    await help_cmd.finish(await _render_plugin_help(name, event))


@on_cmd.handle()
async def _handle_on(event: MessageEvent, args: Message = CommandArg()) -> None:
    await _apply_toggle(on_cmd, event, args, enabled=True)


@off_cmd.handle()
async def _handle_off(event: MessageEvent, args: Message = CommandArg()) -> None:
    await _apply_toggle(off_cmd, event, args, enabled=False)


@plugin_cmd.handle()
async def _handle_plugin(event: MessageEvent, args: Message = CommandArg()) -> None:
    name = args.extract_plain_text().strip()
    try:
        _require_plugin(name)
    except ValueError as exc:
        await plugin_cmd.finish(f"{_ERR} {exc}")
    await plugin_cmd.finish(await _render_plugin_status(name))


@admin_cmd.handle()
async def _handle_admin(event: MessageEvent, args: Message = CommandArg()) -> None:
    tokens = args.extract_plain_text().split()
    sub = tokens[0].lower() if tokens else ""
    operator = event.get_user_id()

    if sub in ("add", "加", "增加", "+"):
        qq = tokens[1] if len(tokens) > 1 else ""
        if not qq.isdigit():
            await admin_cmd.finish(f"{_ERR} 用法：%admin add <QQ> [群号]")
        group = _resolve_group(tokens[2] if len(tokens) > 2 else "", event)
        if not group:
            await admin_cmd.finish(f"{_ERR} 当前不在群聊，请指定群号：%admin add <QQ> <群号>")
        await perm.set_level(qq, LEVEL_ADMIN, group_id=group, updated_by=operator)
        await admin_cmd.finish(f"{_OK} 已授予 {qq} 在 群[{group}] 的管理员权限（2级）")

    if sub in ("del", "删", "移除", "-", "delete"):
        qq = tokens[1] if len(tokens) > 1 else ""
        if not qq.isdigit():
            await admin_cmd.finish(f"{_ERR} 用法：%admin del <QQ> [群号]")
        group = _resolve_group(tokens[2] if len(tokens) > 2 else "", event)
        if not group:
            await admin_cmd.finish(f"{_ERR} 当前不在群聊，请指定群号：%admin del <QQ> <群号>")
        removed = await perm.clear_level(qq, group_id=group)
        if removed:
            await admin_cmd.finish(f"{_OK} 已移除 {qq} 在 群[{group}] 的手动授权")
        await admin_cmd.finish(f"ℹ️ {qq} 在 群[{group}] 本来就没有手动授权")

    if sub in ("black", "拉黑", "ban"):
        qq = tokens[1] if len(tokens) > 1 else ""
        if not qq.isdigit():
            await admin_cmd.finish(f"{_ERR} 用法：%admin black <QQ>")
        await perm.set_level(qq, LEVEL_BLACKLIST, group_id=None, updated_by=operator)
        await admin_cmd.finish(f"{_OK} 已将 {qq} 加入全局黑名单（0级，不再回应任何消息）")

    if sub in ("unblack", "解除拉黑", "unban"):
        qq = tokens[1] if len(tokens) > 1 else ""
        if not qq.isdigit():
            await admin_cmd.finish(f"{_ERR} 用法：%admin unblack <QQ>")
        removed = await perm.clear_level(qq, group_id=None)
        if removed:
            await admin_cmd.finish(f"{_OK} 已将 {qq} 移出全局黑名单")
        await admin_cmd.finish(f"ℹ️ {qq} 本来就不在黑名单里")

    await admin_cmd.finish(ADMIN_HELP)


@reset_cmd.handle()
async def _handle_reset(event: MessageEvent, args: Message = CommandArg()) -> None:
    tokens = args.extract_plain_text().split()
    try:
        name = _require_plugin(tokens[0] if tokens else "")
        scope_type, scope_id = _parse_scope(tokens[1] if len(tokens) > 1 else None, event)
    except ValueError as exc:
        await reset_cmd.finish(f"{_ERR} {exc}")

    label = _scope_label(scope_type, scope_id)
    removed = await service.clear_rule(name, scope_type, scope_id)
    if removed:
        await reset_cmd.finish(f"{_OK} 已清除「{name}」在 {label} 的规则")
    await reset_cmd.finish(f"ℹ️ 「{name}」在 {label} 本来就没有规则")


async def _apply_toggle(matcher, event: MessageEvent, args: Message, enabled: bool) -> None:
    tokens = args.extract_plain_text().split()
    try:
        name = _require_plugin(tokens[0] if tokens else "")
        scope_type, scope_id = _parse_target(
            tokens[1] if len(tokens) > 1 else None, event
        )
    except ValueError as exc:
        await matcher.finish(f"{_ERR} {exc}")

    feat = registry.get(name)
    if feat is not None and feat.protected:
        await matcher.finish(f"{_ERR} {name} 是受保护插件，不允许禁用")

    await service.set_rule(
        name, scope_type, scope_id, enabled, updated_by=event.get_user_id()
    )
    verb = "启用" if enabled else "禁用"
    await matcher.finish(f"{_OK} 已在 {_scope_label(scope_type, scope_id)} {verb}「{name}」")
