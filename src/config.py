"""项目级配置。

与 NoneBot2 的 `.env` 共用同一个文件，但这里只声明本项目自己关心的字段，
`extra="ignore"` 保证不会因为 NoneBot 的其它环境变量而报错。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（src/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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


settings = Settings()


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
