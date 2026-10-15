"""统一 LLM 服务（OpenAI 兼容）。

**插件只需要一个接口**（其余全部由框架兜住）：

```python
from src.core import llm

text = await llm.ask("你好呀", preset="paper_white", scope="user", scope_id=str(event.get_user_id()))
result = await llm.chat([{"role": "user", "content": "讲个冷笑话"}])   # 结构化结果
async for chunk in llm.stream("写首诗"):                              # 流式（可开关）
    print(chunk.delta)
```

能力一览：
- **服务商**：配置存 `llm_provider` 表（可存多套 + 指定默认），`.env` 里的 `LLM_*` 只做兜底；
- **人设**：配置存 `llm_preset` 表，支持内置示例（`src/core/presets/*.md`）/ 文件导入 / WebUI 手写；
- **上下文**：分「个人（user）/ 群聊（group）」两种作用域，按 `(scope, 会话号, 插件)` 隔离，自动带最近 N 轮；
- **长期记忆**：user 作用域自动注入「用户印象 + 关键信息」，并按阈值自动提炼更新（个性化）；
- **流式**：全局 `LLM_STREAM_ENABLE` 开关 + 调用时 `use_stream=` 覆盖；
- **可测**：传输层可替换（`set_client_factory`），自检脚本无需真网络。

约定：本模块**不 import 插件**，插件反过来 import 它；所有 DB 读写都走 `src.core.database.get_session`。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Optional, Sequence

import httpx
import nonebot
from sqlalchemy import and_, func, or_, select

from src.config import settings
from src.core.database import get_session
from src.core.textutil import CST, fmt_dt  # noqa: F401 - 对外兼容导出
from src.core.models import (
    CHAT_TYPE_GROUP,
    FACT_SCOPE_GLOBAL,
    FACT_SCOPE_GROUP,
    FACT_SCOPE_PRIVATE,
    FACT_SCOPES,
    LLM_FACT_AUTO,
    LLM_FACT_MANUAL,
    LLM_PRESET_BUILTIN,
    LLM_PRESET_IMPORT,
    LLM_PRESET_MANUAL,
    LLM_SCOPE_GROUP,
    LLM_SCOPE_USER,
    LLM_SCOPES,
    LlmGroupProfile,
    LlmMessage,
    LlmPreset,
    LlmProvider,
    LlmSession,
    LlmUserFact,
    LlmUserProfile,
)

logger = nonebot.logger

# 内置人设目录（跟着框架走，属于「被代码引用的必要素材」）
PRESET_DIR = Path(__file__).with_name("presets")
PRESET_SUFFIXES = (".md", ".markdown", ".txt", ".json", ".yaml", ".yml")

# api_key 回显给前端时的掩码
SECRET_MASK = "********"

# `.env` 兜底配置的「虚拟服务商名」（DB 里的服务商可以起任何别的名字）
ENV_PROVIDER_NAME = "env"

# 单个服务商最多可配置的模型数（优先 + 备用）
MAX_MODELS = 3

# 数据库里的时间统一按 UTC 存（SQLite 的 func.now() 就是 UTC），对外展示时换算成本地时区。
# ℹ️ CST / fmt_dt 已抽到 `src.core.textutil`（顶部重新导出）。


def _utcnow() -> datetime:
    """当前 UTC 时间（naive，与 SQLite `func.now()` 保持一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _validate_provider_fields(
    *, base_url: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None
) -> None:
    """校验服务商字段：非 ASCII（请求头 / URL 必须是 ASCII）与漏写协议头都提前拦下。

    否则 openai / httpx 会抛出 `'ascii' codec can't encode characters...` 这种天书，
    用户根本看不懂自己错在哪。
    """
    for label, value in (("base_url", base_url), ("api_key", api_key), ("model", model)):
        if not value:
            continue
        try:
            str(value).encode("ascii")
        except UnicodeEncodeError as exc:
            raise LLMError(
                f"{label} 含非 ASCII 字符（常见于复制时带上了中文或全角符号）：{value!r}"
            ) from exc
    if base_url and not str(base_url).lower().startswith(("http://", "https://")):
        raise LLMError(f"base_url 必须以 http:// 或 https:// 开头：{base_url!r}")


class LLMError(RuntimeError):
    """LLM 相关错误（未配置 / 被禁用 / 请求失败）。插件捕获它即可友好提示。"""


# ============================== 数据结构 ==============================


@dataclass
class ProviderConfig:
    """一次调用实际使用的服务商配置。"""

    name: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    # 多模型优先级列表（含首选）：["gpt-4o", "gpt-4o-mini"] → 先 4o，失败切 4o-mini
    models: list[str] = field(default_factory=list)
    # 手动优先级：数字越小越靠前；0 = 未设置（按 models 数组顺序）
    priority: int = 0
    temperature: float = 0.8
    max_tokens: int = 0
    timeout: float = 60.0
    extra: dict = field(default_factory=dict)
    source: str = "env"  # env | db

    def masked(self) -> dict:
        data = {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "models": list(self.models or []),
            "priority": self.priority or 0,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "extra": self.extra,
            "source": self.source,
            "api_key_set": bool(self.api_key),
        }
        return data


@dataclass
class LLMResult:
    """一次调用的结构化结果。"""

    text: str = ""
    model: str = ""
    provider: str = ""
    preset: str = ""
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0
    finish_reason: str = ""
    streamed: bool = False
    raw: Any = None


@dataclass
class LLMChunk:
    """流式片段；`done=True` 时 `result` 里带完整结果。"""

    delta: str = ""
    text: str = ""
    done: bool = False
    result: Optional[LLMResult] = None


# ============================== 传输层 ==============================


class ChatClient:
    """传输层接口：把（配置 + 消息 + 参数）变成结果。

    默认实现是 OpenAI 兼容客户端；自检 / 单测可 `set_client_factory()` 换成假的。
    """

    async def complete(
        self, cfg: ProviderConfig, messages: list[dict], params: dict
    ) -> LLMResult:  # pragma: no cover - 抽象
        raise NotImplementedError

    def stream(
        self, cfg: ProviderConfig, messages: list[dict], params: dict
    ) -> AsyncIterator[LLMChunk]:  # pragma: no cover - 抽象
        raise NotImplementedError


class OpenAIChatClient(ChatClient):
    """基于官方 `openai` SDK 的实现（任何 OpenAI 兼容服务都能用）。"""

    def __init__(self) -> None:
        self._clients: dict[tuple, Any] = {}

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        """把中转站地址规范成 OpenAI SDK 能用的形态。

        很多中转站填的地址不带 `/v1`（如 `https://xxx.top`），
        此时 SDK 会去请求 `https://xxx.top/chat/completions`，
        中转站通常返回 HTML 首页而非 JSON → SDK 解析出空 content。
        这里在缺版本段时自动补 `/v1`，与 `fetch_models` 的行为保持一致。
        """
        url = (base_url or "").strip().rstrip("/")
        if not url:
            return url
        if not re.search(r"/v\d+$", url):
            url += "/v1"
        return url

    def _client(self, cfg: ProviderConfig):
        base_url = self._normalize_base_url(cfg.base_url)
        key = (base_url, cfg.api_key, cfg.timeout, settings.llm_proxy)
        client = self._clients.get(key)
        if client is None:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {
                "api_key": cfg.api_key or "sk-placeholder",
                "timeout": cfg.timeout or 60.0,
            }
            if base_url:
                kwargs["base_url"] = base_url
            if settings.llm_proxy:
                import httpx

                kwargs["http_client"] = httpx.AsyncClient(proxy=settings.llm_proxy)
            client = AsyncOpenAI(**kwargs)
            self._clients[key] = client
        return client

    @staticmethod
    def _payload(cfg: ProviderConfig, messages: list[dict], params: dict) -> dict:
        body: dict[str, Any] = {"model": params.get("model") or cfg.model, "messages": messages}
        temperature = params.get("temperature", cfg.temperature)
        if temperature is not None and float(temperature) >= 0:
            body["temperature"] = float(temperature)
        max_tokens = params.get("max_tokens", cfg.max_tokens)
        if max_tokens:
            body["max_tokens"] = int(max_tokens)
        # 供应商特有参数（extra）原样透传，调用参数优先
        extra = dict(cfg.extra or {})
        extra.update(params.get("extra") or {})
        for k, v in extra.items():
            if v is not None:
                body[str(k)] = v
        return body

    @staticmethod
    def _usage(resp: Any) -> dict:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return {}
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }

    async def complete(self, cfg, messages, params) -> LLMResult:
        started = time.perf_counter()
        try:
            resp = await self._client(cfg).chat.completions.create(
                **self._payload(cfg, messages, params)
            )
        except Exception as exc:  # noqa: BLE001 - 统一包装成 LLMError
            raise LLMError(f"LLM 请求失败（{cfg.name or 'env'}）：{exc}") from exc
        choice = (getattr(resp, "choices", None) or [None])[0]
        message = getattr(choice, "message", None)
        text = getattr(message, "content", None) or ""
        return LLMResult(
            text=text,
            model=getattr(resp, "model", "") or cfg.model,
            provider=cfg.name,
            usage=self._usage(resp),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=getattr(choice, "finish_reason", "") or "",
            raw=resp,
        )

    async def stream(self, cfg, messages, params) -> AsyncIterator[LLMChunk]:
        started = time.perf_counter()
        payload = self._payload(cfg, messages, params)
        payload["stream"] = True
        try:
            stream = await self._client(cfg).chat.completions.create(**payload)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"LLM 流式请求失败（{cfg.name or 'env'}）：{exc}") from exc
        text = ""
        usage: dict = {}
        model = cfg.model
        finish = ""
        async for chunk in stream:
            model = getattr(chunk, "model", model) or model
            if getattr(chunk, "usage", None):
                usage = self._usage(chunk)
            for choice in getattr(chunk, "choices", None) or []:
                delta = getattr(getattr(choice, "delta", None), "content", None) or ""
                if getattr(choice, "finish_reason", None):
                    finish = choice.finish_reason
                if delta:
                    text += delta
                    yield LLMChunk(delta=delta, text=text)
        yield LLMChunk(
            delta="",
            text=text,
            done=True,
            result=LLMResult(
                text=text,
                model=model,
                provider=cfg.name,
                usage=usage,
                latency_ms=int((time.perf_counter() - started) * 1000),
                finish_reason=finish,
                streamed=True,
            ),
        )


_client_impl: Optional[ChatClient] = None
_client_factory: Callable[[], ChatClient] = OpenAIChatClient


def set_client_factory(factory: Callable[[], ChatClient]) -> None:
    """替换传输层实现（自检 / 单测用）。"""
    global _client_factory, _client_impl
    _client_factory = factory
    _client_impl = None


