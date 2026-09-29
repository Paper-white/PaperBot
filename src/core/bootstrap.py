"""启动/关闭钩子。"""

from __future__ import annotations

from src.core.database import dispose_db, init_db
from src.core.service import sync_plugin_info


async def on_startup() -> None:
    """建表 + 同步插件元信息。"""
    await init_db()
    await sync_plugin_info()


async def on_shutdown() -> None:
    await dispose_db()
