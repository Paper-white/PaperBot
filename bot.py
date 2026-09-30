"""PaperBot 启动入口。

启动流程：
1. nonebot.init()（读取 .env）
2. 挂日志文件输出（logs/ 下每次运行一个日志文件）
3. 注册 OneBot v11 适配器
4. 安装「消息输出守卫」（开发测试期锁死消息出口）
5. 加载 plugins/ 下的所有功能插件（插件 import 时向框架注册 Feature）
6. 注册启动/关闭钩子（建表 + 同步插件元信息）
7. 挂载 WebUI 后端路由
"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 初始化 NoneBot（读取项目根目录的 .env）
nonebot.init()

# 日志文件输出（越早越好，后面的启动过程都留痕）
from src.core.logging import log_dir, setup_file_logging  # noqa: E402

_log_file = setup_file_logging()
if _log_file is not None:
    nonebot.logger.success(f"日志文件已开启：{_log_file}（目录：{log_dir()}）")

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
