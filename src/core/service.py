"""插件门控的业务服务层。

QQ 命令与 WebUI API 都复用这里的函数，保证双通道行为一致。
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select

from src.core import registry
from src.core.database import get_session
from src.core.models import (
    SCOPE_GLOBAL,
    SCOPE_GROUP,
    SCOPE_MEMBER,
    PluginInfo,
    PluginRule,
)


# --------------------------------------------------------------------------- #
# 元信息同步
# --------------------------------------------------------------------------- #
async def sync_plugin_info() -> None:
    """把内存注册表里的插件元信息 upsert 到数据库（启动时调用）。"""
    async with get_session() as session:
        for feature in registry.all_features().values():
            info = await session.get(PluginInfo, feature.name)
            if info is None:
                info = PluginInfo(name=feature.name)
                session.add(info)
            info.display_name = feature.display_name
            info.description = feature.description
            info.version = feature.version
            info.author = feature.author
            info.default_enabled = feature.default_enabled
            info.protected = feature.protected


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


async def set_rule(
    plugin_name: str,
    scope_type: str,
    scope_id: str,
    enabled: bool,
    updated_by: str = "",
) -> PluginRule:
    """新增或更新一条门控规则。"""
    async with get_session() as session:
        rule = await _get_rule(session, plugin_name, scope_type, scope_id)
        if rule is None:
            rule = PluginRule(
                plugin_name=plugin_name, scope_type=scope_type, scope_id=scope_id
            )
            session.add(rule)
        rule.enabled = enabled
        rule.updated_by = updated_by
        return rule


async def clear_rule(plugin_name: str, scope_type: str, scope_id: str) -> bool:
    """删除一条规则，返回是否真的删掉了。"""
    async with get_session() as session:
        rule = await _get_rule(session, plugin_name, scope_type, scope_id)
        if rule is None:
            return False
        await session.delete(rule)
        return True


async def list_rules(plugin_name: str) -> List[PluginRule]:
    async with get_session() as session:
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
) -> bool:
    """按「成员 > 群 > 全局 > 默认」的顺序解析某插件此刻是否放行。"""
    async with get_session() as session:
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
    return default


async def scope_enabled(
    plugin_name: str,
    scope_type: str,
    scope_id: str,
    default: bool = True,
) -> bool:
    """只看某一个具体作用域的规则（用于展示，不回溯上级）。"""
    async with get_session() as session:
        rule = await _get_rule(session, plugin_name, scope_type, scope_id)
        if rule is None:
            return default
        return bool(rule.enabled)


# --------------------------------------------------------------------------- #
# 聚合视图（给 WebUI / 命令列表用）
# --------------------------------------------------------------------------- #
async def plugin_overview() -> List[dict]:
    """返回每个插件的元信息 + 全局开关状态。"""
    result: List[dict] = []
    for name, feature in registry.all_features().items():
        global_on = await scope_enabled(
            name, SCOPE_GLOBAL, "", default=feature.default_enabled
        )
        result.append(
            {
                "name": name,
                "display_name": feature.display_name,
                "description": feature.description,
                "version": feature.version,
                "author": feature.author,
                "default_enabled": feature.default_enabled,
                "protected": feature.protected,
                "global_enabled": global_on,
            }
        )
    result.sort(key=lambda x: x["name"])
    return result
