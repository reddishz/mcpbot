"""控制平面 PrivilegeGate：一张 Bearer 凭证对应一组 scope。"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import yaml

logger = logging.getLogger(__name__)

PLANE_READ = "plane:read"
PLANE_WRITE = "plane:write"
PLANE_INVOKE = "plane:invoke"
PLANE_ADMIN = "plane:admin"

DEFAULT_SCOPES: Tuple[str, ...] = (
    PLANE_READ,
    PLANE_WRITE,
    PLANE_INVOKE,
    PLANE_ADMIN,
)

# 操作 kind → 默认 scope。路径上显式声明（含空元组）优先于本表。
DEFAULT_OP_MAP: Dict[str, str] = {
    "list": PLANE_READ,
    "read": PLANE_READ,
    "openapi": PLANE_READ,
    "write": PLANE_WRITE,
    "invoke": PLANE_INVOKE,
}

def token_file_stem(name: str) -> str:
    """子系统名或短名 → 凭证文件词干。

    ``app.w3trade`` 与 ``w3trade`` 都得到 ``w3trade``。
    文件名为 ``{词干}.tokens.yaml`` 与 ``{词干}.token-status.yaml``。
    """
    text = (name or "").strip()
    stem = text.rsplit(".", 1)[-1]
    if not stem or stem in (".", "..") or any(ch in stem for ch in "/\\"):
        raise ValueError(f"invalid token file stem: {name!r}")
    return stem


@dataclass(frozen=True)
class Deny:
    """权限拒绝。

    reason_code：

    * ``unauthenticated`` — 没有凭证，或凭证对不上（两种外形相同）
    * ``missing_scope:{name}`` — 凭证有效，但缺少 ``name``
    * ``unknown_operation:{kind}`` — ``op_map`` 无该操作
    """

    reason_code: str


class _AllowType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "Allow"


Allow = _AllowType()


def _reason_code(category: str, detail: str) -> str:
    return f"{category}:{detail}"


@dataclass(frozen=True)
class PrivilegeSet:
    """本次请求持有的 scope。``unrestricted`` 为本机满权限。"""

    _held: frozenset = field(default_factory=frozenset)
    unrestricted: bool = False

    def can(self, name: str) -> bool:
        return self.unrestricted or name in self._held

    def can_all(self, *names: str) -> bool:
        return all(self.can(n) for n in names)

    @property
    def names(self) -> frozenset:
        return self._held


@dataclass(frozen=True)
class TokenRecord:
    id: str
    secret: str
    scopes: Tuple[str, ...]


@dataclass
class AccessContext:
    """一次已匹配凭证的访问，写入状态文件的最新一条。"""

    client_ip: str = ""
    peer: str = ""
    permission: str = ""
    api: str = ""


def leaf_visible(held: PrivilegeSet, read_scopes: Optional[Tuple[str, ...]]) -> bool:
    """子树展开时，叶子未声明则按 ``plane:read``；空元组公开。"""
    required = (PLANE_READ,) if read_scopes is None else tuple(read_scopes)
    if not required:
        return True
    return held.can_all(*required)


def mask_secret(secret: str) -> str:
    """部分遮掩：保留可与文件对照的首尾。短于 12 时只留开头 4 个字符。"""
    text = secret or ""
    if len(text) < 12:
        head = text[:4]
        return head + "…" if len(text) > 4 else (head or "…")
    return text[:6] + "…" + text[-4:]


def resolve_client_ip(headers: Mapping[str, str], peer: str) -> Tuple[str, str]:
    """来源 IP。有代理头则解析客户端，并保留 TCP 对端。

    ``X-Real-IP`` 优先；否则取 ``X-Forwarded-For`` 最左侧地址。
    """
    lower = {str(k).lower(): str(v) for k, v in headers.items()}
    real = (lower.get("x-real-ip") or "").strip()
    if real:
        return real, peer
    forwarded = (lower.get("x-forwarded-for") or "").strip()
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first, peer
    return peer, peer


def parse_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def normalize_scopes(raw: Any) -> Tuple[str, ...]:
    """原样保留 scope 名字（含当前代码未使用的）。去掉空白和重复。"""
    if raw is None:
        return ()
    if isinstance(raw, str):
        items: List[Any] = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise ValueError("scopes must be a list or comma-separated string")
    out: List[str] = []
    seen = set()
    for item in items:
        name = str(item).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return tuple(out)


def _check_token_id(token_id: str) -> str:
    text = str(token_id).strip()
    if not text or any(ch.isspace() for ch in text) or "/" in text:
        raise ValueError("invalid token id")
    return text


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _mtime_iso(path: Path) -> str:
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return _now_iso()
    return datetime.fromtimestamp(stamp).astimezone().isoformat(timespec="seconds")


def _fatal(message: str) -> None:
    """凭证文件无法使用。不把文件内容写入日志。"""
    logger.critical("%s", message)
    raise SystemExit(1)


def _secrets_equal(presented: str, expected: str) -> bool:
    if len(presented) != len(expected):
        hmac.compare_digest(expected, expected)
        return False
    return hmac.compare_digest(presented, expected)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _dump_yaml(payload: Mapping[str, Any]) -> str:
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def _empty_status(created_at: str) -> Dict[str, str]:
    return {
        "created_at": created_at,
        "last_access_at": "",
        "client_ip": "",
        "peer": "",
        "last_permission": "",
        "last_api": "",
    }


class TokenStore:
    """凭证文件与最近一次访问状态。单进程，写盘后即更新内存。"""

    def __init__(self, directory: Path, *, stem: str) -> None:
        self.directory = Path(directory)
        self.stem = token_file_stem(stem)
        self.tokens_path = self.directory / f"{self.stem}.tokens.yaml"
        self.status_path = self.directory / f"{self.stem}.token-status.yaml"
        self._lock = threading.Lock()
        self.tokens: Dict[str, TokenRecord] = {}
        self.status: Dict[str, Dict[str, str]] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            if not self.tokens_path.exists():
                self._bootstrap_locked()
            else:
                self.tokens = self._read_tokens()
            if not self.status_path.exists():
                created = _mtime_iso(self.tokens_path)
                self.status = {
                    token_id: _empty_status(created) for token_id in self.tokens
                }
                self._write_status_locked()
            else:
                self.status = self._read_status()
                self._fill_missing_status_locked()

    def _bootstrap_locked(self) -> None:
        created = _now_iso()
        reader = TokenRecord(
            id="reader",
            secret=secrets.token_urlsafe(24),
            scopes=(PLANE_READ,),
        )
        admin = TokenRecord(
            id="admin",
            secret=secrets.token_urlsafe(24),
            scopes=DEFAULT_SCOPES,
        )
        self.tokens = {reader.id: reader, admin.id: admin}
        self.status = {
            reader.id: _empty_status(created),
            admin.id: _empty_status(created),
        }
        self._write_tokens_locked()
        self._write_status_locked()
        logger.info("created token file %s", self.tokens_path)

    def _read_tokens(self) -> Dict[str, TokenRecord]:
        try:
            text = self.tokens_path.read_text(encoding="utf-8")
        except OSError:
            _fatal(f"token file is unreadable: {self.tokens_path}")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            _fatal(f"token file is not valid YAML: {self.tokens_path}")
        if not isinstance(data, dict) or not isinstance(data.get("tokens"), list):
            _fatal(f"token file structure is invalid: {self.tokens_path}")
        found: Dict[str, TokenRecord] = {}
        secrets_seen = set()
        for item in data["tokens"]:
            if not isinstance(item, dict):
                _fatal(f"token file structure is invalid: {self.tokens_path}")
            token_id = item.get("id")
            secret = item.get("secret")
            if not isinstance(token_id, str) or not token_id.strip():
                _fatal(f"token file structure is invalid: {self.tokens_path}")
            if not isinstance(secret, str) or secret == "":
                _fatal(f"token file structure is invalid: {self.tokens_path}")
            token_id = token_id.strip()
            if token_id in found:
                _fatal(f"token file has a duplicate id: {token_id}")
            if secret in secrets_seen:
                _fatal(f"token file has a duplicate secret: {self.tokens_path}")
            try:
                scopes = normalize_scopes(item.get("scopes", []))
            except ValueError:
                _fatal(f"token file structure is invalid: {self.tokens_path}")
            secrets_seen.add(secret)
            found[token_id] = TokenRecord(id=token_id, secret=secret, scopes=scopes)
        return found

    def _read_status(self) -> Dict[str, Dict[str, str]]:
        try:
            text = self.status_path.read_text(encoding="utf-8")
        except OSError:
            _fatal(f"token status file is unreadable: {self.status_path}")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            _fatal(f"token status file is not valid YAML: {self.status_path}")
        if data is None:
            return {}
        if not isinstance(data, dict) or not isinstance(data.get("tokens"), dict):
            _fatal(f"token status file structure is invalid: {self.status_path}")
        out: Dict[str, Dict[str, str]] = {}
        for token_id, row in data["tokens"].items():
            if not isinstance(row, dict):
                _fatal(f"token status file structure is invalid: {self.status_path}")
            out[str(token_id)] = {
                "created_at": str(row.get("created_at") or ""),
                "last_access_at": str(row.get("last_access_at") or ""),
                "client_ip": str(row.get("client_ip") or ""),
                "peer": str(row.get("peer") or ""),
                "last_permission": str(row.get("last_permission") or ""),
                "last_api": str(row.get("last_api") or ""),
            }
        return out

    def _fill_missing_status_locked(self) -> None:
        created = _mtime_iso(self.tokens_path)
        changed = False
        for token_id in self.tokens:
            if token_id not in self.status:
                self.status[token_id] = _empty_status(created)
                changed = True
        stale = [token_id for token_id in self.status if token_id not in self.tokens]
        for token_id in stale:
            del self.status[token_id]
            changed = True
        if changed:
            self._write_status_locked()

    def _tokens_payload(self) -> Dict[str, Any]:
        return {
            "tokens": [
                {"id": rec.id, "secret": rec.secret, "scopes": list(rec.scopes)}
                for rec in self.tokens.values()
            ]
        }

    def _status_payload(self) -> Dict[str, Any]:
        return {"tokens": self.status}

    def _write_tokens_locked(self) -> None:
        _atomic_write(self.tokens_path, _dump_yaml(self._tokens_payload()))

    def _write_status_locked(self) -> None:
        _atomic_write(self.status_path, _dump_yaml(self._status_payload()))

    def match(self, presented: str) -> Optional[TokenRecord]:
        """常量时间扫描全部 secret。"""
        found: Optional[TokenRecord] = None
        with self._lock:
            for rec in self.tokens.values():
                if _secrets_equal(presented, rec.secret):
                    found = rec
        return found

    def _admin_count_locked(self) -> int:
        return sum(1 for rec in self.tokens.values() if PLANE_ADMIN in rec.scopes)

    def public_view(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = []
            for rec in self.tokens.values():
                state = self.status.get(rec.id) or _empty_status("")
                rows.append(
                    {
                        "id": rec.id,
                        "scopes": list(rec.scopes),
                        "secret": mask_secret(rec.secret),
                        "created_at": state.get("created_at", ""),
                        "last_access_at": state.get("last_access_at", ""),
                        "client_ip": state.get("client_ip", ""),
                        "peer": state.get("peer", ""),
                        "last_permission": state.get("last_permission", ""),
                        "last_api": state.get("last_api", ""),
                    }
                )
            return rows

    def record_access(self, token_id: str, access: AccessContext) -> None:
        with self._lock:
            row = self.status.get(token_id)
            if row is None:
                row = _empty_status(_now_iso())
                self.status[token_id] = row
            row["last_access_at"] = _now_iso()
            row["client_ip"] = access.client_ip
            row["peer"] = access.peer
            row["last_permission"] = access.permission
            row["last_api"] = access.api
            self._write_status_locked()

    def create(self, token_id: str, scopes: Any) -> TokenRecord:
        token_id = _check_token_id(token_id)
        scope_names = normalize_scopes(scopes)
        with self._lock:
            if token_id in self.tokens:
                raise ValueError("token id exists")
            rec = TokenRecord(
                id=token_id,
                secret=secrets.token_urlsafe(24),
                scopes=scope_names,
            )
            self.tokens[token_id] = rec
            self.status[token_id] = _empty_status(_now_iso())
            self._write_tokens_locked()
            self._write_status_locked()
            return rec

    def update_scopes(self, token_id: str, scopes: Any) -> TokenRecord:
        token_id = _check_token_id(token_id)
        scope_names = normalize_scopes(scopes)
        with self._lock:
            rec = self.tokens.get(token_id)
            if rec is None:
                raise ValueError("token not found")
            if (
                PLANE_ADMIN in rec.scopes
                and PLANE_ADMIN not in scope_names
                and self._admin_count_locked() <= 1
            ):
                raise ValueError("cannot remove plane:admin from the last admin token")
            updated = TokenRecord(id=rec.id, secret=rec.secret, scopes=scope_names)
            self.tokens[token_id] = updated
            self._write_tokens_locked()
            return updated

    def rotate(self, token_id: str) -> TokenRecord:
        token_id = _check_token_id(token_id)
        with self._lock:
            rec = self.tokens.get(token_id)
            if rec is None:
                raise ValueError("token not found")
            updated = TokenRecord(
                id=rec.id,
                secret=secrets.token_urlsafe(24),
                scopes=rec.scopes,
            )
            self.tokens[token_id] = updated
            self._write_tokens_locked()
            return updated

    def delete(self, token_id: str) -> None:
        token_id = _check_token_id(token_id)
        with self._lock:
            rec = self.tokens.get(token_id)
            if rec is None:
                raise ValueError("token not found")
            if PLANE_ADMIN in rec.scopes and self._admin_count_locked() <= 1:
                raise ValueError("cannot delete the last admin token")
            del self.tokens[token_id]
            self.status.pop(token_id, None)
            self._write_tokens_locked()
            self._write_status_locked()


class PrivilegeGate:
    """resolve / require。网络域按 Bearer 查 scope；本机域满权限。"""

    def __init__(
        self,
        store: TokenStore,
        *,
        op_map: Optional[Mapping[str, str]] = None,
        bind_scope: str = "network",
    ) -> None:
        self.store = store
        self.op_map: Dict[str, str] = {**DEFAULT_OP_MAP, **(op_map or {})}
        if bind_scope not in ("local", "network"):
            raise ValueError(f"invalid bind_scope: {bind_scope!r}")
        self.bind_scope = bind_scope

    def privilege_for(self, operation_kind: str) -> str:
        try:
            return self.op_map[operation_kind]
        except KeyError as exc:
            raise KeyError(f"unknown operation kind: {operation_kind!r}") from exc

    def try_privilege_for(self, operation_kind: str) -> Union[str, Deny]:
        try:
            return self.op_map[operation_kind]
        except KeyError:
            return Deny(_reason_code("unknown_operation", operation_kind))

    def required_scopes(
        self,
        operation_kind: str,
        *,
        declared: Optional[Sequence[str]] = None,
    ) -> Union[Tuple[str, ...], Deny]:
        """``declared`` 为 None 时用默认映射。空序列表示该操作不需要凭证。"""
        if declared is not None:
            return tuple(declared)
        mapped = self.try_privilege_for(operation_kind)
        if isinstance(mapped, Deny):
            return mapped
        return (mapped,)

    def resolve(self, authorization: Optional[str] = None) -> Union[PrivilegeSet, Deny]:
        if self.bind_scope == "local":
            return PrivilegeSet(unrestricted=True)
        presented = parse_bearer(authorization)
        if not presented:
            return Deny("unauthenticated")
        rec = self.store.match(presented)
        if rec is None:
            return Deny("unauthenticated")
        return PrivilegeSet(frozenset(rec.scopes))

    def held(self, authorization: Optional[str]) -> PrivilegeSet:
        """当前凭证持有的 scope。未带或对不上时为空集，不因此拒绝。"""
        if self.bind_scope == "local":
            return PrivilegeSet(unrestricted=True)
        rec = self.lookup(authorization)
        if rec is None:
            return PrivilegeSet()
        return PrivilegeSet(frozenset(rec.scopes))

    def lookup(self, authorization: Optional[str]) -> Optional[TokenRecord]:
        presented = parse_bearer(authorization)
        if not presented:
            return None
        return self.store.match(presented)

    def require(
        self, privilege_set: PrivilegeSet, *names: str
    ) -> Union[object, Deny]:
        for name in names:
            if not privilege_set.can(name):
                return Deny(_reason_code("missing_scope", name))
        return Allow

    def enforce(
        self,
        authorization: Optional[str],
        required: Sequence[str],
        *,
        access: Optional[AccessContext] = None,
    ) -> Union[object, Deny]:
        """空 ``required`` 不看凭证。匹配到凭证后先记访问，再判断 scope。"""
        if len(required) == 0:
            return Allow
        if self.bind_scope == "local":
            rec = self.lookup(authorization)
            if rec is not None and access is not None:
                self.store.record_access(rec.id, access)
            return Allow
        presented = parse_bearer(authorization)
        if not presented:
            return Deny("unauthenticated")
        rec = self.store.match(presented)
        if rec is None:
            return Deny("unauthenticated")
        if access is not None:
            self.store.record_access(rec.id, access)
        return self.require(PrivilegeSet(frozenset(rec.scopes)), *required)


def is_loopback_listen_host(host: str) -> bool:
    """判断监听 host 是否为本机环回。"""
    h = (host or "").strip().lower()
    if not h:
        return False
    if h in ("127.0.0.1", "::1", "localhost"):
        return True
    if h.startswith("127."):
        return True
    return False


def assert_bind_scope_matches_listen(*, bind_scope: str, listen_host: str) -> None:
    """``local`` 满权限不得配非环回监听。"""
    if bind_scope not in ("local", "network"):
        raise ValueError(f"invalid bind_scope: {bind_scope!r}")
    host = (listen_host or "").strip()
    if not host:
        return
    if bind_scope == "local" and not is_loopback_listen_host(host):
        raise ValueError(
            f"bind_scope=local is incompatible with listen_host={host!r}; "
            "use a loopback address (127.0.0.1 / ::1) or bind_scope=network"
        )
