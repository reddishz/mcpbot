"""传输侧错误呈报（WC-CMP-011 / EXT-MCP-07 最小集）。

判定仍在 PrivilegeGate / Registry；本模块只做 Deny 与 Registry 异常 → 稳定 client code。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from wcore.dataplane.privilege import Deny

# 权限类（与 HTTP/MCP 共用）
CODE_MISSING_QUERY = "MISSING_QUERY"
CODE_MISSING_TRADE = "MISSING_TRADE"
CODE_CREDENTIAL_CONFLICT = "CREDENTIAL_CONFLICT"
CODE_UNKNOWN_OPERATION = "UNKNOWN_OPERATION"
CODE_FORBIDDEN = "FORBIDDEN"

# 平面业务类（对齐 HTTP 既有 / WC-RUL-011 方向）
CODE_NOT_FOUND = "NOT_FOUND"
CODE_METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
CODE_UNKNOWN_PARAM = "UNKNOWN_PARAM"
CODE_VALIDATION_ERROR = "VALIDATION_ERROR"
CODE_ERROR = "ERROR"
CODE_RATE_LIMITED = "RATE_LIMITED"


def deny_to_payload(outcome: Deny) -> Dict[str, Any]:
    """Deny(reason_code) → 传输无关 payload。"""
    code = outcome.reason_code
    if code.startswith("missing_privilege:query"):
        return {
            "code": CODE_MISSING_QUERY,
            "status": 401,
            "detail": "Unauthorized",
            "reason": code,
        }
    if code.startswith("credential_conflict:"):
        return {
            "code": CODE_CREDENTIAL_CONFLICT,
            "status": 401,
            "detail": "Unauthorized",
            "reason": code,
        }
    if code.startswith("missing_privilege:trade"):
        return {
            "code": CODE_MISSING_TRADE,
            "status": 403,
            "detail": "Forbidden",
            "reason": code,
        }
    if code.startswith("unknown_operation:"):
        return {
            "code": CODE_UNKNOWN_OPERATION,
            "status": 400,
            "detail": "Bad Request",
            "reason": code,
        }
    return {
        "code": CODE_FORBIDDEN,
        "status": 403,
        "detail": "Forbidden",
        "reason": code,
    }


def registry_exc_to_payload(
    exc: BaseException,
    *,
    path: str = "",
) -> Dict[str, Any]:
    """Registry / coerce 异常 → 稳定 code（EXT-MCP-07 业务最小集）。"""
    if isinstance(exc, KeyError):
        return {
            "code": CODE_NOT_FOUND,
            "status": 404,
            "message": str(exc) or "not found",
            "path": path.strip("/") or "/",
            "segment": str(exc.args[0]) if exc.args else "",
        }
    if isinstance(exc, PermissionError):
        return {
            "code": CODE_METHOD_NOT_ALLOWED,
            "status": 405,
            "message": str(exc),
            "path": path.strip("/") or "/",
        }
    if isinstance(exc, ValueError):
        msg = str(exc)
        client = (
            CODE_UNKNOWN_PARAM
            if msg.lower().startswith("unknown param")
            else CODE_VALIDATION_ERROR
        )
        return {
            "code": client,
            "status": 422,
            "message": msg,
            "path": path.strip("/") or "/",
        }
    return {
        "code": CODE_ERROR,
        "status": 500,
        "message": str(exc),
        "path": path.strip("/") or "/",
    }


def payload_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def mcp_error_from_deny(outcome: Deny) -> PermissionError:
    return PermissionError(payload_json(deny_to_payload(outcome)))


def mcp_error_from_exc(exc: BaseException, *, path: str = "") -> PermissionError:
    return PermissionError(payload_json(registry_exc_to_payload(exc, path=path)))
