"""PaperBot 框架核心包。

对外暴露最常用的入口，方便插件 `from src.core import Feature, get_session`。
"""

from src.core.database import dispose_db, get_session, init_db
from src.core.feature import Feature
from src.core.permission import PermLevel, resolve_level

__all__ = [
    "Feature",
    "get_session",
    "init_db",
    "dispose_db",
    "PermLevel",
    "resolve_level",
]
