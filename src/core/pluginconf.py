"""插件级配置项（对标 AstrBot 的 `_conf_schema.json`）。

插件在 `Feature(..., config_schema=[...])` 里声明自己的配置字段，框架负责：

1. 校验 schema 合法性（重复 key / 未知类型 / select 无选项等都直接报错）；
2. 值存进 **`.config/<插件名>/config.json`**（UTF-8、无 BOM、可直接手改）；
3. 读值时**合并默认值**，所以插件代码永远能拿到完整字典；
4. 在 WebUI「插件管理」页自动渲染表单（见 `webui/static/assets/app.js`）。

字段定义（都写在 dict 里，只有 `key` 必填）::

    {"key": "greet",         # 唯一键（必填）
     "label": "打招呼语",     # 表单标签，默认用 key
     "type": "string",       # string / text / int / float / bool / select
     "default": "你好",       # 默认值（未配置时使用）
     "hint": "支持 {name}",   # 表单提示
     "choices": ["a", "b"],  # type=select 时的可选项（必填）
     "secret": False}        # 是否敏感字段（WebUI 脱敏显示）

也可以偷懒写成字符串（等价于 `{"key": "greet"}`）。

## 文件布局

```
.config/                     ← 整个目录已 gitignore（含密钥，不进版本库）
├── main/.env                ← 框架自身配置（原来是根目录的 .env）
└── <插件名>/
    ├── config.json          ← 插件配置项（本模块读写）
    └── ...                  ← 插件自己的其它文件（用 feature.config_dir 拿目录）
```

`config.json` 是**扁平结构**，只有**显式改过**的键才会出现（没写的键实时回落 schema 默认值，
所以文件永远保持干净）；`_` 开头的键是框架保留的元信息，插件不要用::

    {
      "_plugin": "paperbot_ping",
      "_updated_at": "2026-10-14 06:30:00",
      "_updated_by": "webui",
      "greet": "你好呀"
    }
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

from src.config import config_dir  # `.config/` 根目录（项目根下）

FIELD_TYPES = ("string", "text", "int", "float", "bool", "select")

# `_` 开头的键是框架元信息，不算插件配置项
META_PREFIX = "_"

_TRUE_WORDS = {"true", "1", "yes", "y", "on", "是", "开"}
_FALSE_WORDS = {"false", "0", "no", "n", "off", "否", "关"}


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
def plugin_dir(plugin_name: str) -> Path:
    """某插件的配置目录 `.config/<插件名>/`（不存在则创建）。

    插件可以往这里放自己的文件（见 `Feature.config_dir`），
    框架只负责 `config.json` 这一个文件。
    """
    name = (plugin_name or "").strip()
    if not name:
        raise ValueError("插件名不能为空")
    # 防目录穿越：插件名只允许字母/数字/下划线/短横线/点
    if not all(ch.isalnum() or ch in "_-." for ch in name) or name.startswith("."):
        raise ValueError(f"插件名不能用于配置目录：{plugin_name!r}")
    path = config_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file(plugin_name: str) -> Path:
    """某插件的配置文件 `.config/<插件名>/config.json`。"""
    return plugin_dir(plugin_name) / "config.json"


# --------------------------------------------------------------------------- #
# schema 规范化
# --------------------------------------------------------------------------- #
def normalize_schema(schema: Optional[Iterable[Any]]) -> list[dict[str, Any]]:
    """把插件声明的 config_schema 规范化（补全字段并校验），失败时抛 ValueError。"""
    fields: list[dict[str, Any]] = []
    for raw in schema or []:
        if isinstance(raw, str):
            raw = {"key": raw}
        if not isinstance(raw, dict):
            raise ValueError(f"config_schema 字段必须是 dict 或 str，收到 {type(raw).__name__}")

        key = str(raw.get("key") or "").strip()
        if not key:
            raise ValueError("config_schema 字段缺少 key")
        if key.startswith(META_PREFIX):
            raise ValueError(f"config_schema[{key}]: key 不能以 {META_PREFIX!r} 开头（是框架保留前缀）")

        type_ = str(raw.get("type") or "string").strip().lower()
        if type_ not in FIELD_TYPES:
            raise ValueError(f"config_schema[{key}]: 不支持的类型 {type_!r}（可选 {FIELD_TYPES}）")

        choices = [str(c) for c in (raw.get("choices") or [])]
        if type_ == "select" and not choices:
            raise ValueError(f"config_schema[{key}]: type=select 必须提供 choices")

        fields.append(
            {
                "key": key,
                "label": str(raw.get("label") or key),
                "type": type_,
                "default": raw.get("default"),
                "hint": str(raw.get("hint") or ""),
                "choices": choices,
                "secret": bool(raw.get("secret")),
            }
        )

    keys = [f["key"] for f in fields]
    duplicated = sorted({k for k in keys if keys.count(k) > 1})
    if duplicated:
        raise ValueError(f"config_schema 存在重复 key：{', '.join(duplicated)}")
    return fields


def field_index(schema: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f["key"]: f for f in schema}


def default_value(field: dict[str, Any]) -> Any:
    """取字段默认值；未声明时按类型给一个「空值」。"""
    if field.get("default") is not None:
        return field["default"]
    type_ = field["type"]
    if type_ == "bool":
        return False
    if type_ in ("int", "float"):
        return 0
    if type_ == "select":
        return field["choices"][0] if field["choices"] else ""
    return ""


def coerce(field: dict[str, Any], value: Any) -> Any:
    """把外部传入的值按字段类型转换，失败抛 ValueError（带中文提示）。"""
    key = field["key"]
    type_ = field["type"]

    if value is None:
        return default_value(field)

    if type_ == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
        raise ValueError(f"{key} 需要是布尔值（true/false）")
    if type_ == "int":
        if isinstance(value, bool):
            raise ValueError(f"{key} 需要是整数")
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{key} 需要是整数") from None
    if type_ == "float":
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{key} 需要是数字") from None
    if type_ == "select":
        text = "" if value is None else str(value)
        if field["choices"] and text not in field["choices"]:
            raise ValueError(f"{key} 只能是：{' / '.join(field['choices'])}")
        return text
    return "" if value is None else str(value)


# --------------------------------------------------------------------------- #
# 文件读写
# --------------------------------------------------------------------------- #
def _now_text() -> str:
    """当前北京时间字符串（给 `_updated_at` 用，与 LLM 页展示口径一致）。"""
    from src.core.textutil import now_text

    return now_text()


def read_file(plugin_name: str) -> dict[str, Any]:
    """读 `config.json` 的**原始内容**（含 `_` 元信息）。

    文件不存在 / 内容损坏 / 不是 JSON 对象 → 返回空字典（并保留坏文件不覆盖）。
    """
    path = config_file(plugin_name)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _values_of(data: dict[str, Any]) -> dict[str, Any]:
    """从文件内容里挑出「配置项」（丢掉 `_` 元信息）。"""
    return {k: v for k, v in data.items() if not str(k).startswith(META_PREFIX)}


def _write_file(plugin_name: str, data: dict[str, Any]) -> None:
    """原子写 `config.json`（先写临时文件再 replace，避免写坏）。

    值全部为空时**删除文件**（等价于「什么都没改过」，保持目录干净）。
    """
    path = config_file(plugin_name)
    payload = {k: v for k, v in data.items() if not str(k).startswith(META_PREFIX)}
    if not payload:
        path.unlink(missing_ok=True)
        return

    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- #
# 对外 API（service / WebUI / 启动流程用）
# --------------------------------------------------------------------------- #
async def load_config(plugin_name: str) -> dict[str, Any]:
    """读某插件的配置（已合并默认值）。插件不存在或没配过也会返回完整字典。"""
    from src.core import registry  # 延迟导入，避免循环依赖

    feature = registry.get(plugin_name)
    schema = feature.config_schema if feature is not None else []
    values = {f["key"]: default_value(f) for f in schema}

    stored = _values_of(read_file(plugin_name))
    index = field_index(schema)
    for key, raw in stored.items():
        if key in index:
            # 手改过的文件里类型可能不对，按 schema 兜一层（错了就用默认值）
            try:
                values[key] = coerce(index[key], raw)
            except ValueError:
                values[key] = default_value(index[key])
        elif not schema:
            values[key] = raw  # 插件还没注册（罕见）：原样带出，便于排查
    return values


async def stored_config(plugin_name: str) -> dict[str, Any]:
    """只读「显式存过的」键值（不含默认值），供展示「是否被改过」。"""
    return _values_of(read_file(plugin_name))


async def stored_meta(plugin_name: str) -> dict[str, Any]:
    """只读文件里的元信息（`_updated_by` / `_updated_at`）。"""
    data = read_file(plugin_name)
    return {
        "updated_by": str(data.get("_updated_by") or ""),
        "updated_at": str(data.get("_updated_at") or ""),
    }


async def write_config(
    plugin_name: str, values: dict[str, Any], updated_by: str = ""
) -> list[str]:
    """写入若干配置项（按 schema 校验类型），返回实际发生变化的键名列表。

    值与现有值等价时不写盘；未在 schema 里声明的 key 直接忽略。
    """
    from src.core import registry

    feature = registry.get(plugin_name)
    if feature is None:
        raise ValueError(f"插件 {plugin_name} 未注册")
    index = field_index(feature.config_schema)

    validated: dict[str, Any] = {}
    for key, raw in values.items():
        field = index.get(key)
        if field is None:
            continue
        validated[key] = coerce(field, raw)

    data = read_file(plugin_name)
    existing = _values_of(data)

    changed: list[str] = []
    for key, value in validated.items():
        if key in existing and existing[key] == value:
            continue
        existing[key] = value
        changed.append(key)

    if changed:
        data.update(existing)
        data["_plugin"] = plugin_name
        data["_updated_by"] = updated_by
        data["_updated_at"] = _now_text()
        _write_file(plugin_name, data)
        await feature.load_config()  # 刷新插件侧缓存，立即生效
    return changed


async def clear_config(plugin_name: str, key: Optional[str] = None) -> int:
    """删除某插件的配置项（key=None 表示全部清空，退回默认值），返回删除条数。"""
    from src.core import registry

    data = read_file(plugin_name)
    existing = _values_of(data)
    targets = [k for k in existing if key is None or k == key]
    if not targets:
        return 0

    for k in targets:
        existing.pop(k, None)
    meta = {k: v for k, v in data.items() if str(k).startswith(META_PREFIX)}
    new_data = {**meta, **existing}
    if existing:
        new_data["_updated_by"] = "webui"
        new_data["_updated_at"] = _now_text()
    _write_file(plugin_name, new_data)

    feature = registry.get(plugin_name)
    if feature is not None:
        await feature.load_config()
    return len(targets)


async def clear_plugin_files(plugin_name: str) -> int:
    """删除某插件的整个配置目录（含插件自己放的文件），返回删除的文件数。"""
    path = plugin_dir(plugin_name)
    removed = 0
    for item in path.glob("**/*"):
        if item.is_file():
            item.unlink(missing_ok=True)
            removed += 1
    # 目录本身保留（插件可能马上又要写）
    return removed


def migrate_legacy_rows(rows: Iterable[Any]) -> dict[str, int]:
    """把旧 `plugin_config` 表的行搬进 JSON 文件（一次性迁移，同步执行）。

    `rows` 是带 `plugin_name` / `key` / `value` / `updated_by` 属性的对象序列。
    返回 {插件名: 迁移条数}。
    """
    moved: dict[str, int] = {}
    for row in rows:
        name = str(getattr(row, "plugin_name", "") or "")
        key = str(getattr(row, "key", "") or "")
        if not name or not key:
            continue
        try:
            raw = json.loads(getattr(row, "value", "") or "null")
        except (TypeError, ValueError):
            raw = getattr(row, "value", "")
        data = read_file(name)
        data.update(_values_of(data))
        if key in data and data[key] == raw:
            continue
        data[key] = raw
        data["_plugin"] = name
        data["_updated_by"] = str(getattr(row, "updated_by", "") or "legacy")
        data["_updated_at"] = _now_text()
        _write_file(name, data)
        moved[name] = moved.get(name, 0) + 1
    return moved


__all__ = [
    "FIELD_TYPES",
    "META_PREFIX",
    "clear_config",
    "clear_plugin_files",
    "coerce",
    "config_file",
    "default_value",
    "field_index",
    "load_config",
    "migrate_legacy_rows",
    "normalize_schema",
    "plugin_dir",
    "read_file",
    "stored_config",
    "stored_meta",
    "write_config",
]
