"""插件热重载。

改动插件代码后不必重启整个 Bot：WebUI「插件管理」页点「热重载」即可。

流程：
1. 触发 `on_unload` 钩子（让插件释放资源）；
2. 精确销毁该插件注册过的 matcher（**按 `Feature.matchers` 记录**，
   因为 NoneBot 侧的 `plugin_name` / `module_name` 对自研框架插件全是
   `src.core.feature`，认不出是哪个插件）；
3. 从内存注册表移除旧实例；
4. `importlib.reload` 重新执行插件模块（模块顶层会重新建 Feature / matcher）；
5. 刷新插件配置缓存 → 触发 `on_load` 钩子 → 同步数据库元信息。

⚠️ 已知限制：
- 受保护插件（如总控插件）禁止热重载，避免重载失败把管理入口弄没；
- 插件模块里若定义了 ORM 表，`reload` 会撞「表已存在」，
  这里会自动把该模块定义的表从 `Base.metadata` 摘掉再重试一次；
- 插件模块里已 `from xxx import yyy` 绑定的引用不会回溯更新，属 Python 固有行为。
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

from src.core import hooks, registry
from src.core.feature import drop_command_prefixes
from src.core.models import Base

__all__ = ["reload_plugin", "ReloadError"]


class ReloadError(RuntimeError):
    """热重载失败。"""


def _destroy_matchers(feature) -> int:
    """销毁插件注册过的全部 matcher（含清掉 NoneBot 全局命令前缀），返回销毁数量。"""
    removed = 0
    for matcher in list(feature.matchers):
        try:
            matcher.destroy()
            removed += 1
        except (ValueError, KeyError, AttributeError):  # 已被移除 / 无 priority
            continue
    feature.matchers.clear()

    # `Matcher.destroy()` 不会清理 TrieRule 的全局命令前缀，这里补一刀，
    # 否则重载后会打「Duplicated prefix rule」警告。
    try:
        drop_command_prefixes(feature)
    except Exception:  # noqa: BLE001 - 清理失败不影响主流程
        pass

    # 顺手清掉空的优先级分组，保持 matchers 干净
    try:
        from nonebot.matcher import matchers

        for priority in list(matchers.keys()):
            if not matchers[priority]:
                del matchers[priority]
    except Exception:  # noqa: BLE001 - 清理失败不影响主流程
        pass
    return removed


def _module_tables(module) -> list:
    """找出模块里定义的 ORM 表（供热重载时从 metadata 摘除）。"""
    tables = []
    for obj in list(vars(module).values()):
        table = getattr(obj, "__table__", None)
        if table is not None and getattr(obj, "__module__", None) == module.__name__:
            tables.append(table)
    return tables


def _drop_tables(tables: list) -> int:
    dropped = 0
    for table in tables:
        try:
            Base.metadata.remove(table)
            dropped += 1
        except Exception:  # noqa: BLE001 - 被外键依赖的表摘不掉，跳过
            continue
    return dropped


async def reload_plugin(name: str, *, force: bool = False) -> dict[str, Any]:
    """热重载一个插件，返回执行摘要。失败抛 `ReloadError`。"""
    feature = registry.get(name)
    if feature is None:
        raise ReloadError(f"插件 {name} 未注册（可能已被移除）")
    if feature.protected and not force:
        raise ReloadError(f"插件 {name} 受保护，禁止热重载（请重启 Bot）")

    module_name = feature.module_name
    module = sys.modules.get(module_name) if module_name else None
    if module is None:
        raise ReloadError(f"找不到插件模块 {module_name!r}，无法热重载")

    module_tables = _module_tables(module)

    # 1) 通知插件准备卸载
    await hooks.fire(name, "unload", reason="reload")

    # 2) 销毁 matcher + 摘旧实例
    removed_matchers = _destroy_matchers(feature)
    registry.unregister(name)

    # 3) 重新执行模块（撞表冲突就摘掉旧表重试一次）
    importlib.invalidate_caches()
    dropped_tables = 0
    try:
        importlib.reload(module)
    except Exception as first_exc:  # noqa: BLE001
        dropped_tables = _drop_tables(module_tables)
        if not dropped_tables:
            raise ReloadError(f"重载 {name} 失败：{first_exc}") from first_exc
        try:
            importlib.reload(module)
        except Exception as second_exc:  # noqa: BLE001
            raise ReloadError(f"重载 {name} 失败：{second_exc}") from second_exc

    new_feature = registry.get(name)
    if new_feature is None:
        raise ReloadError(
            f"模块 {module_name} 重载后没有重新注册插件 {name!r}"
            f"（请确认 `Feature(...)` 建在模块顶层）"
        )

    # 4) 刷新配置缓存 + 触发 on_load + 同步元信息
    await new_feature.load_config()
    await hooks.fire(name, "load", reason="reload")

    from src.core import service

    await service.sync_plugin_info()

    return {
        "name": name,
        "module": module_name,
        "removed_matchers": removed_matchers,
        "dropped_tables": dropped_tables,
        "version": new_feature.version,
        "commands": len(new_feature.commands),
    }
