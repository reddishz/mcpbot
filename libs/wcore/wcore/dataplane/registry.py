"""平面 Registry（WC-R021）。"""

from __future__ import annotations

import difflib
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .catalog import CatalogNode, ControlSpec, LeafSpec
from .coerce import coerce_param, format_value, type_label
from .types import ControlResult, Plane


def _unknown_control_params_message(extra: List[str], known: List[str]) -> str:
    """未知控制参数错误；相近合法名给出 did you mean（WC-RUL-011）。"""
    bits: List[str] = []
    for name in extra:
        hints = difflib.get_close_matches(name, known, n=1, cutoff=0.5)
        if hints:
            bits.append(f"{name!r} (did you mean {hints[0]}?)")
        else:
            bits.append(repr(name))
    label = "unknown param" if len(bits) == 1 else "unknown params"
    return f"{label} {', '.join(bits)}"


def _reject_unknown_control_params(ctrl: Any, raw: Dict[str, Any]) -> None:
    known = [ps.name for ps in ctrl.params]
    extra = [name for name in raw if name not in set(known)]
    if extra:
        raise ValueError(_unknown_control_params_message(extra, known))


@dataclass
class _DataLeaf:
    name: str
    plane: Plane
    value_type: type
    description: str
    readonly: bool
    getter: Callable[[], Any]
    setter: Optional[Callable[[Any], Any]]


@dataclass
class _ControlLeaf:
    name: str
    description: str
    params: List[Any]
    handler: Callable[[Dict[str, Any]], Any]


class _TreeNode:
    __slots__ = ("name", "parent", "plane", "namespaces", "leaves", "controls")

    def __init__(self, name: str, parent: Optional["_TreeNode"]) -> None:
        self.name = name
        self.parent = parent
        self.plane: Optional[Plane] = None
        self.namespaces: Dict[str, _TreeNode] = {}
        self.leaves: Dict[str, _DataLeaf] = {}
        self.controls: Dict[str, _ControlLeaf] = {}


@dataclass(frozen=True)
class EntryMeta:
    name: str
    kind: str  # dir | leaf | control
    plane: Optional[str] = None
    value_type: Optional[str] = None
    description: str = ""
    readonly: bool = False
    path: str = ""
    access: Tuple[str, ...] = ()
    params: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)


