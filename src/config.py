"""项目级配置。

与 NoneBot2 的 `.env` 共用同一个文件，但这里只声明本项目自己关心的字段，
`extra="ignore"` 保证不会因为 NoneBot 的其它环境变量而报错。

## 配置文件位置（`.config/`，整个目录已 gitignore）

```
.config/
├── main/.env          ← 框架自身配置（原根目录 .env；含密钥，不进版本库）
└── <插件名>/config.json  ← 插件配置项（见 src/core/pluginconf.py）
```

⚠️ 根目录的 `.env` 仍被作为**低优先级兜底**读取（方便老环境平滑迁移）：
`.config/main/.env` 的值会覆盖它。迁移完可以删掉根目录那个。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（src/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent

# 所有本地配置文件的大本营（含密钥 → 已 gitignore）
CONFIG_DIR_NAME = ".config"
MAIN_CONFIG_DIR_NAME = "main"
# 框架自身配置（.env 风格）
MAIN_ENV_PATH = f"{CONFIG_DIR_NAME}/{MAIN_CONFIG_DIR_NAME}/.env"
# 兼容：老环境把 .env 放在根目录，优先级更低
LEGACY_ENV_FILE = ".env"


def config_dir() -> Path:
    """`.config/` 根目录（不存在则创建）。"""
    path = BASE_DIR / CONFIG_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def main_config_dir() -> Path:
    """`.config/main/`（框架自身配置目录）。"""
    path = config_dir() / MAIN_CONFIG_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # 先读老的根目录 .env，再读 .config/main/.env（后者覆盖前者）
        env_file=(LEGACY_ENV_FILE, MAIN_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 共享数据库：所有插件都通过同一个 engine 访问，天然共享数据
    database_url: str = "sqlite+aiosqlite:///data/paperbot.db"

    # WebUI 后端
    webui_enable: bool = True
    webui_prefix: str = "/api"
    webui_token: str = ""

    log_level: str = "INFO"

    # 日志文件：每次运行写入 logs/paperbot-<时间戳>.log
    log_file_enable: bool = True
    log_file_level: str = "DEBUG"
    log_dir: str = "logs"
    # 日志保留天数（<=0 表示不自动清理）
    log_keep_days: int = 7

    # ============ 消息安全（门控默认策略 + 输出守卫共用一份群白名单） ============
    # 「Bot 允许活跃的群」——唯一白名单来源：
    #   · 门控默认策略：未显式配置规则的群，只有在此列表内才跟随插件默认值放行；
    #   · 消息输出守卫：只允许往这些群发消息（私聊一律拦截）。
    # 留空 = 不向任何群发消息（安全默认，真实群号只写在 .env）。
    bot_active_groups: list[str] = []

    # 框架级默认门控策略：
    #   True  = 未配置规则时跟随插件自己的 default_enabled（旧行为）
    #   False = 「全局默认禁用」，未配置规则的群/用户一律按禁用处理，
    #           仅 BOT_ACTIVE_GROUPS 里的群在未配置规则时跟随插件默认值。
    gate_default_enabled: bool = True

    # 消息输出守卫（开发测试期锁死消息出口，只允许发往 BOT_ACTIVE_GROUPS）
    output_guard_enabled: bool = True
    # ⚠️ 已废弃：改用 BOT_ACTIVE_GROUPS（两处白名单合并）。仅为兼容旧 .env 保留。
    output_guard_groups: list[str] = []

    # ============ 渲染（文本 / Markdown → 图片） ============
    # 卡片宽度（像素，固定）与截图缩放倍数（2 = 高清）
    render_width: int = 720
    render_scale: int = 2
    # 浏览器来源：本地复用系统 Edge 用 msedge；搬服务器可改 chromium 或留空走内置
    render_browser_channel: str = "msedge"
    render_browser_path: str = ""
    # OC 背景贴图：相对项目根目录或绝对路径，留空则不叠图
    render_bg_image: str = ""
    render_bg_image_opacity: float = 0.22
    render_bg_image_size: str = "58%"
    render_bg_image_position: str = "bottom right"
    # 单次渲染超时（毫秒）
    render_timeout_ms: int = 30000

    # ============ LLM（统一大模型服务） ============
    # 总开关：关闭后所有插件调用 LLM 都会直接抛 LLMError（便于临时断网/省钱）
    llm_enable: bool = True
    # 兜底服务商配置（DB 里没有默认服务商时用它；只需 OpenAI 兼容即可）
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    # 请求超时（秒）
    llm_timeout: float = 60.0
    # 访问外网用的代理（例：http://127.0.0.1:2080），留空则直连
    llm_proxy: str = ""
    # 采样参数默认值（0 表示该参数不传）
    llm_temperature: float = 0.8
    llm_max_tokens: int = 0
    # 默认人设名（留空 = 不注入人设，只按 messages 原样请求）
    llm_default_preset: str = ""
    # 是否允许流式输出（全局开关；插件调用时可用 use_stream=True/False 再覆盖）
    llm_stream_enable: bool = False
    # 是否启用「用户印象 + 关键信息」长期记忆（个性化）
    llm_memory_enable: bool = True
    # 多轮上下文保留轮数（1 轮 = 一问一答；0 = 不带历史）
    llm_context_rounds: int = 10
    # 每累计 N 条用户消息，触发一次「印象 / 关键信息」提炼（<=0 关闭自动提炼）
    # ⚠️ 已被定时总结（scheduler）取代：保留该键仅兼容旧 .env，不再生效。
    llm_impression_every: int = 10
    # 注入 prompt 的关键信息条数上限
    llm_max_facts: int = 30
    # 单个用户在**单个 scope** 内的事实条数上限（超出按 updated_at 最旧优先淘汰）
    llm_max_facts_stored: int = 200
    # 记忆跨窗口隔离：事实按来源（私聊/某群）过滤注入，群聊不再看到私聊/别群内容
    llm_memory_scope_isolation: bool = True
    # 记忆注入「分槽保底」：通用画像 / 窗口事实 分槽，每槽至少保留若干条，避免近期事实挤掉长期画像
    llm_memory_slots_enable: bool = True
    # 受控 key 词表：auto 提炼时未命中词表的自造 key 直接丢弃（False = 放行原样，旧行为）
    llm_memory_key_exact: bool = True
    # 记忆用法档：注入时按 mention/tone/uncertain 分组渲染（False = 忽略档位、单列表渲染）
    llm_memory_usage_enable: bool = True

    # ============ 聊天记录（原始流水层，见 src/core/chatlog.py） ============
    # 全局钩子把收到的消息（群 + 私聊，含 bot 自己的）逐条写入 chat_message 表。
    chatlog_enable: bool = True

    # ============ 定时记忆总结 + 上下文压缩（见 src/core/scheduler.py） ============
    # 总开关：关闭后不跑定时总结与压缩
    llm_summary_enable: bool = True
    # 总结周期（秒）
    llm_summary_interval: int = 1800
    # 总结用服务商（留空 = 用默认服务商）
    llm_summary_provider: str = ""
    # 总结用模型（留空 = 用服务商首选；建议填便宜模型）
    llm_summary_model: str = ""
    # 单次总结最多取多少条增量（超过则只取最近这些，水位线直推最新）
    llm_summary_max_msgs: int = 300
    # 触发压缩的会话消息条数；实际保留窗口 = 2×LLM_CONTEXT_ROUNDS（与注入窗口对齐）
    llm_compress_threshold: int = 20

    # ============ 版本控制（WebUI 设置页「一键更新」，见 src/core/vcs.py） ============
    # 用途：本地开发机 push → 服务器生产环境在 WebUI 点一下拉取最新代码
    # （只拉不推，且绝不碰 .config/ data/ logs/ 这些已 gitignore 的目录）
    vcs_enable: bool = True
    # 远程名与跟踪分支
    vcs_remote: str = "origin"
    vcs_branch: str = "main"
    # 远程地址：留空 = 用仓库已有的 origin；首次部署可填公开仓库 HTTPS 地址
    vcs_remote_url: str = ""
    # fetch 时使用的代理（如 http://127.0.0.1:2080），留空则直连
    vcs_proxy: str = ""
    # 提交历史展示条数
    vcs_log_limit: int = 30
    # 重启模式：spawn = 自己拉起新进程（开箱即用）；exit = 只退出，交给 systemd/NSSM 守护进程拉起
    vcs_restart_mode: str = "spawn"
    # 守护服务名（如 paperbot.service / PaperBot），留空则不检测守护状态
    vcs_service_name: str = ""


settings = Settings()

# 本文件 `Settings` 里，改了必须重启才生效的项（其余视为可热更——调用时现读）。
# 说明：
#   · webui_enable / webui_prefix  —— 路由注册在应用启动时完成；
#   · database_url                 —— 数据库 engine 已建好，连接串改不掉；
#   · log_* / log_level 等         —— 日志 handler 在启动时装配完毕。
# 除以上之外的字段都视为「可热更」：直接原地覆盖 `settings` 即可让
# `from src.config import settings` 的模块立刻读到新值。
# （NoneBot 自己的驱动配置另见下方 NONEBOT_HOT_KEYS / NONEBOT_RESTART_KEYS。）
RESTART_ONLY_KEYS = {
    "webui_enable", "webui_prefix",
    "database_url",
    "log_level", "log_file_enable", "log_file_level", "log_dir", "log_keep_days",
}

# NoneBot 驱动配置（`nonebot.get_driver().config`）里，改完能**立即生效**的键。
# 这些键不属于上面的 `Settings`，要单独比对；目前只有 superusers：
# 权限校验（NoneBot 的 SUPERUSER + 本项目的 permission.owner_ids）每次都现读
# `driver.config.superusers`，所以原地 setattr 即刻生效，无需重启。
NONEBOT_HOT_KEYS = {"superusers"}
# NoneBot 驱动配置里改了**必须重启**的键（仅用于给出可读提示，避免误报）。
NONEBOT_RESTART_KEYS = {
    "driver", "host", "port",
    "command_start",
    "onebot_ws_urls", "onebot_v11_ws_urls", "onebot_access_token",
}


def _reload_nonebot_config() -> tuple[list[str], list[str]]:
    """重读 `.env`，热更新 NoneBot 驱动配置里「可热更」的键。

    ⚠️ 为什么单独做：`HOST` / `PORT` / `DRIVER` / `SUPERUSERS` / `COMMAND_START` /
    `ONEBOT_*` 这些是 **NoneBot 自己的配置**（`nonebot.get_driver().config`），
    不在本文件 `Settings` 的 `model_fields` 里——只比对 `Settings` 会漏掉它们，
    表现为「明明改了 SUPERUSERS，热重载却说无变动」。

    返回 `(hot, restart)`，键名用 `.env` 里的大写写法（更贴近用户编辑的内容）。
    驱动未就绪（如自检脚本）时安静返回空。
    """
    hot: list[str] = []
    restart: list[str] = []
    try:
        import nonebot

        current = nonebot.get_driver().config
    except Exception:  # noqa: BLE001 - 未 init / 非驱动环境，直接跳过
        return hot, restart

    try:
        from nonebot.config import Config as NoneBotConfig

        # bot.py 里是 `nonebot.init(_env_file=MAIN_ENV_PATH)`，这里用同一来源重建一份做比对
        # （用绝对路径，避免热重载时 cwd 不同导致读错文件）
        fresh = NoneBotConfig(_env_file=str(BASE_DIR / MAIN_ENV_PATH))
    except Exception:  # noqa: BLE001 - 重建失败就只当作没变化
        return hot, restart

    for name in type(fresh).model_fields:
        try:
            old_value = getattr(current, name, None)
            new_value = getattr(fresh, name, None)
        except Exception:  # noqa: BLE001 - 个别字段取不到就跳过
            continue
        if old_value == new_value:
            continue
        env_key = name.upper()
        if name in NONEBOT_HOT_KEYS:
            try:
                setattr(current, name, new_value)
                hot.append(env_key)
                continue
            except Exception:  # noqa: BLE001 - 改不动就当需重启
                pass
        if name in NONEBOT_RESTART_KEYS:
            restart.append(env_key)
    return hot, restart


def reload_settings() -> dict:
    """重新读取 `.config/main/.env`，把能热更的字段**原地**写回 `settings`。

    为什么用「原地 setattr」而不是 `global settings; settings = Settings()`：
        `settings` 是模块级单例，项目里大量模块用 `from src.config import settings`
        拿到了**同一个对象引用**。如果整体替换对象，那些模块手里的引用还是旧对象，
        热重载就白做了。逐字段 `setattr(settings, ...)` 能保持对象 identity 不变，
        所有持有引用的模块立刻看到新值。

    ⚠️ 只读取一次 .env（由 `Settings` 的 `env_file` 决定路径），函数内不会改动
        全局的环境变量或 env 文件位置，避免热重载把配置来源改乱。

    返回：
        {
            "changed": [...],   # 实际发生变化的字段名（下划线形式）
            "hot":     [...],   # 其中可热更、已生效的
            "restart": [...],   # 其中需要重启才生效的
            "message": "...",   # 中文可读摘要
        }
    """
    fresh = Settings()

    changed: list[str] = []
    for name in type(fresh).model_fields:
        old_value = getattr(settings, name, None)
        new_value = getattr(fresh, name)
        if old_value != new_value:
            setattr(settings, name, new_value)
            changed.append(name)

    hot = [n for n in changed if n not in RESTART_ONLY_KEYS]
    restart = [n for n in changed if n in RESTART_ONLY_KEYS]

    # 再补上 NoneBot 驱动配置（HOST/PORT/DRIVER/SUPERUSERS/COMMAND_START/ONEBOT_* 等，
    # 不在上面的 Settings 里，否则改 SUPERUSERS 会「无变动」）。键名统一用大写写法。
    driver_hot, driver_restart = _reload_nonebot_config()
    hot.extend(k for k in driver_hot if k not in hot)
    restart.extend(k for k in driver_restart if k not in restart)

    if not hot and not restart:
        message = "配置无变化"
    else:
        parts = []
        if hot:
            parts.append(f"已热重载 {len(hot)} 项：{'、'.join(hot)}")
        if restart:
            parts.append(f"另有 {len(restart)} 项需重启：{'、'.join(restart)}")
        message = "；".join(parts) or "配置无变化"

    return {"changed": hot + restart, "hot": hot, "restart": restart, "message": message}


def active_groups() -> set[str]:
    """「Bot 允许活跃的群」白名单（门控默认策略 + 输出守卫共用）。

    优先用新键 `BOT_ACTIVE_GROUPS`；若为空而旧键 `OUTPUT_GUARD_GROUPS` 有值，
    则回退到旧键（兼容旧 `.env`，启动时会打一条迁移提示）。
    """
    groups = settings.bot_active_groups or settings.output_guard_groups
    return {str(g) for g in groups}


def using_legacy_groups() -> bool:
    """是否仍在用已废弃的 `OUTPUT_GUARD_GROUPS`（用于启动提示）。"""
    return not settings.bot_active_groups and bool(settings.output_guard_groups)
