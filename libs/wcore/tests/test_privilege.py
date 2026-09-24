"""PrivilegeGate 单元测试（WC-D012）。"""

from __future__ import annotations

import logging

import pytest

from wcore.dataplane.privilege import (
    Allow,
    Deny,
    PrivilegeGate,
    PrivilegeSpec,
    default_w3trade_specs,
)


def test_ensure_keys_generates_when_empty(caplog):
    caplog.set_level(logging.WARNING)
    gate = PrivilegeGate(default_w3trade_specs(query_key="", trade_key=""))
    assert gate.specs["query"].expected
    assert gate.specs["trade"].expected
    assert "generated privilege key" in caplog.text


def test_network_resolve_and_require_ops():
    gate = PrivilegeGate(
        default_w3trade_specs(query_key="q", trade_key="t"),
        bind_scope="network",
    )
    empty = gate.resolve({})
    assert isinstance(gate.require_op(empty, "read"), Deny)
    assert isinstance(gate.require_op(empty, "write"), Deny)

    with_q = gate.resolve({"query": "q"})
    assert gate.require_op(with_q, "read") is Allow
    assert isinstance(gate.require_op(with_q, "write"), Deny)

    with_t = gate.resolve({"trade": "t"})
    assert isinstance(gate.require_op(with_t, "read"), Deny)
    assert gate.require_op(with_t, "write") is Allow
    assert gate.require_op(with_t, "invoke") is Allow


def test_local_full_privileges():
    gate = PrivilegeGate(
        default_w3trade_specs(query_key="q", trade_key="t"),
        bind_scope="local",
    )
    pset = gate.resolve({})
    assert pset.can_all("query", "trade")
    assert gate.require_op(pset, "openapi") is Allow


def test_bearer_maps_to_query():
    gate = PrivilegeGate(
        default_w3trade_specs(query_key="secret", trade_key="t"),
        bind_scope="network",
    )
    creds = gate.extract_http_credentials({}, authorization="Bearer secret")
    pset = gate.resolve(creds)
    assert pset.can("query")
    assert not pset.can("trade")


def test_bearer_header_conflict_denies_query():
    gate = PrivilegeGate(
        default_w3trade_specs(query_key="secret", trade_key="t"),
        bind_scope="network",
    )
    creds = gate.extract_http_credentials(
        {"X-Query-Key": "other"},
        authorization="Bearer secret",
    )
    pset = gate.resolve(creds)
    assert not pset.can("query")


def test_wrong_length_key_denied():
    gate = PrivilegeGate(
        default_w3trade_specs(query_key="abcd", trade_key="t"),
        bind_scope="network",
    )
    pset = gate.resolve({"query": "ab"})
    assert not pset.can("query")


def test_query_required_in_specs():
    with pytest.raises(ValueError, match="query"):
        PrivilegeGate([PrivilegeSpec("trade", expected="t", header="X-Trade-Key")])
