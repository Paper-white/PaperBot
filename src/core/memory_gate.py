"""事实抽取「代码闸门」：入库前对着原始消息做引用校验。

不信任模型改写后的 fact.value，而是回到该批原始消息里找「支撑片段」，
用最长公共子串做引用校验 + marker 词表拒绝四类幻觉：
转述 / 角色扮演 / 临时状态 / 未兑现打算。

只做**保守拒绝**：拿不准就拒（宁缺勿滥），避免幻觉记忆污染长期画像。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# 支撑片段与 fact.value 的最短公共子串长度（中文字符计），低于它就认为「无原文依据」。
# 取 1 而非更大的值：核心用例里 fact.value 可能只有 1~2 个字（如「猫」「累」「吃鱼」），
# 阈值过高会让它们先被判「无原文依据」，从而绕过 marker 词表（四类拒绝规则失去意义）。
MIN_SUPPORT_CHARS = 1
# 支撑片段的相似度下限（相对于 fact.value）——这条才是真正防「弱证据」的闸门：
# 值越长，要求命中的公共子串越长。
MIN_SUPPORT_RATIO = 0.34

# 四类拒绝规则：命中支撑片段所在句子里任一 marker → 拒绝
_REJECT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "转述（并非该用户本人的事实）",
        (
            "他说",
            "她说",
            "据说",
            "听说",
            "转述",
            "有人说",
            "别人说",
            "群里说",
            "据称",
            "传闻",
            "好像说",
        ),
    ),
    (
        "角色扮演/虚构",
        (
            "扮演",
            "台词",
            "剧本",
            "假装",
            "设定是",
            "cos",
            "cosplay",
            "角色扮演",
            "if 我",
            "如果我是",
        ),
    ),
    (
        "临时状态",
        (
            "今天有点",
            "这会儿",
            "就这一次",
            "暂时",
            "刚刚",
            "现在有点",
            "这几天",
        ),
    ),
    (
        "未兑现的打算",
        (
            "打算",
            "计划",
            "准备去",
            "想要去",
            "考虑要",
            "以后要",
            "将来要",
            "明天去",
            "下周去",
        ),
    ),
)


def _norm(text: str) -> str:
    """归一化：去空白与常见标点，降低匹配噪声。"""
    return re.sub(r"[\s,，。、！!？?：:；;“”\"'（）()【】\[\]…~～-]", "", text or "")


def _split_sentences(text: str) -> list[str]:
    """按中英文句末标点 / 换行切句，去掉空段（当前仅备用，供后续更细的按句判定）。"""
    parts = re.split(r"[。！？!?\n；;]+", text or "")
    return [p for p in (x.strip() for x in parts) if p]


def find_support(value: str, messages: list[str]) -> str:
    """在原始消息里找与该 fact.value 最匹配的支撑片段（找不到返回 ""）。"""
    v = _norm(value)
    if not v:
        return ""
    best = ""
    best_ratio = 0.0
    for msg in messages:
        m = _norm(msg)
        if not m:
            continue
        block = SequenceMatcher(None, v, m).find_longest_match(0, len(v), 0, len(m))
        if block.size >= MIN_SUPPORT_CHARS and block.size / max(1, len(v)) >= MIN_SUPPORT_RATIO:
            ratio = block.size / max(1, len(v))
            if ratio > best_ratio:
                best_ratio = ratio
                best = m[block.b : block.b + block.size]
    return best


def check_fact(key: str, value: str, messages: list[str]) -> tuple[bool, str]:
    """判定一条 fact 是否可入库。返回 (是否通过, 拒绝原因/空串)。"""
    support = find_support(value, messages)
    if not support:
        return False, "无原文依据（引用校验未通过）"
    # 在「支撑片段所在的原始句子」里查拒绝词（而非在模型改写后的断言里查）
    for raw in messages:
        if support in _norm(raw):
            for category, markers in _REJECT_RULES:
                if any(mk in raw for mk in markers):
                    return False, f"命中闸门：{category}"
            break
    return True, ""


def filter_facts(facts, messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """批量过滤：输入原始 fact 列表 + 该批消息（dict 需含 content）。

    返回 (通过列表, 被拒列表)，被拒项额外带 "reject_reason"。
    """
    texts = [str(m.get("content") or "") for m in messages]
    kept: list[dict] = []
    rejected: list[dict] = []
    for item in facts or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        value = str(item.get("value") or "").strip()
        if not key or not value:
            continue
        ok, reason = check_fact(key, value, texts)
        if ok:
            kept.append(item)
        else:
            rejected.append({**item, "reject_reason": reason})
    return kept, rejected
