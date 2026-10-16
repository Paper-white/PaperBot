"""LLM 管理接口：`/api/llm/*`。

职责（与 `src/core/llm.py` 一一对应）：
1. **服务商**：增删改查 + 「测试连接」（真实发一条最短请求）；
2. **人设**：列表 / 详情 / 新建编辑 / 删除 / 导入（文本或上传文件）/ 导出（`.md` 文本）；
3. **会话**：列出上下文、看某会话消息、换人设 / 服务商、清空；
4. **用户记忆**：印象 + 关键信息的查看与人工维护；
5. **试聊**：直接在 WebUI 里发一句话（无上下文），用来验证配置通不通。

约定：
- 密钥只回显掩码（`********`），提交掩码或空字符串 = 不改动；
- 所有 `LLMError` 都转成 400 + 可读中文；参数非法 422（pydantic）。
"""

from __future__ import annotations

import time
from typing import Optional

import nonebot
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from src.config import settings
from src.core import llm
from src.core.llm import LLMError, llm_available
from webui.deps import SECRET_PLACEHOLDER, group_name_map, require_token, user_nickname_map

logger = nonebot.logger

router = APIRouter(dependencies=[Depends(require_token)], tags=["llm"])

SCOPE_LABELS = {
    "user": "个人",
    "group": "群聊",
}

PRESET_SOURCE_LABELS = {
    "builtin": "内置",
    "import": "导入",
    "manual": "人工",
}

FACT_SOURCE_LABELS = {
    "auto": "自动提炼",
    "manual": "人工记录",
}


# --------------------------------------------------------------------------- #
# 入参模型
# --------------------------------------------------------------------------- #
class ProviderPayload(BaseModel):
    """新建 / 更新服务商（`None` = 不改动）。"""

    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    # 多模型优先级列表（优先 + 备用，最多 MAX_MODELS 个）
    models: Optional[list[str]] = None
    priority: Optional[int] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: Optional[float] = None
    extra: Optional[str] = Field(None, description="额外请求参数（JSON 文本）")
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None


