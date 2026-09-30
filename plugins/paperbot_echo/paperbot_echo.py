"""示例插件：echo（复读）。

用来验证「自研框架」的插件通路与门控是否生效：
- 用 `%echo 内容` 或 `%复读 内容` 触发；
- 被总控插件（paperbot_console）禁用后应完全无响应。

顺带演示框架的几项能力（新插件可以直接照抄）：
- **元信息**：author / repo / requires（加载期会校验插件协议版本）；
- **插件级配置项**：`config_schema` → WebUI「插件管理」自动出表单，值存库；
- **生命周期钩子**：`on_load / on_unload / on_enable / on_disable`；
- **统一 logger**：`feature.logger`（日志自动带 `[paperbot_echo]` 前缀）。
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg

from src.core.feature import Feature
from src.core.models import LEVEL_USER

feature = Feature(
    "paperbot_echo",
    display_name="回声",
    description="示例插件：复读你输入的内容",
    author="paper_white",
    repo="https://github.com/paper-white/PaperBot",
    version="0.1.0",
    requires=">=1.0,<2.0",
    config_schema=[
        {
            "key": "empty_reply",
            "label": "空消息时的回复",
            "type": "string",
            "default": "喵？你想让我复读什么呀～",
            "hint": "不带参数时会回这句话",
        },
        {
            "key": "upper",
            "label": "转成大写",
            "type": "bool",
            "default": False,
            "hint": "开着玩的小开关，改完即时生效",
        },
    ],
)

# level= 声明的是「默认权限」：普通用户即可（1 级）。
# 实际权限由框架维护，可在 WebUI 命令管理页随时改，改完立即生效。
echo = feature.command(
    "echo",
    aliases={"复读"},
    priority=50,
    level=LEVEL_USER,
    description="复读你输入的内容（用于验证门控与输出守卫）",
)


@echo.handle()
async def _handle(event: MessageEvent, args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    if not text:
        await echo.finish(feature.cfg("empty_reply", "喵？"))
    if feature.cfg("upper", False):
        text = text.upper()
    await echo.finish(text)


@feature.on_load
async def _on_load() -> None:
    feature.logger.info(
        f"回声插件已就绪（空回复={feature.cfg('empty_reply')!r}，大写={feature.cfg('upper')}）"
    )


@feature.on_enable
async def _on_enable(feature, context) -> None:  # noqa: ANN001 - 演示带参写法
    feature.logger.info(f"回声插件被启用（来源：{context.get('reason', '规则变更')}）")


@feature.on_disable
async def _on_disable(feature) -> None:  # noqa: ANN001
    feature.logger.info("回声插件被停用")


@feature.on_unload
async def _on_unload() -> None:
    feature.logger.info("回声插件已卸载")
