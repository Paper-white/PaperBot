"""命令管理接口：`/api/commands`。

职责：
1. 按插件分组列出已注册命令（名称 / 别名 / 说明 / 权限）；
2. **命令实际权限由框架维护**——这里的 level 可在 WebUI 直接改，写进 `command_rule` 表，
   改完即时生效（`CommandPermission` 每次事件实时查表）。
   删除覆盖记录 = 恢复插件声明的默认权限。
"""

from __future__ import annotations

import nonebot
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.core import llm, registry, service
from src.core.models import LEVEL_NAMES
from webui.deps import require_token

router = APIRouter(dependencies=[Depends(require_token)], tags=["commands"])

# 可选的权限等级（0 黑名单是全局身份，不作为命令门槛）
EDITABLE_LEVELS = (1, 2, 3)
LEVEL_LABELS = {level: LEVEL_NAMES.get(level, str(level)) for level in EDITABLE_LEVELS}
LEVEL_COLORS = {1: "grey", 2: "blue", 3: "pink"}
PERMISSION_LABELS = {
    "superuser": "所有者",
    "": "所有用户",
    "custom": "自定义权限",
}


class LevelPayload(BaseModel):
    """修改命令所需权限等级。"""

    level: int = Field(..., description="1 普通 / 2 群管 / 3 所有者")


def _command_prefix() -> str:
    """从 NoneBot 配置里取命令前缀（取第一个）。"""
    try:
        starts = nonebot.get_driver().config.command_start
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(starts, (list, tuple, set)) and starts:
        return str(next(iter(starts)))
    return ""


def _find_command(plugin_name: str, command_name: str):
    """在注册表里找到某插件的某主命令元信息；找不到返回 None。"""
    feature = registry.get(plugin_name)
    if feature is None:
        return None
    for entry in feature.commands:
        names = list(entry.get("commands") or [])
        if names and str(names[0]) == command_name:
            return feature, entry
    return None


def _ensure_editable(plugin_name: str, command_name: str):
    """校验命令存在且权限由框架托管；返回 (feature, entry)。"""
    found = _find_command(plugin_name, command_name)
    if found is None:
        raise HTTPException(status_code=404, detail="插件或命令不存在")
    feature, entry = found
    if entry.get("permission") == "custom":
        raise HTTPException(
            status_code=400, detail="该命令由插件自管权限（custom），框架无法修改"
        )
    if feature.protected:
        raise HTTPException(
            status_code=409, detail="受保护插件（如总控台）的命令权限不可修改"
        )
    return feature, entry


@router.get("/commands")
async def list_commands() -> dict:
    """按插件分组返回命令清单（含默认权限与当前实际权限）。"""
    prefix = _command_prefix()
    overrides = {
        (rule.plugin_name, rule.command_name): rule
        for rule in await service.list_command_rules()
    }

    items = []
    total = 0
    for name, feature in sorted(registry.all_features().items()):
        commands = []
        for entry in feature.commands:
            names = list(entry.get("commands") or [])
            aliases = list(entry.get("aliases") or [])
            main = str(names[0]) if names else ""
            default_level = int(entry.get("default_level", 1) or 1)
            rule = overrides.get((name, main))
            level = int(rule.level) if rule is not None else default_level
            editable = entry.get("permission") != "custom" and not feature.protected
            commands.append(
                {
                    "name": main,
                    "names": names,
                    "aliases": aliases,
                    "display": " / ".join(f"{prefix}{x}" for x in names),
                    "alias_display": "、".join(f"{prefix}{x}" for x in aliases),
                    "description": entry.get("description", ""),
                    "permission": entry.get("permission", ""),
                    "permission_label": PERMISSION_LABELS.get(
                        entry.get("permission", ""), "自定义权限"
                    ),
                    "default_level": default_level,
                    "default_level_label": LEVEL_LABELS.get(
                        default_level, str(default_level)
                    ),
                    "level": level,
                    "level_label": LEVEL_LABELS.get(level, str(level)),
                    "level_color": LEVEL_COLORS.get(level, "grey"),
                    "overridden": rule is not None,
                    "editable": editable,
                    "updated_by": rule.updated_by if rule is not None else "",
                    "updated_at": (
                        llm.fmt_dt(rule.updated_at)
                        if rule is not None and rule.updated_at is not None
                        else ""
                    ),
                }
            )
        total += len(commands)
        items.append(
            {
                "plugin": name,
                "display_name": feature.display_name,
                "description": feature.description,
                "version": feature.version,
                "protected": feature.protected,
                "default_enabled": feature.default_enabled,
                "command_count": len(commands),
                "commands": commands,
            }
        )

    return {
        "prefix": prefix,
        "levels": [
            {"value": level, "label": LEVEL_LABELS[level], "color": LEVEL_COLORS[level]}
            for level in EDITABLE_LEVELS
        ],
        "plugin_count": len(items),
        "command_count": total,
        "override_count": len(overrides),
        "items": items,
    }


@router.put("/commands/{plugin_name}/{command_name}")
async def set_command_level(
    plugin_name: str, command_name: str, payload: LevelPayload
) -> dict:
    """设置某命令的实际权限等级（写 `command_rule` 表，即时生效）。"""
    _feature, entry = _ensure_editable(plugin_name, command_name)
    level = int(payload.level)
    if level not in EDITABLE_LEVELS:
        raise HTTPException(status_code=400, detail="权限等级只能是 1 / 2 / 3")

    default_level = int(entry.get("default_level", 1) or 1)
    if level == default_level:
        # 与默认一致 → 等价于恢复默认，删掉覆盖记录，表保持干净
        await service.clear_command_level(plugin_name, command_name)
        nonebot.logger.info(
            f"WebUI：命令 {plugin_name}.{command_name} 权限与默认一致（{level}），已清除覆盖"
        )
        return {
            "ok": True,
            "overridden": False,
            "level": level,
            "level_label": LEVEL_LABELS.get(level, str(level)),
            "message": "与默认权限一致，已恢复默认",
        }

    await service.set_command_level(plugin_name, command_name, level, updated_by="webui")
    nonebot.logger.info(
        f"WebUI：命令 {plugin_name}.{command_name} 权限改为 {level} 级（默认 {default_level}）"
    )
    return {
        "ok": True,
        "overridden": True,
        "level": level,
        "level_label": LEVEL_LABELS.get(level, str(level)),
        "message": f"已改为「{LEVEL_LABELS.get(level, level)}」并即时生效",
    }


@router.delete("/commands/{plugin_name}/{command_name}")
async def reset_command_level(plugin_name: str, command_name: str) -> dict:
    """删除覆盖记录 → 恢复插件声明的默认权限。"""
    _feature, entry = _ensure_editable(plugin_name, command_name)
    await service.clear_command_level(plugin_name, command_name)
    default_level = int(entry.get("default_level", 1) or 1)
    nonebot.logger.info(f"WebUI：命令 {plugin_name}.{command_name} 权限已恢复默认")
    return {
        "ok": True,
        "overridden": False,
        "level": default_level,
        "level_label": LEVEL_LABELS.get(default_level, str(default_level)),
        "message": "已恢复默认权限",
    }