class PlaneRegistry:
    """层次化平面注册表。"""

    def __init__(self, *, single_root: bool = False) -> None:
        self._single_root = single_root
        self._root = _TreeNode("", None)

    @property
    def single_root(self) -> bool:
        return self._single_root

    def _node_at(self, parts: List[str]) -> _TreeNode:
        node = self._root
        for part in parts:
            if part not in node.namespaces:
                raise KeyError(part)
            node = node.namespaces[part]
        return node

    def _ensure_ns(self, parent: _TreeNode, name: str) -> _TreeNode:
        if name in parent.leaves or name in parent.controls:
            raise ValueError(f"namespace conflicts with endpoint: {name}")
        if name not in parent.namespaces:
            parent.namespaces[name] = _TreeNode(name, parent)
        return parent.namespaces[name]

    def add_instance_tree(self, instance: str, catalog: CatalogNode) -> None:
        """多顶层：在根下挂实例子树。"""
        if self._single_root:
            raise ValueError("single_root registry cannot add named instances")
        inst_node = self._ensure_ns(self._root, instance)
        self._mount_catalog(inst_node, catalog, inherited_plane=None)

    def set_single_root_tree(self, catalog: CatalogNode) -> None:
        """单顶层：四子平面直接挂根下。"""
        self._single_root = True
        self._mount_catalog(self._root, catalog, inherited_plane=None)

    def remove_instance(self, instance: str) -> None:
        self._root.namespaces.pop(instance, None)

    def _mount_catalog(
        self,
        parent: _TreeNode,
        node: CatalogNode,
        *,
        inherited_plane: Optional[Plane],
    ) -> None:
        plane = node.plane or inherited_plane
        for key, child in node.children.items():
            if isinstance(child, CatalogNode):
                ns = self._ensure_ns(parent, child.name or key)
                child_plane = child.plane or plane
                if child_plane is not None:
                    ns.plane = child_plane
                self._mount_catalog(ns, child, inherited_plane=child_plane or plane)
            elif isinstance(child, LeafSpec):
                spec = child
                eff_plane = spec.plane or plane
                if eff_plane is None:
                    raise ValueError(f"leaf {spec.name} missing plane")
                if spec.getter is None:
                    raise ValueError(f"leaf {spec.name} missing getter")
                readonly = spec.readonly or eff_plane == Plane.STATE
                setter = None if readonly else spec.setter
                if eff_plane in (Plane.RUNTIME, Plane.CONFIG) and setter is None and not readonly:
                    raise ValueError(f"writable leaf {spec.name} missing setter")
                parent.leaves[spec.name] = _DataLeaf(
                    name=spec.name,
                    plane=eff_plane,
                    value_type=spec.value_type,
                    description=spec.description,
                    readonly=readonly,
                    getter=spec.getter,
                    setter=setter,
                )
            elif isinstance(child, ControlSpec):
                spec = child
                if spec.handler is None:
                    raise ValueError(f"control {spec.name} missing handler")
                parent.controls[spec.name] = _ControlLeaf(
                    name=spec.name,
                    description=spec.description,
                    params=list(spec.params),
                    handler=spec.handler,
                )

    @staticmethod
    def split_path(path: str) -> List[str]:
        text = path.strip().strip("/")
        if not text:
            return []
        return [p for p in text.replace(".", "/").split("/") if p]

    def list_entries(self, path: str = "") -> List[EntryMeta]:
        parts = self.split_path(path)
        node = self._node_at(parts) if parts else self._root
        base = "/".join(parts) if parts else ""
        out: List[EntryMeta] = []
        for name in node.namespaces:
            ns = node.namespaces[name]
            child_path = f"{base}/{name}" if base else name
            plane_val = ns.plane.value if ns.plane else None
            out.append(
                EntryMeta(
                    name=name,
                    kind="dir",
                    plane=plane_val,
                    path=child_path,
                    access=("GET",),
                    description=_dir_description(plane_val),
                )
            )
        for name, leaf in node.leaves.items():
            child_path = f"{base}/{name}" if base else name
            access: Tuple[str, ...] = ("GET",) if leaf.readonly else ("GET", "PUT")
            out.append(
                EntryMeta(
                    name=name,
                    kind="leaf",
                    plane=leaf.plane.value,
                    value_type=type_label(leaf.value_type),
                    description=leaf.description,
                    readonly=leaf.readonly,
                    path=child_path,
                    access=access,
                )
            )
        for name, ctrl in node.controls.items():
            child_path = f"{base}/{name}" if base else name
            out.append(
                EntryMeta(
                    name=name,
                    kind="control",
                    plane=Plane.CONTROL.value,
                    description=ctrl.description,
                    readonly=True,
                    path=child_path,
                    access=("POST",),
                    params=tuple(_control_param_dict(p) for p in ctrl.params),
                )
            )
        return out

    def inspect_path(self, path: str) -> str:
        """Return ``root`` | ``dir`` | ``leaf`` | ``control`` | ``missing``."""
        parts = self.split_path(path)
        if not parts:
            return "root"
        try:
            _, leaf = self._resolve_data(parts)
            if leaf.plane == Plane.CONTROL:
                return "control"
            return "leaf"
        except KeyError:
            pass
        try:
            self._resolve_control(parts)
            return "control"
        except KeyError:
            pass
        try:
            self._node_at(parts)
            return "dir"
        except KeyError:
            return "missing"

    def missing_segment(self, path: str) -> str:
        parts = self.split_path(path)
        node = self._root
        for part in parts:
            if part in node.namespaces:
                node = node.namespaces[part]
                continue
            return part
        return parts[-1] if parts else ""

    def path_hint(self, path: str, segment: str) -> str:
        parts = self.split_path(path)
        parent = "/".join(parts[:-1]) if len(parts) > 1 else ""
        if parent:
            return (
                f"unknown segment '{segment}' under '{path}'. "
                f"List children with GET .../plane/{parent}?list=1 "
                f"or GET .../plane/{parent} (directory auto-discovery)."
            )
        return (
            f"unknown segment '{segment}'. "
            "Start discovery at GET /api/v1/w3trade/plane?list=1 "
            "(instances → themes → endpoints)."
        )

    def _resolve_data(self, parts: List[str]) -> Tuple[List[str], _DataLeaf]:
        if len(parts) < 1:
            raise KeyError("path")
        parent_parts, leaf_name = parts[:-1], parts[-1]
        node = self._node_at(parent_parts) if parent_parts else self._root
        if leaf_name not in node.leaves:
            raise KeyError(leaf_name)
        return parent_parts, node.leaves[leaf_name]

    def _resolve_control(self, parts: List[str]) -> Tuple[List[str], _ControlLeaf]:
        if len(parts) < 1:
            raise KeyError("path")
        parent_parts, name = parts[:-1], parts[-1]
        node = self._node_at(parent_parts) if parent_parts else self._root
        if name not in node.controls:
            raise KeyError(name)
        return parent_parts, node.controls[name]

    async def read(self, path: str, *, depth: int = 0) -> Any:
        parts = self.split_path(path)
        if depth <= 0:
            _, leaf = self._resolve_data(parts)
            if leaf.plane == Plane.CONTROL:
                raise PermissionError("control endpoints are not readable")
            value = leaf.getter()
            if inspect.isawaitable(value):
                value = await value
            return {
                "path": "/".join(parts),
                "plane": leaf.plane.value,
                "type": type_label(leaf.value_type),
                "readonly": leaf.readonly,
                "value": value,
            }

        node = self._node_at(parts) if parts else self._root
        return await self._read_subtree(node, parts, max_depth=depth)

    async def _read_subtree(self, node: _TreeNode, prefix: List[str], *, max_depth: int) -> Dict[str, Any]:
        if max_depth <= 0:
            return {}
        out: Dict[str, Any] = {
            "path": "/".join(prefix) if prefix else "/",
            "kind": "dir",
            "children": {},
        }
        if node.plane is not None:
            out["plane"] = node.plane.value
        children: Dict[str, Any] = {}
        for name in node.namespaces:
            child_path = prefix + [name]
            ns_node = node.namespaces[name]
            if max_depth == 1:
                entry: Dict[str, Any] = {
                    "kind": "dir",
                    "path": "/".join(child_path),
                }
                if ns_node.plane is not None:
                    entry["plane"] = ns_node.plane.value
                children[name] = entry
            else:
                children[name] = await self._read_subtree(
                    ns_node, child_path, max_depth=max_depth - 1
                )
        for name, leaf in node.leaves.items():
            try:
                val = leaf.getter()
                if inspect.isawaitable(val):
                    val = await val
            except Exception as exc:
                val = None
                err = str(exc)
            else:
                err = None
            children[name] = {
                "kind": "leaf",
                "plane": leaf.plane.value,
                "type": type_label(leaf.value_type),
                "readonly": leaf.readonly,
                "value": val,
                "error": err,
            }
        for name, ctrl in node.controls.items():
            children[name] = {
                "kind": "control",
                "plane": Plane.CONTROL.value,
                "description": ctrl.description,
                "readonly": True,
            }
        out["children"] = children
        return out

    async def write(self, path: str, value: Any) -> Dict[str, Any]:
        parts = self.split_path(path)
        _, leaf = self._resolve_data(parts)
        if leaf.plane == Plane.STATE:
            raise PermissionError("state plane is read-only")
        if leaf.plane == Plane.CONTROL:
            raise PermissionError("use invoke for control plane")
        if leaf.readonly or leaf.setter is None:
            raise PermissionError(f"readonly: {'/'.join(parts)}")
        coerced = coerce_param(value, leaf.value_type)
        result = leaf.setter(coerced)
        if inspect.isawaitable(result):
            await result
        new_val = leaf.getter()
        return {
            "path": "/".join(parts),
            "plane": leaf.plane.value,
            "value": new_val,
        }

    async def invoke(self, path: str, params: Optional[Dict[str, Any]] = None) -> ControlResult:
        parts = self.split_path(path)
        _, ctrl = self._resolve_control(parts)
        raw = dict(params or {})
        _reject_unknown_control_params(ctrl, raw)
        bound: Dict[str, Any] = {}
        for ps in ctrl.params:
            if ps.name in raw:
                bound[ps.name] = coerce_param(raw[ps.name], ps.value_type)
            elif ps.required:
                raise ValueError(f"missing required param: {ps.name}")
        result = ctrl.handler(bound)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, ControlResult):
            return result
        if isinstance(result, dict) and "ok" in result:
            return ControlResult(
                ok=bool(result.get("ok")),
                code=str(result.get("code", "OK" if result.get("ok") else "ERROR")),
                message=str(result.get("message", "")),
            )
        return ControlResult(ok=True, code="OK", message=str(result) if result is not None else "ok")

    def get_leaf_for_shell(self, ns_parts: List[str], name: str) -> _DataLeaf:
        node = self._node_at(ns_parts) if ns_parts else self._root
        if name not in node.leaves:
            raise KeyError(name)
        return node.leaves[name]

    def iter_shell_entries(self) -> List[Tuple[str, str, Union[_DataLeaf, _ControlLeaf]]]:
        out: List[Tuple[str, str, Union[_DataLeaf, _ControlLeaf]]] = []

        def walk(node: _TreeNode, prefix: List[str]) -> None:
            for name in node.namespaces:
                path = "/".join(prefix + [name]) if prefix else name
                out.append((path, "dir", _DataLeaf(name, Plane.STATE, str, "", True, lambda: None, None)))
                walk(node.namespaces[name], prefix + [name])
            for name, leaf in node.leaves.items():
                path = "/".join(prefix + [name]) if prefix else name
                out.append((path, "leaf", leaf))
            for name, ctrl in node.controls.items():
                path = "/".join(prefix + [name]) if prefix else name
                out.append((path, "control", ctrl))

        walk(self._root, [])
        return out

    def control_catalog_text(self) -> str:
        """HTTP OpenAPI 附录用：全部 control 路径与参数摘要。"""
        from .usage import control_param_summary

        lines: List[str] = []
        for path, kind, item in self.iter_shell_entries():
            if kind != "control":
                continue
            ctrl = item
            lines.append(f"POST /plane/{path} — {ctrl.description or 'control'}")
            summary = control_param_summary(ctrl)
            if summary != "(no params)":
                lines.append(f"  params: {summary}")
        return "\n".join(lines)


def _dir_description(plane: Optional[str]) -> str:
    return "theme directory (GET with list=1 or depth>=1 to enumerate)"


def _control_param_dict(param: Any) -> Dict[str, Any]:
    return {
        "name": param.name,
        "type": type_label(param.value_type),
        "required": bool(param.required),
        "description": param.description or "",
    }
