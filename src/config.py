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
    llm_impression_every: int = 10
    # 注入 prompt 的关键信息条数上限
    llm_max_facts: int = 30

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
