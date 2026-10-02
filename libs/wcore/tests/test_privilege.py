"""PrivilegeGate 与凭证文件。"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from wcore.dataplane.privilege import (
    PLANE_ADMIN,
    PLANE_READ,
    PLANE_WRITE,
    Allow,
    Deny,
    PrivilegeGate,
    TokenStore,
    mask_secret,
    resolve_client_ip,
)


_STEM = "demo"


def _seed(directory: Path, tokens: list) -> TokenStore:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{_STEM}.tokens.yaml").write_text(
        yaml.safe_dump({"tokens": tokens}, allow_unicode=True),
        encoding="utf-8",
    )
    return TokenStore(directory, stem=_STEM)


def _reader_writer() -> list:
    return [
        {"id": "reader", "secret": "q-secret-value", "scopes": [PLANE_READ, "book:read"]},
        {
            "id": "trader",
            "secret": "t-secret-value",
            "scopes": [PLANE_WRITE, "plane:invoke"],
        },
        {
            "id": "admin",
            "secret": "a-secret-value",
            "scopes": [PLANE_READ, PLANE_WRITE, "plane:invoke", PLANE_ADMIN],
        },
    ]


def test_bootstrap_writes_files_without_logging_secrets(tmp_path: Path, caplog):
    caplog.set_level(logging.INFO)
    store = TokenStore(tmp_path, stem="app.demo")
    assert (tmp_path / "demo.tokens.yaml").is_file()
    assert (tmp_path / "demo.token-status.yaml").is_file()
    assert not (tmp_path / "w3trade.tokens.yaml").exists()
    assert store.tokens["reader"].scopes == (PLANE_READ,)
    assert PLANE_ADMIN in store.tokens["admin"].scopes
    assert store.tokens["reader"].secret not in caplog.text
    assert store.tokens["admin"].secret not in caplog.text


def test_unknown_scope_roundtrip(tmp_path: Path):
    store = _seed(tmp_path, _reader_writer())
    assert "book:read" in store.tokens["reader"].scopes
    text = (tmp_path / "demo.tokens.yaml").read_text(encoding="utf-8")
    assert "book:read" in text


def test_network_bearer_scopes(tmp_path: Path):
    gate = PrivilegeGate(_seed(tmp_path, _reader_writer()), bind_scope="network")
    missing = gate.enforce(None, (PLANE_READ,))
    assert isinstance(missing, Deny)
    assert missing.reason_code == "unauthenticated"
    wrong = gate.enforce("Bearer nope-secret-xx", (PLANE_READ,))
    assert isinstance(wrong, Deny)
    assert wrong.reason_code == "unauthenticated"

    read_ok = gate.enforce("Bearer q-secret-value", (PLANE_READ,))
    assert read_ok is Allow
    write_denied = gate.enforce("Bearer q-secret-value", (PLANE_WRITE,))
    assert isinstance(write_denied, Deny)
    assert write_denied.reason_code == "missing_scope:plane:write"

    trade_ok = gate.enforce("Bearer t-secret-value", ("plane:invoke",))
    assert trade_ok is Allow
    public = gate.enforce(None, ())
    assert public is Allow


def test_local_is_unrestricted(tmp_path: Path):
    gate = PrivilegeGate(_seed(tmp_path, _reader_writer()), bind_scope="local")
    assert gate.enforce(None, (PLANE_ADMIN, "book:read")) is Allow


def test_mask_keeps_matchable_parts():
    secret = "k3Jx9pABCDEFGHIJm2Qa"
    masked = mask_secret(secret)
    assert masked.startswith(secret[:6])
    assert masked.endswith(secret[-4:])
    assert "…" in masked
    short = mask_secret("short-secret")
    assert short.startswith("shor")
    assert secret not in short


def test_proxy_client_ip():
    client, peer = resolve_client_ip(
        {"X-Forwarded-For": "203.0.113.5, 10.0.0.8"},
        "10.0.0.8",
    )
    assert client == "203.0.113.5"
    assert peer == "10.0.0.8"
    client, peer = resolve_client_ip({"X-Real-IP": "198.51.100.9"}, "10.0.0.8")
    assert client == "198.51.100.9"
    client, peer = resolve_client_ip({}, "10.0.0.8")
    assert client == "10.0.0.8"


def test_access_status_updates_on_denied_scope(tmp_path: Path):
    store = _seed(tmp_path, _reader_writer())
    gate = PrivilegeGate(store, bind_scope="network")
    from wcore.dataplane.privilege import AccessContext

    gate.enforce(
        "Bearer q-secret-value",
        (PLANE_WRITE,),
        access=AccessContext(
            client_ip="203.0.113.5",
            peer="10.0.0.8",
            permission=PLANE_WRITE,
            api="http write global/x",
        ),
    )
    row = store.status["reader"]
    assert row["client_ip"] == "203.0.113.5"
    assert row["peer"] == "10.0.0.8"
    assert row["last_permission"] == PLANE_WRITE
    assert row["last_api"] == "http write global/x"
    assert row["created_at"]


def test_last_admin_cannot_be_deleted(tmp_path: Path):
    store = _seed(tmp_path, _reader_writer())
    store.delete("reader")
    with pytest.raises(ValueError, match="last admin"):
        store.delete("admin")


def test_create_returns_secret_once_and_list_masks(tmp_path: Path):
    store = _seed(tmp_path, _reader_writer())
    created = store.create("extra", ["book:read"])
    assert created.secret
    view = {row["id"]: row for row in store.public_view()}
    assert view["extra"]["secret"] != created.secret
    assert view["extra"]["secret"].startswith(created.secret[:6])
    with pytest.raises(ValueError, match="exists"):
        store.create("extra", ["book:read"])


def test_corrupt_token_file_exits(tmp_path: Path):
    path = tmp_path / "demo.tokens.yaml"
    path.write_text("tokens: [", encoding="utf-8")
    with pytest.raises(SystemExit):
        TokenStore(tmp_path, stem="demo")


def test_assert_bind_scope_matches_listen():
    from wcore.dataplane.privilege import assert_bind_scope_matches_listen

    assert_bind_scope_matches_listen(bind_scope="local", listen_host="127.0.0.1")
    assert_bind_scope_matches_listen(bind_scope="network", listen_host="0.0.0.0")
    assert_bind_scope_matches_listen(bind_scope="local", listen_host="")
    with pytest.raises(ValueError, match="incompatible"):
        assert_bind_scope_matches_listen(bind_scope="local", listen_host="0.0.0.0")
