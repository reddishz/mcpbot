"""类型 coercion 与 Shell 值展示。"""

from __future__ import annotations

import inspect
import json
import math
import re
from typing import Any, Callable


Getter = Callable[[], Any]
_IDENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


async def resolve_getter_value(getter: Getter) -> Any:
    """执行叶子 getter；若返回协程则 await（与 PlaneRegistry.read 一致）。"""
    value = getter()
    if inspect.isawaitable(value):
        value = await value
    return value


def type_label(value_type: type) -> str:
    if value_type is bool:
        return "bool"
    if value_type is int:
        return "int"
    if value_type is float:
        return "float"
    if value_type is dict:
        return "dict"
    if value_type is list:
        return "list"
    return "str"


def coerce_value(raw: str, value_type: type) -> Any:
    text = raw.strip()
    if value_type is bool:
        low = text.lower()
        if low in ("true", "1", "on", "yes"):
            return True
        if low in ("false", "0", "off", "no"):
            return False
        raise ValueError(f"invalid bool: {raw!r}")
    if value_type is int:
        return int(text)
    if value_type is float:
        return float(text)
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1]
    return text


def coerce_param(raw: Any, value_type: type) -> Any:
    if isinstance(raw, str):
        return coerce_value(raw, value_type)
    if value_type is bool and isinstance(raw, (int, float)):
        return bool(raw)
    if value_type is int:
        return int(raw)
    if value_type is float:
        return float(raw)
    return raw


def format_float(value: float) -> str:
    """Shell/日志展示用：按量级保留有效小数，去掉多余尾零。"""
    if not math.isfinite(value):
        return str(value)
    ival = round(value)
    if abs(value - ival) < 1e-9 and abs(ival) < 1e15:
        return str(int(ival))
    abs_v = abs(value)
    if abs_v >= 100:
        text = f"{value:.2f}"
    elif abs_v >= 1:
        text = f"{value:.4f}"
    else:
        text = f"{value:.6f}"
    return text.rstrip("0").rstrip(".") or "0"


def format_value(value: Any, *, compact: bool = False) -> str:
    """Shell 展示。

    单项（读/写/invoke）默认展开嵌套，叶子容器仍保持一行。
    批量列举（ls）传 compact=True，整棵结构压成一行。
    """
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format_float(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        if compact:
            return _format_inline(value)
        return _format_structured(value)
    return str(value)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _is_leaf_container(value: Any) -> bool:
    if isinstance(value, dict):
        return all(_is_scalar(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_is_scalar(v) for v in value)
    return False


def _format_atom(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format_float(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if value == "":
            return '""'
        if any(ch in value for ch in " \t\n\"'"):
            return json.dumps(value, ensure_ascii=False)
        return value
    return str(value)


def _format_key(key: Any) -> str:
    text = str(key)
    if _IDENT_KEY.match(text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _format_inline(value: Any) -> str:
    """任意深度的 dict/list 全部压成一行，供 ls 等批量列举使用。"""
    if isinstance(value, dict):
        inner = ", ".join(f"{_format_key(k)}: {_format_inline(v)}" for k, v in value.items())
        return "{" + inner + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_inline(v) for v in value) + "]"
    return _format_atom(value)


def _format_structured(value: Any, level: int = 0) -> str:
    """嵌套则每个子项一行；子项本身若是叶子容器则保持单行。"""
    pad = "  " * level
    inner = "  " * (level + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        if _is_leaf_container(value):
            return _format_inline(value)
        items = list(value.items())
        lines = ["{"]
        for i, (key, child) in enumerate(items):
            comma = "," if i < len(items) - 1 else ""
            rendered = _format_structured(child, level + 1)
            lines.append(f"{inner}{_format_key(key)}: {rendered}{comma}")
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    if isinstance(value, (list, tuple)):
        items = list(value)
        if not items:
            return "[]"
        if _is_leaf_container(items):
            return _format_inline(items)
        lines = ["["]
        for i, child in enumerate(items):
            comma = "," if i < len(items) - 1 else ""
            rendered = _format_structured(child, level + 1)
            lines.append(f"{inner}{rendered}{comma}")
        lines.append(f"{pad}]")
        return "\n".join(lines)
    return _format_atom(value)
