"""功能插件加载器（自研框架）。

约定的插件目录结构（每个插件一个文件夹）::

    plugins/
    ├── paperbot_console/               # 插件目录（建议 paperbot_ 前缀）
    │   └── paperbot_console.py         # 插件具体代码文件
    └── paperbot_echo/
        └── paperbot_echo.py

也兼容以下两种写法：
- 目录内放 ``__init__.py``（标准包写法）；
- 直接把单文件插件放在 ``plugins/`` 根下（如 ``plugins/xxx.py``）。

**自动注册机制**：加载器只负责 import 代码文件；插件模块在 import 时创建
``Feature(...)``，其构造函数会调用 :func:`src.core.registry.register` 自动登记到
内存注册表；随后启动钩子 ``on_startup`` 会把元信息同步进数据库表 ``plugin_info``。
因此**新增插件只需放到上述目录，无需任何手工注册**。
"""

from __future__ import annotations

import importlib
from pathlib import Path

from loguru import logger

DEFAULT_PLUGINS_DIR = "plugins"


def _iter_plugin_modules(root: Path) -> list[str]:
    """扫描插件目录，返回可导入的模块名列表（如 ``plugins.paperbot_echo.paperbot_echo``）。"""
    modules: list[str] = []
    if not root.is_dir():
        logger.warning(f"插件目录不存在：{root}")
        return modules

    for entry in sorted(root.iterdir()):
        if entry.name.startswith((".", "_")):
            continue

        if entry.is_dir():
            # 优先标准包写法：目录内有 __init__.py
            if (entry / "__init__.py").is_file():
                modules.append(f"{root.name}.{entry.name}")
                continue
            # 否则取目录内的插件代码文件（排除下划线开头的私有文件）
            py_files = [p for p in sorted(entry.glob("*.py")) if not p.name.startswith("_")]
            if not py_files:
                logger.warning(f"插件目录 {entry} 内没有可加载的 .py 文件，已跳过")
                continue
            if len(py_files) > 1:
                logger.warning(
                    f"插件目录 {entry} 内有多个 .py 文件，"
                    f"默认只加载 {py_files[0].name}（如需自定义请改用 __init__.py）"
                )
            modules.append(f"{root.name}.{entry.name}.{py_files[0].stem}")

        elif entry.suffix == ".py":  # 兼容：单文件插件直接放在 plugins/ 下
            modules.append(f"{root.name}.{entry.stem}")

    return modules


def load_feature_plugins(plugins_dir: str = DEFAULT_PLUGINS_DIR) -> list[str]:
    """扫描并导入所有功能插件，返回成功加载的模块名列表。"""
    root = Path(plugins_dir)
    loaded: list[str] = []
    for module in _iter_plugin_modules(root):
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).error(f"加载插件模块 {module} 失败")
            continue
        loaded.append(module)

    logger.info(f"已加载 {len(loaded)} 个功能插件模块：{', '.join(loaded) or '（无）'}")
    return loaded
