"""CustomSkinLoader 日志解析插件（paperbot_csl）。"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from typing import Any, Optional

import httpx
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupUploadNoticeEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.rule import Rule

from src.core.feature import Feature
from src.core.textutil import now_local

feature = Feature(
    "paperbot_csl",
    display_name="皮肤日志解析",
    description="解析群内上传的 CustomSkinLoader 日志（版本 / 时间 / 站点 / 玩家 / 皮肤披风来源）",
    author="paper_white",
    repo="https://github.com/paper-white/PaperBot",
    version="0.1.0",
    requires=">=1.0,<2.0",
    config_schema=[
        {
            "key": "filename_pattern",
            "label": "文件名匹配（正则，忽略大小写）",
            "type": "string",
            "default": r"CustomSkinLoader.*\.log$",
            "hint": "默认匹配 CustomSkinLoader.log、CustomSkinLoader (1).log 等",
        },
        {
            "key": "max_size_mb",
            "label": "最大文件大小（MB）",
            "type": "int",
            "default": 2,
            "hint": "超过则拒绝下载，防止被塞大文件",
        },
        {
            "key": "show_loadlist",
            "label": "显示加载站点列表",
            "type": "bool",
            "default": True,
        },
        {
            "key": "max_listed_sites",
            "label": "站点列表最多显示几个",
            "type": "int",
            "default": 15,
        },
        {
            "key": "stale_days",
            "label": "老日志阈值（天）",
            "type": "int",
            "default": 3,
            "hint": "日志日期早于当前日期超过这么多天时，附加「建议重新生成」提示",
        },
    ],
)

# 14.6a 及更早没有 [CustomSkinLoader X] logger 标记，且 LEVEL 分隔有空格式与斜杠两种写法
_HDR = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] "
    r"\[([^\[\]]+?)\] "
    r"(?:\[CustomSkinLoader [A-Za-z]+\]: )?"
    r"(.*)$"
)
_HASH_URL = re.compile(r"/(?:texture|textures)/([0-9a-f]{32,64})")
_HEX = re.compile(r"([0-9a-f]{32,64})")
_LEVEL_SUFFIX = re.compile(r"[\s/](?:INFO|DEBUG|WARN|WARNING|ERROR|TRACE)\s*$")
# 14.6a 有 ElytraUrl 字段，14.15+ 没有
_FINAL = re.compile(r"\(SkinUrl: (\S+)\s*, Model: (\S+)\s*, CapeUrl: (\S+)\s*(?:, ElytraUrl: \S+)?\s*, Expiry")

_TEXTURE_CDN_SITES = {
    "textures.minecraft.net": "Mojang",
    "littleskin.cn": "LittleSkin",
}

_DEFAULT_14_6A_LOADLIST: list[dict] = [
    {"name": "Mojang", "type": "MojangAPI"},
    {"name": "BlessingSkin", "type": "CustomSkinAPI", "root": "http://skin.prinzeugen.net/"},
    {"name": "OneSkin", "type": "CustomSkinAPI", "root": "http://fleey.org/skin/skin_user/skin_json.php/"},
    {"name": "SkinMe", "type": "UniSkinAPI", "root": "http://www.skinme.cc/uniskin/"},
    {"name": "LocalSkin", "type": "Legacy", "checkPNG": "false",
     "skin": "LocalSkin/skins/{USERNAME}.png", "model": "default",
     "cape": "LocalSkin/capes/{USERNAME}.png", "elytra": "LocalSkin/elytras/{USERNAME}.png"},
]


def _iter_messages(text: str) -> list[list[str]]:
    out: list[list[str]] = []
    for raw in text.splitlines():
        m = _HDR.match(raw)
        if m:
            out.append([m.group(1), m.group(2), m.group(3)])
        elif out:
            out[-1][2] += "\n" + raw
    return out


def _thread_source(thread: str) -> Optional[str]:
    t = _LEVEL_SUFFIX.sub("", thread)
    m = re.search(r"\(([A-Za-z0-9_]+)\)\s*$", t)
    if m:
        return m.group(1)
    return None


def _thread_player(thread: str, known: set[str]) -> Optional[str]:
    t = _LEVEL_SUFFIX.sub("", thread)
    t = re.sub(r"\s*\([A-Za-z0-9_]+\)\s*$", "", t).strip()
    if t in known:
        return t
    if t.endswith("'s skull"):
        base = t[: -len("'s skull")]
        if base in known:
            return base
    m = re.match(r"^(.*?)\s*<[0-9a-fA-F-]+>$", t)
    if m and m.group(1).strip() in known:
        return m.group(1).strip()
    return None


def _legacy_template_source(
    final_url: str, kind: str, player: str, full: list[dict]
) -> Optional[str]:
    if kind == "skin":
        if not final_url.startswith("(LOCAL_LEGACY)"):
            return None
        body = final_url[len("(LOCAL_LEGACY)"):]
        path = body.split(",", 1)[1] if "," in body else ""
        for item in full:
            tmpl = item.get("skin")
            if tmpl and path and tmpl.replace("{USERNAME}", player) == path:
                return item.get("name")
        return None
    if kind == "cape":
        if not final_url.startswith("(LEGACY)"):
            return None
        url = final_url[len("(LEGACY)"):]
        for item in full:
            tmpl = item.get("cape")
            if tmpl and url and tmpl.replace("{USERNAME}", player) == url:
                return item.get("name")
        return None
    return None


def _version_tuple(ver: str) -> tuple:
    parts: list = []
    for p in re.split(r"([a-zA-Z]+)", ver):
        if not p:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)*", p):
            parts.extend(int(x) for x in p.split("."))
        else:
            parts.append(p)
    return tuple(parts)


def _version_le(ver: str, target: str) -> bool:
    return _version_tuple(ver) <= _version_tuple(target)


def _is_1_7_10_forced_default(game_version: str, csl_version: str, model: str) -> bool:
    return (
        game_version == "1.7.10"
        and _version_le(csl_version, "14.6a")
        and model == "default"
    )


def _unproxy_url(url: str) -> str:
    m = re.match(r"https?://127\.0\.0\.1:\d+/(https?)/(.+)", url)
    if m:
        return f"{m.group(1)}://{m.group(2)}"
    return url


def _fallback_source_by_url(url: str) -> Optional[str]:
    if not url or url in ("null", "(null)"):
        return None
    if url.startswith("(LOCAL_LEGACY)") or url.startswith("(LEGACY)"):
        return None
    return _TEXTURE_CDN_SITES.get(_host(url))


def _has_clfcsl(text: str) -> bool:
    return (
        re.search(r"CompatibilityLayerForCustomSkinLoader", text) is not None
        or re.search(r"Successfully transformed method", text) is not None
    )


def _suggest_clfcsl_version(game_version: str, version: str, build: str, text: str) -> bool:
    if not game_version.startswith("1.7"):
        return False
    if not _has_clfcsl(text):
        return False
    if version == "14.15" and build == "348":
        return False
    return True


def _hash_from_url(url: str) -> Optional[str]:
    if not url or url in ("null", "(null)"):
        return None
    m = _HASH_URL.search(url)
    if m:
        return m.group(1)
    m = _HEX.search(url)
    return m.group(1) if m else None


def _host(url: str) -> str:
    if not url:
        return ""
    url = _unproxy_url(url)
    m = re.match(r"^[a-zA-Z]+://([^/]+)", url)
    return (m.group(1) if m else url).lower()


def _site_for_url(url: str, order: list[str], full: list[dict]) -> Optional[str]:
    h = _host(url)
    if h:
        for item in full:
            for key in ("root", "apiRoot", "sessionRoot", "cape", "skin"):
                cand = item.get(key) or ""
                cand = cand.split("{")[0]
                if cand and _host(cand) == h:
                    return item.get("name")
    low = url.lower()
    for name in order:
        if name and name.lower() in low:
            return name
    return None


def _try_json(content: str) -> Any:
    try:
        return json.loads(content)
    except Exception:
        return None


def _produce(name: str, typ: str, content: str, requests: list[str]) -> tuple[Optional[str], Any]:
    obj = _try_json(content) if content else None

    if typ == "CustomSkinAPI":
        if isinstance(obj, dict):
            skin = None
            skins = obj.get("skins")
            if isinstance(skins, dict):
                for v in skins.values():
                    if v:
                        skin = v if isinstance(v, str) else None
                        if skin:
                            break
            cape = obj.get("cape") or None
            skin = _hash_from_url(skin) if skin else None
            cape = _hash_from_url(cape) if cape else None
            return skin, cape
        return None, None

    if typ in ("ElyByAPI", "UniSkinAPI", "GlitchlessAPI"):
        if isinstance(obj, dict):
            skin = cape = None
            s = obj.get("SKIN")
            if isinstance(s, dict) and s.get("url"):
                skin = _hash_from_url(s["url"])
            c = obj.get("CAPE")
            if isinstance(c, dict) and c.get("url"):
                cape = _hash_from_url(c["url"])
            return skin, cape
        return None, None

    if typ == "MojangAPI":
        if isinstance(obj, dict):
            skin = cape = None
            for prop in obj.get("properties", []) or []:
                val = prop.get("value") if isinstance(prop, dict) else None
                if not val:
                    continue
                try:
                    raw = base64.b64decode(val).decode("utf-8", "replace")
                    tex = (json.loads(raw) or {}).get("textures", {})
                    if isinstance(tex, dict):
                        s = tex.get("SKIN") or {}
                        c = tex.get("CAPE") or {}
                        if isinstance(s, dict) and s.get("url"):
                            skin = skin or _hash_from_url(s["url"])
                        if isinstance(c, dict) and c.get("url"):
                            cape = cape or _hash_from_url(c["url"])
                except Exception:
                    continue
            return skin, cape
        return None, None

    if typ == "MinecraftCapesAPI":
        cape = None
        if isinstance(obj, dict):
            url = obj.get("cape_url")
            if not url and isinstance(obj.get("textures"), dict):
                url = obj["textures"].get("cape")
            if url:
                cape = _hash_from_url(url)
        return None, cape

    if typ == "Legacy":
        if requests:
            return None, ("legacy", requests[-1])
        return None, None

    return None, None


def parse_csl_log(text: str) -> dict:
    msgs = _iter_messages(text)

    ver_m = re.search(r"CustomSkinLoader ([\d.]+[a-zA-Z]?)", text)
    if not ver_m:
        ver_m = re.search(r"version : ([\d.]+[a-zA-Z]?)", text)
    version = ver_m.group(1) if ver_m else ""

    game_ver_m = re.search(r"Minecraft:\s*([\d.]+)", text)
    game_version = game_ver_m.group(1) if game_ver_m else ""

    build_m = re.search(r"buildNumber : (\d+)", text)
    build = build_m.group(1) if build_m else ""

    last_time = msgs[-1][0] if msgs else ""

    full_loadlist: list[dict] = []
    ll_m = re.search(r"loadlist : (\[.*\])\s*$", text, re.M)
    if ll_m:
        parsed = _try_json(ll_m.group(1))
        if isinstance(parsed, list):
            full_loadlist = [x for x in parsed if isinstance(x, dict)]

    if not full_loadlist and game_version and _version_le(version, "14.6a"):
        full_loadlist = list(_DEFAULT_14_6A_LOADLIST)

    order = [x.get("name", "") for x in full_loadlist]

    player_order: list[str] = []
    ev_by_player: dict[str, dict[str, dict]] = {}
    final_by_player: dict[str, tuple] = {}

    def _ensure(p: str) -> None:
        if p not in ev_by_player:
            ev_by_player[p] = {}
            player_order.append(p)

    for _ts, _thread, msg in msgs:
        if msg.startswith("Loading ") and msg.rstrip().endswith("'s profile."):
            _ensure(msg[len("Loading"): msg.rindex("'s profile.")].strip())

    known = set(player_order)
    pending_by_player: dict[str, Optional[str]] = {p: None for p in player_order}
    for _ts, thread, msg in msgs:
        player = _thread_player(thread, known)
        if player is None:
            continue
        _ensure(player)
        ev = ev_by_player[player]
        src = _thread_source(thread)
        if msg.startswith("Try to request '"):
            url = msg.split("'", 2)[1]
            if src is None:
                src = _site_for_url(url, order, full_loadlist)
                pending_by_player[player] = src
            if src is not None:
                ev.setdefault(src, {}).setdefault("requests", []).append(url)
        eff = src or pending_by_player.get(player)
        if eff and "Content:" in msg:
            ev.setdefault(eff, {})["content"] = msg.split("Content:", 1)[1].strip()
        fm = _FINAL.search(msg)
        if fm:
            final_by_player[player] = fm.groups()

    def _type_of(name: str) -> str:
        return next((x.get("type", "") for x in full_loadlist if x.get("name") == name), "")

    results: list[dict] = []
    for player in player_order:
        ev = ev_by_player.get(player, {})
        final = final_by_player.get(player)
        info = {"player": player, "skin_source": None, "cape_source": None, "loaded": False}
        if final:
            skin_url, model, cape_url = final[0], final[1], final[2]
            fskin = _hash_from_url(skin_url)
            fcape = _hash_from_url(cape_url)
            _player = player
            info["model"] = model
            info["model_forced"] = _is_1_7_10_forced_default(game_version, version, model)

            def produced(target: str) -> Optional[str]:
                if target == "skin" and skin_url.startswith("(LOCAL_LEGACY)"):
                    return _legacy_template_source(skin_url, "skin", _player, full_loadlist)
                if target == "cape" and cape_url.startswith("(LEGACY)"):
                    hit = _legacy_template_source(cape_url, "cape", _player, full_loadlist)
                    if hit:
                        return hit
                for name in order:
                    e = ev.get(name)
                    if not e:
                        continue
                    sk, cp = _produce(name, _type_of(name), e.get("content", ""), e.get("requests", []))
                    if target == "skin" and sk and sk == fskin:
                        return name
                    if target == "cape" and cp:
                        if isinstance(cp, tuple) and cp[0] == "legacy":
                            if cape_url.replace("(LEGACY)", "") == cp[1]:
                                return name
                            continue
                        if cp == fcape:
                            return name
                return None

            info["skin_source"] = produced("skin")
            info["cape_source"] = produced("cape")
            if _version_le(version, "14.6a"):
                if not info["skin_source"]:
                    info["skin_source"] = _fallback_source_by_url(skin_url)
                if not info["cape_source"]:
                    info["cape_source"] = _fallback_source_by_url(cape_url)
            info["loaded"] = bool(fskin) or bool(fcape)

        results.append(info)

    players = results
    main_player = player_order[0] if player_order else ""

    is_csl = re.search(r"\[CustomSkinLoader [A-Za-z]+\]", text) is not None or bool(version)
    if order or any(final_by_player.values()):
        status = "ok"
    elif is_csl:
        status = "incomplete"
    else:
        status = "not_csl"

    return {
        "ok": status == "ok",
        "status": status,
        "is_csl": is_csl,
        "version": version,
        "build": build,
        "game_version": game_version,
        "last_time": last_time,
        "loadlist": order,
        "main_player": main_player,
        "players": players,
        "has_clfcsl": _has_clfcsl(text),
        "suggest_clfcsl": _suggest_clfcsl_version(game_version, version, build, text),
    }


def _log_age_days(last_time: str, now: Optional[datetime] = None) -> Optional[float]:
    if not last_time:
        return None
    try:
        dt = datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    ref = now or now_local()
    if ref.tzinfo is not None:
        ref = ref.replace(tzinfo=None)
    return (ref - dt).total_seconds() / 86400.0


def format_result(
    res: dict,
    *,
    show_loadlist: bool = True,
    max_sites: int = 15,
    stale_days: int = 3,
    now: Optional[datetime] = None,
) -> str:
    status = res.get("status") or ("ok" if res.get("ok") else "not_csl")

    if status == "not_csl":
        return "这不像是CustomSkinLoader的日志，或异常加载的日志，请等待人工处理"

    if status == "incomplete":
        lines = ["📋 CustomSkinLoader 日志解析"]
        lines.append(f"生成时间：{res.get('last_time') or '未知'}")
        lines.append("")
        lines.append("⚠️ 日志不完整：没有皮肤加载记录，可能是启动阶段或模组加载出错。")
        lines.append("建议重新启动游戏、完整加载皮肤后再上传日志～")
        return "\n".join(lines)

    lines = ["📋 CustomSkinLoader 日志解析"]
    ver = res.get("version") or "未知"
    if res.get("build"):
        ver += f" (build {res['build']})"
    lines.append(f"CSL版本：{ver}")
    if res.get("game_version"):
        lines.append(f"游戏版本：{res['game_version']}")
    lines.append(f"生成时间：{res.get('last_time') or '未知'}")

    main_player = res.get("main_player") or "未知"
    main_info = next((p for p in res.get("players", []) if p["player"] == main_player), None)
    genuine = bool(main_info and main_info.get("skin_source") == "Mojang")
    lines.append(f"主玩家：{main_player}" + ("（命中正版）" if genuine else ""))
    if main_info:
        skin_src = _src_text(main_info.get("skin_source"))
        cape_src = _src_text(main_info.get("cape_source"))
        model = main_info.get("model")
        if model and main_info.get("skin_source"):
            skin_src = f"{skin_src}（{model}模型）"
        lines.append(f"皮肤来源：{skin_src}")
        lines.append(f"披风来源：{cape_src}")
        if main_info.get("model_forced") and main_info.get("skin_source"):
            lines.append("⚠️ 1.7.10 的 14.6a 不支持纤细模型，实际按默认模型加载")
        if (
            main_info.get("skin_source")
            and main_info.get("cape_source")
            and main_info.get("skin_source") != main_info.get("cape_source")
        ):
            lines.append("⚠️ ！皮肤和披风来源不一致")

    if res.get("suggest_clfcsl"):
        lines.append("")
        lines.append("🔧 检测到 CLFCSL 兼容层，但 CSL 版本不是最佳搭配。")
        lines.append("   1.7.10 下 CLFCSL 建议配合 14.15-SNAPSHOT-348 版本使用，效果更稳定。")

    if show_loadlist and res.get("loadlist"):
        sites = res["loadlist"]
        if len(sites) > max_sites:
            shown = " › ".join(sites[:max_sites])
            lines.append("")
            lines.append(f"加载站点（{len(sites)}）：{shown} …（共 {len(sites)} 个）")
        else:
            lines.append("")
            lines.append(f"加载站点（{len(sites)}）：" + " › ".join(sites))

    age = _log_age_days(res.get("last_time", ""), now)
    if age is not None and age > stale_days:
        lines.append("")
        lines.append(
            f"⏰【新生成提示】这份日志是 {int(age)} 天前的啦，可能已过时～ "
            "建议重新启动游戏、进入一次世界后再上传最新日志，结果会更准哦。"
        )

    return "\n".join(lines)


def _src_text(src: Optional[str]) -> str:
    return src if src else "无"


def _filename_ok(name: str) -> bool:
    if not name:
        return False
    pattern = feature.cfg("filename_pattern", r"CustomSkinLoader.*\.log$") or r"CustomSkinLoader.*\.log$"
    try:
        return re.search(pattern, name, re.IGNORECASE) is not None
    except re.error:
        feature.logger.warning("filename_pattern 正则无效，已按默认规则匹配：{}", pattern)
        return re.search(r"CustomSkinLoader.*\.log$", name, re.IGNORECASE) is not None


async def _resolve_url(
    bot: Bot,
    group_id: str,
    direct_url: Optional[str],
    file_id: str,
    busid: Any,
) -> Optional[str]:
    if direct_url:
        return direct_url
    if not file_id:
        return None
    try:
        resp = await bot.call_api(
            "get_group_file_url",
            group_id=int(group_id),
            file_id=str(file_id),
            busid=busid if busid is not None else 0,
        )
    except Exception as exc:  # noqa: BLE001
        feature.logger.warning("get_group_file_url 调用失败（协议端可能不支持）：{}", exc)
        return None
    return (resp or {}).get("url")


async def _download_and_reply(
    bot: Bot,
    group_id: str,
    filename: str,
    file_id: str,
    busid: Any,
    direct_url: Optional[str] = None,
) -> None:
    max_mb = int(feature.cfg("max_size_mb", 2) or 2)
    try:
        url = await _resolve_url(bot, group_id, direct_url, file_id, busid)
        if not url:
            feature.logger.warning("未能拿到文件下载地址：file_id={} direct_url={}", file_id, direct_url)
            return

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.content
        if len(data) > max_mb * 1024 * 1024:
            await bot.send_group_msg(
                group_id=int(group_id),
                message=(
                    f"文件太大啦（＞{max_mb}MB）～ 这么大的日志一般是累积太久导致的，"
                    "建议：先关闭游戏 → 删除旧的日志文件 → 重新启动游戏 → "
                    "进入一次世界、等待大约 30 秒后退出游戏 → "
                    "随后把新生成的日志发出来，这样就能解析啦～"
                ),
            )
            return

        text = None
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except Exception:
                continue
        if text is None:
            text = data.decode("utf-8", errors="replace")

        res = parse_csl_log(text)
        reply = format_result(
            res,
            show_loadlist=bool(feature.cfg("show_loadlist", True)),
            max_sites=int(feature.cfg("max_listed_sites", 15) or 15),
            stale_days=int(feature.cfg("stale_days", 3) or 3),
        )
        _main = next(
            (p for p in res.get("players", []) if p["player"] == res.get("main_player")),
            {},
        )
        feature.logger.info(
            "解析完成：{} → 主玩家={} 皮肤={} 披风={}",
            filename, res.get("main_player"),
            _main.get("skin_source"), _main.get("cape_source"),
        )
        await bot.send_group_msg(group_id=int(group_id), message=reply)
    except Exception as exc:  # noqa: BLE001
        feature.logger.opt(exception=exc).error("处理日志文件失败：{}", filename)


async def _upload_rule(event: Event) -> bool:
    if not isinstance(event, GroupUploadNoticeEvent):
        return False
    fname = getattr(event.file, "name", "") or ""
    return _filename_ok(fname)


group_upload = feature.notice(rule=Rule(_upload_rule))


@group_upload.handle()
async def _handle_upload(bot: Bot, event: GroupUploadNoticeEvent) -> None:
    gid = str(getattr(event, "group_id", "") or "")
    fname = getattr(event.file, "name", "") or ""
    feature.logger.info(
        "群文件上传事件：group_id={} file.name={} file.id={} file.busid={} raw={}",
        gid, fname, getattr(event.file, "id", None),
        getattr(event.file, "busid", None), event.file,
    )
    if not gid:
        return
    await _download_and_reply(
        bot, gid, fname,
        getattr(event.file, "id", "") or "",
        getattr(event.file, "busid", None),
    )


async def _file_msg_rule(event: Event) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    for seg in event.get_message():
        if seg.type == "file":
            fname = seg.data.get("file") or seg.data.get("name") or ""
            if _filename_ok(fname):
                return True
    return False


file_message = feature.message(rule=Rule(_file_msg_rule))


@file_message.handle()
async def _handle_file_message(bot: Bot, event: MessageEvent) -> None:
    gid = str(getattr(event, "group_id", "") or "")
    if not gid:
        return
    for seg in event.get_message():
        if seg.type != "file":
            continue
        fname = seg.data.get("file") or seg.data.get("name") or ""
        feature.logger.info(
            "消息文件段事件：group_id={} seg.data={}", gid, seg.data
        )
        if not _filename_ok(fname):
            continue
        await _download_and_reply(
            bot, gid, fname,
            seg.data.get("file_id") or seg.data.get("id") or "",
            seg.data.get("busid"),
            direct_url=seg.data.get("url") or None,
        )


@feature.on_load
async def _on_load() -> None:
    feature.logger.info("CustomSkinLoader 日志解析插件已就绪（监听群文件上传 / 文件消息）")


@feature.on_unload
async def _on_unload() -> None:
    feature.logger.info("CustomSkinLoader 日志解析插件已卸载")
