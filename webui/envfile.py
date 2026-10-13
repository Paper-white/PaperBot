"""框架 `.env` 的读取与安全写回。

位置：**`.config/main/.env`**（原来是根目录 `.env`，已随 `.config` 一起 gitignore）。

写回策略：**按行替换，保留注释、空行与顺序**；原本不存在的键追加到文件末尾。
未出现在 `updates` 里的键一律不动（避免误删密钥）。

兼容：若 `.config/main/.env` 还不存在而根目录有老的 `.env`，读的时候会回退到老文件
（`env_path()` 仍指向新位置；写回前请先 `migrate_legacy_env()` 搬一次）。
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

from src.config import LEGACY_ENV_FILE, MAIN_ENV_PATH

# 框架自身配置：`.config/main/.env`（WebUI 与 bot.py 的工作目录都是项目根）
_ENV_PATH = Path(MAIN_ENV_PATH)
_LEGACY_PATH = Path(LEGACY_ENV_FILE)

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def env_path() -> Path:
    """框架配置文件的**目标**路径（`.config/main/.env`，绝对路径）。"""
    return _ENV_PATH.resolve()


def legacy_env_path() -> Path:
    """老的根目录 `.env`（迁移用）。"""
    return _LEGACY_PATH.resolve()


def env_exists() -> bool:
    return _ENV_PATH.is_file()


def read_path() -> Path:
    """实际要读的文件：新位置优先，其次老位置。"""
    if _ENV_PATH.is_file():
        return _ENV_PATH
    return _LEGACY_PATH


def migrate_legacy_env() -> str:
    """把根目录的 `.env` 搬到 `.config/main/.env`。

    返回：`moved` / `kept`（新文件已存在，没动）/ `none`（两边都没有）。
    """
    if not _LEGACY_PATH.is_file():
        return "none"
    if _ENV_PATH.is_file():
        return "kept"
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(_LEGACY_PATH), str(_ENV_PATH))
    # 老的备份文件也一起挪走，免得看起来像还在用根目录配置
    legacy_bak = _LEGACY_PATH.with_name(_LEGACY_PATH.name + ".bak")
    if legacy_bak.is_file():
        shutil.move(str(legacy_bak), str(_ENV_PATH.with_name(_ENV_PATH.name + ".bak")))
    return "moved"


def _unquote(raw: str) -> str:
    """去掉值两侧的引号（保留内部内容）。"""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def read_env() -> dict[str, str]:
    """解析 .env → {KEY: 原始值（去引号）}。"""
    path = read_path()
    if not path.is_file():
        return {}
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LINE_RE.match(line)
        if match:
            data[match.group(1)] = _unquote(match.group(2))
    return data


def format_value(value: Any) -> str:
    """把 Python 值序列化成 .env 里的字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return json.dumps(list(value), ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def parse_bool(text: Any) -> bool:
    if isinstance(text, bool):
        return text
    return str(text).strip().lower() in ("1", "true", "yes", "on", "y")


def parse_list(text: Any) -> list[str]:
    """把 `["a","b"]` / `a,b` / `a b` 统一解析成字符串列表。"""
    if text is None:
        return []
    if isinstance(text, (list, tuple, set)):
        return [str(x) for x in text]
    raw = str(text).strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            pass
    parts = [p.strip().strip("'\"") for p in re.split(r"[,\s]+", raw)]
    return [p for p in parts if p]


def write_env(updates: dict[str, Any]) -> list[str]:
    """按行写回 `.config/main/.env`，返回**实际发生变化**的键列表。"""
    if not _ENV_PATH.is_file():
        raise FileNotFoundError(f"未找到配置文件：{env_path()}（可先迁移：migrate_legacy_env()）")
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)

    original = _ENV_PATH.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in original else "\n"
    had_trailing = original.endswith(("\n", "\r"))

    lines = original.splitlines()
    pending: dict[str, str] = {
        key: format_value(value) for key, value in updates.items()
    }
    changed: list[str] = []

    for index, line in enumerate(lines):
        match = _LINE_RE.match(line)
        if not match:
            continue
        key = match.group(1)
        if key not in pending:
            continue
        rendered = pending.pop(key)
        old_raw = _unquote(match.group(2))
        if old_raw == rendered:
            continue
        lines[index] = f"{key}={rendered}"
        changed.append(key)

    if pending:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"# -- 由 WebUI 于 {_now_text()} 追加 --")
        for key, rendered in pending.items():
            lines.append(f"{key}={rendered}")
            changed.append(key)

    text = newline.join(lines)
    if had_trailing:
        text += newline
    _ENV_PATH.write_text(text, encoding="utf-8", newline="")
    return changed


def _now_text() -> str:
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def redact(values: Iterable[str]) -> dict[str, str]:
    """把一组键的值转成脱敏摘要（仅用于日志）。"""
    return {key: "***" for key in values}
