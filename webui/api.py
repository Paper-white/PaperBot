"""WebUI 后端入口。

- 复用 NoneBot 的 fastapi driver app，把 API 挂在 `WEBUI_PREFIX`（默认 `/api`）下；
- 再把 `webui/static/` 作为静态站点挂在 `/`，于是 `http://127.0.0.1:6188/` 就是控制台页面。
- 所有接口与 QQ 命令共用 `src.core` 业务层，保证双通道行为一致。

鉴权：若 `.env` 里设置了 `WEBUI_TOKEN`，则需带 `Authorization: Bearer <token>`
（`/api/health` 与静态页面本身不鉴权）。
"""

from __future__ import annotations

import time
from pathlib import Path

import nonebot
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.core import registry
from webui.routes import commands as commands_routes
from webui.routes import config as config_routes
from webui.routes import database as database_routes
from webui.routes import groups as groups_routes
from webui.routes import llm as llm_routes
from webui.routes import permissions as permissions_routes
from webui.routes import plugins as plugins_routes
from webui.routes import settings as settings_routes
from webui.routes import status as status_routes
from webui.routes import version as version_routes

logger = nonebot.logger

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"
ASSETS_DIR = STATIC_DIR / "assets"

PREFIX_PLACEHOLDER = "%WEBUI_PREFIX%"

# 启动时间戳用作静态资源缓存破坏参数：前端每次访问 /assets/app.js?v=<ts> 都会拉到最新
_STATIC_VERSION = str(int(time.time()))

# 允许直接用页面路径访问（例：http://127.0.0.1:6188/status），统一返回同一个单页
PAGE_NAMES = (
    "status",
    "config",
    "permissions",
    "plugins",
    "llm",
    "database",
    "commands",
    "logs",
    "settings",
)

router = APIRouter()


# --------------------------------------------------------------------------- #
# 健康检查（不鉴权）
# --------------------------------------------------------------------------- #
@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "paperbot",
        "plugin_count": len(registry.names()),
    }


# --------------------------------------------------------------------------- #
# 各页面子路由
# --------------------------------------------------------------------------- #
router.include_router(status_routes.router)
router.include_router(config_routes.router)
router.include_router(plugins_routes.router)
router.include_router(permissions_routes.router)
router.include_router(groups_routes.router)
router.include_router(commands_routes.router)
router.include_router(llm_routes.router)
router.include_router(database_routes.router)
router.include_router(settings_routes.router)
router.include_router(version_routes.router)

# 兼容旧引用
api_router = router


def _driver_host() -> str:
    """尽力从 NoneBot driver 配置里取 HOST，取不到就按本地处理。"""
    try:
        return str(nonebot.get_driver().config.host)
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def _driver_port() -> int:
    """尽力从 NoneBot driver 配置里取 PORT，取不到退回 6188。"""
    try:
        return int(nonebot.get_driver().config.port)
    except Exception:  # noqa: BLE001
        return 6188


def _display_host(host: str) -> str:
    """0.0.0.0 / :: 这类监听地址在提示里换成回环地址更好点。"""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def setup_webui() -> None:
    """把 API 路由与静态页面挂到 NoneBot 的 FastAPI app 上。"""
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

    host = _driver_host()
    if host not in ("127.0.0.1", "localhost", "::1") and not settings.webui_token:
        logger.warning(
            f"⚠️ WebUI 绑定在 {host} 且未设置 WEBUI_TOKEN，管理接口可被任意访问，"
            "请设置 WEBUI_TOKEN 或改回 HOST=127.0.0.1"
        )

    page_hint = ""
    origin = f"http://{_display_host(host)}:{_driver_port()}"
    if INDEX_FILE.is_file():
        if ASSETS_DIR.is_dir():
            # cache_control 用 public：强制浏览器每次请求都带 If-None-Match 重新校验
            app.mount(
                "/assets",
                StaticFiles(directory=str(ASSETS_DIR)),
                name="webui-assets",
            )

        async def _page() -> HTMLResponse:  # pragma: no cover - 页面渲染
            html = INDEX_FILE.read_text(encoding="utf-8")
            html = html.replace(PREFIX_PLACEHOLDER, prefix)
            # 给静态资源加版本参数 + 页面本身禁用缓存，改前端代码后刷新页面即可看到
            html = html.replace("/assets/app.js", f"/assets/app.js?v={_STATIC_VERSION}")
            html = html.replace("/assets/style.css", f"/assets/style.css?v={_STATIC_VERSION}")
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})

        app.add_api_route("/", _page, methods=["GET"], include_in_schema=False)
        # 同时接受 /status、/config 这类路径（hash 路由之外的直达地址）
        for page in PAGE_NAMES:
            app.add_api_route(f"/{page}", _page, methods=["GET"], include_in_schema=False)

        page_hint = f"，页面 → {origin}/"
    else:
        logger.warning(f"未找到前端页面，未挂载控制台：{INDEX_FILE}")

    auth_state = "需 Bearer Token" if settings.webui_token else "无鉴权（仅建议本地使用）"
    logger.success(f"WebUI 已挂载：{origin}{prefix}/health（{auth_state}）{page_hint}")
