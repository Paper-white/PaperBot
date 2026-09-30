"""设置页接口：`/api/settings`。

提供三件事：
1. 版本信息（PaperBot / NoneBot / 适配器 / Python / 平台）；
2. WebUI 访问令牌的查看状态与在线修改（写入 `.env` 并**立即生效**，无需重启）；
3. 日志文件管理（列表、查看尾部、删除单个、清理全部）。
"""

from __future__ import annotations

import shutil
import sys
import time
from collections import deque
from importlib.metadata import PackageNotFoundError, version

import nonebot
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src import __version__ as paperbot_version
from src.config import BASE_DIR, settings
from src.core import logging as logcore
from src.core import registry
from webui import envfile
from webui.deps import (
    STARTED_AT,
    human_duration,
    human_size,
    iso_local,
    mask_secret,
    require_token,
)
from webui.routes import config as config_route

router = APIRouter(dependencies=[Depends(require_token)], tags=["settings"])

# 读取日志尾部时的上限，防止一把梭把大文件读进内存
MAX_TAIL_LINES = 500
MIN_TOKEN_LENGTH = 4


class TokenPayload(BaseModel):
    """修改 WebUI 访问令牌。"""

    token: str = Field(default="", description="新令牌；留空 = 关闭鉴权")


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _pkg_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:  # pragma: no cover - 未安装
        return ""


def _version_payload() -> dict:
    return {
        "paperbot": paperbot_version,
        "nonebot": _pkg_version("nonebot2"),
        "adapter_onebot": _pkg_version("nonebot-adapter-onebot"),
        "python": sys.version.split()[0],
        "platform": f"{sys.platform}",
        "project_dir": str(BASE_DIR),
    }


def _runtime_payload() -> dict:
    try:
        host = str(nonebot.get_driver().config.host)
        port = int(nonebot.get_driver().config.port)
        env = str(nonebot.get_driver().config.environment)
    except Exception:  # noqa: BLE001
        host, port, env = "127.0.0.1", 0, "prod"

    return {
        "started_at": iso_local(STARTED_AT),
        "uptime_text": human_duration(time.time() - STARTED_AT),
        "environment": env,
        "bind": f"{host}:{port}",
        "prefix": settings.webui_prefix,
        "plugins": len(registry.names()),
        "commands": sum(len(f.commands) for f in registry.all_features().values()),
        "log_level": settings.log_level,
    }


def _log_payload() -> dict:
    files = logcore.list_log_files()
    total = sum(item["size_bytes"] for item in files)
    current = logcore.current_log_file()
    return {
        "enabled": settings.log_file_enable,
        "dir": str(logcore.log_dir()),
        "level": settings.log_file_level,
        "keep_days": settings.log_keep_days,
        "current": current.name if current else "",
        "files": files,
        "total_bytes": total,
        "total_text": human_size(total),
    }


def _security_payload() -> dict:
    return {
        "token": mask_secret(settings.webui_token),
        "min_length": MIN_TOKEN_LENGTH,
        "env_file": str(envfile.env_path()),
        "env_exists": envfile.env_exists(),
    }


# --------------------------------------------------------------------------- #
# 读取设置
# --------------------------------------------------------------------------- #
@router.get("/settings")
async def get_settings() -> dict:
    """版本信息 + 运行时摘要 + 日志 + 安全设置 + 可编辑配置分组。"""
    env = envfile.read_env()
    return {
        "version": _version_payload(),
        "runtime": _runtime_payload(),
        "logs": _log_payload(),
        "security": _security_payload(),
        # 「服务与后台」「日志」两组的可编辑字段（写回走 PUT /api/config）
        "sections": config_route.render_sections(config_route.SETTINGS_SECTIONS, env),
        "config_path": str(envfile.env_path()),
    }


# --------------------------------------------------------------------------- #
# 修改 WebUI 访问令牌
# --------------------------------------------------------------------------- #
@router.put("/settings/token")
async def update_token(payload: TokenPayload) -> dict:
    """写入 `.env` 的 `WEBUI_TOKEN` 并即时生效（无需重启）。"""
    token = (payload.token or "").strip()
    if token and len(token) < MIN_TOKEN_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"令牌至少 {MIN_TOKEN_LENGTH} 位（留空表示关闭鉴权）"
        )
    if "\n" in token or "\r" in token:
        raise HTTPException(status_code=400, detail="令牌不能包含换行")
    if token == settings.webui_token:
        return {
            "changed": [],
            "message": "令牌未变化",
            "token": mask_secret(settings.webui_token),
        }

    if not envfile.env_exists():
        raise HTTPException(status_code=400, detail="未找到 .env，无法写入令牌")

    # 与 PUT /api/config 一致：写盘前先备份为 .env.bak
    env_path = envfile.env_path()
    shutil.copyfile(env_path, env_path.with_name(env_path.name + ".bak"))
    changed = envfile.write_env({"WEBUI_TOKEN": token})

    # 立即生效：require_token 每次都读 settings.webui_token
    settings.webui_token = token
    nonebot.logger.info(
        f"WebUI 访问令牌已更新（{'已开启鉴权' if token else '已关闭鉴权'}），即时生效"
    )
    return {
        "changed": changed,
        "message": "令牌已更新并立即生效" if token else "已关闭鉴权（令牌为空）",
        "token": mask_secret(settings.webui_token),
    }


# --------------------------------------------------------------------------- #
# 日志管理
# --------------------------------------------------------------------------- #
@router.get("/settings/logs/{name}")
async def read_log(
    name: str,
    lines: int = Query(default=200, ge=1, le=MAX_TAIL_LINES),
) -> dict:
    """读取某个日志文件的末尾若干行。"""
    try:
        path = logcore.resolve_log_file(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="日志文件不存在")

    size = path.stat().st_size
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        tail = deque(handle, maxlen=lines)
    return {
        "name": path.name,
        "size_bytes": size,
        "size_text": human_size(size),
        "lines": len(tail),
        "content": "".join(tail),
    }


@router.delete("/settings/logs")
async def clear_logs() -> dict:
    """清理全部日志（历史文件删除，当前运行的日志清空）。"""
    result = logcore.clear_log_files()
    nonebot.logger.info(
        f"WebUI 清理日志：删除 {result['deleted']} 个文件，释放 {result['freed_text']}"
    )
    return {"ok": True, **result, "logs": _log_payload()}


@router.delete("/settings/logs/{name}")
async def delete_log(name: str) -> dict:
    """删除单个日志文件（当前正在写入的除外）。"""
    try:
        freed = logcore.remove_log_file(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    nonebot.logger.info(f"WebUI 删除日志文件 {name}（释放 {human_size(freed)}）")
    return {"ok": True, "freed_bytes": freed, "freed_text": human_size(freed), "logs": _log_payload()}
