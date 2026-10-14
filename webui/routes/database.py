"""数据库管理接口：`/api/database*`（**只读**，不提供任意 SQL 执行）。

- 表清单只认 SQLite 里真实存在的表；
- 行数据分页读取，表名走白名单校验 + 双引号包裹，杜绝注入。
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from src.config import settings
from src.core.database import get_session
from webui.deps import human_size, require_token

router = APIRouter(dependencies=[Depends(require_token)], tags=["database"])

TABLE_LABELS = {
    "plugin_info": "插件元信息（启动时从代码同步）",
    "plugin_rule": "插件门控规则（全局 / 分群 / 分成员）",
    "user_permission": "用户权限授予（黑名单 / 群管理员）",
    "chat_message": "聊天记录（原始流水：群 + 私聊，含发送者 QQ）",
    "llm_group_profile": "群记忆（由定时总结提炼）",
}

MAX_LIMIT = 200


def _db_file() -> str:
    return settings.database_url.split("///", 1)[-1]


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


async def _table_names(session) -> list[str]:
    sql = text(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [str(row[0]) for row in (await session.execute(sql)).fetchall()]


def _ensure_table(name: str, allowed: list[str]) -> str:
    if name not in allowed:
        raise HTTPException(status_code=404, detail=f"未知数据表：{name}")
    if '"' in name:  # 保险起见（白名单已保证，理论上到不了这里）
        raise HTTPException(status_code=400, detail="表名非法")
    return name


@router.get("/database")
async def database_overview() -> dict:
    """数据库概览：文件信息 + 表清单（行数 / 列名）。"""
    path = _db_file()
    size = 0
    mtime = None
    for suffix in ("", "-wal", "-shm"):
        try:
            stat = os.stat(path + suffix)
            size += stat.st_size
            if suffix == "":
                mtime = datetime.fromtimestamp(stat.st_mtime).isoformat(
                    sep=" ", timespec="seconds"
                )
        except OSError:
            continue

    async with get_session(readonly=True) as session:
        journal = str((await session.execute(text("PRAGMA journal_mode"))).scalar_one())
        sqlite_version = str(
            (await session.execute(text("SELECT sqlite_version()"))).scalar_one()
        )
        names = await _table_names(session)

        tables = []
        for name in names:
            quoted = f'"{name}"'
            rows = int(
                (await session.execute(text(f"SELECT COUNT(*) FROM {quoted}"))).scalar_one()
            )
            columns = [
                str(row[1])
                for row in (
                    await session.execute(text(f"PRAGMA table_info({quoted})"))
                ).fetchall()
            ]
            tables.append(
                {
                    "name": name,
                    "label": TABLE_LABELS.get(name, ""),
                    "rows": rows,
                    "columns": columns,
                }
            )

    return {
        "file": {
            "path": path,
            "exists": os.path.exists(path),
            "size_bytes": size,
            "size_text": human_size(size),
            "modified_at": mtime,
        },
        "engine": {"dialect": "sqlite", "journal_mode": journal, "version": sqlite_version},
        "tables": tables,
    }


@router.get("/database/tables/{name}")
async def table_rows(
    name: str,
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """分页读取某张表的行数据（只读）。"""
    async with get_session(readonly=True) as session:
        allowed = await _table_names(session)
        _ensure_table(name, allowed)
        quoted = f'"{name}"'

        total = int(
            (await session.execute(text(f"SELECT COUNT(*) FROM {quoted}"))).scalar_one()
        )
        try:
            result = await session.execute(
                text(f"SELECT * FROM {quoted} ORDER BY rowid DESC LIMIT :limit OFFSET :offset"),
                {"limit": limit, "offset": offset},
            )
        except Exception:  # noqa: BLE001 - 少数表没有 rowid
            result = await session.execute(
                text(f"SELECT * FROM {quoted} LIMIT :limit OFFSET :offset"),
                {"limit": limit, "offset": offset},
            )

        columns = list(result.keys())
        rows = [[_jsonable(value) for value in row] for row in result.fetchall()]

    return {
        "name": name,
        "label": TABLE_LABELS.get(name, ""),
        "total": total,
        "limit": limit,
        "offset": offset,
        "columns": columns,
        "rows": rows,
    }
