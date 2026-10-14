"""配置体系自检（插件配置 JSON 文件 + 框架 .env 位置）。

跑法：

    pixi run check-config

覆盖：schema 校验 → 类型转换 → JSON 文件读写（路径 / 原子写 / 只存显式键 / 元信息 /
删键删文件）→ 默认值合并 → 手改文件容错 → 目录穿越防护 → 「插件配置目录」API →
WebUI 三个接口（含密钥脱敏）→ 框架 `.env` 迁到 `.config/main/.env`（含旧文件迁移）→
旧 `plugin_config` 表一次性迁移。

全程在临时目录里跑，**不碰**项目真实的 `.config/` 与 `data/`。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# 让脚本能直接 `python _tests/config_check.py` 跑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    RESULTS.append((bool(ok), name, extra))
    mark = "OK  " if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if extra:
        line += f"  → {extra}"
    print(line)


def main() -> int:  # noqa: C901 - 自检脚本，长一点没关系
    tmp = Path(tempfile.mkdtemp(prefix="config_check_"))
    os.environ["DRIVER"] = "~none"

    # 1) 先把「配置根目录」指到临时目录，再 import 业务代码
    from src.config import settings

    settings.database_url = f"sqlite+aiosqlite:///{(tmp / 'check.db').as_posix()}"

    nonebot.init(driver="~none")

    import httpx
    from fastapi import FastAPI

    from src.core import database, pluginconf, registry
    from src.core.feature import Feature
    from webui import envfile
    from webui.routes import plugins as plugins_routes

    # 关键：把 .config 根目录重定向到临时目录（插件配置 + 框架 .env 都不落进项目）
    tmp_config = tmp / ".config"
    pluginconf.config_dir = lambda: (tmp_config.mkdir(parents=True, exist_ok=True), tmp_config)[1]
    envfile._ENV_PATH = tmp / ".config" / "main" / ".env"
    envfile._LEGACY_PATH = tmp / ".env"

    print("=" * 68)
    print("一、schema 规范化")
    print("=" * 68)
    schema = pluginconf.normalize_schema(
        [
            "greet",
            {"key": "times", "type": "int", "default": 3, "label": "次数"},
            {"key": "loud", "type": "bool", "default": False},
            {"key": "ratio", "type": "float", "default": 0.5},
            {"key": "mode", "type": "select", "choices": ["a", "b"], "default": "a"},
            {"key": "token", "type": "string", "secret": True},
        ]
    )
    check("字段数量正确", len(schema) == 6, str(len(schema)))
    check("字符串简写补全 label", schema[0]["label"] == "greet")
    check("select 保留 choices", schema[4]["choices"] == ["a", "b"])

    for raw, why in (
        ([{"type": "int"}], "缺 key"),
        ([{"key": "a", "type": "date"}], "未知类型"),
        ([{"key": "a", "type": "select"}], "select 无 choices"),
        ([{"key": "a"}, {"key": "a"}], "重复 key"),
        ([{"key": "_meta"}], "下划线开头"),
    ):
        try:
            pluginconf.normalize_schema(raw)
            check(f"非法 schema 被拒（{why}）", False, "居然通过了")
        except ValueError as exc:
            check(f"非法 schema 被拒（{why}）", True, str(exc)[:40])

    print("=" * 68)
    print("二、类型转换 coerce")
    print("=" * 68)
    index = pluginconf.field_index(schema)
    check("int 转换", pluginconf.coerce(index["times"], "7") == 7)
    check("float 转换", pluginconf.coerce(index["ratio"], "0.25") == 0.25)
    check("bool 中文「是」", pluginconf.coerce(index["loud"], "是") is True)
    check("bool 英文 off", pluginconf.coerce(index["loud"], "off") is False)
    check("select 合法值", pluginconf.coerce(index["mode"], "b") == "b")
    check("None → 默认值", pluginconf.coerce(index["times"], None) == 3)
    for field, bad, why in (
        (index["times"], "abc", "非整数"),
        (index["ratio"], "x", "非数字"),
        (index["loud"], "maybe", "非布尔"),
        (index["mode"], "z", "不在选项里"),
    ):
        try:
            pluginconf.coerce(field, bad)
            check(f"非法值被拒（{why}）", False, "居然通过了")
        except ValueError as exc:
            check(f"非法值被拒（{why}）", True, str(exc)[:30])

    print("=" * 68)
    print("三、路径与目录穿越防护")
    print("=" * 68)
    check("插件配置目录在 .config/<名字>/", pluginconf.plugin_dir("demo") == tmp_config / "demo", str(pluginconf.plugin_dir("demo")))
    check("配置文件名为 config.json", pluginconf.config_file("demo").name == "config.json")
    check("目录会被自动创建", pluginconf.plugin_dir("demo").is_dir())
    for bad in ("../evil", "a/b", ".hidden", ""):
        try:
            pluginconf.plugin_dir(bad)
            check(f"非法插件名被拒（{bad!r}）", False, "居然通过了")
        except ValueError:
            check(f"非法插件名被拒（{bad!r}）", True)

    print("=" * 68)
    print("四、JSON 文件读写（含默认值合并）")
    print("=" * 68)
    feature = Feature(
        "demo",
        display_name="自检用",
        config_schema=[
            {"key": "greet", "type": "string", "default": "hi"},
            {"key": "times", "type": "int", "default": 3},
            {"key": "loud", "type": "bool", "default": False},
            {"key": "token", "type": "string", "default": "", "secret": True},
        ],
    )
    registry.register(feature)

    async def run() -> None:
        # 4.1 初始状态：只有默认值，文件不存在
        check("未配置时文件不存在", not pluginconf.config_file("demo").is_file())
        values = await pluginconf.load_config("demo")
        check("未配置时全是默认值", values == {"greet": "hi", "times": 3, "loud": False, "token": ""}, str(values))

        # 4.2 写两个键
        changed = await pluginconf.write_config("demo", {"greet": "你好", "times": 5}, updated_by="webui")
        check("写入返回变更键", sorted(changed) == ["greet", "times"], str(changed))
        path = pluginconf.config_file("demo")
        check("文件已生成", path.is_file())
        raw = json.loads(path.read_text(encoding="utf-8"))
        check("只存显式改过的键", set(pluginconf._values_of(raw)) == {"greet", "times"}, str(raw))
        check("带 _plugin 元信息", raw.get("_plugin") == "demo")
        check("带 _updated_by 元信息", raw.get("_updated_by") == "webui")
        check("带 _updated_at 元信息", bool(raw.get("_updated_at")), str(raw.get("_updated_at")))
        check("文件是 UTF-8 无 BOM", not path.read_bytes().startswith(b"\xef\xbb\xbf"))
        check("值以原样字符存中文", raw["greet"] == "你好")

        # 4.3 同值写入不写盘
        again = await pluginconf.write_config("demo", {"greet": "你好"})
        check("同值不产生变更", again == [], str(again))

        # 4.4 插件侧缓存即时生效
        check("feature.cfg 立即拿到新值", feature.cfg("greet") == "你好" and feature.cfg("times") == 5)

        # 4.5 默认值合并
        merged = await pluginconf.load_config("demo")
        check("未写的键回落默认值", merged["loud"] is False and merged["token"] == "")

        # 4.6 stored / meta
        stored = await pluginconf.stored_config("demo")
        check("stored 只含显式键", set(stored) == {"greet", "times"}, str(stored))
        meta = await pluginconf.stored_meta("demo")
        check("meta 带 updated_by/at", meta["updated_by"] == "webui" and bool(meta["updated_at"]))

        # 4.7 手改文件 → 生效
        data = json.loads(path.read_text(encoding="utf-8"))
        data["times"] = 42
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        check("手改文件后读到新值", (await pluginconf.load_config("demo"))["times"] == 42)

        # 4.8 手改出错误类型 → 回落默认值（不炸）
        data["times"] = "不是数字"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        check("错误类型回落默认值", (await pluginconf.load_config("demo"))["times"] == 3)

        # 4.9 文件损坏 → 当作没配过
        path.write_text("{ 这不是 json", encoding="utf-8")
        check("损坏文件不炸且回落默认值", (await pluginconf.load_config("demo"))["greet"] == "hi")
        check("损坏文件不会被悄悄覆盖", path.read_text(encoding="utf-8") == "{ 这不是 json")

        # 4.10 清单键
        await pluginconf.write_config("demo", {"greet": "你好", "times": 9}, updated_by="webui")
        removed = await pluginconf.clear_config("demo", "times")
        check("清单个键返回 1", removed == 1, str(removed))
        check("清掉的键回落默认值", feature.cfg("times") == 3)
        check("其它键还在", feature.cfg("greet") == "你好")
        check("清键后元信息仍在", set(pluginconf._values_of(pluginconf.read_file("demo"))) == {"greet"})

        # 4.11 清全部 → 文件被删
        removed = await pluginconf.clear_config("demo")
        check("清全部返回 1", removed == 1, str(removed))
        check("清空后文件被删除", not path.is_file())
        check("清空后全是默认值", feature.cfg("greet") == "hi")

        # 4.12 插件的「配置目录」属性 + 自己放文件
        check("feature.config_dir 指向 .config/demo", feature.config_dir == tmp_config / "demo")
        extra_file = feature.config_dir / "notes.txt"
        extra_file.write_text("插件自己的文件", encoding="utf-8")
        removed = await pluginconf.clear_plugin_files("demo")
        check("clear_plugin_files 删掉插件自放文件", removed == 1 and not extra_file.is_file(), str(removed))
        check("目录本身保留", feature.config_dir.is_dir())

        # 4.13 未注册插件写入被拒
        try:
            await pluginconf.write_config("不存在的插件", {"greet": "x"})
            check("未注册插件写入被拒", False, "居然通过了")
        except ValueError as exc:
            check("未注册插件写入被拒", True, str(exc)[:30])

        # 4.14 未知键被忽略
        changed = await pluginconf.write_config("demo", {"nope": 1})
        check("未知键被忽略", changed == [], str(changed))

        # 4.15 legacy 行迁移
        row = type("Row", (), {"plugin_name": "legacy_plug", "key": "k", "value": json.dumps("v"), "updated_by": "old"})()
        moved = pluginconf.migrate_legacy_rows([row])
        check("旧表行迁移到 JSON", moved == {"legacy_plug": 1}, str(moved))
        check("迁移后的文件可读", pluginconf.read_file("legacy_plug").get("k") == "v")

        print("=" * 68)
        print("五、WebUI 接口（插件配置）")
        print("=" * 68)
        app = FastAPI()
        app.include_router(plugins_routes.router)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/plugins/demo/config")
            body = r.json()
            check("GET 插件配置 200", r.status_code == 200, str(r.status_code))
            check("返回文件路径", body.get("path", "").endswith("config.json"), body.get("path", ""))
            check("返回 schema 字段", len(body.get("fields", [])) == 4, str(len(body.get("fields", []))))
            check("未配置时 exists=False", body.get("exists") is False)
            check("提示指向 .config 路径", ".config" in body.get("note", ""), body.get("note", "")[:40])

            r = await client.put("/plugins/demo/config", json={"values": {"greet": "喵", "token": "sk-秘密"}})
            check("PUT 插件配置 200", r.status_code == 200 and sorted(r.json()["changed"]) == ["greet", "token"], str(r.status_code))

            body = (await client.get("/plugins/demo/config")).json()
            check("GET 反映已配置", body.get("exists") is True and body.get("updated_by") == "webui", str(body.get("updated_by")))
            token_field = next(f for f in body["fields"] if f["key"] == "token")
            check("密钥字段被脱敏", token_field["value"] not in ("sk-秘密",) and token_field["configured"] is True, str(token_field["value"]))
            greet_field = next(f for f in body["fields"] if f["key"] == "greet")
            check("明文字段原样返回", greet_field["value"] == "喵")

            r = await client.put("/plugins/demo/config", json={"values": {"nope": 1}})
            check("未知配置项 → 400", r.status_code == 400, str(r.status_code))
            r = await client.put("/plugins/demo/config", json={"values": {"times": "abc"}})
            check("类型错误 → 400", r.status_code == 400, str(r.status_code))
            r = await client.put("/plugins/demo/config", json={"values": {}})
            check("空 values → 400", r.status_code == 400, str(r.status_code))
            r = await client.get("/plugins/nope/config")
            check("未知插件 → 404", r.status_code == 404, str(r.status_code))

            r = await client.delete("/plugins/demo/config", params={"key": "greet"})
            check("DELETE 单键 200", r.status_code == 200 and r.json()["removed"] == 1, str(r.status_code))
            r = await client.delete("/plugins/demo/config")
            check("DELETE 全部 200", r.status_code == 200 and r.json()["removed"] == 1, str(r.status_code))
            check("DELETE 后文件消失", not pluginconf.config_file("demo").is_file())

        print("=" * 68)
        print("六、框架 .env 位置（.config/main/.env）")
        print("=" * 68)
        check("env_path 指向 .config/main/.env", envfile.env_path() == (tmp / ".config" / "main" / ".env").resolve(), str(envfile.env_path()))
        check("还没迁移时 env_exists=False", envfile.env_exists() is False)
        check("两边都没有 → none", envfile.migrate_legacy_env() == "none")

        envfile._LEGACY_PATH.write_text('WEBUI_TOKEN=abc\n# 注释要保留\nBOT_ACTIVE_GROUPS=["1"]\n', encoding="utf-8")
        check("迁移返回 moved", envfile.migrate_legacy_env() == "moved")
        check("迁移后新位置存在", envfile.env_exists() is True)
        check("迁移后旧位置消失", envfile._LEGACY_PATH.exists() is False)
        check("迁移后能读出键", envfile.read_env().get("WEBUI_TOKEN") == "abc")
        check("旧文件已不在 → 再迁移返回 none", envfile.migrate_legacy_env() == "none")
        envfile._LEGACY_PATH.write_text("WEBUI_TOKEN=old\n", encoding="utf-8")
        check("新旧都在 → kept（不覆盖新配置）", envfile.migrate_legacy_env() == "kept")
        check("kept 时新文件未被改", envfile.read_env().get("WEBUI_TOKEN") == "abc")
        envfile._LEGACY_PATH.unlink()

        changed = envfile.write_env({"WEBUI_TOKEN": "def", "NEW_KEY": "1"})
        check("写回返回变更键", sorted(changed) == ["NEW_KEY", "WEBUI_TOKEN"], str(changed))
        text = envfile._ENV_PATH.read_text(encoding="utf-8")
        check("按行替换保留注释", "# 注释要保留" in text)
        check("已存在的键被改写", "WEBUI_TOKEN=def" in text)
        check("新键追加到末尾", text.rstrip().endswith("NEW_KEY=1"))
        check("同值写回无变更", envfile.write_env({"WEBUI_TOKEN": "def"}) == [])

        print("=" * 68)
        print("七、旧 plugin_config 表迁移（启动钩子）")
        print("=" * 68)
        from sqlalchemy import text

        from src.core import bootstrap
        from src.core.database import get_session

        await database.init_db()
        async with get_session() as session:
            await session.execute(
                text(
                    "CREATE TABLE plugin_config ("
                    "id INTEGER PRIMARY KEY, plugin_name VARCHAR(64), key VARCHAR(64), "
                    "value TEXT, updated_by VARCHAR(64), updated_at DATETIME)"
                )
            )
            await session.execute(
                text("INSERT INTO plugin_config (plugin_name, key, value, updated_by) "
                     "VALUES ('demo', 'legacy', '\"来自老库\"', 'old')")
            )
        await bootstrap._migrate_legacy_plugin_configs()
        check("旧行进了 JSON", pluginconf.read_file("demo").get("legacy") == "来自老库", str(pluginconf.read_file("demo")))
        async with get_session(readonly=True) as session:
            names = [
                r[0]
                for r in (
                    await session.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                ).all()
            ]
        check("旧表已改名归档", "plugin_config_legacy" in names and "plugin_config" not in names, str(names))
        await bootstrap._migrate_legacy_plugin_configs()
        check("重复迁移安全（表已归档）", True)

        await database.dispose_db()

    asyncio.run(run())

    print("=" * 68)
    passed = sum(1 for ok, _, _ in RESULTS if ok)
    total = len(RESULTS)
    failed = [name for ok, name, _ in RESULTS if not ok]
    print(f"结果：{passed}/{total} 通过")
    if failed:
        print("失败项：")
        for name in failed:
            print(f"  - {name}")
    print(f"临时目录：{tmp}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