class PresetPayload(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    params: Optional[str] = Field(None, description="额外参数（JSON 文本，如 temperature）")
    enabled: Optional[bool] = None


class ImportPayload(BaseModel):
    text: str = Field(..., description="人设文件内容（支持 YAML front-matter）")
    filename: str = ""
    force: bool = False


class SessionPayload(BaseModel):
    preset_name: Optional[str] = None
    provider_name: Optional[str] = None


class FactPayload(BaseModel):
    key: str
    value: str


class ImpressionPayload(BaseModel):
    impression: str = ""


class ChatPayload(BaseModel):
    prompt: str
    preset: str = ""
    provider: str = ""
    system: str = ""


class FetchModelsPayload(BaseModel):
    base_url: str = ""
    api_key: str = ""


class ProviderModelsPayload(BaseModel):
    models: list[str]
    priority: int = 0


def _masked_key(value: Optional[str]) -> Optional[str]:
    """密钥的「不改动」写法还原成 None（掩码 / 占位符）；空串 = 明确清空；其余原样。"""
    if value is None:
        return None
    text = value.strip()
    if text in (llm.SECRET_MASK, SECRET_PLACEHOLDER):
        return None
    return text


# --------------------------------------------------------------------------- #
# 总览
# --------------------------------------------------------------------------- #
@router.get("/llm/status")
async def llm_status() -> dict:
    """LLM 总览：可用性 / 开关 / 默认服务商 / 各种计数。"""
    # 延迟 import：`webui.routes.config` 会 import `src.config`，放函数内可避免模块级循环导入
    from webui.routes.config import llm_sections

    providers = await llm.list_providers()
    active = next((p for p in providers if p.get("is_default") and p.get("enabled")), None)
    if active is None:
        active = next((p for p in providers if p.get("enabled")), None)
    presets = await llm.list_presets()
    sessions = await llm.list_sessions(limit=1000)
    profiles = await llm.list_profiles(limit=1000)
    group_profiles = await llm.list_group_profiles(limit=1000) if hasattr(llm, "list_group_profiles") else []
    return {
        "enable": settings.llm_enable,
        "available": await llm_available(),
        "stream_enable": settings.llm_stream_enable,
        "memory_enable": settings.llm_memory_enable,
        "context_rounds": settings.llm_context_rounds,
        "summary_enable": settings.llm_summary_enable,
        "summary_interval": settings.llm_summary_interval,
        "max_facts": settings.llm_max_facts,
        "default_preset": settings.llm_default_preset,
        "env_base_url": settings.llm_base_url,
        "env_model": settings.llm_model,
        "env_has_key": bool(settings.llm_api_key),
        "active_provider": active,
        "provider_count": len(providers),
        "preset_count": len(presets),
        "session_count": len(sessions),
        "profile_count": len(profiles),
        "group_count": len(group_profiles),
        "session_messages": sum(int(s.get("message_count") or 0) for s in sessions),
        "scope_labels": SCOPE_LABELS,
        "secret_placeholder": llm.SECRET_MASK,
        # 「LLM」页专用的 .env 兜底 / 定时总结配置分组（字段结构与 /api/config 一致，
        # 前端直接喂给 configBlock / openConfigModal）
        "env_sections": llm_sections(),
    }


# --------------------------------------------------------------------------- #
# 服务商
# --------------------------------------------------------------------------- #
@router.get("/llm/providers")
async def list_providers() -> dict:
    items = await llm.list_providers()
    return {
        "items": items,
        "count": len(items),
        "secret_placeholder": llm.SECRET_MASK,
        "db_count": sum(1 for i in items if not i.get("virtual")),
    }


@router.put("/llm/providers/{name}")
async def upsert_provider(name: str, payload: ProviderPayload) -> dict:
    """新建或更新服务商（`is_default=true` 会把它设为默认，别的自动取消）。"""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="服务商名不能为空")
    if name == llm.ENV_PROVIDER_NAME:
        raise HTTPException(
            status_code=400,
            detail=f"`{llm.ENV_PROVIDER_NAME}` 是 .env 兜底的虚拟名，不能覆盖；换个名字吧",
        )
    try:
        row = await llm.set_provider(
            name,
            base_url=payload.base_url,
            api_key=_masked_key(payload.api_key),
            model=payload.model,
            models=payload.models,
            priority=payload.priority,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            timeout=payload.timeout,
            extra=payload.extra,
            enabled=payload.enabled,
            is_default=payload.is_default,
            updated_by="webui",
        )
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(f"WebUI：服务商 {name} 已保存（默认={row.get('is_default')}）")
    return {"ok": True, "item": row, "message": f"服务商「{name}」已保存"}


@router.delete("/llm/providers/{name}")
async def delete_provider(name: str) -> dict:
    name = name.strip()
    if name == llm.ENV_PROVIDER_NAME:
        raise HTTPException(status_code=400, detail="`.env` 兜底配置不能删除，改 .env 即可")
    deleted = await llm.delete_provider(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"服务商不存在：{name}")
    logger.info(f"WebUI：服务商 {name} 已删除")
    return {"ok": True, "message": f"服务商「{name}」已删除（若没有别的服务商，会回落到 .env）"}


@router.post("/llm/providers/{name}/test")
async def test_provider(name: str) -> dict:
    """真实发一条最短请求验证连通（会消耗一点点 token）。"""
    started = time.perf_counter()
    try:
        result = await llm.test_provider(name.strip() or None)
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(f"WebUI：测试服务商 {name or '默认'} 成功（{elapsed}ms）")
    return {
        "ok": True,
        "text": result.text,
        "model": result.model,
        "provider": result.provider,
        "elapsed_ms": elapsed,
        "usage": result.usage,
        "message": f"连接正常（{elapsed}ms）",
    }


