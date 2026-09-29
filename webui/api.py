"""WebUI 后端 API。

复用 NoneBot 的 fastapi driver app，把路由挂在 `WEBUI_PREFIX`（默认 `/api`）下。
所有接口与 QQ 命令共用 `src.core.service`，保证双通道行为一致。

鉴权：若 `.env` 里设置了 `WEBUI_TOKEN`，则需带 `Authorization: Bearer <token>`。
"""

from __future__ import annotations

from typing import Optional

import nonebot
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from src.config import settings
from src.core import registry, service
from src.core.models import SCOPE_TYPES

logger = nonebot.logger

router = APIRouter()


async def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    """可选的 Bearer Token 鉴权。"""
    token = settings.webui_token
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #
@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "paperbot",
        "plugin_count": len(registry.names()),
    }


# --------------------------------------------------------------------------- #
# 插件
# --------------------------------------------------------------------------- #
@router.get("/plugins", dependencies=[Depends(require_token)])
async def list_plugins() -> dict:
    return {"items": await service.plugin_overview()}


@router.get("/plugins/{name}", dependencies=[Depends(require_token)])
async def plugin_detail(name: str) -> dict:
    feature = registry.get(name)
    if feature is None:
        raise HTTPException(status_code=404, detail=f"plugin '{name}' not found")
    rules = await service.list_rules(name)
    return {
        "name": feature.name,
        "display_name": feature.display_name,
        "description": feature.description,
        "version": feature.version,
        "author": feature.author,
        "default_enabled": feature.default_enabled,
        "protected": feature.protected,
        "rules": [
            {
                "scope_type": r.scope_type,
                "scope_id": r.scope_id,
                "enabled": r.enabled,
                "updated_by": r.updated_by,
            }
            for r in rules
        ],
    }


@router.put("/plugins/{name}/rule", dependencies=[Depends(require_token)])
async def put_rule(name: str, payload: dict = Body(...)) -> dict:
    if not registry.has(name):
        raise HTTPException(status_code=404, detail=f"plugin '{name}' not found")

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


@router.delete("/plugins/{name}/rule", dependencies=[Depends(require_token)])
async def delete_rule(
    name: str,
    scope_type: str = Query(...),
    scope_id: str = Query(default=""),
) -> dict:
    if scope_type not in SCOPE_TYPES:
        raise HTTPException(
            status_code=400, detail=f"scope_type must be one of {SCOPE_TYPES}"
        )
    removed = await service.clear_rule(name, scope_type, scope_id)
    return {"ok": True, "removed": removed}


# --------------------------------------------------------------------------- #
# 挂载
# --------------------------------------------------------------------------- #
def setup_webui() -> None:
    """把路由挂到 NoneBot 的 FastAPI app 上（仅 fastapi driver 有效）。"""
    if not settings.webui_enable:
        logger.info("WebUI 已关闭（WEBUI_ENABLE=false）")
        return

    try:
        app = nonebot.get_app()
    except Exception as exc:  # pragma: no cover - 非 fastapi driver
        logger.warning(f"未找到 FastAPI app，WebUI 未挂载：{exc}")
        return

    prefix = settings.webui_prefix.rstrip("/") or "/api"
    app.include_router(router, prefix=prefix)
    logger.success(f"WebUI 已挂载：http://{{host}}:{{port}}{prefix}/health")
