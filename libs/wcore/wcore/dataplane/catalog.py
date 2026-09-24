"""Catalog 层次树模型（WC-RUL-007）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from .types import ParamSpec, Plane

Getter = Callable[[], Any]
Setter = Callable[[Any], Any]
ControlHandler = Callable[[Dict[str, Any]], Any]


@dataclass
class LeafSpec:
    """数据叶子声明。"""

    name: str
    plane: Plane
    value_type: type = str
    description: str = ""
    bind: str = ""
    readonly: bool = False
    getter: Optional[Getter] = None
    setter: Optional[Setter] = None


@dataclass
class ControlSpec:
    """控制端点声明。"""

    name: str
    description: str = ""
    bind: str = ""
    params: List[ParamSpec] = field(default_factory=list)
    handler: Optional[ControlHandler] = None


@dataclass
class CatalogNode:
    """容器节点；可含子容器、叶子、控制端点。"""

    name: str = ""
    plane: Optional[Plane] = None
    children: Dict[str, Union["CatalogNode", LeafSpec, ControlSpec]] = field(
        default_factory=dict
    )

    def add_node(self, name: str, *, plane: Optional[Plane] = None) -> "CatalogNode":
        child = CatalogNode(name=name, plane=plane)
        self.children[name] = child
        return child

    def add_leaf(self, spec: LeafSpec) -> None:
        self.children[spec.name] = spec

    def add_control(self, spec: ControlSpec) -> None:
        self.children[spec.name] = spec
