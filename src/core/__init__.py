"""PaperBot 框架核心包。

对外暴露最常用的入口，方便插件 `from src.core import Feature, get_session`。
"""

from src.core.cmdperm import CommandPermission
from src.core.database import dispose_db, get_session, init_db
from src.core.feature import Feature
from src.core.llm import (
    LLMChunk,
    LLMError,
    LLMResult,
    get_plugin_llm,
    llm_available,
)
from src.core import llm
from src.core.logger import PrefixedLogger, get_logger
from src.core.permission import PermLevel, resolve_level
from src.core.render import render_markdown, render_text
from src.core.version import FRAMEWORK_VERSION

__all__ = [
    "Feature",
    "CommandPermission",
    "PrefixedLogger",
    "FRAMEWORK_VERSION",
    "get_logger",
    "get_session",
    "init_db",
    "dispose_db",
    "PermLevel",
    "resolve_level",
    "render_markdown",
    "render_text",
    # 统一 LLM 服务（插件常用：`from src.core import llm` / `from src.core import LLMError`）
    "llm",
    "LLMError",
    "LLMResult",
    "LLMChunk",
    "llm_available",
    "get_plugin_llm",
]
