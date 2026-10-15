"""事实 key 受控词表（归一 + 单值/多值区分）。

模型提炼时容易自造同义 key（住址/居住地/家在），导致事实无法归一、注入去重失效。
这里维护一份小而稳的规范词表 + 别名映射；auto 提炼未命中即丢弃（宁缺勿滥），
manual 手写只做别名归一、保留原 key（管理员有意写入，不丢）。

单值维度（multi=False）：同一 (用户, key, 窗口) 只应有一条有效值（改口语义，留给 P2 版本链消费）。
多值维度（multi=True）：可并存多条（爱好/承诺等），不会互相取代。
"""

from __future__ import annotations

from typing import Optional

FACT_SINGLE = "single"
FACT_MULTI = "multi"

# 规范 key 定义：key / label / multi / aliases
FACT_KEYS: tuple[dict, ...] = (
    # --- 单值维度（改口应取代旧值） ---
    {"key": "称呼", "label": "称呼", "multi": False, "aliases": ("昵称", "叫我", "名字")},
    {"key": "姓名", "label": "姓名", "multi": False, "aliases": ("真名", "实名", "本名")},
    {"key": "性别", "label": "性别", "multi": False, "aliases": ()},
    {"key": "生日", "label": "生日", "multi": False, "aliases": ("出生日期", "出生")},
    {"key": "年龄", "label": "年龄", "multi": False, "aliases": ("岁数", "几岁", "年纪")},
    {"key": "职业", "label": "职业", "multi": False, "aliases": ("工作", "上班", "行业", "做什么工作")},
    {"key": "学校", "label": "学校", "multi": False, "aliases": ("就读", "上学", "大学")},
    {"key": "所在地", "label": "所在地", "multi": False, "aliases": ("住址", "居住地", "家住", "住在", "城市")},
    {"key": "家乡", "label": "家乡", "multi": False, "aliases": ("老家", "籍贯", "故乡")},
    # --- 多值维度（可并存） ---
    {"key": "爱好", "label": "爱好", "multi": True, "aliases": ("喜欢", "兴趣", "爱玩")},
    {"key": "擅长", "label": "擅长", "multi": True, "aliases": ("拿手", "技能", "会什么")},
    {"key": "忌口", "label": "忌口", "multi": True, "aliases": ("不吃", "不能吃", "饮食禁忌")},
    {"key": "过敏", "label": "过敏", "multi": True, "aliases": ()},
    {"key": "承诺", "label": "承诺", "multi": True, "aliases": ("答应", "保证", "约定")},
)

# 别名 → 规范 key（含规范 key 自身），import 时构建一次
_ALIAS_MAP: dict[str, str] = {}
# 规范 key → {"label": str, "multi": bool}
KEY_INFO: dict[str, dict] = {}

for _item in FACT_KEYS:
    KEY_INFO[_item["key"]] = {"label": _item["label"], "multi": bool(_item["multi"])}
    _ALIAS_MAP[_item["key"]] = _item["key"]
    for _alias in _item["aliases"]:
        _ALIAS_MAP.setdefault(_alias, _item["key"])


def normalize_key(raw: str) -> Optional[str]:
    """别名 / 规范 key → 规范 key；未命中返回 None。"""
    key = (raw or "").strip()
    if not key:
        return None
    return _ALIAS_MAP.get(key)


def is_multi(key: str) -> bool:
    """该 key 是否多值维度（未命中词表一律按单值处理，保守）。"""
    info = KEY_INFO.get(key or "")
    return bool(info and info["multi"])


def canonical_keys() -> tuple[str, ...]:
    """所有规范 key（供 prompt 列举）。"""
    return tuple(_item["key"] for _item in FACT_KEYS)
