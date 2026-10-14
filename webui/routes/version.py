"""版本控制接口：`/api/version*`（一键拉取云端版本，见 `src/core/vcs.py`）。

设计要点：
- 只拉不推，只追 `<VCS_REMOTE>/<VCS_BRANCH>`（默认 `origin/main`）；
- 工作区有未提交改动时**拒绝更新**（409）；
- 更新后需**重启**才生效（框架代码无法热重载），另提供 `/restart`。
"""

from __future__ import annotations

import asyncio

import nonebot
from fastapi import APIRouter, Body, Depends, HTTPException

from src.config import settings
from src.core import vcs
from webui.deps import require_token

router = APIRouter(dependencies=[Depends(require_token)], tags=["version"])

logger = nonebot.logger

# 语义错误码 → HTTP 状态码
_CODE_STATUS = {
    "no_repo": 400,
    "no_git": 400,
    "bad_commit": 400,
    "unknown_commit": 400,
    "no_branch": 400,
    "no_fetch": 409,
    "dirty": 409,
    "timeout": 504,
    "git_failed": 502,
}


def _http_error(exc: vcs.VcsError) -> HTTPException:
    return HTTPException(status_code=_CODE_STATUS.get(exc.code, 500), detail=str(exc))


def _ensure_enabled() -> None:
    if not settings.vcs_enable:
        raise HTTPException(status_code=400, detail="版本控制功能已关闭（VCS_ENABLE=false）")


@router.get("/version")
async def get_version() -> dict:
    """版本控制状态：仓库信息 / 当前 commit / 是否脏 / 落后领先 / 守护服务。"""
    return await vcs.status()


@router.post("/version/fetch")
async def fetch_version() -> dict:
    """拉取 `origin/main` 并返回更新情况与提交历史（不改动工作区）。"""
    _ensure_enabled()
    try:
        return await vcs.fetch()
    except vcs.VcsError as exc:
        raise _http_error(exc) from exc


@router.post("/version/update")
async def update_version(payload: dict = Body(default={})) -> dict:
    """把工作区对齐到指定 commit（body 留空 = 更新到 `origin/main` 最新）。

    工作区有未提交改动 → 409；commit 非法 / 不存在 → 400。
    """
    _ensure_enabled()
    target = payload.get("commit") if isinstance(payload, dict) else None
    try:
        result = await vcs.update(str(target) if target else None)
    except vcs.VcsError as exc:
        raise _http_error(exc) from exc
    logger.info(f"WebUI 触发版本更新：{result.get('message')}")
    return {**result, "restart_required": bool(result.get("changed"))}


@router.post("/version/restart")
async def restart_bot() -> dict:
    """重启 Bot 进程。

    ⚠️ 本接口会安排进程退出：先把响应返回，再交给后台任务执行重启，
    前端请求可能会收到连接中断——这属正常现象（说明进程真的退出了）。
    """
    _ensure_enabled()
    mode = (settings.vcs_restart_mode or "spawn").strip().lower()

    # 先把响应排出去，再延迟一小会儿执行退出，保证 HTTP 响应能回到前端
    async def _later() -> None:
        await asyncio.sleep(0.8)
        try:
            await vcs.request_restart()
        except vcs.VcsError as exc:
            logger.error(f"重启失败：{exc}")

    asyncio.create_task(_later())
    return {
        "ok": True,
        "mode": mode,
        "message": (
            "正在重启：助手稍后会拉起新进程"
            if mode == "spawn"
            else "正在退出：将由守护进程（systemd / NSSM）拉起"
        ),
    }
