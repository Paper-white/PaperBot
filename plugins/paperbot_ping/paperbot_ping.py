"""在线状态测试插件（paperbot_ping）。

用法：`%ping` → `pong`，用于确认 Bot 在线、消息通路与门控都正常。

顺带保留框架的基础示范（新插件可以直接照抄）：
- **元信息**：author / repo / requires（加载期会校验插件协议版本）；
- **生命周期钩子**：`on_load / on_unload / on_enable / on_disable`；
- **统一 logger**：`feature.logger`（日志自动带 `[paperbot_ping]` 前缀）。
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import MessageEvent

from src.core.feature import Feature
from src.core.models import LEVEL_USER

feature = Feature(
    "paperbot_ping",
    display_name="在线状态",
    description="在线状态测试：%ping → pong",
    author="paper_white",
    repo="https://github.com/paper-white/PaperBot",
    version="0.2.0",
    requires=">=1.0,<2.0",
)

# level= 声明的是「默认权限」：普通用户即可（1 级）。
# 实际权限由框架维护，可在 WebUI 命令管理页随时改，改完立即生效。
ping = feature.command(
    "ping",
    priority=50,
    level=LEVEL_USER,
    description="在线状态测试，回复 pong",
)


@ping.handle()
async def _handle() -> None:
    await ping.finish("pong")


@feature.on_load
async def _on_load() -> None:
    feature.logger.info("在线状态测试插件已就绪（%ping → pong）")


@feature.on_enable
async def _on_enable(feature, context) -> None:  # noqa: ANN001 - 演示带参写法
    feature.logger.info(f"在线状态插件被启用（来源：{context.get('reason', '规则变更')}）")


@feature.on_disable
async def _on_disable(feature) -> None:  # noqa: ANN001
    feature.logger.info("在线状态插件被停用")


@feature.on_unload
async def _on_unload() -> None:
    feature.logger.info("在线状态插件已卸载")
