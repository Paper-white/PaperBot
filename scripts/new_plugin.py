"""插件脚手架：一键生成符合 PaperBot 框架规范的新插件骨架。

用法（在项目根目录）::

    pixi run new-plugin <插件名> [--name 中文名] [--author 作者] [--force]

示例::

    pixi run new-plugin paperbot_weather --name 天气 --author paper_white

生成的目录结构（目录名 = 插件注册名 = 命令命名空间）::

    plugins/paperbot_weather/
    └── paperbot_weather.py

生成后不用做任何手工注册：`src/core/loader.py` 启动时会自动扫 `plugins/*/` 并 import，
模块顶层的 `Feature(...)` 会自动登记进注册表，且在 WebUI「插件管理」页可见。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = PROJECT_ROOT / "plugins"

# 插件名规范：小写字母 / 数字 / 下划线，且必须以字母开头
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

TEMPLATE = '''"""{display_name}插件（{plugin_name}）。

{description}
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg

from src.core.feature import Feature
from src.core.models import LEVEL_USER

feature = Feature(
    "{plugin_name}",
    display_name="{display_name}",
    description="{description}",
    author="{author}",
    repo="",
    version="0.1.0",
    requires=">=1.0,<2.0",
    # 需要 WebUI 可改的配置项时打开（键值会存进 plugin_config 表）：
    # config_schema=[
    #     {{"key": "greet", "label": "打招呼语", "type": "string", "default": "你好呀"}},
    # ],
)

# level= 只是「默认权限」，实际等级由框架维护，可在 WebUI「命令管理」页改，改完即时生效。
hello = feature.command(
    "{command}",
    priority=50,
    level=LEVEL_USER,
    description="{command} 命令示例（请改成你的功能）",
)


@hello.handle()
async def _handle(event: MessageEvent, args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    await hello.finish(f"{{feature.display_name}}收到：{{text or '（空）'}}")


# 生命周期钩子（按需保留 / 删除）：
# @feature.on_load
# async def _on_load() -> None:
#     feature.logger.info("已加载")


# @feature.on_unload
# async def _on_unload() -> None:
#     feature.logger.info("已卸载")
'''


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="new-plugin",
        description="生成 PaperBot 插件骨架",
    )
    parser.add_argument("name", help="插件名（小写字母/数字/下划线，如 paperbot_weather）")
    parser.add_argument("--name", dest="display_name", default="", help="中文显示名（默认同插件名）")
    parser.add_argument("--desc", dest="description", default="", help="插件简介")
    parser.add_argument("--author", dest="author", default="paper_white", help="作者")
    parser.add_argument("--command", dest="command", default="", help="示例命令名（默认取去掉 paperbot_ 前缀的部分）")
    parser.add_argument("--force", action="store_true", help="目标已存在时覆盖")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    plugin_name = args.name.strip()

    if not NAME_RE.match(plugin_name):
        print(f"[x] 插件名不合法：{plugin_name!r}（只允许小写字母/数字/下划线，且字母开头）")
        return 2

    display_name = args.display_name.strip() or plugin_name
    description = args.description.strip() or f"{display_name}功能插件"
    author = args.author.strip() or "paper_white"
    command = args.command.strip() or plugin_name.removeprefix("paperbot_") or plugin_name
    if not NAME_RE.match(command):
        print(f"[x] 命令名不合法：{command!r}")
        return 2

    target_dir = PLUGINS_DIR / plugin_name
    target_file = target_dir / f"{plugin_name}.py"

    if target_file.exists() and not args.force:
        print(f"[x] 已存在：{target_file.relative_to(PROJECT_ROOT)}（如需覆盖请加 --force）")
        return 1

    target_dir.mkdir(parents=True, exist_ok=True)
    target_file.write_text(
        TEMPLATE.format(
            plugin_name=plugin_name,
            display_name=display_name,
            description=description,
            author=author,
            command=command,
        ),
        encoding="utf-8",
    )

    rel = target_file.relative_to(PROJECT_ROOT)
    print(f"[√] 已生成插件：{rel}")
    print(f"    插件注册名：{plugin_name}")
    print(f"    中文显示名：{display_name}")
    print(f"    示例命令：%{command}")
    print("    下一步：编辑该文件实现功能，然后重启 Bot（或在 WebUI「插件管理」页热重载）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
