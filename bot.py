"""PaperBot 启动入口。

启动流程：
1. nonebot.init()（读取 .env）
2. 注册 OneBot v11 适配器
3. 安装「消息输出守卫」（开发测试期锁死消息出口）
4. 加载 plugins/ 下的所有功能插件（插件 import 时向框架注册 Feature）
5. 注册启动/关闭钩子（建表 + 同步插件元信息）
6. 挂载 WebUI 后端路由
"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 初始化 NoneBot（读取项目根目录的 .env）
nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 安装消息输出守卫（务必在加载插件 / 建立连接之前）
from src.core.guard import install_output_guard  # noqa: E402

install_output_guard()

# 加载功能插件（必须在 init 之后）
nonebot.load_plugins("plugins")

# 启动/关闭钩子
from src.core.bootstrap import on_shutdown, on_startup  # noqa: E402

driver.on_startup(on_startup)
driver.on_shutdown(on_shutdown)

# 挂载 WebUI 后端
from webui.api import setup_webui  # noqa: E402

setup_webui()


if __name__ == "__main__":
    nonebot.run()