def reset_client_factory() -> None:
    """恢复默认的 OpenAI 兼容实现。"""
    set_client_factory(OpenAIChatClient)


def client() -> ChatClient:
    """取当前传输层实例（懒加载单例）。"""
    global _client_impl
    if _client_impl is None:
        _client_impl = _client_factory()
    return _client_impl


async def shutdown_llm() -> None:
    """关闭底层 HTTP 连接（进程退出时调用）。"""
    impl = _client_impl
    if impl is None:
        return
    clients = getattr(impl, "_clients", None)
    if clients:
        for cli in list(clients.values()):
            try:
                await cli.close()
            except Exception:  # noqa: BLE001 - 关闭失败无所谓
                pass
        clients.clear()


# ============================== 服务商配置 ==============================


def env_provider() -> ProviderConfig:
    """`.env` 里的兜底配置（DB 里没有任何服务商时用它）。"""
    _validate_provider_fields(
        base_url=settings.llm_base_url, api_key=settings.llm_api_key, model=settings.llm_model
    )
    return ProviderConfig(
        name="env",
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model or "gpt-4o-mini",
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
        extra={},
        source="env",
    )


def _row_to_provider(row: LlmProvider) -> ProviderConfig:
    extra: dict = {}
    if row.extra:
        try:
            loaded = json.loads(row.extra)
            if isinstance(loaded, dict):
                extra = loaded
        except ValueError:
            logger.opt(exception=True).warning(f"服务商 {row.name} 的 extra 不是合法 JSON，已忽略")
    models: list[str] = []
    raw_models = getattr(row, "models", "") or ""
    if raw_models:
        try:
            loaded = json.loads(raw_models)
            if isinstance(loaded, list):
                models = [str(m).strip() for m in loaded if str(m).strip()]
        except ValueError:
            logger.opt(exception=True).warning(f"服务商 {row.name} 的 models 不是合法 JSON，已忽略")
    if not models and row.model:
        models = [row.model]
    return ProviderConfig(
        name=row.name,
        base_url=row.base_url or "",
        api_key=row.api_key or "",
        model=row.model or (models[0] if models else "gpt-4o-mini"),
        models=models,
        priority=int(getattr(row, "priority", None) or 0),
        temperature=row.temperature if row.temperature is not None else 0.8,
        max_tokens=row.max_tokens or 0,
        timeout=row.timeout or 60.0,
        extra=extra,
        source="db",
    )


def _provider_row_dict(row: LlmProvider) -> dict:
    cfg = _row_to_provider(row)
    data = cfg.masked()
    data.update(
        {
            "id": row.id,
            "enabled": bool(row.enabled),
            "is_default": bool(row.is_default),
            "api_key": SECRET_MASK if row.api_key else "",
            "updated_by": row.updated_by or "",
            "updated_at": fmt_dt(row.updated_at),
        }
    )
    return data


async def list_providers() -> list[dict]:
    """列出全部服务商（api_key 掩码）。

    DB 里没有任何服务商（或者有但都没启用）且 `.env` 配了兜底时，
    末尾追加一条 `virtual=True` 的虚拟项，方便前端提示「当前在用 .env」。
    """
    async with get_session(readonly=True) as session:
        rows = (
            (await session.execute(select(LlmProvider).order_by(LlmProvider.id))).scalars().all()
        )
    data = [_provider_row_dict(r) for r in rows]
    env_cfg = env_provider()
    has_active = any(item["enabled"] for item in data)
    if not has_active and (env_cfg.api_key or env_cfg.base_url):
        virtual = env_cfg.masked()
        virtual.update(
            {
                "id": 0,
                "api_key": SECRET_MASK if env_cfg.api_key else "",
                "enabled": True,
                "is_default": True,
                "virtual": True,
                "updated_by": "",
                "updated_at": "",
            }
        )
        data.append(virtual)
    return data


async def get_provider_row(name: str, *, session=None) -> Optional[LlmProvider]:
    stmt = select(LlmProvider).where(LlmProvider.name == name)
    if session is not None:
        return (await session.execute(stmt)).scalar_one_or_none()
    async with get_session(readonly=True) as own:
        return (await own.execute(stmt)).scalar_one_or_none()


