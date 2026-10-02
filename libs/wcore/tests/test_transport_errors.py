"""EXT-MCP-07 最小错误码表测例。"""

from __future__ import annotations

import asyncio
import json

import pytest

from wcore.dataplane.privilege import Deny
from wcore.dataplane.transport_errors import (
    CODE_METHOD_NOT_ALLOWED,
    CODE_MISSING_QUERY,
    CODE_MISSING_TRADE,
    CODE_NOT_FOUND,
    CODE_UNKNOWN_PARAM,
    deny_to_payload,
    registry_exc_to_payload,
)


def test_deny_to_payload_codes():
    assert deny_to_payload(Deny("missing_privilege:query"))["code"] == CODE_MISSING_QUERY
    assert deny_to_payload(Deny("missing_privilege:trade"))["code"] == CODE_MISSING_TRADE
    assert deny_to_payload(Deny("credential_conflict:query"))["status"] == 401


def test_registry_exc_codes():
    assert registry_exc_to_payload(KeyError("x"), path="a/b")["code"] == CODE_NOT_FOUND
    assert (
        registry_exc_to_payload(PermissionError("readonly"), path="a/b")["code"]
        == CODE_METHOD_NOT_ALLOWED
    )
    assert (
        registry_exc_to_payload(ValueError("unknown param: z"), path="a/ctl")["code"]
        == CODE_UNKNOWN_PARAM
    )


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("mcp") is None,
    reason="mcp optional",
)
def test_mcp_tool_surfaces_not_found_code():
    from wcore.dataplane import mcp as wcore_mcp_mod
    from mcp.server.fastmcp.exceptions import ToolError

    from test_transport_equivalence import _build_stack

    reg, gate, _ = _build_stack()
    mcp_inst = wcore_mcp_mod.build_plane_mcp(reg, gate, name="err-test")
    token = wcore_mcp_mod._creds_var.set({"query": "Q", "trade": "T"})
    try:
        with pytest.raises(ToolError) as ei:
            asyncio.run(mcp_inst.call_tool("plane_read", {"path": "no/such/leaf"}))
        text = str(ei.value)
        start = text.find("{")
        assert start >= 0
        payload = json.loads(text[start:])
        assert payload["code"] == CODE_NOT_FOUND
        assert payload["status"] == 404
    finally:
        wcore_mcp_mod._creds_var.reset(token)


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("mcp") is None,
    reason="mcp optional",
)
def test_mcp_tool_surfaces_unknown_param_code():
    from wcore.dataplane import mcp as wcore_mcp_mod
    from mcp.server.fastmcp.exceptions import ToolError

    from test_transport_equivalence import _build_stack

    reg, gate, _ = _build_stack()
    mcp_inst = wcore_mcp_mod.build_plane_mcp(reg, gate, name="err-test2")
    token = wcore_mcp_mod._creds_var.set({"query": "Q", "trade": "T"})
    try:
        with pytest.raises(ToolError) as ei:
            asyncio.run(
                mcp_inst.call_tool(
                    "plane_invoke",
                    {"path": "t/a/ctl", "params": {"name": "x", "bogus_key_x": 1}},
                )
            )
        text = str(ei.value)
        start = text.find("{")
        payload = json.loads(text[start:])
        assert payload["code"] == CODE_UNKNOWN_PARAM
        assert payload["status"] == 422
    finally:
        wcore_mcp_mod._creds_var.reset(token)
