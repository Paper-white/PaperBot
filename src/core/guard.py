"""消息输出守卫（开发测试期防误发）。

需求：开发测试阶段**锁死所有消息输出**，只允许发往白名单群（如 `123456789`），
杜绝不小心把消息发到别的群 / 私聊。

实现原理：
OneBot v11 的 `Bot` 通过 `__getattr__` 把 `bot.send_msg(...)` 之类的调用统一
转成 `self.call_api("send_msg", ...)`，因此**只需给 `call_api` 套一层壳**，
即可拦截所有对外 API 调用。

> 守卫开启且目标是群消息时：仅白名单群放行；
> 守卫开启且目标是私聊/其它群时：直接拦截并打 warning 日志。
"""

from __future__ import annotations

from typing import Any, Dict

import nonebot

from src.config import active_groups, settings, using_legacy_groups

logger = nonebot.logger

# 直接指定群目标的 API
_GROUP_TARGET_APIS = {
    "send_group_msg",
    "send_group_forward_msg",
    "send_group_notice",
    "upload_group_file",
}
# 直接指定私聊目标的 API
_PRIVATE_TARGET_APIS = {
    "send_private_msg",
    "send_private_forward_msg",
    "upload_private_file",
}
# 由 message_type / 参数自动判断目标的 API
_AUTO_TARGET_APIS = {"send_msg"}


def _is_allowed_group(group_id: Any) -> bool:
    if group_id is None:
        return False
    return str(group_id) in active_groups()


def is_api_allowed(api: str, data: Dict[str, Any]) -> bool:
    """判断某次 API 调用是否允许放行。"""
    if not settings.output_guard_enabled:
        return True

    if api in _GROUP_TARGET_APIS:
        return _is_allowed_group(data.get("group_id"))

    if api in _PRIVATE_TARGET_APIS:
        return False

    if api in _AUTO_TARGET_APIS:
        message_type = data.get("message_type")
        if message_type == "group" or (
            message_type is None and data.get("group_id") is not None
        ):
            return _is_allowed_group(data.get("group_id"))
        return False

    # 其它 API（get_* / delete_msg 等）一律放行
    return True


def install_output_guard() -> None:
    """给 OneBot v11 的 Bot.call_api 套上守卫（幂等）。"""
    from nonebot.adapters.onebot.v11 import Bot

    if getattr(Bot, "_paperbot_output_guard", False):
        return

    original_call_api = Bot.call_api

    async def guarded_call_api(self, api: str, **data: Any) -> Any:
        if is_api_allowed(api, data):
            return await original_call_api(self, api, **data)

        logger.warning(
            f"🚧 [输出守卫] 已拦截发往非白名单目标的 API：{api} | data={data!r}"
        )
        # 返回一个空响应，避免 await 结果出错
        return {}

    guarded_call_api.__name__ = "guarded_call_api"
    Bot.call_api = guarded_call_api  # type: ignore[method-assign]
    Bot._paperbot_output_guard = True  # type: ignore[attr-defined]

    if using_legacy_groups():
        logger.warning(
            "⚠️ 检测到已废弃的 OUTPUT_GUARD_GROUPS，请改名为 BOT_ACTIVE_GROUPS"
            "（门控默认策略与输出守卫已共用同一份群白名单）"
        )

    if settings.output_guard_enabled:
        groups = "、".join(sorted(active_groups())) or "（无 → 全部拦截）"
        logger.success(f"🛡️ 消息输出守卫已启用，仅允许发往群：{groups}；私聊一律拦截")
    else:
        logger.warning("⚠️ 消息输出守卫未启用（OUTPUT_GUARD_ENABLED=false），可向任意目标发消息！")

    if not settings.gate_default_enabled:
        groups = "、".join(sorted(active_groups())) or "（无 → 全群默认禁用）"
        logger.success(
            f"🚦 框架级门控默认策略：全局默认禁用，仅白名单群跟随插件默认值 → {groups}"
        )
