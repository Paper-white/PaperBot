"""启动/关闭钩子。"""

from __future__ import annotations

import nonebot

from src.core import hooks, registry
from src.core.database import dispose_db, init_db
from src.core.logging import shutdown_file_logging
from src.core.render import shutdown_renderer
from src.core.service import sync_plugin_info

logger = nonebot.logger


async def _load_plugin_configs() -> None:
    """给每个插件刷新配置缓存（插件代码里 `feature.cfg(...)` 读的就是它）。"""
    for name in registry.names():
        feature = registry.get(name)
        if feature is None:
            continue
        try:
            await feature.load_config()
        except Exception as exc:  # noqa: BLE001 - 单个插件配置读失败不影响启动
            feature.logger.opt(exception=exc).warning("读取插件配置失败，先用默认值")


async def on_startup() -> None:
    """建表 + 同步插件元信息 + 刷新插件配置 + 触发 on_load 钩子。"""
    await init_db()
    await sync_plugin_info()
    await _load_plugin_configs()
    fired = await hooks.fire_all("load", reason="startup")
    if fired:
        logger.info(f"已触发 {fired} 个插件的 on_load 钩子")


async def on_shutdown() -> None:
    # 先让插件自己收尾（关定时任务 / 落库），再动数据库与渲染器
    fired = await hooks.fire_all("unload", reason="shutdown")
    if fired:
        logger.info(f"已触发 {fired} 个插件的 on_unload 钩子")
    await shutdown_renderer()
    await dispose_db()
    # 最后再摘日志 sink，保证前面的关闭过程也进日志文件
    shutdown_file_logging()
