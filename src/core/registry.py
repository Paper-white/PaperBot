"""插件注册表（内存）。

每个 `Feature` 实例化时会自动登记到这里。总管理插件 / WebUI 都从这里
拿到「当前进程里已加载的全部功能插件」。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from src.core.feature import Feature

_features: Dict[str, "Feature"] = {}


def register(feature: "Feature") -> None:
    """登记一个功能插件（同名覆盖，便于热重载）。"""
    _features[feature.name] = feature


def get(name: str) -> Optional["Feature"]:
    return _features.get(name)


def unregister(name: str) -> Optional["Feature"]:
    """移除一个插件（热重载前清理旧实例），返回被移除的实例。"""
    return _features.pop(name, None)


def all_features() -> Dict[str, "Feature"]:
    """返回 name -> Feature 的浅拷贝。"""
    return dict(_features)


def names() -> List[str]:
    return sorted(_features)


def has(name: str) -> bool:
    return name in _features
