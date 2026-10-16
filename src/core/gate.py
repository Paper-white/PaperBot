"""门控规则（Rule）——总管理插件协控其它插件的「闸门」。

实现方式：每个功能插件在注册 matcher 时，都会被注入一条 `gate_rule`，
该规则在消息真正进入 handler **之前**被 NoneBot 求值；
求值时会去数据库查询该插件在当前「成员/群/全局」作用域下是否放行。
返回 False 则整条 matcher 被跳过，效果等同「插件被禁用」。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import nonebot
from nonebot.adapters import Bot, Event

from src.core import registry
from src.core.database import get_session
from src.core.permission import PermLevel, event_role, resolve_level
from src.core.service import resolve_enabled

logger = nonebot.logger


def _output_reachable(group_id: Optional[str]) -> bool:
    """按「消息输出守卫」判断该目标能否收到消息（用来决定要不要运行插件）。

    与 `guard.is_api_allowed` **复用同一套规则**（白名单唯一来源），避免两处判断漂移：
    - 守卫关闭 → 一律可达（放行）；
    - 守卫开启 → 群须在 `BOT_ACTIVE_GROUPS`、私聊一律不可达。
    """
    from src.core import guard

    if group_id is None:
        return guard.is_api_allowed("send_private_msg", {})
    return guard.is_api_allowed("send_group_msg", {"group_id": group_id})


async def is_feature_enabled(feature_name: str, event: Any) -> bool:
    """判断某功能插件在当前事件下是否放行。

    整轮判定**复用同一个只读 session**（权限 + 门控一次开库），
    避免每条消息多次建连 / 多次事务。
    """
    group_id = getattr(event, "group_id", None)
    group_str = str(group_id) if group_id is not None else None

    # ⓪ 输出守卫语义：消息最终发不出去的目标，插件一律不运行。
    #    ⚠️ 以前守卫只在「发送消息」那一步拦，导致插件（尤其会调 LLM 的）已经跑完、
    #    额度已经花掉，消息才被丢弃。这里提前拦截，做到「发不出去 = 不运行」。
    if not _output_reachable(group_str):
        return False

    try:
        user_id = event.get_user_id()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"门控：取 user_id 失败（{exc!r}），按匿名处理")
        user_id = None

    feature = registry.get(feature_name)

    async with get_session(readonly=True) as session:
        # ① 黑名单（0 级）一票否决：任何插件（含受保护插件）都不回应
        level = await resolve_level(
            user_id, group_id=group_str, role=event_role(event), session=session
        )
        if level == PermLevel.BLACKLIST:
            return False

        # ② 未注册的插件不做拦截（放行）；受保护插件永远放行
        if feature is None:
            return True
        if feature.protected:
            return True

        return await resolve_enabled(
            feature_name,
            user_id=str(user_id) if user_id else None,
            group_id=group_str,
            default=feature.default_enabled,
            session=session,
        )


def gate_rule(feature_name: str) -> Callable[..., Any]:
    """生成绑定到某个功能插件的 RuleChecker。"""

    async def _checker(bot: Bot, event: Event) -> bool:
        return await is_feature_enabled(feature_name, event)

    _checker.__name__ = f"gate_{feature_name}"
    return _checker
