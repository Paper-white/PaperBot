"""PaperBot 框架核心包。

对外暴露最常用的入口，方便插件 `from src.core import Feature, get_session`。
"""

from src.core.database import dispose_db, get_session, init_db
from src.core.feature import Feature

__all__ = ["Feature", "get_session", "init_db", "dispose_db"]
