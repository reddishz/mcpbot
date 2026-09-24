"""控制端点用法摘要（Shell / OpenAPI 共用）。"""

from __future__ import annotations

from typing import List

from .coerce import type_label
from .registry import _ControlLeaf


def control_param_summary(ctrl: _ControlLeaf) -> str:
    if not ctrl.params:
        return "(no params)"
    parts: List[str] = []
    for ps in ctrl.params:
        mark = "" if ps.required else "?"
        parts.append(f"{ps.name}{mark}")
    return " ".join(parts)


def format_control_usage(ctrl: _ControlLeaf, name: str) -> str:
    lines: List[str] = [f"{name} — {ctrl.description or 'control'}"]
    if not ctrl.params:
        lines.append("  (no params)")
        lines.append(f"usage: {name}")
        return "\r\n".join(lines)
    for ps in ctrl.params:
        req = "required" if ps.required else "optional"
        hint = f" — {ps.description}" if ps.description else ""
        lines.append(f"  {ps.name} ({type_label(ps.value_type)}, {req}){hint}")
    usage_args = " ".join(
        f"{ps.name}=<{type_label(ps.value_type)}>" if ps.required else f"[{ps.name}=…]"
        for ps in ctrl.params
    )
    lines.append(f"usage: {name} {usage_args}".rstrip())
    return "\r\n".join(lines)
