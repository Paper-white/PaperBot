"""PaperBot 启动入口。

启动流程：
1. nonebot.init()（读 `.config/main/.env`；根目录老 .env 作为低优先级兜底）
2. 挂日志文件输出（logs/ 下每次运行一个日志文件）
3. 注册 OneBot v11 适配器
4. 安装「消息输出守卫」（开发测试期锁死消息出口）
5. 加载 plugins/ 下的所有功能插件（插件 import 时向框架注册 Feature）
6. 注册启动/关闭钩子（建表 + 同步插件元信息）
7. 挂载 WebUI 后端路由

⚠️ 配置文件统一放在 `.config/`（整个目录已 gitignore）：
   `.config/main/.env`（框架）+ `.config/<插件名>/config.json`（插件）。
   根目录的 `.env` 会被 `_env_file` 自动当作低优先级兜底读取。
"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from src.config import MAIN_ENV_PATH

# 初始化 NoneBot（`.config/main/.env`；NoneBot 会自动把根 .env 作为更低优先级合并）
nonebot.init(_env_file=MAIN_ENV_PATH)

# 日志文件输出（越早越好，后面的启动过程都留痕）
from src.core.logging import log_dir, setup_file_logging  # noqa: E402

_log_file = setup_file_logging()
if _log_file is not None:
    nonebot.logger.success(f"日志文件已开启：{_log_file}（目录：{log_dir()}）")

# 连接日志降噪：把「连不上协议端」的刷屏 ERROR + traceback 变成可读的节流提示
from src.core.connlog import install_connection_log_filter  # noqa: E402

install_connection_log_filter()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 安装消息输出守卫（务必在加载插件 / 建立连接之前）
from src.core.guard import install_output_guard  # noqa: E402

install_output_guard()

# 加载功能插件（必须在 init 之后）：扫描 plugins/{插件名}/{代码文件}.py
from src.core.loader import load_feature_plugins  # noqa: E402

load_feature_plugins("plugins")

# 启动/关闭钩子
from src.core.bootstrap import on_shutdown, on_startup  # noqa: E402

driver.on_startup(on_startup)
driver.on_shutdown(on_shutdown)

# 挂载 WebUI 后端
from webui.api import setup_webui  # noqa: E402

setup_webui()


if __name__ == "__main__":
    nonebot.run()
