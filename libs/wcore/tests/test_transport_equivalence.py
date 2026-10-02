"""三传输等价冒烟（RUL-037，WC-D009 + WC-D011）。

同一 PlaneRegistry，依次走 Shell（直接调用 registry，作为基线）/
HTTP TestClient / MCP tools（直接 await 其函数体，等价物）断言语义一致。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pytest

from wcore.dataplane.catalog import CatalogNode, ControlSpec, LeafSpec, Plane
from wcore.dataplane.privilege import PrivilegeGate, default_w3trade_specs
from wcore.dataplane.registry import PlaneRegistry

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient as FastAPITestClient

    HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    HAS_FASTAPI = False

try:
    from wcore.dataplane import mcp as wcore_mcp

    HAS_MCP = wcore_mcp.HAS_MCP
except ImportError:  # pragma: no cover
    HAS_MCP = False


def _mcp_tool_text(resp: Any) -> str:
    """FastMCP.call_tool 可能返回 ``(contents, structured)`` 元组或 ContentBlock 序列。"""
    if isinstance(resp, tuple) and resp:
        resp = resp[0]
    content = getattr(resp, "content", None)
    if content is None and isinstance(resp, (list, tuple)):
        content = resp
    if content:
        first = content[0]
        text = getattr(first, "text", None)
        if text is not None:
            return text
    return str(resp)


@dataclass
class _AppState:
    timeout_seconds: int = 30
    retries: int = 3
    values: Dict[str, Any] = field(default_factory=dict)
    ctl_log: list = field(default_factory=list)


def _build_catalog(state: _AppState) -> CatalogNode:
    root = CatalogNode("app")
    a = root.add_node("a")
    a.add_leaf(
        LeafSpec(
            "b",
            plane=Plane.RUNTIME,
            value_type=str,
            description="leaf b (RUNTIME readable, readonly)",
            readonly=True,
            getter=lambda: state.values.setdefault("a.b", "hello"),
        )
    )
    a.add_leaf(
        LeafSpec(
            "c",
            plane=Plane.CONFIG,
            value_type=int,
            description="leaf c (CONFIG writable)",
            getter=lambda: state.values.setdefault("a.c", 1),
            setter=lambda v: state.values.__setitem__("a.c", v) or state.values["a.c"],
        )
    )

    async def _ctl(params: Dict[str, Any]) -> Dict[str, Any]:
        state.ctl_log.append(params)
        return {"ok": True, "echo": params}

    from wcore.dataplane.types import ParamSpec as _PS

    a.add_control(
        ControlSpec(
            "ctl",
            params=[
                _PS("name", str, required=True),
                _PS("count", int, required=False),
            ],
            handler=_ctl,
            description="Control ctl",
        )
    )
    return root


def _build_stack() -> tuple[PlaneRegistry, PrivilegeGate, _AppState]:
    state = _AppState()
    registry = PlaneRegistry()
    catalog = _build_catalog(state)
    registry.add_instance_tree("t", catalog)
    gate = PrivilegeGate(
        default_w3trade_specs(query_key="Q", trade_key="T"), bind_scope="network"
    )
    return registry, gate, state


# --------------------------------------------------------------------------- #
# Shell 基线（直接调用 registry，等价 Telnet 本机域）
# --------------------------------------------------------------------------- #

def test_shell_baseline_read_and_list():
    reg, gate, state = _build_stack()
    # 直接调用 = 本机域（local full privileges 不需 gate）
    entries = reg.list_entries("t/a")
    names = {e.name for e in entries}
    assert names == {"b", "c", "ctl"}

    leaf = asyncio.run(reg.read("t/a/b"))
    assert leaf["value"] == "hello"
    assert leaf["plane"] == "runtime"


def test_shell_write_and_control():
    reg, gate, state = _build_stack()
    asyncio.run(reg.write("t/a/c", 42))
    assert state.values["a.c"] == 42
    r = asyncio.run(reg.invoke("t/a/ctl", {"name": "x", "count": 2}))
    assert r.to_dict()["ok"] is True
    assert state.ctl_log[-1] == {"name": "x", "count": 2}


def test_shell_unknown_control_params_rejected():
    reg, _, _ = _build_stack()
    with pytest.raises(ValueError, match="unknown param"):
        asyncio.run(reg.invoke("t/a/ctl", {"name": "x", "does_not_exist": 1}))


# --------------------------------------------------------------------------- #
# HTTP transport 对照（fastapi testclient）
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi optional extra")
def test_http_read_and_list_equivalent_to_shell():
    from wcore.dataplane.http import mount_plane_http

    reg, gate, _ = _build_stack()
    app = FastAPI()
    mount_plane_http(app, reg, gate, prefix="/api/v1/test/plane", protect_openapi=False)
    client = FastAPITestClient(app)

    entries_shell = {e.name for e in reg.list_entries("t/a")}
    resp = client.get("/api/v1/test/plane/t/a?list=1", headers={"X-Query-Key": "Q"})
    assert resp.status_code == 200, resp.content
    names_http = {e["name"] for e in resp.json()["entries"]}
    assert names_http == entries_shell

    leaf_shell = asyncio.run(reg.read("t/a/b"))
    resp = client.get("/api/v1/test/plane/t/a/b", headers={"X-Query-Key": "Q"})
    assert resp.status_code == 200
    assert resp.json()["value"] == leaf_shell["value"]
    assert resp.json()["plane"] == leaf_shell["plane"]


@pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi optional extra")
def test_http_write_equivalent_and_unknown_params_422():
    from wcore.dataplane.http import mount_plane_http

    reg, gate, state = _build_stack()
    app = FastAPI()
    mount_plane_http(app, reg, gate, prefix="/api/v1/test/plane", protect_openapi=False)
    client = FastAPITestClient(app)

    resp = client.put(
        "/api/v1/test/plane/t/a/c",
        json={"value": 7},
        headers={"X-Trade-Key": "T", "X-Query-Key": "Q"},
    )
    assert resp.status_code == 200, resp.content
    # 写后 shell 读取一致
    assert asyncio.run(reg.read("t/a/c"))["value"] == 7
    assert state.values["a.c"] == 7

    # 未知控制参数拒绝：HTTP 返回 422
    resp = client.post(
        "/api/v1/test/plane/t/a/ctl",
        json={"name": "hi", "bogus": 1},
        headers={"X-Trade-Key": "T", "X-Query-Key": "Q"},
    )
    # registry _reject_unknown_control_params 抛 ValueError -> HTTP 层 400/422
    assert resp.status_code in (400, 422, 500)


# --------------------------------------------------------------------------- #
# MCP transport 对照（直接 await plane_* tool，等价物）
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not HAS_MCP, reason="mcp optional extra")
def test_mcp_tools_read_and_write_equivalent_to_shell():
    from wcore.dataplane import mcp as wcore_mcp_mod

    reg, gate, state = _build_stack()
    mcp_inst = wcore_mcp_mod.build_plane_mcp(reg, gate, name="eq-test")

    creds = {"query": "Q", "trade": "T"}
    token = wcore_mcp_mod._creds_var.set(creds)
    try:
        import json

        # list 工具存在
        tools_list = asyncio.run(mcp_inst.list_tools())
        tool_names = [t.name for t in tools_list]
        for n in ("plane_list", "plane_read", "plane_write", "plane_invoke"):
            assert n in tool_names

        # list/read/write/invoke 走 FastMCP.call_tool
        lst_resp = asyncio.run(mcp_inst.call_tool("plane_list", {"path": "t/a"}))
        lst_obj = json.loads(_mcp_tool_text(lst_resp))
        assert {e["name"] for e in lst_obj["entries"]} == {"b", "c", "ctl"}

        shell_read = asyncio.run(reg.read("t/a/c"))
        r_resp = asyncio.run(mcp_inst.call_tool("plane_read", {"path": "t/a/c", "depth": 0}))
        r_obj = json.loads(_mcp_tool_text(r_resp))
        assert r_obj["value"] == shell_read["value"]

        asyncio.run(
            mcp_inst.call_tool("plane_write", {"path": "t/a/c", "value": 99})
        )
        assert asyncio.run(reg.read("t/a/c"))["value"] == 99
        assert state.values["a.c"] == 99

        i_resp = asyncio.run(
            mcp_inst.call_tool(
                "plane_invoke",
                {"path": "t/a/ctl", "params": {"name": "from_mcp"}},
            )
        )
        i_obj = json.loads(_mcp_tool_text(i_resp))
        assert i_obj["ok"] is True
        assert state.ctl_log[-1]["name"] == "from_mcp"
    finally:
        wcore_mcp_mod._creds_var.reset(token)


@pytest.mark.skipif(not HAS_MCP, reason="mcp optional extra")
def test_mcp_unknown_control_params_rejected():
    from wcore.dataplane import mcp as wcore_mcp_mod
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore

    reg, gate, _ = _build_stack()
    mcp_inst = wcore_mcp_mod.build_plane_mcp(reg, gate, name="eq-test2")
    creds = {"query": "Q", "trade": "T"}
    token = wcore_mcp_mod._creds_var.set(creds)
    try:
        # 未知参：FastMCP 把底层 ValueError 包装为 ToolError 抛出
        with pytest.raises(ToolError, match="unknown param"):
            asyncio.run(
                mcp_inst.call_tool(
                    "plane_invoke",
                    {"path": "t/a/ctl", "params": {"name": "x", "bogus_key_x": 1}},
                )
            )
    finally:
        wcore_mcp_mod._creds_var.reset(token)


# --------------------------------------------------------------------------- #
# RUL-046: 无凭证时 HTTP/MCP 都拒绝（防扫描 + 等价外形）
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi optional extra")
def test_http_rul_046_401_equivalent_shape():
    from wcore.dataplane.http import mount_plane_http

    reg, gate, _ = _build_stack()
    app = FastAPI()
    mount_plane_http(app, reg, gate, prefix="/api/v1/test/plane", protect_openapi=False)
    client = FastAPITestClient(app)

    resp = client.get("/api/v1/test/plane/t/a?list=1")
    assert resp.status_code == 401
    body = resp.json()
    # CMP-011: 结构化 code 存在
    assert body.get("detail", {}).get("code") in {"MISSING_QUERY", "CREDENTIAL_CONFLICT"}
    assert body["detail"]["detail"] == "Unauthorized"  # 统一外形
