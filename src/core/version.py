"""框架版本 + 插件依赖（requires）校验。

插件在 `Feature(..., requires=">=1.0,<2.0")` 里声明它兼容的**插件协议版本范围**；
加载器会在导入插件后校验，不满足就抛错并拒绝加载（避免插件与框架不兼容时炸在运行期）。

支持的条件写法（逗号或空格分隔，多个条件之间是「且」）：

- ``>=1.0`` / ``>1.0`` / ``<=1.0`` / ``<1.0`` / ``==1.0`` / ``!=1.0``
- ``^1.2`` 等价 ``>=1.2,<2.0``；``~1.2`` 等价 ``>=1.2,<1.3``
- 省略运算符时按 ``==`` 处理；空串表示不限制；**写错的条件会被忽略**（不拒绝插件）。

版本号按点分数字比较（``1.0`` < ``1.2`` < ``1.10``），非数字后缀（如 ``1.0.0-beta``）
只取数字段参与比较。
"""

from __future__ import annotations

# 插件协议版本：插件 `requires` 校验的就是它
FRAMEWORK_VERSION = "1.0.0"
DEFAULT_REQUIRES = ">=1.0,<2.0"

_OPERATORS = (">=", "<=", "==", "!=", ">", "<", "^", "~")


def parse_version(text: str) -> tuple[int, ...]:
    """把版本号解析成数字元组（忽略非数字后缀）。

    完全解析不出数字时抛 `ValueError`（调用方决定是忽略还是报错）。
    """
    parts: list[int] = []
    for chunk in str(text).strip().split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits == "":
            break
        parts.append(int(digits))
    if not parts:
        raise ValueError(f"无法解析版本号：{text!r}")
    return tuple(parts)


def _compare(version: tuple[int, ...], other: tuple[int, ...]) -> int:
    """比较两个版本元组，返回 -1 / 0 / 1（短的按 0 补齐）。"""
    length = max(len(version), len(other))
    left = version + (0,) * (length - len(version))
    right = other + (0,) * (length - len(other))
    return (left > right) - (left < right)


def _check_one(condition: str, version: tuple[int, ...]) -> bool:
    condition = condition.strip()
    if not condition:
        return True

    op = ""
    for candidate in _OPERATORS:
        if condition.startswith(candidate):
            op = candidate
            condition = condition[len(candidate) :].strip()
            break
    if not op:
        op = "=="

    target = parse_version(condition)
    if op == ">=":
        return _compare(version, target) >= 0
    if op == ">":
        return _compare(version, target) > 0
    if op == "<=":
        return _compare(version, target) <= 0
    if op == "<":
        return _compare(version, target) < 0
    if op == "!=":
        return _compare(version, target) != 0
    if op == "^":
        upper = (target[0] + 1,) if target else (1,)
        return _compare(version, target) >= 0 and _compare(version, upper) < 0
    if op == "~":
        upper = list(target)
        if len(upper) >= 2:
            upper[1] += 1
            upper = tuple(upper[:2])
        else:
            upper = (upper[0] + 1,)
        return _compare(version, target) >= 0 and _compare(version, upper) < 0
    return _compare(version, target) == 0


def satisfies(requires: str, version: str = FRAMEWORK_VERSION) -> bool:
    """判断 `version` 是否满足 `requires` 声明的范围。

    单个条件**写法非法**（版本号解析不出来）时忽略该条件而不是拒绝插件
    —— 元信息写错不该导致插件加载失败。
    """
    text = (requires or "").replace(" ", ",")
    conditions = [c for c in text.split(",") if c.strip()]
    if not conditions:
        return True
    parsed = parse_version(version)
    for condition in conditions:
        try:
            if not _check_one(condition, parsed):
                return False
        except ValueError:  # 条件写错（如 "abc"）→ 跳过这一条
            continue
        except Exception:  # noqa: BLE001 - 其它意外也不该让框架崩
            continue
    return True


def check_requires(requires: str, version: str = FRAMEWORK_VERSION) -> tuple[bool, str]:
    """返回 (是否通过, 说明文字)，用于启动日志 / 报错。"""
    ok = satisfies(requires, version)
    if ok:
        return True, f"兼容（框架版本 {version}，要求 {requires or '不限'}）"
    return False, (
        f"框架版本 {version} 不满足插件要求 {requires}，"
        f"请升级框架或改用兼容插件"
    )
