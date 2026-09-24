"""控制平面 PrivilegeGate（WC-D012）。"""

from __future__ import annotations

import hmac
import logging
import secrets
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional, Union

logger = logging.getLogger(__name__)

# 操作 kind → 权限名（WC-DEC-023）
DEFAULT_OP_MAP: Dict[str, str] = {
    "list": "query",
    "read": "query",
    "openapi": "query",
    "write": "trade",
    "invoke": "trade",
}

PRIVILEGE_QUERY = "query"
PRIVILEGE_TRADE = "trade"


@dataclass
class PrivilegeSpec:
    """一条权限配置：名、运行时期望值、HTTP 头名。"""

    name: str
    expected: str = ""
    header: str = ""


@dataclass(frozen=True)
class Deny:
    reason_code: str


class _AllowType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "Allow"


Allow = _AllowType()


@dataclass(frozen=True)
class PrivilegeSet:
    """段 A 解析结果：已持有的权限名集合。"""

    _held: frozenset = field(default_factory=frozenset)

    def can(self, name: str) -> bool:
        return name in self._held

    def can_all(self, *names: str) -> bool:
        return all(self.can(n) for n in names)

    def can_query(self) -> bool:
        return self.can(PRIVILEGE_QUERY)

    @property
    def names(self) -> frozenset:
        return self._held


class PrivilegeGate:
    """resolve / require / privilege_for（WC-CMP-005）。"""

    def __init__(
        self,
        specs: Iterable[PrivilegeSpec],
        *,
        op_map: Optional[Mapping[str, str]] = None,
        bind_scope: str = "network",
        key_logger: Optional[logging.Logger] = None,
    ) -> None:
        self._specs: Dict[str, PrivilegeSpec] = {s.name: s for s in specs}
        if PRIVILEGE_QUERY not in self._specs:
            raise ValueError("PrivilegeSpec table MUST include 'query'")
        self.op_map: Dict[str, str] = {**DEFAULT_OP_MAP, **(op_map or {})}
        if bind_scope not in ("local", "network"):
            raise ValueError(f"invalid bind_scope: {bind_scope!r}")
        self.bind_scope = bind_scope
        self._log = key_logger or logger
        self.ensure_keys()

    @property
    def specs(self) -> Mapping[str, PrivilegeSpec]:
        return self._specs

    def ensure_keys(self) -> None:
        """空期望 key：生成、打印、写入运行时期望值（WC-DEC-015）。"""
        for spec in self._specs.values():
            if not spec.expected:
                spec.expected = secrets.token_urlsafe(24)
                self._log.warning(
                    "generated privilege key name=%s key=%s",
                    spec.name,
                    spec.expected,
                )

    def privilege_for(self, operation_kind: str) -> str:
        try:
            return self.op_map[operation_kind]
        except KeyError as exc:
            raise KeyError(f"unknown operation kind: {operation_kind!r}") from exc

    def resolve(
        self,
        credential_map: Optional[Mapping[str, Optional[str]]] = None,
        *,
        bind_scope: Optional[str] = None,
    ) -> PrivilegeSet:
        scope = bind_scope or self.bind_scope
        if scope == "local":
            return PrivilegeSet(frozenset(self._specs.keys()))
        creds = credential_map or {}
        held = set()
        for name, spec in self._specs.items():
            presented = creds.get(name)
            if presented is None or presented == "":
                continue
            if _secrets_equal(str(presented), str(spec.expected)):
                held.add(name)
        return PrivilegeSet(frozenset(held))

    def require(
        self, privilege_set: PrivilegeSet, *names: str
    ) -> Union[object, Deny]:
        for name in names:
            if not privilege_set.can(name):
                return Deny(f"missing_privilege:{name}")
        return Allow

    def require_op(
        self, privilege_set: PrivilegeSet, operation_kind: str
    ) -> Union[object, Deny]:
        return self.require(privilege_set, self.privilege_for(operation_kind))

    def extract_http_credentials(
        self,
        headers: Mapping[str, str],
        *,
        authorization: Optional[str] = None,
    ) -> Dict[str, Optional[str]]:
        """从 Header / Bearer 抽取 CredentialMap（WC-DEC-022）。"""
        # normalize header lookup case-insensitive
        lower = {str(k).lower(): v for k, v in headers.items()}
        out: Dict[str, Optional[str]] = {}
        for name, spec in self._specs.items():
            presented: Optional[str] = None
            if spec.header:
                presented = lower.get(spec.header.lower())
            out[name] = presented

        bearer = _parse_bearer(authorization)
        if bearer is not None:
            header_q = out.get(PRIVILEGE_QUERY)
            if header_q and header_q != bearer:
                # 冲突：标记为无效（用哨兵使 compare 失败）
                out[PRIVILEGE_QUERY] = "\x00conflict"
            elif not header_q:
                out[PRIVILEGE_QUERY] = bearer
        return out


def _secrets_equal(presented: str, expected: str) -> bool:
    """常量时间比较；长度不同时返回 False（避免 compare_digest 抛错）。"""
    if len(presented) != len(expected):
        hmac.compare_digest(expected, expected)
        return False
    return hmac.compare_digest(presented, expected)


def _parse_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def default_w3trade_specs(
    *,
    query_key: str = "",
    trade_key: str = "",
) -> list:
    """应用常用双钥 PrivilegeSpec（非框架内核强制）。"""
    return [
        PrivilegeSpec(PRIVILEGE_QUERY, expected=query_key, header="X-Query-Key"),
        PrivilegeSpec(PRIVILEGE_TRADE, expected=trade_key, header="X-Trade-Key"),
    ]
