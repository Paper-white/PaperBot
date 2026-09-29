"""WebUI 后端包（FastAPI 路由，挂载到 NoneBot 的 fastapi driver）。"""

from webui.api import setup_webui

__all__ = ["setup_webui"]
