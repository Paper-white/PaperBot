"""共享数据库层。

全局单例 engine + async session 工厂。任何插件都可以：

    from src.core.database import get_session

    async with get_session() as session:
        session.add(obj)          # 退出上下文时自动 commit
        ...

model 定义在 `src.core.models.Base` 之上，插件 import 各自的 model 时会自动
注册到 `Base.metadata`，因此启动时 `init_db()` 能一次性建好所有表。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import settings
from src.core.logger import logger
from src.core.models import Base

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _ensure_engine() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """惰性创建 engine（避免 import 时就建连）。"""
    global _engine, _session_factory
    if _engine is None:
        url = settings.database_url

        # SQLite：确保数据目录存在
        if _is_sqlite(url):
            raw_path = url.split("///", 1)[-1]
            if raw_path and raw_path != ":memory:":
                Path(raw_path).parent.mkdir(parents=True, exist_ok=True)

        _engine = create_async_engine(url, echo=False, future=True)

        # SQLite 调优：开启 WAL（读写并发）+ busy_timeout（避免 database is locked）
        if _is_sqlite(url):

            @event.listens_for(_engine.sync_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _record) -> None:  # noqa: ANN001
                cursor = dbapi_conn.cursor()
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA busy_timeout=5000")
                    cursor.execute("PRAGMA foreign_keys=ON")
                finally:
                    cursor.close()

        _session_factory = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    assert _session_factory is not None
    return _engine, _session_factory


async def _add_missing_columns(conn) -> list[str]:  # noqa: ANN001 - AsyncConnection
    """SQLite 极简迁移：给「已存在但少列」的表补上 ORM 新增的列。

    `create_all()` 只建新表、不改旧表，所以在已有 `data/paperbot.db` 上
    新增字段（如 `plugin_info.repo`）会报 `no such column`。这里用
    `ALTER TABLE ... ADD COLUMN` 兜住：只加列、不改类型、不删列，安全无痛。

    跳过：主键列、以及「非空且无默认值」的列（SQLite 加不了，只记日志）。
    """
    from sqlalchemy import inspect as sa_inspect

    existing_tables = set(await conn.run_sync(lambda c: sa_inspect(c).get_table_names()))

    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {col["name"] for col in await conn.run_sync(
            lambda c, t=table.name: sa_inspect(c).get_columns(t)
        )}
        for column in table.columns:
            if column.name in have or column.primary_key:
                continue
            if not column.nullable and column.server_default is None:
                logger.warning(
                    f"表 {table.name} 缺少非空列 {column.name} 且无默认值，"
                    f"无法自动补列，请手工处理"
                )
                continue
            ddl_type = column.type.compile(dialect=conn.dialect)
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}'
            if column.server_default is not None:
                # server_default 是 SQL 片段（如 CURRENT_TIMESTAMP），原样拼进去
                default_sql = getattr(column.server_default, "arg", column.server_default)
                default_sql = getattr(default_sql, "text", default_sql)
                ddl += f" DEFAULT {default_sql}"
            await conn.exec_driver_sql(ddl)
            added.append(f"{table.name}.{column.name}")
    return added


async def _migrate_llm_fact_scope(conn) -> bool:  # noqa: ANN001 - AsyncConnection
    """把 llm_user_fact 的唯一键从 (user_id, fact_key) 重建为含 scope 的四元组。

    SQLite 的 UNIQUE 约束随建表生成、无法直接 DROP，只能整表重建（幂等：
    已存在 `uq_llm_fact_scope` 则跳过）。旧表保留为 `llm_user_fact_legacy` 以备回滚。
    """
    exists = (
        await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_user_fact'"
        )
    ).fetchall()
    if not exists:
        return False
    idx = {
        r[1]
        for r in (await conn.exec_driver_sql("PRAGMA index_list('llm_user_fact')")).fetchall()
    }
    if "uq_llm_fact_scope" in idx:
        return False

    await conn.exec_driver_sql("ALTER TABLE llm_user_fact RENAME TO llm_user_fact_legacy")
    # 旧名索引可能随表保留，删除以免与新表同名索引冲突（自动索引 sqlite_autoindex_* 不动）
    for old in ("ix_llm_user_fact_user_id", "ix_llm_user_fact_fact_key", "uq_llm_fact"):
        await conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{old}"')
    await conn.run_sync(lambda c: Base.metadata.tables["llm_user_fact"].create(c))
    await conn.exec_driver_sql(
        "INSERT INTO llm_user_fact "
        "(id, user_id, fact_key, fact_value, source, scope_type, scope_id, updated_at) "
        "SELECT id, user_id, fact_key, fact_value, source, "
        "COALESCE(scope_type, 'private'), COALESCE(scope_id, ''), "
        "COALESCE(updated_at, CURRENT_TIMESTAMP) "
        "FROM llm_user_fact_legacy"
    )
    return True


async def init_db() -> None:
    """建立连接并创建所有表（若不存在），并补齐旧表缺失的新列。"""
    engine, _ = _ensure_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        added = await _add_missing_columns(conn)
        migrated = await _migrate_llm_fact_scope(conn)
    if added:
        logger.info(f"数据库结构已自动补齐：{', '.join(added)}")
    if migrated:
        logger.info("llm_user_fact 唯一键已重建（含 scope），旧表保留为 llm_user_fact_legacy")


@asynccontextmanager
async def get_session(*, readonly: bool = False) -> AsyncIterator[AsyncSession]:
    """获取一个 session 上下文。

    - ``readonly=False``（默认）：退出时自动 commit（写路径用）；
    - ``readonly=True``：退出时不 commit（纯读路径用，省一次事务开销）。
    两种情况出错都会 rollback。
    """
    _, factory = _ensure_engine()
    async with factory() as session:
        try:
            yield session
            if not readonly:
                await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_db() -> None:
    """关闭连接池（进程退出时调用）。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
