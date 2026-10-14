"""功能插件加载器（自研框架）。

约定的插件目录结构（每个插件一个文件夹）::

    plugins/
    ├── paperbot_console/               # 插件目录（建议 paperbot_ 前缀）
    │   └── paperbot_console.py         # 插件具体代码文件
    └── paperbot_ping/
        └── paperbot_ping.py

也兼容以下两种写法：
- 目录内放 ``__init__.py``（标准包写法）；
- 直接把单文件插件放在 ``plugins/`` 根下（如 ``plugins/xxx.py``）。

**自动注册机制**：加载器只负责 import 代码文件；插件模块在 import 时创建
``Feature(...)``，其构造函数会调用 :func:`src.core.registry.register` 自动登记到
内存注册表；随后启动钩子 ``on_startup`` 会把元信息同步进数据库表 ``plugin_info``。
因此**新增插件只需放到上述目录，无需任何手工注册**。

**加载期校验**：导入插件后会检查它声明的 ``requires``（兼容的插件协议版本范围），
不满足就销毁它的 matcher 并把它从注册表移除（= 拒绝加载），只打 ERROR 日志，
**不让一个不兼容的插件拖垮整个 Bot**。
"""

from __future__ import annotations

import importlib
from pathlib import Path

from loguru import logger

from src.core import registry
from src.core.feature import drop_command_prefixes
from src.core.version import FRAMEWORK_VERSION, check_requires

DEFAULT_PLUGINS_DIR = "plugins"


def _iter_plugin_modules(root: Path) -> list[str]:
    """扫描插件目录，返回可导入的模块名列表（如 ``plugins.paperbot_ping.paperbot_ping``）。"""
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


def _reject(feature, reason: str) -> None:
    """拒绝一个插件：销毁其 matcher 并移出注册表（保持框架可用）。"""
    for matcher in list(getattr(feature, "matchers", [])):
        try:
            matcher.destroy()
        except (ValueError, KeyError, AttributeError):
            continue
    feature.matchers.clear()
    try:
        drop_command_prefixes(feature)
    except Exception:  # noqa: BLE001 - 清理失败不影响主流程
        pass
    registry.unregister(feature.name)
    logger.error(f"插件 {feature.name} 已拒绝加载：{reason}")


def _verify_requirements(name: str) -> bool:
    """校验插件声明的 requires，通过返回 True。"""
    feature = registry.get(name)
    if feature is None or not getattr(feature, "requires", ""):
        return True
    ok, message = check_requires(feature.requires, FRAMEWORK_VERSION)
    if not ok:
        _reject(feature, message)
        return False
    return True


def load_feature_plugins(plugins_dir: str = DEFAULT_PLUGINS_DIR) -> list[str]:
    """扫描并导入所有功能插件，返回成功加载的模块名列表。"""
    root = Path(plugins_dir)
    loaded: list[str] = []
    for module in _iter_plugin_modules(root):
        before = set(registry.names())
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).error(f"加载插件模块 {module} 失败")
            continue
        after = set(registry.names())

        # 模块里新注册的插件：校验它声明的框架版本要求
        accepted = [
            name
            for name in sorted(after - before)
            if _verify_requirements(name)
        ]
        if after - before and not accepted:
            continue
        loaded.append(module)
        for name in accepted:
            feature = registry.get(name)
            logger.success(
                f"已加载插件 {name}"
                f"（{feature.display_name} v{feature.version}"
                f"{'，受保护' if feature.protected else ''}"
                f"{'，可配置' if feature.config_schema else ''}）"
            )

    logger.info(f"已加载 {len(loaded)} 个功能插件模块：{', '.join(loaded) or '（无）'}")
    return loaded
