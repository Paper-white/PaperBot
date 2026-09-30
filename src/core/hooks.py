"""插件生命周期钩子分发。

插件用 `Feature` 上的装饰器注册回调::

    feature = Feature("paperbot_xxx", ...)

    @feature.on_load
    async def _startup():
        ...            # 插件被加载时（框架启动 / 热重载后）

    @feature.on_enable
    async def _enabled(feature):
        ...            # 全局规则把插件「启用」时

钩子函数支持 0 个参数、`feature`、或 `(feature, context_dict)` 三种写法，
同步 / 异步都可以；单个钩子报错只记日志，**不影响其它钩子与主流程**。
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from src.core import registry

HOOK_EVENTS = ("load", "unload", "enable", "disable")


async def _invoke(fn: Callable, feature, context: dict[str, Any]) -> None:
    try:
        params = [
            p
            for p in inspect.signature(fn).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
    except (TypeError, ValueError):  # 内建 / C 扩展函数
        params = []

    count = len(params)
    if count == 0:
        result = fn()
    elif count == 1:
        result = fn(feature)
    else:
        result = fn(feature, context)

    if inspect.isawaitable(result):
        await result


async def fire(plugin_name: str, event: str, **context: Any) -> int:
    """触发某插件的某类钩子，返回成功执行的钩子数量。"""
    if event not in HOOK_EVENTS:
        raise ValueError(f"未知的钩子事件 {event!r}（可选 {HOOK_EVENTS}）")

    feature = registry.get(plugin_name)
    if feature is None:
        return 0

    handlers = list(feature.hooks.get(event, []))
    done = 0
    for fn in handlers:
        try:
            await _invoke(fn, feature, dict(context))
            done += 1
        except Exception as exc:  # noqa: BLE001 - 钩子出错不拖垮框架
            feature.logger.opt(exception=exc).error(
                f"生命周期钩子 {event} 执行失败（{getattr(fn, '__name__', fn)}）"
            )
    return done


async def fire_all(event: str, plugin_names: list[str] | None = None, **context: Any) -> int:
    """批量触发（启动 / 关闭用）。"""
    names = plugin_names if plugin_names is not None else registry.names()
    total = 0
    for name in names:
        total += await fire(name, event, **context)
    return total
