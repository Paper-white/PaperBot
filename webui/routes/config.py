"""系统配置接口：`/api/config`（读写 `.config/main/.env`，敏感字段脱敏）。

⚠️ 所有改动**写回 `.config/main/.env`**，需要**重启 Bot** 才会生效；
写回前会自动备份为同目录下的 `.env.bak`。
"""

from __future__ import annotations

import shutil
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from src.config import active_groups, settings
from webui import envfile
from webui.deps import SECRET_PLACEHOLDER, mask_secret, require_token

router = APIRouter(dependencies=[Depends(require_token)], tags=["config"])


def _field(
    key: str,
    label: str,
    type_: str = "string",
    *,
    secret: bool = False,
    hint: str = "",
    choices: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "type": type_,
        "secret": secret,
        "hint": hint,
        "choices": choices or [],
    }


QQ_ADAPTER = {
    "id": "onebot11",
    "name": "QQ（OneBot v11）",
    "available": True,
    "note": "正向 WebSocket 连接协议端；`~websockets` 驱动必须开启",
    "fields": [
        _field(
            "ONEBOT_WS_URLS",
            "协议端 WebSocket 地址",
            "list",
            hint="如 [\"ws://127.0.0.1:3001\"]，支持多个",
        ),
        _field("ONEBOT_ACCESS_TOKEN", "访问令牌（秘令）", "string", secret=True),
    ],
}

TELEGRAM_ADAPTER = {
    "id": "telegram",
    "name": "Telegram",
    "available": False,
    "note": "暂未支持（尚未注册 telegram 适配器，字段仅作占位展示）",
    "fields": [
        _field("TELEGRAM_BOT_TOKEN", "Bot Token", "string", secret=True),
        _field("TELEGRAM_API_BASE", "API 反代地址"),
    ],
}

SECTIONS: list[dict[str, Any]] = [
    {
        "id": "runtime",
        "name": "基础运行",
        "note": "NoneBot 驱动、所有者与命令前缀（监听地址见「设置」页）",
        "fields": [
            _field(
                "DRIVER",
                "驱动",
                hint="正向 WS 必须带 ~websockets，如 ~fastapi+~httpx+~websockets",
            ),
            _field("SUPERUSERS", "所有者 QQ 列表", "list", hint="3 级权限，可执行全部管理命令"),
            _field("COMMAND_START", "命令前缀", "list", hint="如 [\"%\"]"),
        ],
    },
    {
        "id": "guard",
        "name": "消息安全（门控 + 守卫）",
        "note": "群白名单为唯一来源：门控默认策略与消息输出守卫共用；私聊一律拦截",
        "fields": [
            _field(
                "BOT_ACTIVE_GROUPS",
                "允许活跃的群（白名单）",
                "list",
                hint="如 [\"123456789\"]；为空 = 不向任何群发消息（安全默认）",
            ),
            _field(
                "GATE_DEFAULT_ENABLED",
                "门控默认跟随插件",
                "bool",
                hint="关闭 = 全局默认禁用，未配置规则的群按白名单判断；开启 = 跟随插件默认值",
            ),
            _field("OUTPUT_GUARD_ENABLED", "启用消息输出守卫", "bool"),
        ],
    },
    {
        "id": "database",
        "name": "数据库",
        "note": "所有插件共享同一个 SQLite",
        "fields": [
            _field("DATABASE_URL", "连接串", hint="sqlite+aiosqlite:///data/paperbot.db"),
        ],
    },
    {
        "id": "llm",
        "name": "LLM 兜底与行为",
        "note": "服务商 / 人设建议在「LLM」页管理（存库、即时生效）；这里的 .env 配置只作兜底",
        "fields": [
            _field(
                "LLM_ENABLE",
                "启用 LLM",
                "bool",
                hint="关闭后所有 LLM 调用直接报错（插件会提示用不了）",
            ),
            _field("LLM_BASE_URL", "兜底 API 地址", hint="如 https://api.openai.com/v1"),
            _field("LLM_API_KEY", "兜底 API Key", secret=True, hint="留空 = 只用库里的服务商"),
            _field("LLM_MODEL", "兜底模型名"),
            _field("LLM_DEFAULT_PRESET", "默认人设名", hint="未指定人设时用它，如 paper_white"),
            _field("LLM_STREAM_ENABLE", "默认流式输出", "bool", hint="插件显式传 use_stream 时以插件为准"),
            _field("LLM_MEMORY_ENABLE", "启用长期记忆", "bool", hint="关闭后不再注入印象 / 关键信息"),
            _field("LLM_CONTEXT_ROUNDS", "上下文轮数", "int", hint="1 轮 = 用户 + 助手"),
            _field("LLM_IMPRESSION_EVERY", "每 N 条消息提炼印象", "int"),
            _field("LLM_MAX_FACTS", "最多注入几条关键信息", "int"),
            _field("LLM_TIMEOUT", "请求超时（秒）", "float"),
            _field("LLM_PROXY", "代理地址", hint="如 http://127.0.0.1:2080，留空直连"),
        ],
    },
    {
        "id": "render",
        "name": "图片渲染",
        "note": "文本 / Markdown → PNG",
        "fields": [
            _field("RENDER_WIDTH", "画布宽度", "int"),
            _field("RENDER_SCALE", "缩放倍率", "int", hint="输出宽度 = 宽度 × 倍率"),
            _field("RENDER_BROWSER_CHANNEL", "浏览器通道", choices=["msedge", "chromium"]),
            _field("RENDER_BROWSER_PATH", "浏览器可执行文件", hint="留空则用系统浏览器"),
            _field("RENDER_BG_IMAGE", "背景贴图", hint="留空不叠图"),
            _field("RENDER_BG_IMAGE_OPACITY", "叠图不透明度", "float"),
            _field("RENDER_BG_IMAGE_SIZE", "叠图尺寸"),
            _field("RENDER_BG_IMAGE_POSITION", "叠图位置"),
        ],
    },
]

