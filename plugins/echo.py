"""示例插件：echo（复读）。

用来验证「自研框架」的插件通路与门控是否生效：
- 用 `%echo 内容` 或 `%复读 内容` 触发；
- 被总控插件（Paperbot_CONSOLE）禁用后应完全无响应。
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg

from src.core.feature import Feature

feature = Feature(
    "echo",
    display_name="回声",
    description="示例插件：复读你输入的内容",
    author="paper_white",
    version="0.1.0",
)

echo = feature.command("echo", aliases={"复读"}, priority=50)


@echo.handle()
async def _handle(event: MessageEvent, args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    await echo.finish(text if text else "喵？你想让我复读什么呀～")
