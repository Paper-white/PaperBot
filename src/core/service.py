"""插件门控的业务服务层。

QQ 命令与 WebUI API 都复用这里的函数，保证双通道行为一致。
"""

from __future__ import annotations

from typing import List, Optional

import nonebot
from sqlalchemy import select

from src.config import active_groups, settings
from src.core import pluginconf, registry
from src.core.database import get_session
from src.core.models import (
    LEVEL_USER,
    SCOPE_GLOBAL,
    SCOPE_GROUP,
    SCOPE_MEMBER,
    CommandRule,
    PluginInfo,
    PluginRule,
)

logger = nonebot.logger


# --------------------------------------------------------------------------- #
# 元信息同步
# --------------------------------------------------------------------------- #
async def sync_plugin_info() -> None:
    """把内存注册表里的插件元信息 upsert 到数据库（启动时调用）。

    同时**清理**已被卸载插件遗留的元信息与规则。
    """
    features = registry.all_features()
    known = set(features)
    async with get_session() as session:
        for feature in features.values():
            info = await session.get(PluginInfo, feature.name)
            if info is None:
                info = PluginInfo(name=feature.name)
                session.add(info)
            info.display_name = feature.display_name
            info.description = feature.description
            info.version = feature.version
            info.author = feature.author
            info.repo = feature.repo
            info.requires = feature.requires
            info.default_enabled = feature.default_enabled
            info.protected = feature.protected

        # 清理：数据库里有、但当前进程已不存在的插件
        stale_infos = [
            info
            for info in (await session.execute(select(PluginInfo))).scalars().all()
            if info.name not in known
        ]
        if stale_infos:
            stale_names = [info.name for info in stale_infos]
            stale_rules = (
                (
                    await session.execute(
                        select(PluginRule).where(
                            PluginRule.plugin_name.in_(stale_names)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for rule in stale_rules:
                await session.delete(rule)
            for info in stale_infos:
                await session.delete(info)
            logger.info(f"已清理已卸载插件的残留记录：{', '.join(stale_names)}")

        # 清理：命令已不存在（插件卸载或改名）的权限覆盖记录
        known_commands = set(command_defaults())
        stale_commands = [
            rule
            for rule in (await session.execute(select(CommandRule))).scalars().all()
            if (rule.plugin_name, rule.command_name) not in known_commands
        ]
        if stale_commands:
            for rule in stale_commands:
                await session.delete(rule)
            logger.info(f"已清理 {len(stale_commands)} 条失效的命令权限覆盖")


# --------------------------------------------------------------------------- #
# 规则 CRUD
# --------------------------------------------------------------------------- #
async def _get_rule(
    session, plugin_name: str, scope_type: str, scope_id: str
) -> Optional[PluginRule]:
    stmt = select(PluginRule).where(
        PluginRule.plugin_name == plugin_name,
        PluginRule.scope_type == scope_type,
        PluginRule.scope_id == scope_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _global_default(plugin_name: str) -> bool:
    """无显式全局规则时插件的全局状态（= 插件的 default_enabled）。"""
    feature = registry.get(plugin_name)
    return bool(feature.default_enabled) if feature is not None else True


async def _fire_global_gate_hook(plugin_name: str, before: bool, after: bool) -> None:
    """全局开关状态发生变化时触发 on_enable / on_disable 钩子。

    只认「全局作用域」的规则变化：分群/分成员规则不影响插件自身的初始化状态。
    """
    if before == after:
        return
    from src.core import hooks

    await hooks.fire(
        plugin_name,
        "enable" if after else "disable",
        scope_type=SCOPE_GLOBAL,
        scope_id="",
        before=before,
        after=after,
    )


async def set_rule(
    plugin_name: str,
    scope_type: str,
    scope_id: str,
    enabled: bool,
    updated_by: str = "",
) -> PluginRule:
    """新增或更新一条门控规则。"""
    is_global = scope_type == SCOPE_GLOBAL
    feature = registry.get(plugin_name)
    async with get_session() as session:
        rule = await _get_rule(session, plugin_name, scope_type, scope_id)
        if rule is None:
            rule = PluginRule(
                plugin_name=plugin_name, scope_type=scope_type, scope_id=scope_id
            )
            session.add(rule)
        if is_global and feature is not None:
            before = bool(rule.enabled) if rule.id is not None else feature.default_enabled
        else:
            before = None
        rule.enabled = enabled
        rule.updated_by = updated_by

    if is_global and feature is not None and before is not None:
        await _fire_global_gate_hook(plugin_name, before, bool(enabled))
    return rule


async def clear_rule(plugin_name: str, scope_type: str, scope_id: str) -> bool:
    """删除一条规则，返回是否真的删掉了。"""
    is_global = scope_type == SCOPE_GLOBAL
    async with get_session() as session:
        rule = await _get_rule(session, plugin_name, scope_type, scope_id)
        if rule is None:
            return False
        before = bool(rule.enabled)

        await session.delete(rule)

    if is_global:
        await _fire_global_gate_hook(plugin_name, before, _global_default(plugin_name))
    return True


async def list_rules(plugin_name: str) -> List[PluginRule]:
    async with get_session(readonly=True) as session:
        stmt = (
            select(PluginRule)
            .where(PluginRule.plugin_name == plugin_name)
            .order_by(PluginRule.scope_type, PluginRule.scope_id)
        )
        return list((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------- #
# 门控解析（核心：就近优先）
# --------------------------------------------------------------------------- #
async def resolve_enabled(
    plugin_name: str,
    user_id: Optional[str] = None,
    group_id: Optional[str] = None,
    default: bool = True,
    *,
    session=None,
) -> bool:
    """按「成员 > 群 > 全局 > 默认」的顺序解析某插件此刻是否放行。

    ``session`` 可选：由调用方传入可复用的 session（避免一轮事件里重复开库）。
    """
    if session is not None:
        return await _resolve_enabled_in(session, plugin_name, user_id, group_id, default)
    async with get_session(readonly=True) as own_session:
        return await _resolve_enabled_in(
            own_session, plugin_name, user_id, group_id, default
        )


async def _resolve_enabled_in(
    session,
    plugin_name: str,
    user_id: Optional[str],
    group_id: Optional[str],
    default: bool,
) -> bool:
    if user_id:
        rule = await _get_rule(session, plugin_name, SCOPE_MEMBER, str(user_id))
        if rule is not None:
            return bool(rule.enabled)
    if group_id:
        rule = await _get_rule(session, plugin_name, SCOPE_GROUP, str(group_id))
        if rule is not None:
            return bool(rule.enabled)
    rule = await _get_rule(session, plugin_name, SCOPE_GLOBAL, "")
    if rule is not None:
        return bool(rule.enabled)
    return await _fallback_enabled(group_id, default)


async def _fallback_enabled(
    group_id: Optional[str], default: bool
) -> bool:
    """未命中任何规则时的兜底：框架级默认门控策略。

    - `GATE_DEFAULT_ENABLED=true`（旧行为）→ 直接跟随插件的 `default_enabled`；
    - `GATE_DEFAULT_ENABLED=false` → 「全局默认禁用」：
      只有位于 `BOT_ACTIVE_GROUPS` 的群、且插件默认启用时才放行；私聊一律不放行。
    """
    if settings.gate_default_enabled:
        return default
    if not default or group_id is None:
        return False
    return str(group_id) in active_groups()


async def scope_enabled(
    plugin_name: str,
    scope_type: str,
    scope_id: str,
    default: bool = True,
) -> bool:
    """只看某一个具体作用域的规则（用于展示，不回溯上级）。"""
    async with get_session(readonly=True) as session:
        rule = await _get_rule(session, plugin_name, scope_type, scope_id)
        if rule is None:
            return default
        return bool(rule.enabled)


# --------------------------------------------------------------------------- #
# 聚合视图（给 WebUI / 命令列表用）
# --------------------------------------------------------------------------- #
async def plugin_overview() -> List[dict]:
    """返回每个插件的元信息 + 全局开关状态（一次查询取全部全局规则）。"""
    features = registry.all_features()
    async with get_session(readonly=True) as session:
        global_rows = (
            (
                await session.execute(
                    select(PluginRule).where(
                        PluginRule.scope_type == SCOPE_GLOBAL,
                        PluginRule.scope_id == "",
                    )
                )
            )
            .scalars()
            .all()
        )
    global_map = {r.plugin_name: bool(r.enabled) for r in global_rows}

    result: List[dict] = []
    for name, feature in features.items():
        result.append(
            {
                "name": name,
                "display_name": feature.display_name,
                "description": feature.description,
                "version": feature.version,
                "author": feature.author,
                "repo": feature.repo,
                "requires": feature.requires,
                "default_enabled": feature.default_enabled,
                "protected": feature.protected,
                "global_enabled": global_map.get(name, feature.default_enabled),
                "configurable": bool(feature.config_schema),
                "reloadable": bool(feature.module_name) and not feature.protected,
            }
        )
    result.sort(key=lambda x: x["name"])
    return result


async def enabled_plugin_names(
    user_id: Optional[str] = None, group_id: Optional[str] = None
) -> List[str]:
    """返回在当前上下文下「放行」的插件名（含受保护插件）。

    一次查询取全部规则后在内存解析，避免按插件逐条查库（N+1）。
    """
    features = registry.all_features()
    async with get_session(readonly=True) as session:
        rows = (await session.execute(select(PluginRule))).scalars().all()
    index = {
        (r.plugin_name, r.scope_type, r.scope_id): bool(r.enabled) for r in rows
    }

    uid = str(user_id) if user_id else None
    gid = str(group_id) if group_id else None
    names: List[str] = []
    for name, feature in features.items():
        if feature.protected:
            names.append(name)
            continue
        # 就近优先：成员 > 群 > 全局
        for scope_type, scope_id in (
            (SCOPE_MEMBER, uid),
            (SCOPE_GROUP, gid),
            (SCOPE_GLOBAL, ""),
        ):
            if scope_id is None:
                continue
            key = (name, scope_type, scope_id)
            if key in index:
                if index[key]:
                    names.append(name)
                break
        else:
            if feature.default_enabled:
                names.append(name)
    return sorted(names)


# --------------------------------------------------------------------------- #
# 命令权限：默认值来自插件声明，实际值由框架（本表）覆盖维护
# --------------------------------------------------------------------------- #
def command_defaults() -> dict:
    """扫内存注册表 -> {(插件名, 主命令名): 默认权限等级}。"""
    defaults: dict = {}
    for name, feature in registry.all_features().items():
        for entry in feature.commands:
            names = list(entry.get("commands") or [])
            if not names:
                continue
            defaults[(name, str(names[0]))] = int(
                entry.get("default_level", LEVEL_USER)
            )
    return defaults


async def _get_command_rule(session, plugin_name: str, command_name: str):
    stmt = select(CommandRule).where(
        CommandRule.plugin_name == plugin_name,
        CommandRule.command_name == command_name,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_command_rules() -> List[CommandRule]:
    async with get_session(readonly=True) as session:
        stmt = select(CommandRule).order_by(
            CommandRule.plugin_name, CommandRule.command_name
        )
        return list((await session.execute(stmt)).scalars().all())


async def set_command_level(
    plugin_name: str,
    command_name: str,
    level: int,
    updated_by: str = "",
) -> CommandRule:
    """写入/更新一条命令权限覆盖。"""
    async with get_session() as session:
        rule = await _get_command_rule(session, plugin_name, command_name)
        if rule is None:
            rule = CommandRule(plugin_name=plugin_name, command_name=command_name)
            session.add(rule)
        rule.level = int(level)
        rule.updated_by = updated_by
        return rule


async def clear_command_level(plugin_name: str, command_name: str) -> bool:
    """删除覆盖记录（= 恢复默认权限）。"""
    async with get_session() as session:
        rule = await _get_command_rule(session, plugin_name, command_name)
        if rule is None:
            return False
        await session.delete(rule)
        return True


async def resolve_command_level(
    plugin_name: str,
    command_name: str,
    default_level: int = LEVEL_USER,
    *,
    session=None,
) -> int:
    """取某命令此刻的实际权限等级（有覆盖用覆盖，否则用插件声明的默认值）。"""
    if session is not None:
        rule = await _get_command_rule(session, plugin_name, command_name)
    else:
        async with get_session(readonly=True) as own_session:
            rule = await _get_command_rule(own_session, plugin_name, command_name)
    return int(rule.level) if rule is not None else int(default_level)


# --------------------------------------------------------------------------- #
# 插件级配置项（薄封装 pluginconf，供 WebUI / 命令侧共用）
# --------------------------------------------------------------------------- #
async def plugin_config_values(plugin_name: str) -> dict:
    """读某插件配置（已合并 schema 默认值）。"""
    return await pluginconf.load_config(plugin_name)


async def plugin_config_stored(plugin_name: str) -> dict:
    """只读「显式存过」的配置（不含默认值）。"""
    return await pluginconf.stored_config(plugin_name)


async def plugin_config_meta(plugin_name: str) -> dict:
    """只读配置文件的元信息（`updated_by` / `updated_at`）。"""
    return await pluginconf.stored_meta(plugin_name)


async def set_plugin_config(
    plugin_name: str, values: dict, updated_by: str = ""
) -> List[str]:
    """写入插件配置，返回实际发生变化的键名列表（会校验 schema 类型）。"""
    return await pluginconf.write_config(plugin_name, values, updated_by)


async def clear_plugin_config(plugin_name: str, key: Optional[str] = None) -> int:
    """清除插件配置（key=None 清空全部 → 全部退回默认值）。"""
    return await pluginconf.clear_config(plugin_name, key)
