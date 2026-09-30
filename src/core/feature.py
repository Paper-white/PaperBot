"""功能插件基类（Feature）。

这是「自研框架」的核心抽象：功能插件不再直接 `nonebot.on_command(...)`，
而是通过 `Feature` 实例注册，从而自动获得：
1. 统一的元信息（名称/描述/版本/作者/依赖）；
2. 自动登记进全局注册表（供总管理插件 / WebUI 枚举）；
3. **自动注入门控 Rule**——总管理插件据此随时禁用该插件；
4. 带插件名前缀的 `logger`；
5. 插件级配置项（`config_schema` → WebUI 自动渲染表单，值存库）；
6. 生命周期钩子（`on_load / on_unload / on_enable / on_disable`）。

用法（一插件一文件夹，`plugins/<插件注册名>/<插件注册名>.py`）：

    from src.core.feature import Feature

    feature = Feature(
        "paperbot_xxx", display_name="XXX", description="...",
        author="白纸", repo="https://...", requires=">=1.0,<2.0",
        config_schema=[{"key": "greet", "label": "打招呼语", "default": "你好"}],
    )

    # level= 声明默认权限等级（1 普通 / 2 群管 / 3 所有者）；
    # 实际权限由框架维护（command_rule 表），可在 WebUI 命令管理页改，改完立即生效。
    cmd = feature.command("xxx", aliases={"xxx别名"}, level=1, description="...")

    @cmd.handle()
    async def _(...):
        greeting = feature.cfg("greet")   # 读插件配置（已合并默认值）
        feature.logger.info("干活啦")

    @feature.on_load
    async def _on_load():
        await feature.load_config()       # 启动时刷新配置缓存
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Optional, Union

import nonebot
from nonebot import on, on_command, on_message, on_notice, on_request
from nonebot.permission import SUPERUSER, Permission
from nonebot.rule import Rule

from src.core import pluginconf, registry
from src.core.cmdperm import CommandPermission
from src.core.gate import gate_rule
from src.core.logger import PrefixedLogger, get_logger
from src.core.models import LEVEL_OWNER, LEVEL_USER
from src.core.version import DEFAULT_REQUIRES

T_Rule = Optional[Union[Rule, Any]]


def drop_command_prefixes(feature) -> int:
    """从 NoneBot 的命令前缀 Trie 里摘掉本插件注册过的命令 / 别名。

    ⚠️ NoneBot 的 `TrieRule.prefix` 是**全局单例**，`Matcher.destroy()` 不会清理它，
    热重载时会残留并打出 `Duplicated prefix rule` 警告；这里手动收拾干净。
    """
    from nonebot.rule import TrieRule

    try:
        starts = nonebot.get_driver().config.command_start
    except Exception:  # noqa: BLE001 - driver 未就绪时不做清理
        return 0

    names: list[str] = []
    for entry in feature.commands:
        names.extend(entry.get("commands") or [])
        names.extend(entry.get("aliases") or [])

    removed = 0
    for name in set(names):
        for start in starts:
            key = f"{start}{name}"
            if key in TrieRule.prefix:
                try:
                    TrieRule.prefix.pop(key)
                    removed += 1
                except KeyError:  # pragma: no cover - 并发删除
                    continue
    return removed


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
        repo: str = "",
        requires: str = DEFAULT_REQUIRES,
        config_schema: Optional[list] = None,
        default_enabled: bool = True,
        protected: bool = False,
    ) -> None:
        self.name = name
        self.display_name = display_name or name
        self.description = description
        self.version = version
        self.author = author
        self.repo = repo
        # 兼容的插件协议版本范围（空串 = 不限制），由加载器校验
        self.requires = requires or ""
        self.default_enabled = default_enabled
        # protected=True：永久放行（例如总管理插件自身）
        self.protected = protected
        # 已注册的命令元信息：[{"commands": [...], "aliases": [...]}, ...]
        self.commands: list[dict] = []
        # 本插件注册的 matcher 类（供热重载时精确销毁，NoneBot 的 plugin/module 认不出来）
        self.matchers: list[type] = []
        # 插件级配置项 schema（写错直接抛错，加载期就能发现）
        self.config_schema: list[dict] = pluginconf.normalize_schema(config_schema)
        # 配置值缓存（启动时 / 配置变更后刷新），插件同步读取
        self._config_cache: dict[str, Any] = {
            f["key"]: pluginconf.default_value(f) for f in self.config_schema
        }
        # 生命周期钩子
        self.hooks: dict[str, list[Callable]] = {
            "load": [],
            "unload": [],
            "enable": [],
            "disable": [],
        }
        # 带插件名前缀的统一 logger
        self.logger: PrefixedLogger = get_logger(name)
        # 记录定义它的模块，供热重载定位
        caller = sys._getframe(1)
        self.module_name: str = caller.f_globals.get("__name__", "")
        self.module_file: str = caller.f_globals.get("__file__", "")
        registry.register(self)

    # ------------------------------------------------------------------ #
    # 插件级配置项
    # ------------------------------------------------------------------ #
    async def load_config(self) -> dict[str, Any]:
        """从库里读配置（合并默认值）并刷新缓存，返回最新字典。"""
        self._config_cache = await pluginconf.load_config(self.name)
        return dict(self._config_cache)

    @property
    def config(self) -> dict[str, Any]:
        """当前配置缓存（未加载时只有默认值）。"""
        return dict(self._config_cache)

    def cfg(self, key: str, default: Any = None) -> Any:
        """读一个配置项（同步，用缓存值）。"""
        return self._config_cache.get(key, default)

    # ------------------------------------------------------------------ #
    # 生命周期钩子（装饰器）
    # ------------------------------------------------------------------ #
    def _add_hook(self, event: str, fn: Callable) -> Callable:
        self.hooks[event].append(fn)
        return fn

    def on_load(self, fn: Callable) -> Callable:
        """插件加载完成时触发（框架启动 / 热重载后）。"""
        return self._add_hook("load", fn)

    def on_unload(self, fn: Callable) -> Callable:
        """框架关闭（或热重载替换前）触发，用来释放资源。"""
        return self._add_hook("unload", fn)

    def on_enable(self, fn: Callable) -> Callable:
        """全局规则把本插件从「停用」切到「启用」时触发。"""
        return self._add_hook("enable", fn)

    def on_disable(self, fn: Callable) -> Callable:
        """全局规则把本插件从「启用」切到「停用」时触发。"""
        return self._add_hook("disable", fn)

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
    def _remember(self, matcher):
        """记录 matcher 类，供热重载销毁。"""
        self.matchers.append(matcher)
        return matcher

    def on(self, type_: str = "", *, rule: T_Rule = None, permission=None, **kwargs):
        return self._remember(
            on(
                type_,
                rule=self._compose_rule(rule),
                permission=permission,
                **kwargs,
            )
        )

    def command(
        self,
        cmd: Union[str, tuple],
        *,
        aliases: Optional[set] = None,
        rule: T_Rule = None,
        permission=None,
        description: str = "",
        level: Optional[int] = None,
        **kwargs,
    ):
        """注册命令。

        Args:
            cmd: 命令名（或名称元组）。
            aliases: 别名集合。
            rule: 额外的 Rule（会与门控 Rule 做 AND）。
            permission: 自定义 Permission。传 `None` 或 `SUPERUSER` 时，
                框架会自动挂上 `CommandPermission`（实际权限可在 WebUI 改）；
                传其它自定义 Permission 时保持原样（插件自管）。
            description: 命令功能介绍（供 `%help` 与 WebUI 展示）。
            level: **默认权限等级**（1 普通 / 2 群管 / 3 所有者）。
                不填时按 permission 推断：SUPERUSER → 3，其余 → 1。
        """
        kw = dict(kwargs)
        if aliases is not None:
            kw["aliases"] = aliases

        # ---- 记录命令元信息（供 %help / WebUI 命令管理展示） ---- #
        names = [cmd] if isinstance(cmd, str) else list(cmd)
        if permission is None:
            permission_tag = ""
        elif permission is SUPERUSER:
            permission_tag = "superuser"
        else:
            permission_tag = "custom"

        if level is not None:
            default_level = int(level)
        elif permission is SUPERUSER:
            default_level = LEVEL_OWNER
        else:
            default_level = LEVEL_USER
        default_level = max(LEVEL_USER, min(LEVEL_OWNER, default_level))

        self.commands.append(
            {
                "commands": list(names),
                "aliases": sorted(aliases) if aliases else [],
                "description": description,
                "permission": permission_tag,
                "default_level": default_level,
            }
        )

        # ---- matcher 的权限：把「框架级命令权限」挂上去 ---- #
        if permission is None or permission is SUPERUSER:
            matcher_permission = CommandPermission(
                self.name, str(names[0]) if names else str(cmd), default_level
            )
        else:
            # 插件自带校验逻辑，保持原样（不自作主张）
            matcher_permission = permission

        return self._remember(
            on_command(
                cmd,
                rule=self._compose_rule(rule),
                permission=matcher_permission,
                **kw,
            )
        )

    def message(self, *, rule: T_Rule = None, permission=None, **kwargs):
        return self._remember(
            on_message(
                rule=self._compose_rule(rule),
                permission=permission,
                **kwargs,
            )
        )

    def notice(self, *, rule: T_Rule = None, permission=None, **kwargs):
        return self._remember(
            on_notice(
                rule=self._compose_rule(rule),
                permission=permission,
                **kwargs,
            )
        )

    def request(self, *, rule: T_Rule = None, permission=None, **kwargs):
        return self._remember(
            on_request(
                rule=self._compose_rule(rule),
                permission=permission,
                **kwargs,
            )
        )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<Feature {self.name} protected={self.protected} "
            f"matchers={len(self.matchers)}>"
        )
