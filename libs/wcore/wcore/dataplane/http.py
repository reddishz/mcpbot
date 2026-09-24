"""平面 HTTP 传输（WC-D009 / WC-DEC-024）。可选依赖 FastAPI。"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

try:
    from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
    from fastapi.responses import JSONResponse

    HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    HAS_FASTAPI = False

from wcore.dataplane.privilege import Deny, PrivilegeGate
from wcore.dataplane.registry import EntryMeta, PlaneRegistry

logger = logging.getLogger(__name__)

PLANE_API_TAG = "plane"

DEFAULT_PLANE_DESCRIPTION = """
## Plane HTTP

Canonical plane REST over theme/endpoint paths (WC-D009).

### Auth (WC-D012)

- GET (list/read): `X-Query-Key` (or Bearer → query)
- PUT / POST: `X-Trade-Key`
"""

PLANE_GET_SUMMARY = "Read plane path (leaf, directory discovery, or depth subtree)"
PLANE_PUT_SUMMARY = "Write config/runtime leaf"
PLANE_POST_SUMMARY = "Invoke control endpoint"


def _entry_dict(entry: EntryMeta) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "name": entry.name,
        "kind": entry.kind,
        "path": entry.path,
        "access": list(entry.access),
    }
    if entry.plane is not None:
        data["plane"] = entry.plane
    if entry.value_type is not None:
        data["type"] = entry.value_type
    if entry.description:
        data["description"] = entry.description
    if entry.kind == "leaf":
        data["readonly"] = entry.readonly
    if entry.kind == "control" and entry.params:
        data["params"] = list(entry.params)
    return data


def _format_list(registry: PlaneRegistry, path: str) -> Dict[str, Any]:
    canonical = path.strip("/")
    entries = registry.list_entries(path)
    return {
        "path": canonical or "/",
        "kind": "dir",
        "entries": [_entry_dict(e) for e in entries],
    }


def _not_found(registry: PlaneRegistry, path: str, segment: str) -> "HTTPException":
    return HTTPException(
        status_code=404,
        detail={
            "code": "NOT_FOUND",
            "path": path.strip("/") or "/",
            "segment": segment,
            "hint": registry.path_hint(path, segment),
        },
    )


def create_plane_router(
    registry: PlaneRegistry,
    gate: PrivilegeGate,
    *,
    check_write_rate: Optional[Callable[[str], bool]] = None,
    description: str = DEFAULT_PLANE_DESCRIPTION,
    tag: str = PLANE_API_TAG,
) -> Any:
    """创建平面 APIRouter；鉴权经 PrivilegeGate（操作映射）。"""
    if not HAS_FASTAPI:
        raise RuntimeError("FastAPI required for plane HTTP (optional extra)")

    list_q = Query(
        0,
        alias="list",
        description="Set to 1 to list immediate children.",
    )
    depth_q = Query(0, ge=0, description="Sub-tree expansion depth.")
    query_hdr = Header(None, alias="X-Query-Key", description="query privilege key")
    trade_hdr = Header(None, alias="X-Trade-Key", description="trade privilege key")
    auth_hdr = Header(None, alias="Authorization", description="Bearer → query")

    router = APIRouter(tags=[tag], redirect_slashes=False)

    def _authorize(
        operation_kind: str,
        *,
        x_query_key: Optional[str],
        x_trade_key: Optional[str],
        authorization: Optional[str],
        path_for_rate: str = "",
    ) -> None:
        if operation_kind in ("write", "invoke") and check_write_rate is not None:
            if not check_write_rate(path_for_rate):
                raise HTTPException(status_code=429, detail="Too many requests")
        headers = {}
        if x_query_key is not None:
            headers["X-Query-Key"] = x_query_key
        if x_trade_key is not None:
            headers["X-Trade-Key"] = x_trade_key
        creds = gate.extract_http_credentials(headers, authorization=authorization)
        pset = gate.resolve(creds)
        outcome = gate.require_op(pset, operation_kind)
        if isinstance(outcome, Deny):
            raise HTTPException(status_code=401, detail="Unauthorized")

    async def plane_get(
        full_path: str = "",
        list: int = list_q,
        depth: int = depth_q,
        x_query_key: Optional[str] = query_hdr,
        authorization: Optional[str] = auth_hdr,
    ):
        path = full_path.strip("/")
        kind = registry.inspect_path(path)
        if list or (depth == 0 and kind in ("root", "dir")):
            op = "list"
        else:
            op = "read"
        _authorize(op, x_query_key=x_query_key, x_trade_key=None, authorization=authorization)

        if list:
            if kind == "missing":
                raise _not_found(registry, path, registry.missing_segment(path))
            return _format_list(registry, path)

        if depth == 0 and kind in ("root", "dir"):
            return _format_list(registry, path)

        if kind == "missing":
            raise _not_found(registry, path, registry.missing_segment(path))

        try:
            return await registry.read(path, depth=depth)
        except KeyError as exc:
            raise _not_found(registry, path, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=405,
                detail={
                    "code": "METHOD_NOT_ALLOWED",
                    "path": path,
                    "message": str(exc),
                    "hint": (
                        "state endpoints are read-only; control endpoints require POST invoke; "
                        "discover POST targets with list=1 under the theme."
                    ),
                },
            ) from exc

    async def plane_put(
        full_path: str,
        body: Dict[str, Any] = Body(...),
        x_trade_key: Optional[str] = trade_hdr,
        x_query_key: Optional[str] = query_hdr,
        authorization: Optional[str] = auth_hdr,
    ):
        path = full_path.strip("/")
        _authorize(
            "write",
            x_query_key=x_query_key,
            x_trade_key=x_trade_key,
            authorization=authorization,
            path_for_rate=path,
        )
        if "value" not in body:
            raise HTTPException(status_code=422, detail="body must contain value")
        if "at" in body or "after" in body:
            raise HTTPException(
                status_code=422,
                detail="at/after belong to POST pause, not PUT",
            )
        try:
            return await registry.write(path, body["value"])
        except KeyError as exc:
            raise _not_found(registry, path, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=405,
                detail={"code": "METHOD_NOT_ALLOWED", "path": path, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def plane_post(
        full_path: str,
        body: Optional[Dict[str, Any]] = Body(default=None),
        x_trade_key: Optional[str] = trade_hdr,
        x_query_key: Optional[str] = query_hdr,
        authorization: Optional[str] = auth_hdr,
    ):
        path = full_path.strip("/")
        _authorize(
            "invoke",
            x_query_key=x_query_key,
            x_trade_key=x_trade_key,
            authorization=authorization,
            path_for_rate=path,
        )
        try:
            result = await registry.invoke(path, body or {})
        except KeyError as exc:
            raise _not_found(registry, path, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("plane invoke failed: %s", path)
            return JSONResponse(
                status_code=500,
                content={"ok": False, "code": "ERROR", "message": str(exc)},
            )
        status = 200 if result.ok else 400
        return JSONResponse(status_code=status, content=result.to_dict())

    for route_path in ("", "/"):
        router.add_api_route(
            route_path,
            plane_get,
            methods=["GET"],
            summary=PLANE_GET_SUMMARY,
            description=description,
            responses={
                200: {"description": "Leaf value, directory listing, or depth subtree"},
                401: {"description": "Unauthorized"},
                404: {"description": "Unknown path segment; body includes hint"},
                405: {"description": "Plane forbids read"},
            },
        )
    router.add_api_route(
        "/{full_path:path}",
        plane_get,
        methods=["GET"],
        summary=PLANE_GET_SUMMARY,
        description=description,
        responses={
            200: {"description": "Leaf value, directory listing, or depth subtree"},
            401: {"description": "Unauthorized"},
            404: {"description": "Unknown path segment; body includes hint"},
            405: {"description": "Plane forbids read"},
        },
    )
    router.add_api_route(
        "/{full_path:path}",
        plane_put,
        methods=["PUT"],
        summary=PLANE_PUT_SUMMARY,
        description='Write a config or runtime leaf. Body MUST be `{"value": <typed>}`.',
        responses={
            200: {"description": "Updated leaf value"},
            401: {"description": "Unauthorized"},
            404: {"description": "Unknown path"},
            405: {"description": "Read-only or wrong plane"},
            429: {"description": "Write rate limit"},
        },
    )
    router.add_api_route(
        "/{full_path:path}",
        plane_post,
        methods=["POST"],
        summary=PLANE_POST_SUMMARY,
        description="Invoke a control endpoint. Body is a JSON object of parameters.",
        responses={
            200: {"description": "Control succeeded"},
            400: {"description": "Control rejected (ok=false)"},
            401: {"description": "Unauthorized"},
            404: {"description": "Unknown control path"},
            429: {"description": "Write rate limit"},
        },
    )
    return router


def mount_plane_http(
    app: Any,
    registry: PlaneRegistry,
    gate: PrivilegeGate,
    *,
    prefix: str,
    check_write_rate: Optional[Callable[[str], bool]] = None,
    description: str = DEFAULT_PLANE_DESCRIPTION,
    protect_openapi: bool = True,
) -> Any:
    """挂载平面路由；可选保护 OpenAPI（须 query）。"""
    router = create_plane_router(
        registry,
        gate,
        check_write_rate=check_write_rate,
        description=description,
    )
    app.include_router(router, prefix=prefix.rstrip("/") or prefix)
    if protect_openapi:
        install_openapi_privilege_gate(app, gate)
    return router


def install_openapi_privilege_gate(app: Any, gate: PrivilegeGate) -> None:
    """网络域下 OpenAPI / docs 须持有 query（WC-RUL-046）。"""
    if not HAS_FASTAPI:
        return

    @app.middleware("http")
    async def _openapi_gate(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path.endswith("/openapi.json") or path in ("/docs", "/redoc") or path.endswith("/docs") or path.endswith("/redoc"):
            headers = {k: v for k, v in request.headers.items()}
            creds = gate.extract_http_credentials(
                headers,
                authorization=request.headers.get("authorization"),
            )
            pset = gate.resolve(creds)
            if isinstance(gate.require_op(pset, "openapi"), Deny):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        return await call_next(request)
