"""插件管理接口：`/api/plugins*`（复用 `src.core.service`，与 QQ 命令双通道一致）。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from src.core import pluginconf
from src.core import registry, reload as reload_mod, service
from src.core.models import SCOPE_GLOBAL, SCOPE_TYPES
from webui.deps import SECRET_PLACEHOLDER, mask_secret, require_token

router = APIRouter(dependencies=[Depends(require_token)], tags=["plugins"])


def _ensure_mutable(name: str) -> None:
    """与 QQ 命令侧保持一致：受保护插件不允许通过 WebUI 改规则。"""
    if not registry.has(name):
        raise HTTPException(status_code=404, detail=f"plugin '{name}' not found")
    feature = registry.get(name)
    if feature is not None and feature.protected:
        raise HTTPException(
            status_code=409,
            detail=f"plugin '{name}' is protected and cannot be modified",
        )


@router.get("/plugins")
async def list_plugins() -> dict:
    return {"items": await service.plugin_overview()}


@router.get("/plugins/{name}")
async def plugin_detail(name: str) -> dict:
    feature = registry.get(name)
    if feature is None:
        raise HTTPException(status_code=404, detail=f"plugin '{name}' not found")
    rules = await service.list_rules(name)
    # ⚠️ 前端插件卡片用的是详情数据，必须带上 global_enabled，
    # 否则 undefined → 一律显示「已停用」（2026-09-30 踩过）。
    global_enabled = (
        True
        if feature.protected
        else await service.scope_enabled(
            name, SCOPE_GLOBAL, "", feature.default_enabled
        )
    )
    stored = await service.plugin_config_stored(name)
    return {
        "name": feature.name,
        "display_name": feature.display_name,
        "description": feature.description,
        "version": feature.version,
        "author": feature.author,
        "repo": feature.repo,
        "requires": feature.requires,
        "default_enabled": feature.default_enabled,
        "protected": feature.protected,
        "global_enabled": global_enabled,
        "module": feature.module_name,
        "reloadable": bool(feature.module_name) and not feature.protected,
        "hooks": {
            event: len(fns) for event, fns in feature.hooks.items() if fns
        },
        "commands": feature.commands,
        "config_schema": [f for f in feature.config_schema],
        "config_stored_keys": sorted(stored),
        "rules": [
            {
                "scope_type": r.scope_type,
                "scope_id": r.scope_id,
                "enabled": r.enabled,
                "updated_by": r.updated_by,
                "updated_at": r.updated_at.isoformat(sep=" ", timespec="seconds")
                if r.updated_at
                else None,
            }
            for r in rules
        ],
    }


@router.get("/plugins/{name}/config")
async def get_plugin_config(name: str) -> dict:
    """插件配置项：schema + 当前值（默认值已合并，敏感字段脱敏）。"""
    feature = registry.get(name)
    if feature is None:
        raise HTTPException(status_code=404, detail=f"plugin '{name}' not found")

    values = await service.plugin_config_values(name)
    stored = await service.plugin_config_stored(name)
    meta = await service.plugin_config_meta(name)

    fields: list[dict] = []
    for field in feature.config_schema:
        item = dict(field)
        key = field["key"]
        if field["secret"]:
            raw = stored.get(key)
            info = mask_secret(None if raw is None else str(raw))
            item.update(
                value=info["value"],
                configured=info["configured"],
                length=info["length"],
            )
        else:
            item["value"] = values.get(key)
            item["configured"] = key in stored
        fields.append(item)

    return {
        "plugin": name,
        "fields": fields,
        "stored_keys": sorted(stored),
        "path": str(pluginconf.config_file(name)),
        "exists": pluginconf.config_file(name).is_file(),
        "updated_by": meta.get("updated_by", ""),
        "updated_at": meta.get("updated_at", ""),
        "note": "改动即时生效（无需重启）；值存 .config/<插件名>/config.json，删掉键即恢复默认值",
    }


@router.put("/plugins/{name}/config")
async def put_plugin_config(name: str, payload: dict = Body(...)) -> dict:
    """写入插件配置项（按 schema 校验类型；敏感字段留空/占位 = 不修改）。"""
    feature = registry.get(name)
    if feature is None:
        raise HTTPException(status_code=404, detail=f"plugin '{name}' not found")
    if not feature.config_schema:
        raise HTTPException(status_code=400, detail=f"plugin '{name}' 没有声明配置项")

    values = payload.get("values")
    if not isinstance(values, dict) or not values:
        raise HTTPException(status_code=400, detail="values 不能为空")

    index = {f["key"]: f for f in feature.config_schema}
    unknown = [k for k in values if k not in index]
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"不支持的配置项：{', '.join(unknown)}"
        )

    cleaned: dict = {}
    for key, raw in values.items():
        field = index[key]
        if field["secret"]:
            text = "" if raw is None else str(raw)
            if text in ("", SECRET_PLACEHOLDER):
                continue  # 未改动 → 保持原值
        cleaned[key] = raw

    if not cleaned:
        return {"ok": True, "changed": [], "message": "没有需要修改的配置项"}

    try:
        changed = await service.set_plugin_config(name, cleaned, updated_by="webui")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"ok": True, "changed": changed, "restart_required": False}


@router.delete("/plugins/{name}/config")
async def delete_plugin_config(name: str, key: str = Query(default="")) -> dict:
    """清除配置项（key 留空 = 全部清空，退回默认值）。"""
    if not registry.has(name):
        raise HTTPException(status_code=404, detail=f"plugin '{name}' not found")
    removed = await service.clear_plugin_config(name, key or None)
    return {"ok": True, "removed": removed}


@router.post("/plugins/{name}/reload")
async def reload_plugin(name: str) -> dict:
    """热重载插件（销毁旧 matcher + 重新 import 模块）。"""
    if not registry.has(name):
        raise HTTPException(status_code=404, detail=f"plugin '{name}' not found")
    feature = registry.get(name)
    if feature is not None and feature.protected:
        raise HTTPException(
            status_code=409, detail=f"plugin '{name}' 受保护，禁止热重载（请重启 Bot）"
        )
    try:
        summary = await reload_mod.reload_plugin(name)
    except reload_mod.ReloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **summary}


@router.put("/plugins/{name}/rule")
async def put_rule(name: str, payload: dict = Body(...)) -> dict:
    _ensure_mutable(name)

    scope_type = str(payload.get("scope_type", ""))
    if scope_type not in SCOPE_TYPES:
        raise HTTPException(
            status_code=400, detail=f"scope_type must be one of {SCOPE_TYPES}"
        )

    scope_id = str(payload.get("scope_id", "") or "")
    if scope_type != "global" and not scope_id:
        raise HTTPException(status_code=400, detail="scope_id is required")

    rule = await service.set_rule(
        name,
        scope_type,
        scope_id,
        bool(payload.get("enabled", True)),
        updated_by=str(payload.get("updated_by", "webui")),
    )
    return {
        "ok": True,
        "plugin": name,
        "scope_type": rule.scope_type,
        "scope_id": rule.scope_id,
        "enabled": rule.enabled,
    }


@router.delete("/plugins/{name}/rule")
async def delete_rule(
    name: str,
    scope_type: str = Query(...),
    scope_id: str = Query(default=""),
) -> dict:
    _ensure_mutable(name)
    if scope_type not in SCOPE_TYPES:
        raise HTTPException(
            status_code=400, detail=f"scope_type must be one of {SCOPE_TYPES}"
        )
    removed = await service.clear_rule(name, scope_type, scope_id)
    return {"ok": True, "removed": removed}
