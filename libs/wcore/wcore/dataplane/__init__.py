"""平面框架（WC-D009）。"""

from .catalog import CatalogNode, ControlSpec, LeafSpec
from .history import CommandHistory
from .privilege import (
    DEFAULT_OP_MAP,
    DEFAULT_SCOPES,
    PLANE_ADMIN,
    PLANE_INVOKE,
    PLANE_READ,
    PLANE_WRITE,
    Deny,
    PrivilegeGate,
    PrivilegeSet,
    TokenStore,
    assert_bind_scope_matches_listen,
    token_file_stem,
    is_loopback_listen_host,
)
from .registry import EntryMeta, PlaneRegistry
from .shell import PlaneShell, TabResult
from .types import ControlResult, ParamSpec, Plane

__all__ = [
    "CatalogNode",
    "CommandHistory",
    "ControlResult",
    "ControlSpec",
    "DEFAULT_OP_MAP",
    "DEFAULT_SCOPES",
    "Deny",
    "EntryMeta",
    "LeafSpec",
    "PLANE_ADMIN",
    "PLANE_INVOKE",
    "PLANE_READ",
    "PLANE_WRITE",
    "ParamSpec",
    "Plane",
    "PlaneRegistry",
    "PlaneShell",
    "PrivilegeGate",
    "PrivilegeSet",
    "TabResult",
    "TokenStore",
    "assert_bind_scope_matches_listen",
    "token_file_stem",
    "is_loopback_listen_host",
]