# 「设置」页里的配置分组（服务监听 + 管理后台 + 日志）。
# 说明：令牌（WEBUI_TOKEN）不在这里——设置页有专属卡片，可**不改 .env 即热更新**。
SETTINGS_SECTIONS: list[dict[str, Any]] = [
    {
        "id": "server",
        "name": "服务与后台",
        "note": "监听地址与 WebUI 本体（改动需重启 Bot）",
        "fields": [
            _field("HOST", "监听地址", hint="127.0.0.1 仅本机可访问；0.0.0.0 对局域网开放"),
            _field("PORT", "监听端口", "int"),
            _field("WEBUI_ENABLE", "启用 WebUI", "bool"),
            _field("WEBUI_PREFIX", "API 前缀"),
        ],
    },
    {
        "id": "logging",
        "name": "日志",
        "note": "终端 + 每次运行的日志文件（logs/ 目录）",
        "fields": [
            _field(
                "LOG_LEVEL",
                "终端日志级别",
                choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            ),
            _field("LOG_FILE_ENABLE", "启用日志文件", "bool"),
            _field(
                "LOG_FILE_LEVEL",
                "文件日志级别",
                choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                hint="建议 DEBUG，便于事后排查",
            ),
            _field("LOG_DIR", "日志目录", hint="相对项目根目录"),
            _field("LOG_KEEP_DAYS", "保留天数", "int", hint="启动时清理更早的日志，<=0 不清理"),
        ],
    },
]

_FIELD_INDEX: dict[str, dict[str, Any]] = {
    f["key"]: f for section in (SECTIONS + SETTINGS_SECTIONS) for f in section["fields"]
}
_FIELD_INDEX.update({f["key"]: f for f in QQ_ADAPTER["fields"]})
_FIELD_INDEX.update({f["key"]: f for f in TELEGRAM_ADAPTER["fields"]})


def _backup_path():
    env = envfile.env_path()
    return env.with_name(env.name + ".bak")