async def default_provider_name() -> str:
    """当前默认服务商名（DB 默认 → 第一个启用 → 空）。"""
    async with get_session(readonly=True) as session:
        row = (
            await session.execute(
                select(LlmProvider).where(LlmProvider.is_default.is_(True)).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            row = (
                await session.execute(
                    select(LlmProvider).where(LlmProvider.enabled.is_(True)).order_by(LlmProvider.id).limit(1)
                )
            ).scalar_one_or_none()
    return row.name if row else ""


async def resolve_provider(name: Optional[str] = None) -> ProviderConfig:
    """解析出这次调用要用的服务商：显式 name → DB 默认 → .env 兜底。"""
    if not settings.llm_enable:
        raise LLMError("LLM 已全局禁用（LLM_ENABLE=false）")
    if name and name != ENV_PROVIDER_NAME:
        row = await get_provider_row(name)
        if row is None:
            raise LLMError(f"服务商不存在：{name}")
        if not row.enabled:
            raise LLMError(f"服务商已禁用：{name}")
        cfg = _row_to_provider(row)
    elif name == ENV_PROVIDER_NAME:
        cfg = env_provider()
    else:
        async with get_session(readonly=True) as session:
            row = (
                await session.execute(
                    select(LlmProvider).where(LlmProvider.is_default.is_(True)).limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                row = (
                    await session.execute(
                        select(LlmProvider)
                        .where(LlmProvider.enabled.is_(True))
                        .order_by(LlmProvider.id)
                        .limit(1)
                    )
                ).scalar_one_or_none()
        cfg = _row_to_provider(row) if row is not None else env_provider()
    if not cfg.api_key and not cfg.base_url:
        raise LLMError(
            "尚未配置 LLM 服务商：请在 WebUI「LLM」页新增服务商，"
            "或设置 .env 的 LLM_API_KEY / LLM_BASE_URL"
        )
    if not cfg.model:
        cfg.model = settings.llm_model or "gpt-4o-mini"
    if not cfg.models:
        cfg.models = [cfg.model]
    return cfg


async def set_provider(
    name: str,
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    models: Optional[list[str]] = None,
    priority: Optional[int] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[float] = None,
    extra: Optional[dict | str] = None,
    enabled: Optional[bool] = None,
    is_default: Optional[bool] = None,
    updated_by: str = "",
) -> dict:
    """新增 / 更新服务商（None = 不改动，`api_key` 传掩码 = 保持原值）。"""
    name = (name or "").strip()
    if not name:
        raise LLMError("服务商名不能为空")
    if api_key == SECRET_MASK:
        api_key = None
    extra_text: Optional[str] = None
    if isinstance(extra, dict):
        extra_text = json.dumps(extra, ensure_ascii=False) if extra else ""
    elif isinstance(extra, str):
        if extra.strip():
            try:
                json.loads(extra)
            except ValueError as exc:
                raise LLMError(f"extra 必须是合法 JSON 对象：{exc}") from exc
        extra_text = extra.strip()
    models_text: Optional[str] = None
    if models is not None:
        models = [str(m).strip() for m in models if str(m).strip()]
        if not models:
            raise LLMError("模型列表不能为空")
        if len(models) > MAX_MODELS:
            raise LLMError(f"最多只能配置 {MAX_MODELS} 个模型（优先 + 备用）")
        for m in models:
            _validate_provider_fields(model=m)
        models_text = json.dumps(models, ensure_ascii=False)

    async with get_session() as session:
        row = await get_provider_row(name, session=session)
        created = row is None
        if row is None:
            row = LlmProvider(name=name)
            session.add(row)
        if base_url is not None:
            _validate_provider_fields(base_url=base_url)
            row.base_url = base_url.strip()
        if api_key is not None:
            _validate_provider_fields(api_key=api_key)
            row.api_key = api_key.strip()
        if model is not None:
            _validate_provider_fields(model=model)
            row.model = model.strip()
        if models_text is not None:
            row.models = models_text
        if priority is not None:
            row.priority = max(0, int(priority))
        if temperature is not None:
            row.temperature = float(temperature)
        if max_tokens is not None:
            row.max_tokens = int(max_tokens)
        if timeout is not None:
            row.timeout = float(timeout)
        if extra_text is not None:
            row.extra = extra_text
        if enabled is not None:
            row.enabled = bool(enabled)
        if updated_by:
            row.updated_by = updated_by
        if is_default:
            # 默认只能有一个
            for other in (
                (await session.execute(select(LlmProvider).where(LlmProvider.id != row.id)))
                .scalars()
                .all()
            ):
                other.is_default = False
            row.is_default = True
        elif is_default is False:
            row.is_default = False
        await session.commit()
        await session.refresh(row)
        data = _provider_row_dict(row)
    data["action"] = "created" if created else "updated"
    return data


async def delete_provider(name: str) -> bool:
    async with get_session() as session:
        row = await get_provider_row(name, session=session)
        if row is None:
            return False
        was_default = bool(row.is_default)
        await session.delete(row)
        if was_default:
            nxt = (
                await session.execute(
                    select(LlmProvider).where(LlmProvider.enabled.is_(True)).order_by(LlmProvider.id).limit(1)
                )
            ).scalar_one_or_none()
            if nxt is not None:
                nxt.is_default = True
        await session.commit()
    return True


# ============================== 模型拉取 ==============================


async def _resolve_model_config(
    base_url: Optional[str] = None, api_key: Optional[str] = None
) -> tuple[str, str]:
    """把「中转站地址 + token」解析成可发 HTTP 请求的 base_url / api_key。

    - base_url 为空 → 从当前默认服务商取；还没有 → 用 .env 兜底；
    - 老 base_url 没带 `/v1` → 自动补上（中转站常见写法），保证 /models 能拼对。
    """
    raw = (base_url or "").strip()
    key = (api_key or "").strip()
    if not raw or not key:
        cfg = await resolve_provider(None)
        raw = raw or cfg.base_url
        key = key or cfg.api_key
    raw = raw.strip()
    if not raw:
        raise LLMError("请先填写中转站 API 地址")
    if not key:
        raise LLMError("请先填写中转站 Token")
    _validate_provider_fields(base_url=raw, api_key=key)
    url = raw if raw.lower().startswith(("http://", "https://")) else f"https://{raw}"
    # 与对话调用共用同一套规范化逻辑（缺 /v1 自动补）
    url = OpenAIChatClient._normalize_base_url(url)
    return url, key


async def fetch_models(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    *,
    timeout: float = 20.0,
) -> list[str]:
    """从 OpenAI 兼容中转站拉取可用模型名列表（GET /models）。

    中转站不实现该端点时，错误信息会给出常见原因（key 不对 / 地址不对 / 端点不支持）。
    """
    url, key = await _resolve_model_config(base_url, api_key)
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=timeout) as hc:
        try:
            resp = await hc.get(f"{url.rstrip('/')}/models", headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"拉取模型列表失败（网络错误）：{exc}") from exc
    if resp.status_code == 401:
        raise LLMError("Token 无效或已过期（401）")
    if resp.status_code == 403:
        raise LLMError("没有权限访问该中转站（403）")
    if resp.status_code == 404:
        raise LLMError("该地址不支持 /models 端点（404），请检查是否填到了 /v1 那一层")
    if resp.status_code >= 400:
        raise LLMError(f"拉取模型列表失败：HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise LLMError("模型列表返回的不是合法 JSON") from exc
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise LLMError("返回内容里没有 data 列表，可能不是 OpenAI 兼容的中转站")
    models: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            models.append(str(item["id"]))
    if not models:
        raise LLMError("中转站返回的模型列表为空")
    return models


# ============================== 核心调用（多模型优先级） ==============================


def _order_models(cfg: ProviderConfig, preferred: Optional[str] = None) -> list[str]:
    """按「手动优先级 → 会话首选 → 配置顺序」排出候选模型列表。

    - 手动优先级（`priority`，数字小者靠前）优先；同优先级按配置顺序稳定；
    - `preferred`（本次会话首选，如群/个人显式指定的模型）总是提到最前；
    - 去重、限 `MAX_MODELS` 个。
    """
    base = [m for m in (cfg.models or []) if m]
    if not base:
        base = [cfg.model] if cfg.model else []
    out: list[str] = []
    for m in base:
        if m not in out:
            out.append(m)
    if preferred:
        pref = str(preferred).strip()
        if pref in out:
            out.remove(pref)
        out.insert(0, pref)
        out = out[:MAX_MODELS]
    return out


async def _try_models(
    cfg: ProviderConfig,
    payload: list[dict],
    params: dict,
    candidates: list[str],
    *,
    stream: bool = False,
) -> tuple[LLMResult, str]:
    """按候选顺序逐个尝试，失败自动切下一个；全失败抛最后一个 LLMError。"""
    errors: list[str] = []
    last_exc: Optional[LLMError] = None
    for idx, model in enumerate(candidates):
        attempt_params = dict(params)
        attempt_params["model"] = model
        try:
            if stream:
                pieces: list[str] = []
                result: Optional[LLMResult] = None
                async for chunk in client().stream(cfg, payload, attempt_params):
                    if not chunk.done:
                        pieces.append(chunk.delta)
                    else:
                        result = chunk.result
                        if result is not None:
                            result.text = "".join(pieces) or result.text
                        break
                if result is None:
                    raise LLMError("流式返回为空")
                return result, model
            result = await client().complete(cfg, payload, attempt_params)
            return result, model
        except LLMError as exc:
            last_exc = exc
            hint = f"{model}: {exc}"
            errors.append(hint)
            logger.warning(f"模型 {model} 调用失败，尝试下一个（{idx + 1}/{len(candidates)}）：{exc}")
    raise LLMError("全部模型都调用失败：" + "；".join(errors)) from last_exc


async def test_provider(name: Optional[str] = None) -> LLMResult:
    """连通性自检：发一条极短请求，返回耗时 / 模型 / 回复片段。"""
    cfg = await resolve_provider(name)
    result = await client().complete(
        cfg,
        [{"role": "user", "content": "ping"}],
        {"max_tokens": 16, "temperature": 0.0},
    )
    result.provider = cfg.name or "env"
    result.text = (result.text or "").strip()
    return result


# ============================== 人设（preset） ==============================

_FRONT_MATTER_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n?", re.S)
# front-matter 里可识别的参数键（其余忽略）
_PARAM_KEYS = (
    "temperature",
    "max_tokens",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
    "seed",
)


def _parse_yaml_block(text: str) -> dict:
    """解析 front-matter：优先 PyYAML，没有就退化成「key: value」逐行解析。"""
    try:
        import yaml  # type: ignore
    except Exception:  # noqa: BLE001 - 没装 yaml 也能用
        yaml = None
    if yaml is not None:
        try:
            data = yaml.safe_load(text) or {}
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001 - 手写 front-matter 不严格，退化解析
            pass
    data: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip().strip("'\"")
        if not key:
            continue
        if value.lower() in ("true", "false"):
            data[key] = value.lower() == "true"
        else:
            try:
                data[key] = int(value)
            except ValueError:
                try:
                    data[key] = float(value)
                except ValueError:
                    data[key] = value
    return data


def parse_preset_text(text: str, default_name: str = "preset") -> dict:
    """把「Markdown（带 front-matter）」或「JSON」文本解析成人设字典。"""
    raw = (text or "").lstrip("\ufeff")
    meta: dict = {}
    body = raw
    if raw.lstrip().startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise LLMError(f"人设 JSON 解析失败：{exc}") from exc
        if not isinstance(data, dict):
            raise LLMError("人设 JSON 必须是对象")
        meta = {k: v for k, v in data.items() if k not in ("system_prompt", "prompt")}
        body = str(data.get("system_prompt") or data.get("prompt") or "")
    else:
        match = _FRONT_MATTER_RE.match(raw)
        if match:
            meta = _parse_yaml_block(match.group(1))
            body = raw[match.end() :]
    name = str(meta.get("name") or default_name or "preset").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-\.]{1,64}", name):
        raise LLMError(f"人设名不合法（只允许字母数字下划线连字符点）：{name}")
    params: dict = {}
    meta_params = meta.get("params")
    if isinstance(meta_params, dict):
        params.update({k: v for k, v in meta_params.items() if v is not None})
    for key in _PARAM_KEYS:
        if meta.get(key) is not None:
            params[key] = meta[key]
    system_prompt = (body or "").strip()
    if not system_prompt:
        raise LLMError(f"人设 {name} 没有正文（system prompt）")
    return {
        "name": name,
        "display_name": str(meta.get("display_name") or meta.get("title") or name).strip(),
        "description": str(meta.get("description") or "").strip(),
        "system_prompt": system_prompt,
        "params": params,
        "enabled": bool(meta.get("enabled", True)),
    }


def preset_to_text(data: dict) -> str:
    """人设字典 → Markdown 文本（用于导出 / 当模板）。"""
    lines = ["---"]
    lines.append(f"name: {data.get('name', '')}")
    if data.get("display_name"):
        lines.append(f"display_name: {data['display_name']}")
    if data.get("description"):
        lines.append(f"description: {data['description']}")
    for key, value in (data.get("params") or {}).items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(data.get("system_prompt", ""))
    return "\n".join(lines).rstrip() + "\n"


def _preset_row_dict(row: LlmPreset, *, with_prompt: bool = False) -> dict:
    data = {
        "id": row.id,
        "name": row.name,
        "display_name": row.display_name or row.name,
        "description": row.description or "",
        "params": _safe_json(row.params, {}),
        "enabled": bool(row.enabled),
        "source": row.source or LLM_PRESET_MANUAL,
        "prompt_length": len(row.system_prompt or ""),
        "updated_at": fmt_dt(row.updated_at),
    }
    if with_prompt:
        data["system_prompt"] = row.system_prompt or ""
    return data


def _safe_json(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


async def list_presets() -> list[dict]:
    async with get_session(readonly=True) as session:
        rows = (await session.execute(select(LlmPreset).order_by(LlmPreset.id))).scalars().all()
    return [_preset_row_dict(r) for r in rows]


async def get_preset(name: str) -> Optional[dict]:
    """取人设（含 system_prompt）；不存在返回 None。"""
    if not name:
        return None
    async with get_session(readonly=True) as session:
        row = (
            await session.execute(select(LlmPreset).where(LlmPreset.name == name))
        ).scalar_one_or_none()
    if row is None or not row.enabled:
        return None
    return _preset_row_dict(row, with_prompt=True)


async def set_preset(
    name: str,
    *,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    system_prompt: Optional[str] = None,
    params: Optional[dict | str] = None,
    enabled: Optional[bool] = None,
    source: str = LLM_PRESET_MANUAL,
    updated_by: str = "",
) -> dict:
    """新增 / 更新人设（None = 不改动）。"""
    name = (name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-\.]{1,64}", name or ""):
        raise LLMError(f"人设名不合法：{name!r}")
    params_text: Optional[str] = None
    if isinstance(params, dict):
        params_text = json.dumps(params, ensure_ascii=False) if params else ""
    elif isinstance(params, str):
        if params.strip():
            _safe_json(params, None) or None
            if _safe_json(params, None) is None:
                raise LLMError("人设参数必须是合法 JSON 对象")
        params_text = params.strip()
    async with get_session() as session:
        row = (
            await session.execute(select(LlmPreset).where(LlmPreset.name == name))
        ).scalar_one_or_none()
        created = row is None
        if row is None:
            row = LlmPreset(name=name)
            session.add(row)
        if display_name is not None:
            row.display_name = display_name.strip()
        if description is not None:
            row.description = description.strip()
        if system_prompt is not None:
            row.system_prompt = system_prompt
        if params_text is not None:
            row.params = params_text
        if enabled is not None:
            row.enabled = bool(enabled)
        if created or source:
            row.source = source or row.source or LLM_PRESET_MANUAL
        if updated_by:
            row.updated_by = updated_by
        await session.commit()
        await session.refresh(row)
        data = _preset_row_dict(row, with_prompt=True)
    data["action"] = "created" if created else "updated"
    return data


async def delete_preset(name: str) -> bool:
    async with get_session() as session:
        row = (
            await session.execute(select(LlmPreset).where(LlmPreset.name == name))
        ).scalar_one_or_none()
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
    return True


async def import_preset_text(
    text: str,
    *,
    filename: str = "",
    force: bool = False,
    source: str = LLM_PRESET_IMPORT,
    updated_by: str = "",
) -> dict:
    """导入一份人设文本 → 落库。

    同名已存在时的覆盖规则（`force=True` 一律覆盖）：

    | 已有 source | 新 source | 行为 |
    |---|---|---|
    | `manual` | 任意 | 跳过（人工编辑优先） |
    | `import` | `builtin` | 跳过（别让内置盖掉用户导入的） |
    | 其它（`import` / `builtin`） | 其它 | 覆盖 |
    """
    default_name = Path(filename).stem if filename else "preset"
    data = parse_preset_text(text, default_name=default_name)
    name = data["name"]
    async with get_session(readonly=True) as session:
        row = (
            await session.execute(select(LlmPreset).where(LlmPreset.name == name))
        ).scalar_one_or_none()
        existing_source = (row.source if row else "") or LLM_PRESET_MANUAL
    if row is not None and not force:
        if existing_source == LLM_PRESET_MANUAL:
            return {
                "name": name,
                "action": "skipped",
                "reason": "已存在人工编辑的人设（force=True 可覆盖）",
            }
        if existing_source == LLM_PRESET_IMPORT and source == LLM_PRESET_BUILTIN:
            return {
                "name": name,
                "action": "skipped",
                "reason": "已存在用户导入的人设，内置人设不覆盖它",
            }
    saved = await set_preset(
        name,
        display_name=data["display_name"],
        description=data["description"],
        system_prompt=data["system_prompt"],
        params=data["params"],
        enabled=data["enabled"],
        source=source,
        updated_by=updated_by,
    )
    saved["filename"] = filename
    return saved


async def import_preset_file(
    path: str | Path, *, force: bool = False, source: str = LLM_PRESET_IMPORT
) -> dict:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    return await import_preset_text(text, filename=file.name, force=force, source=source)


async def import_presets_from_dir(
    path: str | Path | None = None, *, force: bool = False, source: str = LLM_PRESET_IMPORT
) -> list[dict]:
    """把一个目录下的人设文件全部导入（`*.md/.json/.yaml/.txt`）。"""
    folder = Path(path) if path else PRESET_DIR
    if not folder.is_dir():
        raise LLMError(f"目录不存在：{folder}")
    results: list[dict] = []
    for file in sorted(folder.iterdir()):
        if not file.is_file() or file.suffix.lower() not in PRESET_SUFFIXES:
            continue
        try:
            results.append(await import_preset_file(file, force=force, source=source))
        except LLMError as exc:
            logger.warning(f"跳过人设文件 {file.name}：{exc}")
    return results


async def load_builtin_presets(*, force: bool = False) -> list[dict]:
    """启动时导入内置人设（`src/core/presets/`）——已有的人工人设不覆盖。"""
    if not PRESET_DIR.is_dir():
        return []
    return await import_presets_from_dir(PRESET_DIR, force=force, source=LLM_PRESET_BUILTIN)


async def export_preset(name: str) -> str:
    """导出人设为 Markdown 文本。"""
    data = await get_preset(name)
    if data is None:
        raise LLMError(f"人设不存在：{name}")
    return preset_to_text(data)


# ============================== 会话（上下文） ==============================


def normalize_scope(scope: Optional[str]) -> Optional[str]:
    """校验作用域：None / "user" / "group"（空串视为 None）。"""
    if scope is None:
        return None
    scope = str(scope).strip().lower()
    if not scope:
        return None
    if scope not in LLM_SCOPES:
        raise LLMError(f"作用域只能是 {LLM_SCOPES} 之一，收到 {scope!r}")
    return scope


def normalize_messages(messages: Iterable[dict]) -> list[dict]:
    """消息列表归一化（role / content / name），非 dict 直接报错。"""
    out: list[dict] = []
    for item in messages:
        if not isinstance(item, dict):
            raise LLMError("messages 必须是 dict 列表")
        role = str(item.get("role") or "user").strip() or "user"
        content = item.get("content")
        if isinstance(content, (list, dict)):
            content = json.dumps(content, ensure_ascii=False)
        msg = {"role": role, "content": "" if content is None else str(content)}
        if item.get("name"):
            msg["name"] = str(item["name"])
        out.append(msg)
    if not out:
        raise LLMError("messages 不能为空")
    return out


async def _get_or_create_session(
    session,
    scope_type: str,
    scope_id: str,
    plugin_name: str,
    *,
    preset_name: str = "",
    provider_name: str = "",
) -> LlmSession:
    row = (
        await session.execute(
            select(LlmSession)
            .where(
                LlmSession.scope_type == scope_type,
                LlmSession.scope_id == scope_id,
                LlmSession.plugin_name == plugin_name,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        row = LlmSession(scope_type=scope_type, scope_id=scope_id, plugin_name=plugin_name)
        session.add(row)
        await session.flush()
    if preset_name:
        row.preset_name = preset_name
    if provider_name:
        row.provider_name = provider_name
    return row


def _session_row_dict(row: LlmSession) -> dict:
    return {
        "id": row.id,
        "scope_type": row.scope_type,
        "scope_id": row.scope_id,
        "plugin_name": row.plugin_name or "",
        "preset_name": row.preset_name or "",
        "provider_name": row.provider_name or "",
        "message_count": row.message_count or 0,
        "user_message_count": row.user_message_count or 0,
        "summary": row.summary or "",
        "created_at": fmt_dt(row.created_at),
        "updated_at": fmt_dt(row.updated_at),
    }


async def find_session(scope_type: str, scope_id: str, plugin_name: str = "") -> Optional[dict]:
    """按作用域查会话（不存在返回 None）。"""
    scope_type = normalize_scope(scope_type) or LLM_SCOPE_USER
    async with get_session(readonly=True) as session:
        row = (
            await session.execute(
                select(LlmSession)
                .where(
                    LlmSession.scope_type == scope_type,
                    LlmSession.scope_id == str(scope_id),
                    LlmSession.plugin_name == (plugin_name or ""),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return _session_row_dict(row) if row else None


async def ensure_session(
    scope_type: str, scope_id: str, plugin_name: str = "", *, preset_name: str = ""
) -> dict:
    """确保会话存在并返回它（插件想提前建会话 / 查计数时用）。"""
    scope_type = normalize_scope(scope_type) or LLM_SCOPE_USER
    async with get_session() as session:
        row = await _get_or_create_session(
            session,
            scope_type,
            str(scope_id),
            plugin_name or "",
            preset_name=preset_name,
        )
        await session.commit()
        await session.refresh(row)
        return _session_row_dict(row)


async def set_session_preset(
    scope_type: str,
    scope_id: str,
    plugin_name: str = "",
    *,
    preset_name: Optional[str] = None,
    provider_name: Optional[str] = None,
) -> dict:
    """设置某会话用的人设 / 服务商（`None` = 不改动，空串 = 清空回落默认）。

    人设名非空但库里查不到 → `LLMError`（避免静默用错人设）；
    服务商名同理（`env` 这个虚拟名放行）。
    """
    if preset_name:
        if await get_preset(preset_name) is None:
            raise LLMError(f"人设不存在：{preset_name}")
    if provider_name and provider_name.strip() != ENV_PROVIDER_NAME:
        if await get_provider_row(provider_name.strip()) is None:
            raise LLMError(f"服务商不存在：{provider_name}")
    info = await ensure_session(scope_type, scope_id, plugin_name)
    async with get_session() as session:
        row = (
            await session.execute(select(LlmSession).where(LlmSession.id == info["id"]))
        ).scalar_one_or_none()
        if row is None:
            raise LLMError("会话不存在（可能刚被清理）")
        if preset_name is not None:
            row.preset_name = preset_name.strip()
        if provider_name is not None:
            row.provider_name = provider_name.strip()
        await session.commit()
        await session.refresh(row)
        return _session_row_dict(row)


async def load_history(session_id: int, *, rounds: Optional[int] = None) -> list[dict]:
    """取某会话最近 `rounds` 轮（1 轮 = 用户 + 助手）的消息。"""
    rounds = settings.llm_context_rounds if rounds is None else rounds
    if not session_id or rounds <= 0:
        return []
    limit = max(1, int(rounds)) * 2
    async with get_session(readonly=True) as session:
        rows = (
            (
                await session.execute(
                    select(LlmMessage)
                    .where(LlmMessage.session_id == session_id)
                    .order_by(LlmMessage.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    out: list[dict] = []
    for row in reversed(rows):
        if row.role not in ("user", "assistant", "system"):
            continue
        item = {"role": row.role, "content": row.content or ""}
        if row.name:
            item["name"] = row.name
        out.append(item)
    return out


async def append_message(session_id: int, role: str, content: str, name: str = "") -> int:
    """往会话里追加一条消息，返回消息 id。"""
    async with get_session() as session:
        row = LlmMessage(session_id=session_id, role=role, content=content or "", name=name or "")
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def record_messages(session_id: int, messages: Sequence[dict]) -> int:
    """批量追加消息（调用方输入 + 助手回复），并维护会话计数。"""
    if not session_id or not messages:
        return 0
    added = 0
    async with get_session() as session:
        sess_row = (
            await session.execute(select(LlmSession).where(LlmSession.id == session_id).limit(1))
        ).scalar_one_or_none()
        for item in messages:
            role = str(item.get("role") or "user")
            session.add(
                LlmMessage(
                    session_id=session_id,
                    role=role,
                    content=str(item.get("content") or ""),
                    name=str(item.get("name") or ""),
                )
            )
            added += 1
            if sess_row is not None:
                sess_row.message_count = (sess_row.message_count or 0) + 1
                if role == "user":
                    sess_row.user_message_count = (sess_row.user_message_count or 0) + 1
        await session.commit()
    return added


async def list_sessions(limit: int = 200) -> list[dict]:
    """列出会话（最近更新的排前面）。"""
    async with get_session(readonly=True) as session:
        rows = (
            (
                await session.execute(
                    select(LlmSession).order_by(LlmSession.updated_at.desc()).limit(max(1, limit))
                )
            )
            .scalars()
            .all()
        )
    return [_session_row_dict(r) for r in rows]


async def session_messages(session_id: int, limit: int = 100) -> list[dict]:
    """读某会话的消息（按时间正序）。"""
    async with get_session(readonly=True) as session:
        rows = (
            (
                await session.execute(
                    select(LlmMessage)
                    .where(LlmMessage.session_id == session_id)
                    .order_by(LlmMessage.id)
                    .limit(max(1, limit))
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": r.id,
            "role": r.role,
            "content": r.content or "",
            "name": r.name or "",
            "created_at": fmt_dt(r.created_at),
        }
        for r in rows
    ]


async def clear_sessions(
    *,
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
    plugin_name: Optional[str] = None,
    session_id: Optional[int] = None,
) -> int:
    """清空会话（连带消息），返回清掉的会话数。"""
    from sqlalchemy import delete

    async with get_session() as session:
        stmt = select(LlmSession)
        if session_id:
            stmt = stmt.where(LlmSession.id == session_id)
        else:
            if scope_type:
                stmt = stmt.where(LlmSession.scope_type == scope_type)
            if scope_id is not None:
                stmt = stmt.where(LlmSession.scope_id == str(scope_id))
            if plugin_name is not None:
                stmt = stmt.where(LlmSession.plugin_name == plugin_name)
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return 0
        ids = [r.id for r in rows]
        await session.execute(delete(LlmMessage).where(LlmMessage.session_id.in_(ids)))
        for row in rows:
            await session.delete(row)
        await session.commit()
    return len(ids)


async def get_session_by_id(session_id: int) -> Optional[dict]:
    """按主键取会话（WebUI 用）。"""
    async with get_session(readonly=True) as session:
        row = (
            await session.execute(select(LlmSession).where(LlmSession.id == session_id))
        ).scalar_one_or_none()
    return _session_row_dict(row) if row is not None else None


async def set_session_preset_by_id(
    session_id: int,
    *,
    preset_name: Optional[str] = None,
    provider_name: Optional[str] = None,
) -> dict:
    """按会话主键设置人设 / 服务商（`None` = 不改动，空串 = 清空回落默认）。

    内部转调 `set_session_preset()`，校验逻辑只维护一份。
    """
    info = await get_session_by_id(session_id)
    if info is None:
        raise LLMError(f"会话不存在：{session_id}")
    return await set_session_preset(
        info["scope_type"],
        info["scope_id"],
        info.get("plugin_name", ""),
        preset_name=preset_name,
        provider_name=provider_name,
    )


# ============================== 长期记忆（印象 + 关键信息） ==============================

_IMPRESSION_PROMPT = """你是一个用户信息抽取助手。下面是一位用户与机器人的近期对话，
以及此前已经记录过的印象与关键信息。请更新它们。

只输出**严格 JSON**（不要代码块、不要解释），格式：
{{"impression": "80 字以内的用户画像", "facts": [{{"key": "简短键名", "value": "具体内容"}}]}}

要求：
- 只写对话 / 旧记录中能确认的信息，不要编造；
- facts 最多 8 条，只保留对长期个性化有用的（称呼、偏好、身份、习惯、禁忌等）；
- 没有新信息时返回空数组；
- 不要记录密码、密钥等隐私敏感信息。

【旧印象】{old}
【旧关键信息】{old_facts}
【近期对话】
{convo}"""


_GROUP_USER_FACT_PROMPT = """你是一个信息抽取助手。下面是某用户在一个 QQ 群里的发言，
以及此前已记录过的、关于该用户的群内可见信息。请只更新「关于这位用户本人」的事实。

只输出**严格 JSON**（不要代码块、不要解释），格式：
{{"facts": [{{"key": "简短键名", "value": "具体内容"}}]}}

要求：
- 只写这些发言里能确认的、关于该用户本人的信息（称呼、偏好、身份、习惯、禁忌等）；
- 不要把「群聊氛围/别人的事/临时话题」写成该用户的事实；
- 不要编造；没有新信息时返回空数组；最多 6 条；
- 不要记录密码、密钥等隐私敏感信息。

【旧关键信息】{old_facts}
【该用户在本群的发言】
{convo}"""


def _group_by_scope(msgs: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """把消息按来源分组：群消息 → (group, 群号)；私聊 → (private, "")。"""
    out: dict[tuple[str, str], list[dict]] = {}
    for m in msgs:
        if m.get("message_type") == CHAT_TYPE_GROUP:
            key = (FACT_SCOPE_GROUP, str(m.get("group_id") or ""))
        else:
            key = (FACT_SCOPE_PRIVATE, "")
        out.setdefault(key, []).append(m)
    return out


def _facts_text(facts: list[dict]) -> str:
    return "\n".join(f"- {f.get('key')}：{f.get('value', '')}" for f in facts) or "（暂无）"


def _convo_lines(msgs: list[dict]) -> str:
    return "\n".join(
        f"{m.get('nickname') or m.get('user_id')}：{(m.get('content') or '')[:300]}" for m in msgs
    )


def _extract_json(text: str) -> dict:
    """从模型输出里抠出第一个合法 JSON 对象（容忍代码块 / 前后废话）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"```\s*$", "", raw).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


async def get_profile(
    user_id: str | int,
    *,
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
) -> dict:
    """取用户长期记忆：印象 + 关键信息列表。

    `scope_type` 决定事实的可见窗口（跨窗口隔离）：
    - `FACT_SCOPE_PRIVATE` → 只取 `global + private`（私聊用）；
    - `FACT_SCOPE_GROUP`   → 取 `global + 本群 group`（群聊用）；
    - `None`               → 不过滤（全量，WebUI 管理页用）。
    """
    uid = str(user_id)
    async with get_session(readonly=True) as session:
        row = (
            await session.execute(
                select(LlmUserProfile).where(LlmUserProfile.user_id == uid).limit(1)
            )
        ).scalar_one_or_none()
        stmt = select(LlmUserFact).where(LlmUserFact.user_id == uid)
        if scope_type == FACT_SCOPE_PRIVATE:
            stmt = stmt.where(LlmUserFact.scope_type.in_((FACT_SCOPE_GLOBAL, FACT_SCOPE_PRIVATE)))
        elif scope_type == FACT_SCOPE_GROUP:
            stmt = stmt.where(
                or_(
                    LlmUserFact.scope_type == FACT_SCOPE_GLOBAL,
                    and_(
                        LlmUserFact.scope_type == FACT_SCOPE_GROUP,
                        LlmUserFact.scope_id == str(scope_id or ""),
                    ),
                )
            )
        facts = (
            (await session.execute(stmt.order_by(LlmUserFact.updated_at.desc())))
            .scalars()
            .all()
        )
    return {
        "user_id": uid,
        "impression": row.impression if row else "",
        "message_count": row.message_count if row else 0,
        "last_msg_id": row.last_msg_id if row else 0,
        "impression_at": (
            fmt_dt(row.impression_at)
            if row and row.impression_at
            else ""
        ),
        "facts": [
            {
                "key": f.fact_key,
                "value": f.fact_value or "",
                "source": f.source or LLM_FACT_AUTO,
                "scope_type": f.scope_type or FACT_SCOPE_PRIVATE,
                "scope_id": f.scope_id or "",
                "updated_at": (
                    fmt_dt(f.updated_at)
                ),
            }
            for f in facts
        ],
    }


async def set_impression(
    user_id: str | int, impression: str, *, count: Optional[int] = None
) -> dict:
    """写入用户印象（WebUI / 插件手写）。"""
    uid = str(user_id)
    async with get_session() as session:
        row = (
            await session.execute(
                select(LlmUserProfile).where(LlmUserProfile.user_id == uid).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            row = LlmUserProfile(user_id=uid)
            session.add(row)
        row.impression = impression or ""
        row.impression_at = _utcnow()
        if count is not None:
            row.message_count = int(count)
        await session.commit()
    return await get_profile(uid)


async def set_fact(
    user_id: str | int,
    key: str,
    value: str,
    *,
    source: str = LLM_FACT_MANUAL,
    scope_type: str = FACT_SCOPE_GLOBAL,
    scope_id: str = "",
    overwrite_manual: bool = False,
) -> dict:
    """写入 / 更新一条关键信息（`source=auto` 默认不覆盖同 scope 的人工记录）。

    `scope_type` 决定该条事实在哪些窗口可见（见 FACT_SCOPE_* 常量）：
    - `global`（默认）：任何窗口可见（WebUI 手写默认）；
    - `group`：仅该群可见，`scope_id` 必须为群号；
    - `private`：仅私聊可见，`scope_id` 恒为 ""。
    唯一键为 `(user_id, fact_key, scope_type, scope_id)`。
    """
    uid = str(user_id)
    key = (key or "").strip()
    if not key:
        raise LLMError("关键信息的 key 不能为空")
    if len(key) > 64:
        raise LLMError("关键信息的 key 太长（最多 64 字符）")
    if scope_type not in FACT_SCOPES:
        raise LLMError(f"记忆作用域非法：{scope_type}（可选 {', '.join(FACT_SCOPES)}）")
    if scope_type == FACT_SCOPE_GROUP:
        scope_id = (scope_id or "").strip()
        if not scope_id:
            raise LLMError("群作用域的关键信息必须给 scope_id（群号）")
    else:
        scope_id = ""
    async with get_session() as session:
        row = (
            await session.execute(
                select(LlmUserFact)
                .where(
                    LlmUserFact.user_id == uid,
                    LlmUserFact.fact_key == key,
                    LlmUserFact.scope_type == scope_type,
                    LlmUserFact.scope_id == scope_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None and row.source == LLM_FACT_MANUAL and source == LLM_FACT_AUTO and not overwrite_manual:
            return await get_profile(uid)
        if row is None:
            row = LlmUserFact(
                user_id=uid, fact_key=key, scope_type=scope_type, scope_id=scope_id
            )
            session.add(row)
        row.fact_value = value or ""
        row.source = source
        await session.commit()
    return await get_profile(uid)


async def list_profiles(limit: int = 200) -> list[dict]:
    """列出有印象 / 关键信息的用户（WebUI 用，最近更新的排前面）。"""
    from sqlalchemy import func

    async with get_session(readonly=True) as session:
        rows = (
            (
                await session.execute(
                    select(LlmUserProfile)
                    .order_by(LlmUserProfile.updated_at.desc())
                    .limit(max(1, limit))
                )
            )
            .scalars()
            .all()
        )
        counts = dict(
            (
                await session.execute(
                    select(LlmUserFact.user_id, func.count(LlmUserFact.id)).group_by(
                        LlmUserFact.user_id
                    )
                )
            ).all()
        )
    items = []
    for row in rows:
        items.append(
            {
                "user_id": row.user_id,
                "impression": row.impression or "",
                "message_count": row.message_count or 0,
                "impression_at": (
                    fmt_dt(row.impression_at)
                    if row.impression_at
                    else ""
                ),
                "updated_at": (
                    fmt_dt(row.updated_at)
                    if row.updated_at
                    else ""
                ),
                "fact_count": int(counts.get(row.user_id, 0) or 0),
            }
        )
    return items


async def delete_fact(user_id: str | int, key: Optional[str] = None) -> int:
    """删除一条（key 为空 = 全部）关键信息，返回删除条数。"""
    from sqlalchemy import delete

    uid = str(user_id)
    async with get_session() as session:
        stmt = delete(LlmUserFact).where(LlmUserFact.user_id == uid)
        if key:
            stmt = stmt.where(LlmUserFact.fact_key == key)
        result = await session.execute(stmt)
        await session.commit()
    return int(result.rowcount or 0)


async def clear_profile(user_id: str | int) -> int:
    """清空某用户的全部长期记忆（印象 + 关键信息），返回清掉的记录数。"""
    from sqlalchemy import delete

    uid = str(user_id)
    async with get_session() as session:
        facts = await session.execute(delete(LlmUserFact).where(LlmUserFact.user_id == uid))
        profile = await session.execute(delete(LlmUserProfile).where(LlmUserProfile.user_id == uid))
        await session.commit()
    return int((facts.rowcount or 0) + (profile.rowcount or 0))


def _build_sender_anchor(name: str, user_id: str) -> str:
    """群聊身份锚：以 QQ 号唯一标识当前发言人（昵称可能重名/变更，不可作身份）。"""
    who = (name or "").strip() or "群成员"
    return (
        f"【当前和你说话的人】{who}（QQ：{user_id}）。"
        "QQ 号才是唯一身份标识：判断「谁在说话」「某句话/某条信息是谁说的」一律以 QQ 号为准；"
        "昵称仅供参考（可能重名或改过），不要因为昵称相同就把不同人的信息混在一起，"
        "也不要把别人的信息当成这个人的。"
    )


def _dedup_facts(facts: list[dict]) -> list[dict]:
    """同一 key 只保留一条：非 global 的 scope 优先（group/private 版覆盖 global 版）。"""
    ordered = sorted(facts, key=lambda f: 1 if f.get("scope_type") == FACT_SCOPE_GLOBAL else 0)
    seen: dict[str, dict] = {}
    for f in ordered:
        seen[f.get("key")] = f
    return list(seen.values())


def build_memory_block(
    profile: dict, *, max_facts: Optional[int] = None, owner: str = "", scope: str = "private"
) -> str:
    """把长期记忆拼成一段注入 system 的提示词（空白记忆返回空串）。

    `owner` 为空时用「ta」（私聊语义）；群聊应传「昵称（QQ 123）」这类明确归属，
    让模型知道这段记忆属于谁，避免多说话人场景乱认人。

    `scope` 决定这段记忆在哪个窗口用（跨窗口隔离）：
    - `"group"`   → **不渲染印象**（印象降级为私聊专属），并声明「别窗口的信息一律不知道」；
    - `"private"` → 渲染印象 + facts，并声明「别泄漏 ta 在群里说过的话」；
    - `"all"`     → 关闭隔离时的中性说明（不含隔离边界）。
    """
    if not profile:
        return ""
    who = (owner or "").strip() or "ta"
    lines: list[str] = []
    impression = (profile.get("impression") or "").strip()
    # 印象降级为私聊专属：群聊窗口不注入
    if impression and scope != "group":
        lines.append(f"【你对 {who} 的印象】{impression}")
    facts = _dedup_facts(list(profile.get("facts") or []))
    limit = settings.llm_max_facts if max_facts is None else max_facts
    if limit and limit > 0:
        facts = facts[:limit]
    facts = [f for f in facts if f.get("key")]
    if facts:
        lines.append(
            "【已知关键信息】\n" + "\n".join(f"- {f['key']}：{f.get('value', '')}" for f in facts)
        )
    if not lines:
        return ""
    if scope == "group":
        head = "【当前发言者在本群可见的信息（仅来自 ta 在本群的发言或通用画像）】"
        lead = (
            "严格边界：不要使用、也不要透露 ta 在私聊或其他群说过的话；"
            "只在别的窗口见过的信息一律当作不知道。"
        )
    elif scope == "private":
        head = "【长期记忆：这是你与对话对象过往积累的信息，仅供理解与个性化参考】"
        lead = (
            "以下信息来自与 ta 的私聊或通用画像。"
            "不要提及 ta 在群里说过的话，除非 ta 本轮自己先提到。"
        )
    else:
        head = "【长期记忆：这是你与对话对象过往积累的信息，仅供理解与个性化参考】"
        lead = f"以下是你与 {who} 过往对话中积累的长期记忆。"
    return (
        head + "\n"
        "使用规则：只在自然合适时使用；不要逐条复述；不要暴露你在「读取记忆」；"
        "如果它与当前对话的内容冲突，一律以当前对话为准。\n"
        + lead + "\n"
        "--- 记忆开始 ---\n"
        + "\n".join(lines)
        + "\n--- 记忆结束 ---"
    )


# ============================== 群记忆 ==============================


async def get_group_profile(group_id: str | int) -> dict:
    """取群记忆（共同话题/群梗/正在进行的事）。"""
    gid = str(group_id)
    async with get_session(readonly=True) as session:
        row = (
            await session.execute(
                select(LlmGroupProfile).where(LlmGroupProfile.group_id == gid).limit(1)
            )
        ).scalar_one_or_none()
    return {
        "group_id": gid,
        "memory": row.memory if row else "",
        "last_msg_id": row.last_msg_id if row else 0,
        "message_count": row.message_count if row else 0,
        "updated_at": fmt_dt(row.updated_at) if row else "",
    }


async def list_group_profiles(limit: int = 200) -> list[dict]:
    """列出所有群记忆（最近更新的排前面，WebUI 用）。"""
    async with get_session(readonly=True) as session:
        rows = (
            (
                await session.execute(
                    select(LlmGroupProfile)
                    .order_by(LlmGroupProfile.updated_at.desc())
                    .limit(max(1, limit))
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "group_id": r.group_id,
            "memory": r.memory or "",
            "last_msg_id": r.last_msg_id or 0,
            "message_count": r.message_count or 0,
            "updated_at": fmt_dt(r.updated_at),
        }
        for r in rows
    ]


def build_group_memory_block(profile: dict) -> str:
    """把群记忆拼成注入 system 的提示词（空记忆返回空串）。"""
    if not profile:
        return ""
    memory = (profile.get("memory") or "").strip()
    if not memory:
        return ""
    return (
        "【群共同记忆：这个群过往聊天积累的话题/群梗/约定，仅供理解氛围参考】\n"
        "使用规则：只在自然合适时使用；不要逐条复述；不要暴露你在「读取记忆」；"
        "如果它与当前对话的内容冲突，一律以当前对话为准。\n"
        "--- 群记忆开始 ---\n" + memory + "\n--- 群记忆结束 ---"
    )


async def set_group_memory(
    group_id: str | int,
    memory: str,
    *,
    last_msg_id: Optional[int] = None,
    message_count: Optional[int] = None,
) -> dict:
    """写入群记忆（供定时总结 / WebUI 人工维护）。"""
    gid = str(group_id)
    async with get_session() as session:
        row = (
            await session.execute(
                select(LlmGroupProfile).where(LlmGroupProfile.group_id == gid).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            row = LlmGroupProfile(group_id=gid)
            session.add(row)
        row.memory = memory or ""
        if last_msg_id is not None:
            row.last_msg_id = int(last_msg_id)
        if message_count is not None:
            row.message_count = int(message_count)
        await session.commit()
        await session.refresh(row)
    return await get_group_profile(gid)


# ============================== 定时总结（增量提炼） ==============================

_GROUP_SUMMARY_PROMPT = """你是一个群聊信息总结助手。下面是一个 QQ 群自上次总结以来的新消息，
以及此前已记录过的群共同记忆。请更新记忆。

只输出**严格 JSON**（不要代码块、不要解释），格式：
{{"memory": "200 字以内的群共同记忆（正在聊的话题、群梗、约定、重要事件）", "topics": ["话题1", "话题2"]}}

要求：
- 只写消息中能确认的信息，不要编造；
- memory 要连贯自然，用第三人称描述群的状态；
- topics 最多 5 个，没有就空数组。

【旧记忆】{old}
【近期群消息】
{convo}"""


async def _summary_cfg() -> ProviderConfig:
    """总结/压缩用的服务商配置（未单独配置时回落到默认）。"""
    name = (settings.llm_summary_provider or "").strip()
    model = (settings.llm_summary_model or "").strip()
    cfg = await resolve_provider(name or None)
    if model:
        cfg.model = model
    return cfg


async def _write_facts(uid: str, raw_facts, *, scope_type: str, scope_id: str) -> None:
    """写入一批自动提炼的事实（auto 不覆盖同 scope 的 manual）。"""
    if not isinstance(raw_facts, list):
        return
    existing = await get_profile(uid, scope_type=scope_type, scope_id=scope_id)
    manual_keys = {f.get("key") for f in (existing.get("facts") or []) if f.get("source") == LLM_FACT_MANUAL}
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        value = str(item.get("value") or "").strip()
        if not key or not value or key in manual_keys:
            continue
        await set_fact(uid, key, value, source=LLM_FACT_AUTO, scope_type=scope_type, scope_id=scope_id)


async def _summarize_private(uid: str, batch: list[dict], cfg, *, fact_scope: str = FACT_SCOPE_PRIVATE) -> None:
    """私聊批次：更新印象 + 写 fact_scope 作用域的事实。"""
    prof = await get_profile(uid, scope_type=None)  # 取全量旧值作上下文
    old = prof.get("impression") or ""
    facts_text = _facts_text(prof.get("facts") or [])
    prompt = _IMPRESSION_PROMPT.format(old=old or "（暂无）", old_facts=facts_text, convo=_convo_lines(batch))
    res = await client().complete(cfg, [{"role": "user", "content": prompt}], {"temperature": 0.2, "max_tokens": 700})
    data = _extract_json(res.text)
    impression = str(data.get("impression") or "").strip() or old
    await set_impression(uid, impression, count=len(batch))
    await _write_facts(uid, data.get("facts"), scope_type=fact_scope, scope_id="")


async def _summarize_group_facts(uid: str, gid: str, batch: list[dict], cfg) -> None:
    """群批次：只写该群的 group 作用域事实，**不写印象**。"""
    prof = await get_profile(uid, scope_type=FACT_SCOPE_GROUP, scope_id=gid)
    prompt = _GROUP_USER_FACT_PROMPT.format(
        old_facts=_facts_text(prof.get("facts") or []), convo=_convo_lines(batch)
    )
    res = await client().complete(cfg, [{"role": "user", "content": prompt}], {"temperature": 0.2, "max_tokens": 700})
    data = _extract_json(res.text)
    await _write_facts(uid, data.get("facts"), scope_type=FACT_SCOPE_GROUP, scope_id=gid)


async def _advance_user_watermark(uid: str, msg_id: int) -> None:
    """推进个人记忆水位线（本轮最大 chat_message.id）。"""
    async with get_session() as session:
        row = (
            await session.execute(
                select(LlmUserProfile).where(LlmUserProfile.user_id == uid).limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            row.last_msg_id = int(msg_id)
            await session.commit()


async def summarize_user(user_id: str | int) -> dict:
    """从聊天流水增量提炼某用户的个人记忆（按来源 scope 分离）。

    素材来自 `chat_message`（该用户所有发言），水位线 `llm_user_profile.last_msg_id`。
    开启跨窗口隔离时，消息按来源拆成「私聊 / 各群」批次分别提炼：私聊批次更新印象 +
    private 事实，群批次只写该群的 group 事实（不写印象）。失败只记日志，不推进水位线。
    """
    from src.core import chatlog

    uid = str(user_id)
    profile_row = await get_profile(uid)
    watermark = int(profile_row.get("last_msg_id") or 0)
    try:
        cfg = await _summary_cfg()
        msgs = await chatlog.messages_since(
            user_id=uid, watermark=watermark, limit=settings.llm_summary_max_msgs
        )
        msgs = [m for m in msgs if not m.get("is_bot")]
        if not msgs:
            return {"ok": True, "reason": "没有增量", "user_id": uid}

        if settings.llm_memory_scope_isolation:
            for (stype, sid), batch in _group_by_scope(msgs).items():
                if len(batch) < 3:      # 每个 scope 至少 3 条才提炼，控制调用量
                    continue
                if stype == FACT_SCOPE_PRIVATE:
                    await _summarize_private(uid, batch, cfg)
                else:
                    await _summarize_group_facts(uid, sid, batch, cfg)
        else:
            # 关闭隔离：退回旧行为（印象 + 全量通用事实）
            await _summarize_private(uid, msgs, cfg, fact_scope=FACT_SCOPE_GLOBAL)

        # 推进水位线（本轮最大 id；不足 3 条的小批次视为已消费）
        await _advance_user_watermark(uid, int(msgs[-1]["id"]))
        logger.info(f"已提炼用户 {uid} 的个人记忆（增量 {len(msgs)} 条）")
        return {"ok": True, "user_id": uid, "count": len(msgs), "model": None}
    except Exception as exc:  # noqa: BLE001 - 提炼失败不能影响聊天
        logger.warning(f"提炼用户 {uid} 的个人记忆失败：{exc}")
        return {"ok": False, "error": str(exc)}


async def summarize_group(group_id: str | int) -> dict:
    """从聊天流水里提炼某群的共同记忆（增量）。

    素材来自 `chat_message`（该群所有消息），水位线 `llm_group_profile.last_msg_id`。
    """
    from src.core import chatlog

    gid = str(group_id)
    profile = await get_group_profile(gid)
    watermark = int(profile.get("last_msg_id") or 0)
    try:
        cfg = await _summary_cfg()
        msgs = await chatlog.messages_since(
            group_id=gid, watermark=watermark, limit=settings.llm_summary_max_msgs
        )
        if not msgs:
            return {"ok": True, "reason": "没有增量", "group_id": gid}
        convo = "\n".join(
            f"{m.get('nickname') or m.get('user_id')}：{(m.get('content') or '')[:300]}"
            for m in msgs
        )
        prompt = _GROUP_SUMMARY_PROMPT.format(
            old=profile.get("memory") or "（暂无）", convo=convo
        )
        res = await client().complete(
            cfg,
            [{"role": "user", "content": prompt}],
            {"temperature": 0.2, "max_tokens": 700},
        )
        data = _extract_json(res.text)
        memory = str(data.get("memory") or "").strip() or (profile.get("memory") or "")
        await set_group_memory(
            gid,
            memory,
            last_msg_id=int(msgs[-1]["id"]),
            message_count=len(msgs),
        )
        logger.info(f"已提炼群 {gid} 的共同记忆（增量 {len(msgs)} 条）")
        return {"ok": True, "group_id": gid, "count": len(msgs), "model": res.model}
    except Exception as exc:  # noqa: BLE001 - 提炼失败不能影响聊天
        logger.warning(f"提炼群 {gid} 的共同记忆失败：{exc}")
        return {"ok": False, "error": str(exc)}


# ============================== 上下文压缩 ==============================

_COMPRESS_PROMPT = """你是一个对话摘要助手。把下面这段对话压缩成一段连贯的摘要，
保留关键信息（事实、决定、话题、语气），丢弃寒暄与重复。

只输出摘要正文，不要标题、不要代码块、不要解释。

【对话】
{convo}"""


async def compress_session(session_id: int) -> dict:
    """把某会话「超出保留窗口的旧消息」压进 `llm_session.summary`。

    保留最近 `LLM_COMPRESS_THRESHOLD` 条原文，更早的交给模型压成摘要
    并合并到已有 summary；返回 {"ok", "compressed", "kept"}。
    """
    try:
        cfg = await _summary_cfg()
        async with get_session() as session:
            sess = (
                await session.execute(
                    select(LlmSession).where(LlmSession.id == session_id).limit(1)
                )
            ).scalar_one_or_none()
            if sess is None:
                return {"ok": False, "error": "会话不存在"}
            # 实际消息数（不依赖可能不准的 message_count）
            total = int(
                (
                    await session.execute(
                        select(func.count(LlmMessage.id)).where(
                            LlmMessage.session_id == session_id
                        )
                    )
                ).scalar_one()
                or 0
            )
            threshold = int(settings.llm_compress_threshold or 0)
            if threshold <= 0 or total <= threshold:
                return {"ok": True, "compressed": 0, "kept": total}

            # 取最旧的（需要压缩的部分）
            old_rows = (
                (
                    await session.execute(
                        select(LlmMessage)
                        .where(LlmMessage.session_id == session_id)
                        .order_by(LlmMessage.id.asc())
                        .limit(max(0, total - threshold))
                    )
                )
                .scalars()
                .all()
            )
            if not old_rows:
                return {"ok": True, "compressed": 0, "kept": total}
            convo = "\n".join(
                f"{m.role}：{(m.content or '')[:400]}" for m in old_rows
            )
            prompt = _COMPRESS_PROMPT.format(convo=convo)
            res = await client().complete(
                cfg,
                [{"role": "user", "content": prompt}],
                {"temperature": 0.2, "max_tokens": 600},
            )
            digest = (res.text or "").strip()
            if not digest:
                return {"ok": False, "error": "压缩返回为空"}

            old_summary = sess.summary or ""
            sess.summary = f"{old_summary}\n{digest}".strip() if old_summary else digest
            # 删除已压缩的原文，保留最近 threshold 条
            from sqlalchemy import delete

            await session.execute(
                delete(LlmMessage).where(LlmMessage.id.in_([m.id for m in old_rows]))
            )
            sess.message_count = max(0, total - len(old_rows))
            await session.commit()
        logger.info(f"会话 {session_id} 已压缩 {len(old_rows)} 条旧消息")
        return {"ok": True, "compressed": len(old_rows), "kept": total - len(old_rows)}
    except Exception as exc:  # noqa: BLE001 - 压缩失败不能影响聊天
        logger.warning(f"压缩会话 {session_id} 失败：{exc}")
        return {"ok": False, "error": str(exc)}


# ============================== 核心调用 ==============================


async def llm_available() -> bool:
    """当前是否可用（开关打开 + 有 api_key 或 base_url）。"""
    if not settings.llm_enable:
        return False
    try:
        cfg = await resolve_provider(None)
    except LLMError:
        return False
    return bool(cfg.api_key or cfg.base_url)


async def _build_request(
    messages: Sequence[dict],
    *,
    preset: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    extra: Optional[dict] = None,
    scope: Optional[str] = None,
    scope_id: Optional[str | int] = None,
    memory: Optional[bool] = None,
    plugin: Optional[str] = None,
) -> dict:
    """解析服务商 / 人设 / 记忆，拼出这次请求（不落库）。"""
    if not settings.llm_enable:
        raise LLMError("LLM 已全局禁用（LLM_ENABLE=false）")
    scope_type = normalize_scope(scope)
    scope_key = "" if scope_id is None else str(scope_id)
    if scope_type and not scope_key:
        raise LLMError("指定了作用域就必须给 scope_id（用户 QQ / 群号）")
    cfg = await resolve_provider(provider)
    conv = normalize_messages(messages)

    preset_name = (preset or settings.llm_default_preset or "").strip()
    preset_data = None
    if preset_name:
        preset_data = await get_preset(preset_name)
        if preset_data is None:
            if preset_name != (settings.llm_default_preset or "").strip():
                raise LLMError(f"人设不存在或已禁用：{preset_name}")
            preset_name = ""
        else:
            for key, value in (preset_data.get("params") or {}).items():
                if key == "temperature" and temperature is None:
                    temperature = value
                elif key == "max_tokens" and max_tokens is None:
                    max_tokens = value
                elif key not in ("temperature", "max_tokens"):
                    extra = {key: value, **(extra or {})}

    sys_parts: list[str] = []
    if preset_data:
        sys_parts.append(preset_data["system_prompt"])

    # 长期记忆注入
    use_memory = settings.llm_memory_enable if memory is None else bool(memory)
    if scope_type == LLM_SCOPE_USER and scope_key and use_memory:
        # 个人对话：注入个人记忆（印象 + 关键信息）
        if settings.llm_memory_scope_isolation:
            profile = await get_profile(scope_key, scope_type=FACT_SCOPE_PRIVATE)
            block = build_memory_block(profile, scope="private")
        else:
            block = build_memory_block(await get_profile(scope_key), scope="all")
        if block:
            sys_parts.append(block)
        # 会话压缩摘要：超阈值后旧原文被物理删除，摘要写进了 llm_session.summary，
        # 这里必须补注入，否则私聊历史会静默丢失。
        info = await find_session(LLM_SCOPE_USER, scope_key, plugin or "")
        if info and (info.get("summary") or "").strip():
            sys_parts.append(f"【更早的对话摘要】\n{info['summary']}")
    elif scope_type == LLM_SCOPE_GROUP and scope_key:
        # 群聊：先钉死「当前发言人是谁」（QQ 号为准），再注入群记忆 + 该发言者个人档案
        sender_id = str((extra or {}).get("_sender_id") or "").strip() if extra else ""
        sender_name = str((extra or {}).get("_sender_name") or "").strip() if extra else ""
        if sender_id:
            # 身份锚不依赖记忆开关：认人是群聊的基础能力
            sys_parts.append(_build_sender_anchor(sender_name, sender_id))
        if use_memory:
            group_block = build_group_memory_block(await get_group_profile(scope_key))
            if group_block:
                sys_parts.append(group_block)
            if sender_id:
                owner = f"{sender_name}（QQ {sender_id}）" if sender_name else f"QQ {sender_id}"
                if settings.llm_memory_scope_isolation:
                    # 只取该发言者「通用 + 本群」可见的信息，绝不带出私聊/别群内容
                    sender_profile = await get_profile(
                        sender_id, scope_type=FACT_SCOPE_GROUP, scope_id=scope_key
                    )
                    sender_block = build_memory_block(
                        sender_profile, owner=owner, scope="group"
                    )
                else:
                    sender_block = build_memory_block(
                        await get_profile(sender_id), owner=owner, scope="all"
                    )
                if sender_block:
                    # build_memory_block(scope="group") 自带标题与边界声明，直接追加即可
                    sys_parts.append(sender_block)
            # 会话压缩摘要（若之前压缩过，补充进 system）
            info = await find_session(LLM_SCOPE_GROUP, scope_key, plugin or "")
            if info and (info.get("summary") or "").strip():
                sys_parts.append(f"【更早的对话摘要】\n{info['summary']}")

    convo: list[dict] = []
    for msg in conv:
        if msg["role"] == "system":
            text = (msg.get("content") or "").strip()
            if text:
                sys_parts.append(text)
        else:
            convo.append(msg)
    if not convo:
        raise LLMError("除 system 外至少要有一条消息")

    params = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # 内部键（`_` 开头）只用于框架传递信息（如发言者 id），不透传给模型
        "extra": {k: v for k, v in (extra or {}).items() if not str(k).startswith("_")},
    }
    return {
        "cfg": cfg,
        "system": "\n\n".join(p for p in sys_parts if p and p.strip()),
        "convo": convo,
        "params": {k: v for k, v in params.items() if v is not None},
        "preset": preset_name,
        "scope": scope_type,
        "scope_id": scope_key,
        "plugin": plugin or "",
    }


def _assemble(req: dict, history: Sequence[dict]) -> list[dict]:
    """system + 历史 + 本轮，拼成最终发给模型的消息列表。"""
    messages: list[dict] = []
    if req["system"]:
        messages.append({"role": "system", "content": req["system"]})
    messages.extend(history)
    messages.extend(req["convo"])
    return messages


async def _touch_session(session_id: int, *, preset_name: str = "", provider_name: str = "") -> None:
    """把本轮用的预设 / 服务商记到会话上（便于 WebUI 展示）。"""
    if not session_id or not (preset_name or provider_name):
        return
    async with get_session() as session:
        row = (
            await session.execute(select(LlmSession).where(LlmSession.id == session_id).limit(1))
        ).scalar_one_or_none()
        if row is None:
            return
        if preset_name:
            row.preset_name = preset_name
        if provider_name:
            row.provider_name = provider_name
        await session.commit()


async def _persist_turn(req: dict, *, session_id: Optional[int], reply: str) -> None:
    """落库本轮对话（记忆提炼改由 scheduler 定时任务负责，不在这里同步触发）。"""
    if not session_id:
        return
    to_write = list(req["convo"])
    if reply:
        to_write.append({"role": "assistant", "content": reply})
    await record_messages(session_id, to_write)
    await _touch_session(
        session_id, preset_name=req["preset"], provider_name=req["cfg"].name or ""
    )


async def _prepare_context(req: dict) -> tuple[Optional[int], list[dict]]:
    """按需建会话并取出历史（无作用域 → 无历史）。

    群聊（group scope）不走 llm_session：群上下文由 chatlog（chat_message）
    在插件层注入，避免一份历史存两处。这里只对 user scope 建会话。
    """
    if not req["scope"] or req["scope"] == LLM_SCOPE_GROUP:
        return None, []
    info = await ensure_session(
        req["scope"], req["scope_id"], req["plugin"], preset_name=req["preset"]
    )
    history = await load_history(info["id"], rounds=settings.llm_context_rounds)
    return info["id"], history


async def chat(
    messages: Sequence[dict],
    *,
    preset: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    extra: Optional[dict] = None,
    scope: Optional[str] = None,
    scope_id: Optional[str | int] = None,
    memory: Optional[bool] = None,
    plugin: Optional[str] = None,
    use_stream: Optional[bool] = None,
) -> LLMResult:
    """一次性调用（即使全局开了流式，也会在内部聚合好再返回）。

    - `scope="user"` + `scope_id=QQ` → 带「个人上下文 + 长期记忆」；
    - `scope="group"` + `scope_id=群号` → 带「群聊上下文」（不注入个人记忆）；
    - 不传 scope → 无状态单次问答（不落库）。
    """
    req = await _build_request(
        messages,
        preset=preset,
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        extra=extra,
        scope=scope,
        scope_id=scope_id,
        memory=memory,
        plugin=plugin,
    )
    session_id, history = await _prepare_context(req)
    payload = _assemble(req, history)
    stream_on = settings.llm_stream_enable if use_stream is None else bool(use_stream)
    candidates = _order_models(req["cfg"], req["params"].get("model"))
    result, used_model = await _try_models(
        req["cfg"], payload, req["params"], candidates, stream=stream_on
    )
    result.model = result.model or used_model
    result.preset = req["preset"]
    result.provider = req["cfg"].name or "env"
    await _persist_turn(req, session_id=session_id, reply=result.text)
    return result


async def ask(
    text: str,
    *,
    preset: Optional[str] = None,
    system: Optional[str] = None,
    **kwargs,
) -> str:
    """最简用法：给一句话，拿一句话回来。额外参数原样透传给 `chat()`。"""
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": str(text or "")})
    result = await chat(messages, preset=preset, **kwargs)
    return result.text


async def stream(
    source: str | Sequence[dict],
    *,
    preset: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    extra: Optional[dict] = None,
    scope: Optional[str] = None,
    scope_id: Optional[str | int] = None,
    memory: Optional[bool] = None,
    plugin: Optional[str] = None,
    use_stream: Optional[bool] = None,
) -> AsyncIterator[LLMChunk]:
    """流式调用：`async for chunk in llm.stream("写首诗")`。

    全局 `LLM_STREAM_ENABLE=false`（或 `use_stream=False`）时**退化成一次返回**，
    插件代码不用改：只在 `done=True` 的那个 chunk 里拿到完整文本。
    """
    raw = [{"role": "user", "content": str(source)}] if isinstance(source, str) else list(source)
    req = await _build_request(
        raw,
        preset=preset,
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        extra=extra,
        scope=scope,
        scope_id=scope_id,
        memory=memory,
        plugin=plugin,
    )
    session_id, history = await _prepare_context(req)
    payload = _assemble(req, history)
    effective = settings.llm_stream_enable if use_stream is None else bool(use_stream)
    candidates = _order_models(req["cfg"], req["params"].get("model"))
    if not effective:
        result, used_model = await _try_models(
            req["cfg"], payload, req["params"], candidates, stream=False
        )
        result.model = result.model or used_model
        result.preset = req["preset"]
        result.provider = req["cfg"].name or "env"
        await _persist_turn(req, session_id=session_id, reply=result.text)
        yield LLMChunk(delta=result.text, text=result.text, done=True, result=result)
        return
    # 逐片流式：每个模型都真实流式输出；失败切下一个模型重试
    last_exc: Optional[LLMError] = None
    errors: list[str] = []
    for idx, model in enumerate(candidates):
        attempt_params = dict(req["params"])
        attempt_params["model"] = model
        try:
            pieces: list[str] = []
            result: Optional[LLMResult] = None
            async for chunk in client().stream(req["cfg"], payload, attempt_params):
                if not chunk.done:
                    pieces.append(chunk.delta)
                    yield chunk
                    continue
                result = chunk.result
                if result is not None:
                    result.text = "".join(pieces) or result.text
                    result.model = result.model or model
                    result.preset = req["preset"]
                    result.provider = req["cfg"].name or "env"
                yield LLMChunk(delta="", text="".join(pieces), done=True, result=result)
                if result is not None:
                    await _persist_turn(req, session_id=session_id, reply=result.text)
                return
            # 正常情况不会走到这里（客户端总会发 done 片）
            if result is None:
                raise LLMError("流式返回为空")
        except LLMError as exc:
            last_exc = exc
            errors.append(f"{model}: {exc}")
            logger.warning(f"模型 {model} 流式调用失败，尝试下一个（{idx + 1}/{len(candidates)}）：{exc}")
    raise LLMError("全部模型都调用失败：" + "；".join(errors)) from last_exc


# ============================== 插件门面 ==============================


class PluginLLM:
    """绑定到某个插件的 LLM 门面（`feature.llm`），省掉每次传 `plugin=`。"""

    def __init__(self, plugin_name: str) -> None:
        self.plugin_name = plugin_name

    # --- 调用 ---
    async def chat(self, messages: Sequence[dict], **kwargs) -> LLMResult:
        kwargs.setdefault("plugin", self.plugin_name)
        return await chat(messages, **kwargs)

    async def ask(self, text: str, **kwargs) -> str:
        kwargs.setdefault("plugin", self.plugin_name)
        return await ask(text, **kwargs)

    async def complete(self, text: str, **kwargs) -> str:
        """`ask` 的别名（想让代码读起来像「补全」时用）。"""
        return await self.ask(text, **kwargs)

    def stream(self, source: str | Sequence[dict], **kwargs) -> AsyncIterator[LLMChunk]:
        kwargs.setdefault("plugin", self.plugin_name)
        return stream(source, **kwargs)

    # --- 长期记忆 ---
    async def remember(self, user_id: str | int, key: str, value: str) -> dict:
        """人工记一条用户关键信息（不会被自动提炼覆盖）。"""
        return await set_fact(user_id, key, value, source=LLM_FACT_MANUAL)

    async def forget(self, user_id: str | int, key: Optional[str] = None) -> int:
        return await delete_fact(user_id, key)

    async def profile(self, user_id: str | int) -> dict:
        return await get_profile(user_id)

    async def set_impression(self, user_id: str | int, text: str) -> dict:
        return await set_impression(user_id, text)

    async def clear_memory(self, user_id: str | int) -> int:
        return await clear_profile(user_id)

    # --- 上下文 ---
    async def context(self, scope: str, scope_id: str | int) -> Optional[dict]:
        return await find_session(scope, scope_id, self.plugin_name)

    async def use_preset(
        self, scope: str, scope_id: str | int, preset_name: Optional[str] = None
    ) -> dict:
        """给某会话换人设（`None`/空串 = 清空回落默认）。"""
        return await set_session_preset(
            scope, scope_id, self.plugin_name, preset_name=preset_name
        )

    async def use_provider(
        self, scope: str, scope_id: str | int, provider_name: Optional[str] = None
    ) -> dict:
        """给某会话换服务商（`None`/空串 = 清空回落默认）。"""
        return await set_session_preset(
            scope, scope_id, self.plugin_name, provider_name=provider_name
        )

    async def clear_context(self, scope: str, scope_id: str | int) -> int:
        return await clear_sessions(
            scope_type=normalize_scope(scope) or LLM_SCOPE_USER,
            scope_id=str(scope_id),
            plugin_name=self.plugin_name,
        )

    # --- 群记忆 ---
    async def group_profile(self, group_id: str | int) -> dict:
        return await get_group_profile(group_id)

    async def set_group_memory(self, group_id: str | int, memory: str) -> dict:
        return await set_group_memory(group_id, memory)

    # --- 定时总结 / 压缩（一般由 scheduler 调用，插件也可手动触发） ---
    async def summarize_user(self, user_id: str | int) -> dict:
        return await summarize_user(user_id)

    async def summarize_group(self, group_id: str | int) -> dict:
        return await summarize_group(group_id)

    async def compress_session(self, session_id: int) -> dict:
        return await compress_session(session_id)

    # --- 元信息 ---
    async def available(self) -> bool:
        return await llm_available()

    async def providers(self) -> list[dict]:
        return await list_providers()

    async def presets(self) -> list[dict]:
        return await list_presets()

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<PluginLLM {self.plugin_name}>"


_plugin_llm_cache: dict[str, PluginLLM] = {}


def get_plugin_llm(plugin_name: str) -> PluginLLM:
    """取（缓存的）插件 LLM 门面。"""
    name = plugin_name or ""
    facade = _plugin_llm_cache.get(name)
    if facade is None:
        facade = PluginLLM(name)
        _plugin_llm_cache[name] = facade
    return facade
