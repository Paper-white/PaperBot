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

    # 消息输出守卫（开发测试期锁死消息出口，只允许发往白名单群）
    output_guard_enabled: bool = True
    output_guard_groups: list[str] = ["123456789"]


settings = Settings()
