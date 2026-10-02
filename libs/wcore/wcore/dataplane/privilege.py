"""控制平面 PrivilegeGate（WC-D012）。"""

from __future__ import annotations

import hmac
import logging
import secrets
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional, Union

logger = logging.getLogger(__name__)

# 操作 kind → 权限名（WC-DEC-023）
# 框架保留名 ``query`` 为读/发现地板；写/invoke 的默认名 ``trade`` 仅为常用约定，
# 应用 MAY 用 op_map / PrivilegeSpec 换成其它高级权限名（w3trade 即用 trade）。
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
    """权限拒绝（判定层统一输出。

    reason_code 语义约定 ``{category}:{detail}``：

    * ``missing_privilege:{name}`` — 未持有 ``name`` 权限
    * ``credential_conflict:{name}`` — ``name`` 的 Bearer 与 Header 不一致
    * ``unknown_operation:{kind}`` — ``op_map`` 无该 ``operation_kind``
    """

    reason_code: str


class _AllowType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "Allow"


Allow = _AllowType()


def _reason_code(category: str, detail: str) -> str:
    """构造形如 ``category:detail`` 的标准化 reason_code。"""
    return f"{category}:{detail}"


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
        """按 operation kind 返回权限名（WC-DEC-023）。缺失 kind 抛 KeyError。"""
        try:
            return self.op_map[operation_kind]
        except KeyError as exc:
            raise KeyError(f"unknown operation kind: {operation_kind!r}") from exc

    def try_privilege_for(self, operation_kind: str) -> Union[str, Deny]:
        """不抛异常的 ``privilege_for``：缺失 kind 时返回 ``Deny(unknown_operation)``。"""
        try:
            return self.op_map[operation_kind]
        except KeyError:
            return Deny(_reason_code("unknown_operation", operation_kind))

    def resolve(
        self,
        credential_map: Optional[Mapping[str, Optional[str]]] = None,
        *,
        bind_scope: Optional[str] = None,
        conflicts: Optional[Iterable[str]] = None,
    ) -> Union[PrivilegeSet, Deny]:
        """段 A 解析。

        @satisfies WC-CMP-005 / WC-R028

        ``conflicts`` 可选集合用于标记 Bearer ↔ Header 同名冲突的权限名：
        若某权限出现在 ``conflicts`` 中，直接以 ``Deny(credential_conflict:{name})``
        返回（视为比 missing 更强的拒绝），不进入常量时间 compare。
        """
        scope = bind_scope or self.bind_scope
        if scope == "local":
            return PrivilegeSet(frozenset(self._specs.keys()))
        conflict_set = set(conflicts or ())
        for name in sorted(conflict_set):
            if name in self._specs:
                return Deny(_reason_code("credential_conflict", name))
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
                return Deny(_reason_code("missing_privilege", name))
        return Allow

    def require_op(
        self, privilege_set: PrivilegeSet, operation_kind: str
    ) -> Union[object, Deny]:
        """``operation_kind → op_map → require``。

        @satisfies WC-DEC-021 / WC-DEC-023
        """
        result = self.try_privilege_for(operation_kind)
        if isinstance(result, Deny):
            return result
        return self.require(privilege_set, result)

    def extract_http_credentials(
        self,
        headers: Mapping[str, str],
        *,
        authorization: Optional[str] = None,
    ) -> tuple[Dict[str, Optional[str]], set[str]]:
        """从 Header / Bearer 抽取 CredentialMap + 冲突集合。

        @satisfies WC-DEC-022

        Returns:
            ``(credential_map, conflicts)`` — ``conflicts`` 内的权限名表示
            Bearer 与 Header 同时给出但值不一致；应在 :meth:`resolve` 中
            以 ``credential_conflict:{name}`` 结构化拒绝，不再进入 key compare。
        """
        lower = {str(k).lower(): v for k, v in headers.items()}
        out: Dict[str, Optional[str]] = {}
        for name, spec in self._specs.items():
            presented: Optional[str] = None
            if spec.header:
                presented = lower.get(spec.header.lower())
            out[name] = presented

        conflicts: set[str] = set()
        bearer = _parse_bearer(authorization)
        if bearer is not None:
            header_q = out.get(PRIVILEGE_QUERY)
            if header_q and header_q != bearer:
                conflicts.add(PRIVILEGE_QUERY)
                out[PRIVILEGE_QUERY] = bearer
            elif not header_q:
                out[PRIVILEGE_QUERY] = bearer
        return out, conflicts


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


def is_loopback_listen_host(host: str) -> bool:
    """判断监听 host 是否为本机环回（WC-R028 / BindScope）。"""
    h = (host or "").strip().lower()
    if not h:
        return False
    if h in ("127.0.0.1", "::1", "localhost"):
        return True
    if h.startswith("127."):
        return True
    return False


def assert_bind_scope_matches_listen(*, bind_scope: str, listen_host: str) -> None:
    """``local`` 满权限 **MUST NOT** 配非环回监听（防误暴露）。

    ``network`` 可配任意 host。``listen_host`` 为空则跳过（未实际 listen）。
    """
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
