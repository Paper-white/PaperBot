"""功能插件基类（Feature）。

这是「自研框架」的核心抽象：功能插件不再直接 `nonebot.on_command(...)`，
而是通过 `Feature` 实例注册，从而自动获得：
1. 统一的元信息（名称/描述/版本/默认开关）；
2. 自动登记进全局注册表（供总管理插件 / WebUI 枚举）；
3. **自动注入门控 Rule**——总管理插件据此随时禁用该插件。

用法（插件目录 `src/plugins/xxx/__init__.py`）：

    from src.core.feature import Feature

    feature = Feature("xxx", display_name="XXX", description="...")

    cmd = feature.command("xxx", aliases={"xxx别名"})

    @cmd.handle()
    async def _(...):
        ...
"""

from __future__ import annotations

from typing import Any, Optional, Union

from nonebot import on, on_command, on_message, on_notice, on_request
from nonebot.permission import Permission
from nonebot.rule import Rule

from src.core import registry
from src.core.gate import gate_rule

T_Rule = Optional[Union[Rule, Any]]


class Feature:
    """一个可被总管理插件统一管控的功能插件。"""

    def __init__(
        self,
        name: str,
        *,
        display_name: str = "",
        description: str = "",
        version: str = "0.1.0",
        author: str = "",
        default_enabled: bool = True,
        protected: bool = False,
    ) -> None:
        self.name = name
        self.display_name = display_name or name
        self.description = description
        self.version = version
        self.author = author
        self.default_enabled = default_enabled
        # protected=True：永久放行（例如总管理插件自身）
        self.protected = protected
        # 已注册的命令元信息：[{"commands": [...], "aliases": [...]}, ...]
        self.commands: list[dict] = []
        registry.register(self)

    # ------------------------------------------------------------------ #
    # 内部：把门控规则与用户自定义规则做 AND 组合
    # ------------------------------------------------------------------ #
    def _compose_rule(self, rule: T_Rule) -> Rule:
        gate = Rule(gate_rule(self.name))
        if rule is None:
            return gate
        return Rule(rule) & gate

    # ------------------------------------------------------------------ #
    # 注册 matcher 的便捷方法（签名与 NoneBot 保持一致，额外自动加门控）
    # ------------------------------------------------------------------ #
    def on(self, type_: str = "", *, rule: T_Rule = None, permission=None, **kwargs):
        return on(
            type_,
            rule=self._compose_rule(rule),
            permission=permission,
            **kwargs,
        )

    def command(
        self,
        cmd: Union[str, tuple],
        *,
        aliases: Optional[set] = None,
        rule: T_Rule = None,
        permission=None,
        **kwargs,
    ):
        kw = dict(kwargs)
        if aliases is not None:
            kw["aliases"] = aliases
        # 记录命令元信息，供总控插件 %help 展示
        names = [cmd] if isinstance(cmd, str) else list(cmd)
        self.commands.append(
            {
                "commands": list(names),
                "aliases": sorted(aliases) if aliases else [],
            }
        )
        return on_command(
            cmd,
            rule=self._compose_rule(rule),
            permission=permission,
            **kw,
        )

    def message(self, *, rule: T_Rule = None, permission=None, **kwargs):
        return on_message(
            rule=self._compose_rule(rule),
            permission=permission,
            **kwargs,
        )

    def notice(self, *, rule: T_Rule = None, permission=None, **kwargs):
        return on_notice(
            rule=self._compose_rule(rule),
            permission=permission,
            **kwargs,
        )

    def request(self, *, rule: T_Rule = None, permission=None, **kwargs):
        return on_request(
            rule=self._compose_rule(rule),
            permission=permission,
            **kwargs,
        )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Feature {self.name} protected={self.protected}>"