def _render_field(field: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    raw = env.get(field["key"])
    item = dict(field)
    if field["secret"]:
        info = mask_secret(raw)
        item["value"] = info["value"]
        item["configured"] = info["configured"]
        item["length"] = info["length"]
        return item
    item["value"] = _render_value(field, raw)
    item["configured"] = raw is not None and str(raw).strip() != ""
    return item


def _render_fields(fields: list[dict[str, Any]], env: dict[str, str]) -> list[dict]:
    return [_render_field(field, env) for field in fields]


def _render_section(section: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    return {**section, "fields": _render_fields(section["fields"], env)}


def _render_value(field: dict[str, Any], raw: Optional[str]) -> Any:
    if raw is None:
        return None
    if field["type"] == "bool":
        return envfile.parse_bool(raw)
    if field["type"] == "list":
        return envfile.parse_list(raw)
    return raw


def render_sections(
    sections: list[dict[str, Any]], env: Optional[dict[str, str]] = None
) -> list[dict[str, Any]]:
    """把分组定义渲染成带当前值的结构（设置页/配置页共用）。"""
    if env is None:
        env = envfile.read_env()
    return [_render_section(section, env) for section in sections]


@router.get("/config")
async def get_config() -> dict:
    """读取 `.config/main/.env` 并返回分组的配置项（敏感字段已脱敏）。"""
    env = envfile.read_env()
    adapters = []
    for adapter in (QQ_ADAPTER, TELEGRAM_ADAPTER):
        adapters.append({**adapter, "fields": _render_fields(adapter["fields"], env)})
    return {
        "path": str(envfile.env_path()),
        "exists": envfile.env_exists(),
        "adapters": adapters,
        "sections": render_sections(SECTIONS, env),
        "defaults": {
            "host": "127.0.0.1",
            "guard_groups": sorted(active_groups()),
        },
    }


def _coerce(field: dict[str, Any], value: Any) -> Any:
    if field["type"] == "bool":
        return envfile.parse_bool(value)
    if field["type"] == "list":
        parsed = envfile.parse_list(value)
        if not parsed and value not in (None, "", []):
            raise HTTPException(
                status_code=400, detail=f"{field['key']} 需要是列表（如 [\"a\",\"b\"]）"
            )
        return parsed
    if field["type"] == "int":
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{field['key']} 需要是整数") from None
    if field["type"] == "float":
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{field['key']} 需要是数字") from None
    return "" if value is None else str(value)


def _is_same_value(field: dict[str, Any], raw: Optional[str], coerced: Any) -> bool:
    """判断新值与配置文件里的旧值是否等价（避免无意义的写盘与备份）。"""
    if raw is None:
        return False
    kind = field["type"]
    if kind == "bool":
        return envfile.parse_bool(raw) == bool(coerced)
    if kind == "list":
        return envfile.parse_list(raw) == [str(x) for x in envfile.parse_list(coerced)]
    if kind in ("int", "float"):
        try:
            return float(str(raw).strip()) == float(coerced)
        except (TypeError, ValueError):
            return False
    return str(raw) == str(coerced)


@router.put("/config")
async def put_config(payload: dict = Body(...)) -> dict:
    """批量写回 `.config/main/.env`（只处理出现过的键；敏感字段留空/占位则不改）。"""
    values = payload.get("values")
    if not isinstance(values, dict) or not values:
        raise HTTPException(status_code=400, detail="values 不能为空")

    unknown = [k for k in values if k not in _FIELD_INDEX]
    if unknown:
        raise HTTPException(status_code=400, detail=f"不支持的配置项：{', '.join(unknown)}")

    env = envfile.read_env()
    updates: dict[str, Any] = {}
    for key, raw_value in values.items():
        field = _FIELD_INDEX[key]
        if field["secret"]:
            text = "" if raw_value is None else str(raw_value)
            if text in ("", SECRET_PLACEHOLDER):
                continue  # 未改动 → 保持原值
            if text == env.get(key):
                continue
            updates[key] = text
            continue
        coerced = _coerce(field, raw_value)
        if _is_same_value(field, env.get(key), coerced):
            continue  # 与现有值等价 → 不写盘
        updates[key] = coerced

    if not updates:
        return {"ok": True, "changed": [], "message": "没有需要修改的配置项"}

    backup = _backup_path()
    try:
        shutil.copyfile(envfile.env_path(), backup)
    except OSError:
        backup = None

    try:
        changed = envfile.write_env(updates)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"写入 .config/main/.env 失败：{exc}") from exc

    return {
        "ok": True,
        "changed": changed,
        "backup": str(backup) if backup else "",
        "restart_required": True,
        "message": "已写入 .config/main/.env，需重启 Bot 生效",
    }
