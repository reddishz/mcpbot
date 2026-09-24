"""MCP 平面适配（WC-D011）。可选依赖 ``mcp``；Streamable HTTP。

实现取舍：
- 暴露 plane_list / plane_read / plane_write / plane_invoke 四工具（操作模型），
  避免 Catalog→tools 一对一爆炸（RSK-001）。
- 凭证经 ASGI 中间件写入 contextvars，供 tool 内 ``require_op``。
- 宿主 **MUST** 在 lifespan 中 ``async with session_manager.run()``。
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Mapping, Optional

from wcore.dataplane.privilege import Deny, PrivilegeGate
from wcore.dataplane.registry import PlaneRegistry

logger = logging.getLogger(__name__)

_creds_var: contextvars.ContextVar[Mapping[str, Optional[str]]] = contextvars.ContextVar(
    "wcore_mcp_creds", default={}
)

try:
    from mcp.server.fastmcp import FastMCP
    from starlette.responses import JSONResponse
    from starlette.types import ASGIApp, Receive, Scope, Send

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False
    FastMCP = Any  # type: ignore[misc, assignment]


def _require(gate: PrivilegeGate, operation_kind: str) -> None:
    pset = gate.resolve(_creds_var.get())
    outcome = gate.require_op(pset, operation_kind)
    if isinstance(outcome, Deny):
        raise PermissionError(outcome.reason_code)


def build_plane_mcp(
    registry: PlaneRegistry,
    gate: PrivilegeGate,
    *,
    name: str = "wcore-plane",
) -> Any:
    """构造 FastMCP 实例（四操作 tools）。"""
    if not HAS_MCP:
        raise RuntimeError("mcp package required for MCP plane (optional extra)")

    mcp = FastMCP(name)

    @mcp.tool(description="List plane directory children (operation: list)")
    async def plane_list(path: str = "") -> Dict[str, Any]:
        _require(gate, "list")
        entries = registry.list_entries(path)
        return {
            "path": path.strip("/") or "/",
            "kind": "dir",
            "entries": [
                {
                    "name": e.name,
                    "kind": e.kind,
                    "path": e.path,
                    "plane": e.plane,
                    "access": list(e.access),
                }
                for e in entries
            ],
        }

    @mcp.tool(description="Read plane leaf or subtree (operation: read)")
    async def plane_read(path: str, depth: int = 0) -> Any:
        _require(gate, "read")
        return await registry.read(path, depth=depth)

    @mcp.tool(description="Write config/runtime leaf (operation: write)")
    async def plane_write(path: str, value: Any) -> Any:
        _require(gate, "write")
        return await registry.write(path, value)

    @mcp.tool(description="Invoke control endpoint (operation: invoke)")
    async def plane_invoke(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        _require(gate, "invoke")
        result = await registry.invoke(path, params or {})
        return result.to_dict()

    return mcp


class PrivilegeGateASGIMiddleware:
    """解析凭证入 contextvars；无 query 则 401（防枚举）。"""

    def __init__(self, app: "ASGIApp", gate: PrivilegeGate) -> None:
        self.app = app
        self.gate = gate

    async def __call__(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header_map = {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in scope.get("headers", [])
        }
        creds = self.gate.extract_http_credentials(
            header_map,
            authorization=header_map.get("authorization"),
        )
        token = _creds_var.set(creds)
        try:
            pset = self.gate.resolve(creds)
            if isinstance(self.gate.require_op(pset, "list"), Deny):
                resp = JSONResponse({"detail": "Unauthorized"}, status_code=401)
                await resp(scope, receive, send)
                return
            await self.app(scope, receive, send)
        finally:
            _creds_var.reset(token)


class RewriteExactPrefixSlash:
    """Starlette ``Mount`` 只匹配 ``prefix/``；把正式路径 ``prefix`` 改写为 ``prefix/``。

    对齐 WC-DEC-018（正式 URL 无强制尾斜杠）。
    """

    def __init__(self, app: "ASGIApp", prefix: str) -> None:
        self.app = app
        self.prefix = prefix.rstrip("/") or "/"

    async def __call__(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        if scope["type"] == "http" and scope.get("path") == self.prefix:
            scope = dict(scope)
            scope["path"] = self.prefix + "/"
            raw = scope.get("raw_path")
            if isinstance(raw, (bytes, bytearray)):
                scope["raw_path"] = (self.prefix + "/").encode("ascii")
        await self.app(scope, receive, send)


def mount_mcp_http(
    app: Any,
    registry: PlaneRegistry,
    gate: PrivilegeGate,
    *,
    prefix: str,
    name: str = "wcore-plane",
) -> tuple:
    """挂载 MCP 到 ``prefix``（如 ``/api/v1/w3trade/mcp``）。

    Returns:
        ``(mcp, session_manager)`` — 宿主 lifespan **MUST**
        ``async with session_manager.run()``.
    """
    if not HAS_MCP:
        raise RuntimeError("mcp package required for MCP plane (optional extra)")

    base = prefix.rstrip("/") or "/"
    mcp = build_plane_mcp(registry, gate, name=name)
    mcp.settings.streamable_http_path = "/"
    mcp.settings.stateless_http = True
    starlette_app = mcp.streamable_http_app()
    wrapped = PrivilegeGateASGIMiddleware(starlette_app, gate)
    app.mount(base, wrapped)
    # 最外层改写：无尾斜杠正式路径亦可进入 Mount
    app.add_middleware(RewriteExactPrefixSlash, prefix=base)
    return mcp, mcp.session_manager


@asynccontextmanager
async def mcp_session_lifespan(session_manager: Any) -> AsyncIterator[None]:
    async with session_manager.run():
        yield
