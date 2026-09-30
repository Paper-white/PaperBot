"""框架级「命令权限」Permission。

设计原则（白纸姐姐的需求）：

- 插件**只负责声明默认权限**（`Feature.command("xxx", level=1)`）和命令介绍；
- **实际权限由框架维护**——存在 `command_rule` 表里，可以在 WebUI 命令管理页随时改，
  改完**立即生效**（每次事件实时查表），不用重启、也不用改插件代码；
- 删除覆盖记录即「恢复默认」。

用法上插件**无感**：`Feature.command()` 会自动把本类挂到 matcher 的 permission 上，
用户等级不足时整条命令静默跳过（与 NoneBot 原生权限行为一致）。

⚠️ 实现注意：NoneBot 的 `Permission` 带 `__slots__`，且 `__and__` / 多 checker 都是
**OR** 语义、`&` 直接抛 RuntimeError。所以本类只做「单一 checker」：
把校验逻辑作为 `self._check` 传给基类 `super().__init__(...)`，
从而既能被 `|` 组合，又不会踩 `checkers` 缺失的坑（2026-09-30 踩过）。
"""

from __future__ import annotations

import nonebot
from nonebot.adapters import Bot, Event
from nonebot.permission import Permission
from nonebot.rule import TrieRule

from src.core.permission import event_role, resolve_level
from src.core.service import resolve_command_level

logger = nonebot.logger


class CommandPermission(Permission):
    """按「用户等级 >= 命令所需等级」放行。"""

    __slots__ = ("plugin_name", "command_name", "default_level")

    def __init__(
        self, plugin_name: str, command_name: str, default_level: int = 1
    ) -> None:
        self.plugin_name = plugin_name
        self.command_name = command_name
        self.default_level = int(default_level)
        # 关键：把 checker 交给基类，基类会包成 Dependent[bool]
        super().__init__(self._check)

    @staticmethod
    def _looks_like_command(bot: Bot, event: Event) -> bool:
        """用框架自己的 Command Trie 判断「这条消息是不是命令」。

        ⚠️ 性能关键：NoneBot 校验顺序是 **先 permission 再 rule**（`message._check_matcher`），
        所以每条群消息都会把所有命令 matcher 的 permission 跑一遍——
        若无脑查库，一个活跃群每秒就是十几次无谓查询，日志也会被刷屏。
        这里直接复用 `TrieRule.get_value()`（`on_command` 认命令用的同一套 Trie），
        非命令消息立刻放行，交给 matcher 的 rule 去拒绝。
        解析异常时**保守地按命令处理**（宁可多查一次库，也不能漏判权限）。
        """
        try:
            result = TrieRule.get_value(bot, event, {})
        except Exception:  # noqa: BLE001
            return True
        return result.get("command") is not None

    async def _check(self, bot: Bot, event: Event) -> bool:
        """实际校验：查用户等级 + 查命令所需等级（实时读库）。"""
        if not self._looks_like_command(bot, event):
            return True

        try:
            user_id = event.get_user_id()
        except Exception:  # noqa: BLE001
            user_id = None
        group_id = getattr(event, "group_id", None)
        group_str = str(group_id) if group_id is not None else None

        level = await resolve_level(user_id, group_id=group_str, role=event_role(event))
        required = await resolve_command_level(
            self.plugin_name, self.command_name, self.default_level
        )
        passed = int(level) >= int(required)
        if not passed:
            logger.debug(
                f"命令权限不足：{self.plugin_name}.{self.command_name} "
                f"需要 {required} 级，当前 {int(level)} 级（用户 {user_id}）"
            )
        return passed

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<CommandPermission {self.plugin_name}.{self.command_name} "
            f"default_level={self.default_level}>"
        )
