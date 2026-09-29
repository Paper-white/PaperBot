"""白纸bot总控插件（Paperbot_CONSOLE）。

统一管控其它功能插件的启停，支持三级作用域（就近优先：成员 > 群 > 全局）：
- 全局禁用 / 启用
- 分群禁用 / 启用
- 分成员黑白名单（`用户:<QQ>`）

命令（前缀 `%`，需超级用户权限）::

    %help                    查看帮助
    %list                    列出所有插件及全局开关
    %status <插件名>         查看该插件在当前会话下的放行状态
    %on  <插件名> [作用域]   放行
    %off <插件名> [作用域]   拦截
    %reset <插件名> [作用域] 清除该作用域规则

作用域写法（可省略，默认「群聊→本群 / 私聊→全局」）：
    (省略)          当前会话作用域
    全局            全局
    本群            当前群
    群:123456       指定群
    用户:10001      指定成员（白名单=on / 黑名单=off）
"""

from __future__ import annotations

from typing import Optional

from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from src.core import registry, service
from src.core.feature import Feature
from src.core.models import SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_MEMBER

# 本插件的注册信息（注册名 / 中文名）
feature = Feature(
    "Paperbot_CONSOLE",
    display_name="白纸bot总控插件",
    description="白纸bot 的总控台：统一管控其它功能插件的启停（全局 / 分群 / 分成员黑白名单）",
    author="paper_white",
    version="0.1.0",
    protected=True,  # 总控台自己永远放行
)

help_cmd = feature.command("help", aliases={"帮助", "菜单"}, permission=SUPERUSER, priority=1)
list_cmd = feature.command("list", aliases={"列表", "插件列表"}, permission=SUPERUSER, priority=1)
status_cmd = feature.command("status", aliases={"状态"}, permission=SUPERUSER, priority=1)
on_cmd = feature.command("on", aliases={"开", "启用"}, permission=SUPERUSER, priority=1)
off_cmd = feature.command("off", aliases={"关", "禁用"}, permission=SUPERUSER, priority=1)
reset_cmd = feature.command("reset", aliases={"重置"}, permission=SUPERUSER, priority=1)

HELP_TEXT = (
    "🧭 白纸bot总控台（Paperbot_CONSOLE）\n"
    "──────────────\n"
    "%help                  本帮助\n"
    "%list                  列出全部插件\n"
    "%status <名>           查看当前会话状态\n"
    "%on <名> [作用域]      放行\n"
    "%off <名> [作用域]     拦截\n"
    "%reset <名> [作用域]   清除该作用域规则\n"
    "\n作用域：[省略]=当前 | 全局 | 本群 | 群:号 | 用户:号"
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


def _parse_scope(token: Optional[str], event: MessageEvent) -> tuple[str, str]:
    """解析作用域 token → (scope_type, scope_id)。"""
    gid = getattr(event, "group_id", None)
    token = (token or "").strip()

    if token in ("", "当前", "默认"):
        if gid is not None:
            return SCOPE_GROUP, str(gid)
        return SCOPE_GLOBAL, ""

    if token in ("全局", "global", "g"):
        return SCOPE_GLOBAL, ""
    if token in ("本群", "群", "group"):
        if gid is None:
            raise ValueError("当前不是群聊，无法使用「本群」作用域")
        return SCOPE_GROUP, str(gid)
    if token.startswith("群:"):
        sid = token.split(":", 1)[1].strip()
        if not sid:
            raise ValueError("群号不能为空")
        return SCOPE_GROUP, sid
    if token.startswith("用户:"):
        sid = token.split(":", 1)[1].strip()
        if not sid:
            raise ValueError("用户 QQ 不能为空")
        return SCOPE_MEMBER, sid

    raise ValueError(f"无法识别的作用域：{token}（可用：全局 / 本群 / 群:号 / 用户:号）")


def _require_plugin(name: str) -> str:
    if not name:
        raise ValueError("请提供插件名")
    if not registry.has(name):
        raise ValueError(f"没有名为「{name}」的插件")
    return name


# --------------------------------------------------------------------------- #
# 命令实现
# --------------------------------------------------------------------------- #
@help_cmd.handle()
async def _handle_help() -> None:
    await help_cmd.finish(HELP_TEXT)


@list_cmd.handle()
async def _handle_list() -> None:
    await list_cmd.finish(await _render_list())


@status_cmd.handle()
async def _handle_status(event: MessageEvent, args: Message = CommandArg()) -> None:
    name = args.extract_plain_text().strip()
    try:
        _require_plugin(name)
    except ValueError as exc:
        await status_cmd.finish(f"{_ERR} {exc}")
    await status_cmd.finish(await _render_status(name, event))


@on_cmd.handle()
async def _handle_on(event: MessageEvent, args: Message = CommandArg()) -> None:
    await _apply_from_args(on_cmd, event, args, enabled=True)


@off_cmd.handle()
async def _handle_off(event: MessageEvent, args: Message = CommandArg()) -> None:
    await _apply_from_args(off_cmd, event, args, enabled=False)


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


# --------------------------------------------------------------------------- #
# 渲染 / 应用
# --------------------------------------------------------------------------- #
async def _render_list() -> str:
    overview = await service.plugin_overview()
    if not overview:
        return "（还没有任何功能插件）"
    lines = ["📦 功能插件一览", "──────────────"]
    for item in overview:
        flag = "🟢" if item["global_enabled"] else "🔴"
        lock = "🔒" if item["protected"] else "  "
        desc = f" — {item['description']}" if item["description"] else ""
        lines.append(f"{flag}{lock} {item['name']}{desc}")
    lines.append("\n用「%status <名>」查看详情")
    return "\n".join(lines)


async def _render_status(name: str, event: MessageEvent) -> str:
    feat = registry.get(name)
    assert feat is not None
    user_id = event.get_user_id()
    gid = getattr(event, "group_id", None)

    if feat.protected:
        return f"🔒 {name} 是受保护插件，永远放行。"

    allowed = await service.resolve_enabled(
        name,
        user_id=str(user_id),
        group_id=str(gid) if gid is not None else None,
        default=feat.default_enabled,
    )
    rules = await service.list_rules(name)
    lines = [
        f"🔍 {name}（{feat.display_name}）",
        f"当前会话：{'🟢 放行' if allowed else '🔴 拦截'}",
        f"默认开关：{'开' if feat.default_enabled else '关'}",
    ]
    if rules:
        lines.append("已配置规则：")
        for r in rules:
            mark = "🟢" if r.enabled else "🔴"
            lines.append(f"  {mark} {_scope_label(r.scope_type, r.scope_id)}")
    else:
        lines.append("（暂无自定义规则）")
    return "\n".join(lines)


async def _apply_from_args(matcher, event: MessageEvent, args: Message, enabled: bool) -> None:
    tokens = args.extract_plain_text().split()
    try:
        name = _require_plugin(tokens[0] if tokens else "")
        scope_type, scope_id = _parse_scope(tokens[1] if len(tokens) > 1 else None, event)
    except ValueError as exc:
        await matcher.finish(f"{_ERR} {exc}")

    feat = registry.get(name)
    if feat is not None and feat.protected:
        await matcher.finish(f"{_ERR} {name} 是受保护插件，不允许禁用。")

    await service.set_rule(
        name, scope_type, scope_id, enabled, updated_by=event.get_user_id()
    )
    verb = "放行" if enabled else "拦截"
    await matcher.finish(
        f"{_OK} 已在 {_scope_label(scope_type, scope_id)} 将「{name}」设为{verb}"
    )
