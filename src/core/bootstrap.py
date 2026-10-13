"""启动/关闭钩子。"""

from __future__ import annotations

import nonebot

from src.config import settings
from src.core import hooks, llm, registry
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


async def _load_llm_presets() -> None:
    """把内置人设（`src/core/presets/`）同步进库（人工改过的不会被覆盖）。"""
    if not settings.llm_enable:
        return
    try:
        results = await llm.load_builtin_presets()
    except Exception as exc:  # noqa: BLE001 - 人设导入失败不该拦启动
        logger.opt(exception=exc).warning("导入内置人设失败，LLM 仍可手动配置人设")
        return
    created = [r["name"] for r in results if r.get("action") == "created"]
    if created:
        logger.info(f"已导入 {len(created)} 个内置人设：{', '.join(created)}")


async def _migrate_legacy_plugin_configs() -> None:
    """一次性迁移：旧 `plugin_config` 表 → `.config/<插件名>/config.json`。

    表里没数据 / 表不存在都直接跳过；迁移完把表改名为 `plugin_config_legacy`
    归档（不删数据，万一要回滚还在）。
    """
    from sqlalchemy import text

    from src.core import pluginconf
    from src.core.database import get_session

    try:
        async with get_session() as session:
            exists = (
                await session.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name='plugin_config'")
                )
            ).scalar_one_or_none()
            if not exists:
                return
            rows = (
                await session.execute(
                    text("SELECT plugin_name, key, value, updated_by FROM plugin_config")
                )
            ).all()
            if rows:
                moved = pluginconf.migrate_legacy_rows(
                    [
                        type("LegacyRow", (), {
                            "plugin_name": r[0], "key": r[1], "value": r[2], "updated_by": r[3]
                        })()
                        for r in rows
                    ]
                )
                total = sum(moved.values())
                logger.info(
                    f"插件配置已从数据库迁到 .config/：{total} 项"
                    f"（{', '.join(f'{k}×{v}' for k, v in moved.items())}）"
                )
            await session.execute(text("ALTER TABLE plugin_config RENAME TO plugin_config_legacy"))
            logger.info("旧 plugin_config 表已归档为 plugin_config_legacy")
    except Exception as exc:  # noqa: BLE001 - 迁移失败不该拦住启动
        logger.opt(exception=exc).warning("迁移旧插件配置失败（新的 JSON 配置不受影响）")


async def on_startup() -> None:
    """建表 + 迁移旧配置 + 同步插件元信息 + 刷新插件配置 + 导入内置人设 + 触发 on_load 钩子。"""
    await init_db()
    await _migrate_legacy_plugin_configs()
    await sync_plugin_info()
    await _load_plugin_configs()
    await _load_llm_presets()
    fired = await hooks.fire_all("load", reason="startup")
    if fired:
        logger.info(f"已触发 {fired} 个插件的 on_load 钩子")


async def on_shutdown() -> None:
    # 先让插件自己收尾（关定时任务 / 落库），再动数据库与渲染器
    fired = await hooks.fire_all("unload", reason="shutdown")
    if fired:
        logger.info(f"已触发 {fired} 个插件的 on_unload 钩子")
    await shutdown_renderer()
    await llm.shutdown_llm()
    await dispose_db()
    # 最后再摘日志 sink，保证前面的关闭过程也进日志文件
    shutdown_file_logging()
