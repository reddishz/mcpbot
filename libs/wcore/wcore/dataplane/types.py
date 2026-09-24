"""平面框架类型定义（WC-D009）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class Plane(str, Enum):
    """四子平面。"""

    STATE = "state"
    RUNTIME = "runtime"
    CONFIG = "config"
    CONTROL = "control"


@dataclass(frozen=True)
class ControlResult:
    """控制平面统一响应（WC-RUL-011）。"""

    ok: bool
    code: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "code": self.code, "message": self.message}


@dataclass
class ParamSpec:
    """控制端点参数。"""

    name: str
    value_type: type = str
    required: bool = True
    description: str = ""
