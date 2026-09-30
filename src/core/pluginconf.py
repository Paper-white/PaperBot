"""插件级配置项（对标 AstrBot 的 `_conf_schema.json`）。

插件在 `Feature(..., config_schema=[...])` 里声明自己的配置字段，框架负责：

1. 校验 schema 合法性（重复 key / 未知类型 / select 无选项等都直接报错）；
2. 值存进共享表 `plugin_config`（`plugin_name + key` 唯一）；
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
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from sqlalchemy import select

from src.core.database import get_session
from src.core.models import PluginConfig

FIELD_TYPES = ("string", "text", "int", "float", "bool", "select")

_TRUE_WORDS = {"true", "1", "yes", "y", "on", "是", "开"}
_FALSE_WORDS = {"false", "0", "no", "n", "off", "否", "关"}


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
# 值序列化
# --------------------------------------------------------------------------- #
def dump_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


def load_value(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


# --------------------------------------------------------------------------- #
# 存储读写
# --------------------------------------------------------------------------- #
async def _rows(session, plugin_name: str) -> list[PluginConfig]:
    stmt = select(PluginConfig).where(PluginConfig.plugin_name == plugin_name)
    return list((await session.execute(stmt)).scalars().all())


async def load_config(plugin_name: str) -> dict[str, Any]:
    """读某插件的配置（已合并默认值）。插件不存在或没配过也会返回完整字典。"""
    from src.core import registry  # 延迟导入，避免循环依赖

    feature = registry.get(plugin_name)
    schema = feature.config_schema if feature is not None else []
    values = {f["key"]: default_value(f) for f in schema}

    async with get_session(readonly=True) as session:
        for row in await _rows(session, plugin_name):
            if row.key in values:
                values[row.key] = load_value(row.value)
    return values


async def stored_config(plugin_name: str) -> dict[str, Any]:
    """只读「显式存过的」键值（不含默认值），供展示「是否被改过」。"""
    async with get_session(readonly=True) as session:
        return {row.key: load_value(row.value) for row in await _rows(session, plugin_name)}


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

    changed: list[str] = []
    async with get_session() as session:
        existing = {row.key: row for row in await _rows(session, plugin_name)}
        for key, value in validated.items():
            row = existing.get(key)
            if row is None:
                session.add(
                    PluginConfig(
                        plugin_name=plugin_name,
                        key=key,
                        value=dump_value(value),
                        updated_by=updated_by,
                    )
                )
                changed.append(key)
                continue
            if load_value(row.value) == value:
                continue
            row.value = dump_value(value)
            row.updated_by = updated_by
            changed.append(key)

    if changed:
        await feature.load_config()  # 刷新插件侧缓存，立即生效
    return changed


async def clear_config(plugin_name: str, key: Optional[str] = None) -> int:
    """删除某插件的配置项（key=None 表示全部清空，退回默认值），返回删除条数。"""
    from src.core import registry

    async with get_session() as session:
        rows = await _rows(session, plugin_name)
        targets = [r for r in rows if key is None or r.key == key]
        for row in targets:
            await session.delete(row)

    feature = registry.get(plugin_name)
    if feature is not None and targets:
        await feature.load_config()
    return len(targets)
