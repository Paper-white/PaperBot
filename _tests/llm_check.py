"""LLM 模块 + WebUI LLM 接口 + 真实 SDK 链路自检脚本。

覆盖三层：

1. **WebUI 接口**（`webui/routes/llm.py`）：服务商 / 人设 / 会话 / 用户记忆 / 试聊，
   用 `httpx.ASGITransport` 直接打 router（同一个事件循环，避免 SQLite 跨 loop 的坑）。
2. **业务层**（`src/core/llm.py`）：会话写库、记忆注入、人设解析。
3. **真实 SDK 链路**：起一个本地「假 OpenAI 兼容服务」，用真正的 `OpenAIChatClient`
   （openai SDK）跑通 `complete()` 与 `stream()`，验证不是只有 FakeClient 能过。

用法（项目根目录）：

    .\\.pixi\\envs\\default\\python.exe scripts\\llm_check.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nonebot

nonebot.init(driver="~none", log_level="ERROR")

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from src.config import settings  # noqa: E402
from src.core import database, llm  # noqa: E402
from src.core.llm import LLMChunk, LLMResult, ProviderConfig  # noqa: E402
from webui import api as webui_api  # noqa: E402

# 隔离真实环境：真实 .env 若配了 WEBUI_TOKEN，会给自检的 HTTP 请求带来 401
settings.webui_token = ""

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    RESULTS.append((bool(ok), name, extra))
    mark = "OK  " if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if extra:
        line += f"  → {extra}"
    print(line, flush=True)


# ============================== 假客户端 ==============================
class FakeClient(llm.ChatClient):
    """记录调用、返回可控内容；流式拆成 3 个片段。"""

    def __init__(self, reply: str = "喵～") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def _remember(self, cfg: ProviderConfig, messages: list[dict], params: dict) -> None:
        self.calls.append({"cfg": cfg, "messages": messages, "params": params})

    async def complete(self, cfg, messages, params) -> LLMResult:
        self._remember(cfg, messages, params)
        return LLMResult(
            text=self.reply,
            model=params.get("model") or cfg.model or "fake-model",
            provider=cfg.name or "env",
            usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        )

    async def stream(self, cfg, messages, params):
        self._remember(cfg, messages, params)
        for piece in ("喵", "～", "喵"):
            yield LLMChunk(delta=piece, text=piece)
        yield LLMChunk(
            done=True,
            text=self.reply,
            result=LLMResult(text=self.reply, model="fake-model", provider=cfg.name or "env", streamed=True),
        )


# ============================== 假 OpenAI 服务 ==============================
class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    """最小可用的 OpenAI 兼容端点：只实现 POST /v1/chat/completions。"""

    protocol_version = "HTTP/1.1"
    server_version = "FakeOpenAI/1.0"

    def log_message(self, *args):  # 静音
        pass

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def _send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802 - http.server 规定的名字
        body = self._body()
        model = body.get("model") or "fake-model"
        if not self.path.endswith("/chat/completions"):
            self._send_json({"error": {"message": "not found"}}, status=404)
            return
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            for piece in ("你", "好", "呀"):
                chunk = {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        self._send_json(
            {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            }
        )


def start_fake_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}/v1"


# ============================== 主体 ==============================
async def main() -> int:  # noqa: C901 - 自检脚本，长一点没关系
    tmp = Path(tempfile.mkdtemp(prefix="llm_check_"))
    settings.database_url = f"sqlite+aiosqlite:///{tmp / 'check.db'}"
    await database.init_db()

    app = FastAPI()
    app.include_router(webui_api.router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30)

    fake = FakeClient("喵～")
    llm.set_client_factory(lambda: fake)

    print("=" * 68)
    print("一、WebUI：状态与服务商")
    print("=" * 68)
    r = await client.get("/api/llm/status")
    st = r.json()
    check("GET /llm/status 200", r.status_code == 200, str(r.status_code))
    check("status 含关键字段", all(k in st for k in ("enable", "available", "stream_enable", "provider_count", "session_count", "scope_labels")))
    check("未配置任何服务商时不 available", st["available"] is False)
    check("secret_placeholder 已回传", bool(st.get("secret_placeholder")))

    r = await client.get("/api/llm/providers")
    check("GET /llm/providers 200 且为空", r.status_code == 200 and r.json()["items"] == [])

    r = await client.put(
        "/api/llm/providers/deepseek",
        json={"base_url": "https://api.deepseek.com/v1", "api_key": "sk-abc", "model": "deepseek-chat"},
    )
    prov = r.json().get("item", {})
    check("PUT 新建服务商 200", r.status_code == 200, str(r.status_code))
    check("密钥回显为掩码", prov.get("api_key") == llm.SECRET_MASK and prov.get("api_key_set") is True)

    r = await client.put("/api/llm/providers/deepseek", json={"api_key": llm.SECRET_MASK, "model": "deepseek-v3"})
    item = r.json().get("item", {})
    check("提交掩码不改密钥", item.get("api_key_set") is True and item.get("model") == "deepseek-v3")

    r = await client.put("/api/llm/providers/moonshot", json={"base_url": "https://api.moonshot.cn/v1", "api_key": "sk-xyz", "is_default": True})
    check("PUT 第二个服务商并设为默认", r.status_code == 200 and r.json()["item"]["is_default"] is True)

    rows = (await client.get("/api/llm/providers")).json()["items"]
    default_names = [p["name"] for p in rows if p["is_default"]]
    check("默认服务商唯一", default_names == ["moonshot"], str(default_names))

    st = (await client.get("/api/llm/status")).json()
    check("status 反映当前服务商", (st.get("active_provider") or {}).get("name") == "moonshot")

    r = await client.put("/api/llm/providers/env", json={"model": "x"})
    check("不能覆盖 env 虚拟名 → 400", r.status_code == 400, str(r.status_code))

    r = await client.put("/api/llm/providers/%20", json={"model": "x"})
    check("空名字服务商 → 400", r.status_code == 400, str(r.status_code))

    r = await client.put("/api/llm/providers/nonascii-key", json={"api_key": "sk-测试密钥"})
    check("非 ASCII 密钥 → 400 且提示可读", r.status_code == 400 and "非 ASCII" in r.text, r.text[:60])
    names = [p["name"] for p in (await client.get("/api/llm/providers")).json()["items"]]
    check("校验失败未落库", "nonascii-key" not in names and "badurl" not in names, str(names))

    r = await client.put("/api/llm/providers/badurl", json={"base_url": "127.0.0.1:8000/v1"})
    check("缺协议头的 base_url → 400", r.status_code == 400 and "http" in r.text, r.text[:60])

    r = await client.put("/api/llm/providers/非ASCII名", json={"api_key": "sk-ascii"})
    check("服务商名可以是非 ASCII", r.status_code == 200 and r.json()["item"]["name"] == "非ASCII名", str(r.status_code))
    r = await client.delete("/api/llm/providers/非ASCII名")
    check("删除非 ASCII 名服务商", r.status_code == 200, str(r.status_code))

    r = await client.post("/api/llm/providers/moonshot/test")
    ok = r.status_code == 200
    check("POST 测试连接 200", ok, str(r.status_code))
    if ok:
        data = r.json()
        check("测试返回耗时与回复", data.get("text") == "喵～" and isinstance(data.get("elapsed_ms"), int))

    r = await client.post("/api/llm/providers/nope/test")
    check("测试不存在的服务商 → 400", r.status_code == 400, str(r.status_code))

    r = await client.delete("/api/llm/providers/moonshot")
    check("DELETE 服务商 200", r.status_code == 200, str(r.status_code))
    check("再删同一个 → 404", (await client.delete("/api/llm/providers/moonshot")).status_code == 404)
    check("删 env → 400", (await client.delete("/api/llm/providers/env")).status_code == 400)
    check("删默认后自动补一个默认", (await client.get("/api/llm/providers")).json()["items"][0]["is_default"] is True)

    print("=" * 68)
    print("二、WebUI：人设")
    print("=" * 68)
    r = await client.get("/api/llm/presets")
    check("GET /llm/presets 200", r.status_code == 200 and r.json()["items"] == [])

    r = await client.put(
        "/api/llm/presets/cat",
        json={"display_name": "小猫", "description": "测试人设", "system_prompt": "你是猫娘，说话带喵。"},
    )
    check("PUT 新建人设 200（人工来源）", r.status_code == 200 and r.json()["item"]["source"] == llm.LLM_PRESET_MANUAL, str(r.status_code))

    d = (await client.get("/api/llm/presets/cat")).json()["item"]
    check("GET 人设详情带正文与导出文本", "猫娘" in d.get("text", "") and d.get("system_prompt", "").startswith("你是猫娘"))

    r = await client.get("/api/llm/presets/nope")
    check("GET 不存在人设 → 404", r.status_code == 404)

    builtin_text = (
        "---\n"
        "name: helper\n"
        "display_name: 小助手\n"
        "description: 内置\n"
        "params:\n"
        "  temperature: 0.3\n"
        "---\n"
        "你是一个乐于助人的助手。\n"
    )
    r = await client.post("/api/llm/presets/import", json={"text": builtin_text, "filename": "helper.md"})
    body = r.json()
    check("导入人设（front-matter）成功", r.status_code == 200 and body.get("action") == "created", str(body.get("action")))
    check("导入解析出显示名", body["item"].get("display_name") == "小助手")

    r = await client.post("/api/llm/presets/import", json={"text": builtin_text, "filename": "helper.md"})
    check("重复导入 import→import 会更新", r.json().get("action") == "updated", str(r.json().get("action")))

    r = await client.post("/api/llm/presets/import", json={"text": builtin_text, "filename": "helper.md", "force": True})
    check("force=1 覆盖导入", r.json().get("action") == "updated", str(r.json().get("action")))

    r = await client.post("/api/llm/presets/import", json={"text": builtin_text.replace("helper", "cat")})
    check("人工人设不被导入覆盖", r.json().get("action") == "skipped", str(r.json().get("action")))

    r = await client.post(
        "/api/llm/presets/import",
        json={"text": builtin_text.replace("helper", "cat"), "force": True},
    )
    check("force=1 能覆盖人工人设", r.json().get("action") == "updated", str(r.json().get("action")))
    # 还原 cat（否则后面按人工来源断言会被这次 force 覆盖影响）
    await client.put(
        "/api/llm/presets/cat",
        json={"display_name": "小猫", "description": "测试人设", "system_prompt": "你是猫娘，说话带喵。"},
    )

    r = await client.post("/api/llm/presets/import", json={"text": "   "})
    check("空内容导入 → 400", r.status_code == 400, str(r.status_code))

    items = (await client.get("/api/llm/presets?with_prompt=1")).json()["items"]
    cat = [p for p in items if p["name"] == "cat"][0]
    check("with_prompt=1 带出正文", "猫娘" in cat.get("system_prompt", ""))
    check("人设带来源中文标签", cat.get("source_label") == "人工", str(cat.get("source_label")))

    r = await client.get("/api/llm/presets/cat/export")
    check("导出人设 200 且是 Markdown", r.status_code == 200 and r.text.startswith("---"), r.headers.get("content-type", ""))
    check("导出内容含正文", "猫娘" in r.text)
    check("导出不存在人设 → 404", (await client.get("/api/llm/presets/nope/export")).status_code == 404)

    check("DELETE 人设 200", (await client.delete("/api/llm/presets/helper")).status_code == 200)
    check("DELETE 不存在人设 → 404", (await client.delete("/api/llm/presets/helper")).status_code == 404)

    print("=" * 68)
    print("三、WebUI：会话与上下文")
    print("=" * 68)
    await llm.chat(
        [{"role": "user", "content": "你好"}], scope=llm.LLM_SCOPE_USER, scope_id="10001"
    )
    # group scope 不再建 llm_session（群上下文改由 chatlog / chat_message 提供）
    await llm.chat(
        [{"role": "user", "content": "群里的大家好"}],
        scope=llm.LLM_SCOPE_GROUP,
        scope_id="123456789",
    )

    sessions = (await client.get("/api/llm/sessions")).json()
    check("GET /llm/sessions 只列 user 会话（group 不再建 llm_session）", sessions["count"] == 1, str(sessions["count"]))
    user_sess = [s for s in sessions["items"] if s["scope_type"] == "user"][0]
    check("会话带中文作用域标签", user_sess["scope_label"] == "个人", str(user_sess["scope_label"]))
    check("会话消息数已累计", user_sess["message_count"] == 2, str(user_sess["message_count"]))

    r = await client.get(f"/api/llm/sessions/{user_sess['id']}/messages")
    msgs = r.json()
    check("GET 会话消息 200", r.status_code == 200 and msgs["count"] == 2, str(msgs.get("count")))
    check("消息含 user / assistant", {m["role"] for m in msgs["items"]} == {"user", "assistant"})
    check("不存在会话消息 → 404", (await client.get("/api/llm/sessions/999999/messages")).status_code == 404)
    check("limit 越界 → 422", (await client.get("/api/llm/sessions?limit=999")).status_code == 422)

    r = await client.put(f"/api/llm/sessions/{user_sess['id']}", json={"preset_name": "cat"})
    check("给会话换人设 200", r.status_code == 200 and r.json()["item"]["preset_name"] == "cat", str(r.status_code))

    r = await client.put(f"/api/llm/sessions/{user_sess['id']}", json={"preset_name": "nope"})
    check("换成不存在的人设 → 400", r.status_code == 400, str(r.status_code))
    r = await client.put(f"/api/llm/sessions/{user_sess['id']}", json={"provider_name": "nope"})
    check("换成不存在的服务商 → 400", r.status_code == 400, str(r.status_code))

    r = await client.put(f"/api/llm/sessions/{user_sess['id']}", json={"preset_name": ""})
    check("清空人设回落默认", r.status_code == 200 and r.json()["item"]["preset_name"] == "")

    r = await client.delete(f"/api/llm/sessions/{user_sess['id']}")
    check("DELETE 会话 200", r.status_code == 200, str(r.status_code))
    check("删除后消息 404", (await client.get(f"/api/llm/sessions/{user_sess['id']}/messages")).status_code == 404)

    r = await client.delete("/api/llm/sessions?scope_type=group")
    check("按作用域批量清空（group 无 llm_session）", r.status_code == 200 and r.json()["removed"] == 0, str(r.json()))
    check("非法作用域 → 400", (await client.delete("/api/llm/sessions?scope_type=x")).status_code == 400)

    print("=" * 68)
    print("四、WebUI：用户长期记忆")
    print("=" * 68)
    await llm.set_impression("10001", "喜欢猫的群友")
    # 受控 key 词表：manual 手写也做别名归一（「城市」→「所在地」）
    await llm.set_fact("10001", "城市", "杭州")

    users = (await client.get("/api/llm/users")).json()
    check("GET /llm/users 列出用户", users["count"] == 1, str(users["count"]))
    check("用户条目带关键信息计数", users["items"][0].get("fact_count") == 1, str(users["items"][0]))

    d = (await client.get("/api/llm/users/10001")).json()["item"]
    check("GET 用户详情含印象", d.get("impression") == "喜欢猫的群友")
    check("GET 用户详情含关键信息", d["facts"][0]["key"] == "所在地" and d["facts"][0]["value"] == "杭州")
    check("未知用户返回空记忆", (await client.get("/api/llm/users/99999")).json()["item"]["facts"] == [])

    r = await client.put("/api/llm/users/10001/impression", json={"impression": "猫奴一枚"})
    check("PUT 印象 200", r.status_code == 200 and (await client.get("/api/llm/users/10001")).json()["item"]["impression"] == "猫奴一枚")

    r = await client.put("/api/llm/users/10001/facts", json={"key": "爱好", "value": "撸猫"})
    detail = (await client.get("/api/llm/users/10001")).json()["item"]
    hobby = [f for f in detail["facts"] if f["key"] == "爱好"]
    check(
        "PUT 关键信息 200（人工来源）",
        r.status_code == 200 and hobby and hobby[0]["source"] == llm.LLM_FACT_MANUAL,
        str(r.status_code),
    )
    r = await client.put("/api/llm/users/10001/facts", json={"key": "  ", "value": "x"})
    check("空键 → 400", r.status_code == 400)

    r = await client.delete("/api/llm/users/10001/facts?key=%E7%88%B1%E5%A5%BD")
    check("DELETE 单条关键信息", r.status_code == 200 and r.json()["removed"] == 1, str(r.json()))
    r = await client.delete("/api/llm/users/10001/facts?key=nope")
    check("删除不存在关键信息 → 404", r.status_code == 404)

    r = await client.delete("/api/llm/users/10001/facts")
    check("DELETE 全部关键信息", r.status_code == 200 and r.json()["removed"] == 1, str(r.json()))
    r = await client.delete("/api/llm/users/10001")
    check("DELETE 用户记忆 200", r.status_code == 200 and r.json()["removed"] >= 1, str(r.json()))
    check("清空后印象为空", (await client.get("/api/llm/users/10001")).json()["item"]["impression"] == "")

    print("=" * 68)
    print("五、WebUI：试聊")
    print("=" * 68)
    r = await client.post("/api/llm/chat", json={"prompt": "在吗"})
    check("POST /llm/chat 200", r.status_code == 200 and r.json()["text"] == "喵～", str(r.status_code))
    check("空内容 → 400", (await client.post("/api/llm/chat", json={"prompt": "   "})).status_code == 400)
    r = await client.post("/api/llm/chat", json={"prompt": "hi", "provider": "nope"})
    check("指定不存在服务商 → 400", r.status_code == 400, str(r.status_code))

    fake.calls.clear()
    await client.post("/api/llm/chat", json={"prompt": "hi", "preset": "cat"})
    system_prompt = fake.calls[0]["messages"][0]
    check("试聊按人设注入系统提示", system_prompt["role"] == "system" and "猫娘" in system_prompt["content"], system_prompt.get("content", "")[:20])

    print("=" * 68)
    print("五点五、聊天记录 / 群记忆 / 上下文压缩")
    print("=" * 68)
    from src.core import chatlog
    from src.core.database import get_session
    from src.core.models import ChatMessage

    async with get_session() as session:
        session.add(ChatMessage(message_type="group", group_id="999", user_id="111", nickname="甲", content="今天聊聊游戏", message_id="m1"))
        session.add(ChatMessage(message_type="group", group_id="999", user_id="222", nickname="乙", content="好呀，最近在玩什么", message_id="m2"))
        session.add(ChatMessage(message_type="private", group_id="", user_id="111", nickname="甲", content="私聊你一句", message_id="m3"))
    recent = await chatlog.recent_messages("999", limit=5)
    check("chatlog 取群最近消息", len(recent) == 2 and recent[0]["user_id"] == "111", str(recent))
    since = await chatlog.messages_since(group_id="999", watermark=0)
    check("chatlog 增量取群消息", len(since) == 2, str(len(since)))
    by_user = await chatlog.messages_by_user("111")
    check("chatlog 按用户取发言", len(by_user) == 2, str(len(by_user)))

    gprofile = await llm.get_group_profile("999")
    check("群记忆初始为空", gprofile["memory"] == "", gprofile["memory"])
    await llm.set_group_memory("999", "群里最近在聊游戏")
    gprofile = await llm.get_group_profile("999")
    check("群记忆可写入", "游戏" in gprofile["memory"], gprofile["memory"])
    check("群记忆列表可见", len(await llm.list_group_profiles()) == 1)

    # 上下文压缩：建一个超阈值会话，压缩旧消息进 summary
    sess = await llm.ensure_session("user", "10003")
    for i in range(int(settings.llm_compress_threshold) + 5):
        await llm.append_message(sess["id"], "user" if i % 2 == 0 else "assistant", f"msg{i}")
    result = await llm.compress_session(sess["id"])
    check("压缩会话返回 ok", result.get("ok") is True, str(result))
    check("压缩掉旧消息", result.get("compressed", 0) > 0, str(result.get("compressed")))
    info = await llm.get_session_by_id(sess["id"])
    check("摘要已写入会话", bool(info and (info.get("summary") or "").strip()), str(info and info.get("summary")))

    print("=" * 68)
    print("六、真实 openai SDK 链路（本地假服务）")
    print("=" * 68)
    server, base_url = start_fake_server()
    try:
        await client.put(
            "/api/llm/providers/fake-sdk",
            json={"base_url": base_url, "api_key": "sk-test", "model": "fake-model", "is_default": True},
        )
        llm.set_client_factory(llm.OpenAIChatClient)  # 换回真实实现

        r = await client.post("/api/llm/providers/fake-sdk/test")
        data = r.json() if r.status_code == 200 else {}
        check("真实 SDK 连通性测试 200", r.status_code == 200, str(r.status_code))
        check("真实 SDK 收到回复与用量", data.get("text") == "pong" and data.get("usage", {}).get("total_tokens") == 4, str(data.get("usage")))

        result = await llm.ask("hi", provider="fake-sdk")
        check("真实 SDK ask() 可用", result == "pong", result)

        chunks = [c async for c in llm.stream("hi", provider="fake-sdk", use_stream=True)]
        text = "".join(c.delta for c in chunks if c.delta)
        check("真实 SDK 流式收到 3 个片段", len([c for c in chunks if c.delta]) == 3, str([c.delta for c in chunks]))
        check("真实 SDK 流式拼接正确", text == "你好呀", text)
        check("流式末片带完整结果", chunks[-1].done is True and chunks[-1].result is not None)
    finally:
        server.shutdown()
        server.server_close()
        await llm.shutdown_llm()

    print("=" * 68)
    print("七、时间显示（库里存 UTC，对外按北京时间展示）")
    print("=" * 68)
    utc_naive = datetime(2026, 9, 30, 10, 11, 45)  # 库里那种 naive UTC
    check("fmt_dt 把 UTC 换算成北京时间", llm.fmt_dt(utc_naive) == "2026-09-30 18:11:45+08:00", llm.fmt_dt(utc_naive))
    check("fmt_dt 空值 → 空串", llm.fmt_dt(None) == "", repr(llm.fmt_dt(None)))
    aware = datetime(2026, 9, 30, 10, 11, 45, tzinfo=timezone.utc)
    check("fmt_dt 兼容带时区的时间", llm.fmt_dt(aware) == "2026-09-30 18:11:45+08:00", llm.fmt_dt(aware))

    preset_detail = (await client.get("/api/llm/presets/cat")).json()["item"]
    offset = (datetime.fromisoformat(preset_detail["updated_at"]) - datetime.now(timezone.utc)).total_seconds()
    check("接口返回的人设 updated_at 带 +8 偏移", -900 < offset < 900, f"{offset:.0f}s → {preset_detail['updated_at']}")

    await llm.set_impression("10002", "刚写的印象")
    prof = (await client.get("/api/llm/users/10002")).json()["item"]
    offset = (datetime.fromisoformat(prof["impression_at"]) - datetime.now(timezone.utc)).total_seconds()
    check("接口返回的印象时间带 +8 偏移", -900 < offset < 900, f"{offset:.0f}s → {prof['impression_at']}")

    await client.aclose()
    await database.dispose_db()

    print("=" * 68)
    passed = sum(1 for ok, _, _ in RESULTS if ok)
    total = len(RESULTS)
    failed = [name for ok, name, _ in RESULTS if not ok]
    print(f"结果：{passed}/{total} 通过")
    if failed:
        print("失败项：")
        for name in failed:
            print(f"  - {name}")
    print(f"临时库：{tmp}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
