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

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import settings
from src.core.models import Base

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _ensure_engine() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """惰性创建 engine（避免 import 时就建连）。"""
    global _engine, _session_factory
    if _engine is None:
        url = settings.database_url

        # SQLite：确保数据目录存在
        if url.startswith("sqlite"):
            raw_path = url.split("///", 1)[-1]
            if raw_path and raw_path != ":memory:":
                Path(raw_path).parent.mkdir(parents=True, exist_ok=True)

        _engine = create_async_engine(url, echo=False, future=True)
        _session_factory = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    assert _session_factory is not None
    return _engine, _session_factory


async def init_db() -> None:
    """建立连接并创建所有表（若不存在）。"""
    engine, _ = _ensure_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """获取一个自动提交/回滚的 session 上下文。"""
    _, factory = _ensure_engine()
    async with factory() as session:
        try:
            yield session
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
