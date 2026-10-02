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

from wcore.dataplane.privilege import (
    AccessContext,
    Deny,
    PrivilegeGate,
    leaf_visible,
    resolve_client_ip,
)
from wcore.dataplane.registry import PlaneRegistry
from wcore.dataplane.transport_errors import (
    mcp_error_from_deny,
    mcp_error_from_exc,
)

logger = logging.getLogger(__name__)

_request_var: contextvars.ContextVar[Mapping[str, str]] = contextvars.ContextVar(
    "wcore_mcp_request", default={}
)

try:
    from mcp.server.fastmcp import FastMCP
    from starlette.types import ASGIApp, Receive, Scope, Send

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False
    FastMCP = Any  # type: ignore[misc, assignment]


def _require(
    gate: PrivilegeGate,
    registry: PlaneRegistry,
    operation_kind: str,
    path: str,
) -> None:
    ctx = dict(_request_var.get() or {})
    declared = registry.declared_scopes(path, operation_kind)
    required = gate.required_scopes(operation_kind, declared=declared)
    if isinstance(required, Deny):
        raise mcp_error_from_deny(required)
    if not required:
        return
    outcome = gate.enforce(
        ctx.get("authorization"),
        required,
        access=AccessContext(
            client_ip=ctx.get("client_ip", ""),
            peer=ctx.get("peer", ""),
            permission=",".join(required),
            api=f"mcp {operation_kind} {path or '/'}",
        ),
    )
    if isinstance(outcome, Deny):
        raise mcp_error_from_deny(outcome)


def build_plane_mcp(
    registry: PlaneRegistry,
    gate: PrivilegeGate,
    *,
    name: str = "wcore-plane",
) -> Any:
    """构造 FastMCP 实例（四操作 tools）。

    @satisfies WC-CMP-001 / WC-R025 / WC-R026
    """
    if not HAS_MCP:
        raise RuntimeError("mcp package required for MCP plane (optional extra)")

    mcp = FastMCP(name)

    @mcp.tool(description="List plane directory children (operation: list)")
    async def plane_list(path: str = "") -> Dict[str, Any]:
        _require(gate, registry, "list", path)
        try:
            entries = registry.list_entries(path)
        except (KeyError, PermissionError, ValueError) as exc:
            raise mcp_error_from_exc(exc, path=path) from exc
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
        _require(gate, registry, "read", path)
        try:
            held = gate.held(_request_var.get().get("authorization"))
            return await registry.read(
                path,
                depth=depth,
                can_read_leaf=lambda scopes: leaf_visible(held, scopes),
            )
        except (KeyError, PermissionError, ValueError) as exc:
            raise mcp_error_from_exc(exc, path=path) from exc

    @mcp.tool(description="Write config/runtime leaf (operation: write)")
    async def plane_write(path: str, value: Any) -> Any:
        _require(gate, registry, "write", path)
        try:
            return await registry.write(path, value)
        except (KeyError, PermissionError, ValueError) as exc:
            raise mcp_error_from_exc(exc, path=path) from exc

    @mcp.tool(description="Invoke control endpoint (operation: invoke)")
    async def plane_invoke(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        _require(gate, registry, "invoke", path)
        try:
            result = await registry.invoke(path, params or {})
        except (KeyError, PermissionError, ValueError) as exc:
            raise mcp_error_from_exc(exc, path=path) from exc
        return result.to_dict()

    return mcp


class PrivilegeGateASGIMiddleware:
    """把本次 HTTP 的 Bearer 与来源地址放进上下文。

    initialize / tools/list 不在这里鉴权（只暴露四个固定 tool 名）。
    具体路径的 scope 在 tool 内按与 HTTP 相同的声明判定。
    """

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
        client = scope.get("client")
        peer = ""
        if isinstance(client, (list, tuple)) and client:
            peer = str(client[0] or "")
        client_ip, peer = resolve_client_ip(header_map, peer)
        token = _request_var.set(
            {
                "authorization": header_map.get("authorization", ""),
                "client_ip": client_ip,
                "peer": peer,
            }
        )
        try:
            await self.app(scope, receive, send)
        finally:
            _request_var.reset(token)


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
    strict: bool = False,
) -> tuple:
    """挂载 MCP 到 ``prefix``（如 ``/api/v1/w3trade/mcp``）。

    @satisfies WC-DEC-018 / WC-DEC-017

    Args:
        strict: 为 True 时强制 prefix 匹配 ``/api/v1/{app}/mcp``；
            避免挂载到扫描高频 ``/mcp`` 别名路径（WC-DEC-018 MUST）。

    Returns:
        ``(mcp, session_manager)`` — 宿主 lifespan **MUST**
        ``async with session_manager.run()``.
    """
    if not HAS_MCP:
        raise RuntimeError("mcp package required for MCP plane (optional extra)")

    import re

    if strict:
        if not re.fullmatch(r"/api/v1/[^/]+/mcp", prefix.rstrip("/")):
            raise ValueError(
                "WC-DEC-018: When strict=True, prefix MUST be /api/v1/{app}/mcp. "
                f"Got {prefix!r} (MUST NOT alias to /mcp or other paths)."
            )

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