@router.post("/llm/models/fetch")
async def fetch_models(payload: FetchModelsPayload) -> dict:
    """拉取中转站可用模型列表（base_url / api_key 留空 = 用当前默认服务商的）。"""
    started = time.perf_counter()
    try:
        models = await llm.fetch_models(payload.base_url.strip(), payload.api_key.strip())
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(f"WebUI：拉取模型列表成功（{len(models)} 个，{elapsed}ms）")
    return {
        "ok": True,
        "models": models,
        "count": len(models),
        "max_models": llm.MAX_MODELS,
        "elapsed_ms": elapsed,
        "message": f"拉到 {len(models)} 个模型",
    }


@router.post("/llm/providers/{name}/models")
async def update_provider_models(name: str, payload: ProviderModelsPayload) -> dict:
    """单独更新某服务商的多模型优先级（优先 + 备用，最多 3 个）。"""
    try:
        row = await llm.set_provider(
            name,
            models=payload.models,
            priority=payload.priority,
            updated_by="webui",
        )
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(f"WebUI：服务商 {name} 的多模型已更新（{payload.models}）")
    return {"ok": True, "item": row, "message": f"服务商「{name}」的模型优先级已保存"}


# --------------------------------------------------------------------------- #
# 人设
# --------------------------------------------------------------------------- #
@router.get("/llm/presets")
async def list_presets(with_prompt: bool = False) -> dict:
    items = await llm.list_presets()
    if with_prompt:
        for item in items:
            detail = await llm.get_preset(str(item.get("name") or ""))
            if detail:
                item["system_prompt"] = detail.get("system_prompt", "")
    for item in items:
        item["source_label"] = PRESET_SOURCE_LABELS.get(item.get("source", ""), item.get("source", ""))
    return {
        "items": items,
        "count": len(items),
        "default_preset": settings.llm_default_preset,
        "source_labels": PRESET_SOURCE_LABELS,
    }


@router.get("/llm/presets/{name}")
async def get_preset(name: str) -> dict:
    data = await llm.get_preset(name)
    if data is None:
        raise HTTPException(status_code=404, detail=f"人设不存在：{name}")
    data["source_label"] = PRESET_SOURCE_LABELS.get(data.get("source", ""), data.get("source", ""))
    data["text"] = llm.preset_to_text(data)
    return {"ok": True, "item": data}


@router.put("/llm/presets/{name}")
async def upsert_preset(name: str, payload: PresetPayload) -> dict:
    """新建 / 更新人设（WebUI 里改过的会标成「人工」，不会被文件导入覆盖）。"""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="人设名不能为空")
    try:
        data = await llm.set_preset(
            name,
            display_name=payload.display_name,
            description=payload.description,
            system_prompt=payload.system_prompt,
            params=payload.params,
            enabled=payload.enabled,
            source=llm.LLM_PRESET_MANUAL,
            updated_by="webui",
        )
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(f"WebUI：人设 {name} 已保存")
    return {"ok": True, "item": data, "message": f"人设「{name}」已保存"}


@router.delete("/llm/presets/{name}")
async def delete_preset(name: str) -> dict:
    deleted = await llm.delete_preset(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"人设不存在：{name}")
    logger.info(f"WebUI：人设 {name} 已删除")
    return {"ok": True, "message": f"人设「{name}」已删除"}


@router.post("/llm/presets/import")
async def import_preset(payload: ImportPayload) -> dict:
    """导入人设文本（前端读取上传文件内容后提交）。"""
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="人设内容为空")
    try:
        result = await llm.import_preset_text(
            payload.text,
            filename=payload.filename,
            force=payload.force,
            source=llm.LLM_PRESET_IMPORT,
            updated_by="webui",
        )
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    action = result.get("action")
    message = {
        "created": f"人设「{result.get('name')}」已导入",
        "updated": f"人设「{result.get('name')}」已更新",
        "skipped": f"已跳过：{result.get('reason') or '同名已存在'}",
    }.get(str(action), f"导入结果：{action}")
    logger.info(f"WebUI：导入人设 {result.get('name')}（{action}）")
    return {"ok": action != "skipped", "item": result, "action": action, "message": message}


@router.get("/llm/presets/{name}/export", response_class=PlainTextResponse)
async def export_preset(name: str) -> PlainTextResponse:
    """导出人设为 `.md` 文本（带 YAML front-matter，可直接再导入）。"""
    try:
        text = await llm.export_preset(name)
    except LLMError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.md"'},
    )


# --------------------------------------------------------------------------- #
# 会话（上下文）
# --------------------------------------------------------------------------- #
@router.get("/llm/sessions")
async def list_sessions(limit: int = 100) -> dict:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit 需在 1~500 之间")
    items = await llm.list_sessions(limit=limit)
    # 群作用域用群名、个人作用域用最近昵称，便于区分（离线时名字为空，只显示 ID）
    gmap = await group_name_map()
    umap = await user_nickname_map(
        [it["scope_id"] for it in items if it.get("scope_type") != "group"]
    )
    for item in items:
        item["scope_label"] = SCOPE_LABELS.get(item.get("scope_type", ""), item.get("scope_type", ""))
        item["scope_name"] = (
            gmap.get(item.get("scope_id", ""), "")
            if item.get("scope_type") == "group"
            else umap.get(item.get("scope_id", ""), "")
        )
    return {"items": items, "count": len(items), "scope_labels": SCOPE_LABELS}


@router.get("/llm/sessions/{session_id}/messages")
async def session_messages(session_id: int, limit: int = 100) -> dict:
    info = await llm.get_session_by_id(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit 需在 1~500 之间")
    messages = await llm.session_messages(session_id, limit=limit)
    info["scope_label"] = SCOPE_LABELS.get(info.get("scope_type", ""), info.get("scope_type", ""))
    return {"session": info, "items": messages, "count": len(messages)}


@router.put("/llm/sessions/{session_id}")
async def update_session(session_id: int, payload: SessionPayload) -> dict:
    """给某个会话换人设 / 服务商（空串 = 清空回落默认）。"""
    try:
        data = await llm.set_session_preset_by_id(
            session_id, preset_name=payload.preset_name, provider_name=payload.provider_name
        )
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(f"WebUI：会话 {session_id} 已更新（人设={data.get('preset_name')}）")
    return {"ok": True, "item": data, "message": "会话已更新"}


@router.delete("/llm/sessions/{session_id}")
async def delete_session(session_id: int) -> dict:
    if await llm.get_session_by_id(session_id) is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    removed = await llm.clear_sessions(session_id=session_id)
    logger.info(f"WebUI：已清空会话 {session_id}")
    return {"ok": True, "message": f"已清空该会话（{removed} 个）"}


@router.delete("/llm/sessions")
async def clear_sessions(
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
    plugin_name: Optional[str] = None,
) -> dict:
    """按条件批量清空会话：`scope_type` / `scope_id` / `plugin_name` 都可选（不传 = 全清）。"""
    if scope_type and scope_type not in ("user", "group"):
        raise HTTPException(status_code=400, detail="scope_type 只能是 user / group")
    removed = await llm.clear_sessions(
        scope_type=scope_type, scope_id=scope_id, plugin_name=plugin_name
    )
    logger.info(f"WebUI：已清空 {removed} 个会话（scope={scope_type},{scope_id},plugin={plugin_name}）")
    return {"ok": True, "removed": removed, "message": f"已清空 {removed} 个会话"}


# --------------------------------------------------------------------------- #
# 用户记忆（印象 + 关键信息）
# --------------------------------------------------------------------------- #
@router.get("/llm/users")
async def list_users(limit: int = 100) -> dict:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit 需在 1~500 之间")
    items = await llm.list_profiles(limit=limit)
    nick_map = await user_nickname_map([it["user_id"] for it in items])
    for it in items:
        it["nickname"] = nick_map.get(it["user_id"], "")
    return {"items": items, "count": len(items)}


@router.get("/llm/users/{user_id}")
async def get_user(user_id: str) -> dict:
    """查看某用户的印象 + 关键信息（QQ 命令 `%ai 印象` 的 WebUI 版本）。"""
    data = await llm.get_profile(user_id)
    data["source_labels"] = FACT_SOURCE_LABELS
    return {"ok": True, "item": data}


@router.put("/llm/users/{user_id}/impression")
async def set_user_impression(user_id: str, payload: ImpressionPayload) -> dict:
    data = await llm.set_impression(user_id, payload.impression)
    logger.info(f"WebUI：用户 {user_id} 的印象已更新（{len(payload.impression)} 字）")
    return {"ok": True, "item": data, "message": "印象已更新"}


@router.put("/llm/users/{user_id}/facts")
async def set_user_fact(user_id: str, payload: FactPayload) -> dict:
    if not payload.key.strip():
        raise HTTPException(status_code=400, detail="键不能为空")
    try:
        data = await llm.set_fact(user_id, payload.key, payload.value, source=llm.LLM_FACT_MANUAL)
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(f"WebUI：用户 {user_id} 的关键信息「{payload.key}」已写入")
    return {"ok": True, "item": data, "message": f"已记录「{payload.key}」"}


@router.delete("/llm/users/{user_id}/facts")
async def delete_user_fact(user_id: str, key: Optional[str] = None) -> dict:
    removed = await llm.delete_fact(user_id, key)
    if not removed:
        raise HTTPException(status_code=404, detail="没有匹配的关键信息")
    logger.info(f"WebUI：用户 {user_id} 的关键信息已删除 {removed} 条（key={key or '全部'}）")
    return {"ok": True, "removed": removed, "message": f"已删除 {removed} 条"}


@router.delete("/llm/users/{user_id}")
async def clear_user(user_id: str) -> dict:
    """清空某用户的全部长期记忆（印象 + 关键信息）。"""
    removed = await llm.clear_profile(user_id)
    logger.info(f"WebUI：用户 {user_id} 的长期记忆已清空（{removed} 条）")
    return {"ok": True, "removed": removed, "message": f"已清空长期记忆（{removed} 条）"}


# --------------------------------------------------------------------------- #
# 群记忆
# --------------------------------------------------------------------------- #
@router.get("/llm/groups")
async def list_group_profiles(limit: int = 100) -> dict:
    """列出有群记忆的群（最近更新的排前面）。"""
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit 需在 1~500 之间")
    items = await llm.list_group_profiles(limit=limit)
    gmap = await group_name_map()
    for it in items:
        it["group_name"] = gmap.get(it["group_id"], "")
    return {"items": items, "count": len(items)}


@router.put("/llm/groups/{group_id}/memory")
async def set_group_memory(group_id: str, payload: dict) -> dict:
    """人工改写某群的群记忆。"""
    memory = str(payload.get("memory", "") or "").strip()
    data = await llm.set_group_memory(group_id, memory)
    logger.info(f"WebUI：群 {group_id} 的群记忆已更新（{len(memory)} 字）")
    return {"ok": True, "item": data, "message": "群记忆已更新"}


# --------------------------------------------------------------------------- #
# 试聊（不带上下文，用来验证配置）
# --------------------------------------------------------------------------- #
@router.post("/llm/chat")
async def test_chat(payload: ChatPayload) -> dict:
    """在 WebUI 里试一句（无上下文、不写库），用来确认人设 / 服务商配好了。"""
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="内容不能为空")
    started = time.perf_counter()
    try:
        text = await llm.ask(
            prompt,
            preset=payload.preset.strip() or None,
            provider=payload.provider.strip() or None,
            system=payload.system.strip() or None,
        )
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(f"WebUI：试聊成功（{elapsed}ms，{len(text)} 字）")
    return {"ok": True, "text": text, "elapsed_ms": elapsed, "message": f"收到回复（{elapsed}ms）"}
