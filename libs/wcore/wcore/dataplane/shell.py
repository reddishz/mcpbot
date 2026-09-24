"""交互式平面 Shell（WC-RUL-009 / WC-RUL-033~036）。"""

from __future__ import annotations

import inspect
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .coerce import coerce_param, format_value, resolve_getter_value, type_label
from .history import CommandHistory
from .registry import PlaneRegistry, _ControlLeaf, _DataLeaf, _TreeNode
from .types import Plane

_BUILTIN = frozenset({"cd", "pwd", "ls", "help", "?", "quit", "exit", "history"})
_FUZZY_LIMIT = 20
_PLANE_SEGMENTS = frozenset(p.value for p in Plane)
_HELP_FLAGS = frozenset({"-h", "--help"})
_HISTORY_FILE = ".plane_history"


from .runtime_info import format_runtime_lines
from .usage import control_param_summary, format_control_usage


@dataclass(frozen=True)
class TabResult:
    suffix: str
    candidates: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _ParsedPath:
    segments: Tuple[str, ...]
    absolute: bool
    trailing: bool


def parse_shell_path(raw: str) -> _ParsedPath:
    """分段并保留 `..` / `.`（WC-RUL-033）；禁止用全局 replace('.') 拆毁 `..`。"""
    text = raw.strip()
    if not text:
        return _ParsedPath((), False, False)
    absolute = text.startswith("/")
    # trailing：以 `/` 结尾，或以「段分隔用的」`.` 结尾（`foo.`）；单独的 `.` / `..` 不是 trailing
    if text.endswith("/"):
        trailing = True
        body = text.strip("/")
    elif text.endswith(".") and not text.endswith("..") and text not in (".",):
        trailing = True
        body = text[:-1].strip("/")
    else:
        trailing = False
        body = text.strip("/")
    if not body:
        return _ParsedPath((), absolute, trailing or absolute)
    return _ParsedPath(tuple(_tokenize_shell_path(body)), absolute, trailing)


def _tokenize_shell_path(body: str) -> List[str]:
    """按 `/` 与点分分隔，整段保留 `..` 与 `.`。"""
    parts: List[str] = []
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "/":
            i += 1
            continue
        if ch == ".":
            nxt = body[i + 1] if i + 1 < n else ""
            if nxt == ".":
                after = body[i + 2] if i + 2 < n else ""
                if after in ("", "/"):
                    parts.append("..")
                    i += 2
                    continue
            if nxt in ("", "/"):
                parts.append(".")
                i += 1
                continue
            # 点分分隔符
            i += 1
            continue
        j = i
        while j < n and body[j] not in "/.":
            j += 1
        parts.append(body[i:j])
        i = j
    return parts


_LS_VALUE_COMPACT_MAX = 48
# 定宽列：类型标签、标量值（meta 列由此对齐）
_LS_TYPE_W = 9  # [control] / float / bool / str / int / [dir]
_LS_VAL_W = 20  # bool/int/float 右对齐；短 str（含时间戳）左对齐


class PlaneShell:
    """基于 PlaneRegistry 的行命令解释器。"""

    def __init__(
        self,
        registry: PlaneRegistry,
        *,
        welcome: str = "",
        start_time: Optional[datetime] = None,
        history: Optional[CommandHistory] = None,
        history_path: Optional[Path] = None,
        history_max: int = 50,
        persist_history: bool = True,
    ) -> None:
        self._registry = registry
        self._pwd: List[str] = []
        self._welcome = welcome
        self._start_time = start_time
        self._view_plane: Optional[str] = None
        if history is not None:
            self._history = history
        else:
            path = history_path if persist_history else None
            self._history = CommandHistory(maxlen=history_max, path=path)

    @property
    def command_history(self) -> CommandHistory:
        return self._history

    def new_session(self) -> "PlaneShell":
        """每 Telnet 连接独立 pwd/@ 视图，共享 registry 与命令历史（WC-RUL-036）。"""
        sess = PlaneShell.__new__(PlaneShell)
        sess._registry = self._registry
        sess._welcome = self._welcome
        sess._start_time = self._start_time
        sess._history = self._history
        sess._pwd = []
        sess._view_plane = None
        return sess

    @property
    def view_plane(self) -> Optional[str]:
        return self._view_plane

    def _view_active(self) -> bool:
        """平面类型过滤是否激活（WC-RUL-026）；不改变 pwd。"""
        return self._view_plane is not None

    def prompt_line(self) -> str:
        """Telnet 提示符：主题路径为主；可选平面类型过滤后缀。"""
        scope = self.pwd
        if self._view_active():
            base = scope or "/"
            return f"{base}@{self._view_plane}> "
        if scope:
            return f"{scope}> "
        return "> "

    @staticmethod
    def _has_path_sep(text: str) -> bool:
        return "/" in text or ("." in text and not text.startswith("."))

    @staticmethod
    def _looks_like_path_query(text: str) -> bool:
        if not text:
            return False
        if text in ("..", ".", "/"):
            return True
        if text.startswith("../") or text.startswith(".."):
            return True
        if text.startswith("/"):
            return True
        return PlaneShell._has_path_sep(text)

    @staticmethod
    def _format_path(parts: List[str]) -> str:
        return "/".join(parts)

    @property
    def pwd(self) -> str:
        return self._format_path(self._pwd)

    def greeting(self) -> str:
        lines: List[str] = []
        if self._welcome:
            lines.append(self._welcome)
        if self._start_time is not None:
            lines.extend(format_runtime_lines(self._start_time))
        lines.append(
            "Type 'help' for commands; cd <theme>; @ state|runtime|... filters by plane type."
        )
        lines.append("History: ↑/↓, history, !!, !n")
        return "\r\n".join(lines) + "\r\n"

    def _expand_history_refs(self, text: str) -> Tuple[str, Optional[str]]:
        if text == "!!":
            if not self._history:
                raise ValueError("no history")
            expanded = self._history.last()
            return expanded, expanded
        if text.startswith("!") and len(text) > 1 and text[1:].isdigit():
            n = int(text[1:])
            try:
                expanded = self._history.get(n)
            except IndexError as exc:
                raise ValueError(str(exc)) from exc
            return expanded, expanded
        return text, None

    async def execute_async(self, line: str) -> str:
        raw = line.strip()
        if not raw:
            return ""
        try:
            text, echo = self._expand_history_refs(raw)
        except ValueError as exc:
            return f"error: {exc}"

        out = await self._dispatch(text)
        if out != "__QUIT__":
            self._history.append(text)
        if echo:
            return f"{echo}\r\n{out}" if out else echo
        return out

    async def _dispatch(self, text: str) -> str:
        lower = text.lower()
        if lower in ("quit", "exit"):
            return "__QUIT__"

        if text.startswith("@"):
            return self._cmd_at(text[1:].strip())

        if lower == "history" or lower.startswith("history "):
            return self._cmd_history(text[len("history") :].strip())

        if lower.startswith("cd "):
            return self._cmd_cd(text[3:].strip())
        if lower == "cd":
            return self._cmd_cd("")
        if lower == "pwd":
            return self.pwd or "/"
        if lower == "ls":
            return await self._cmd_ls()
        if lower.startswith("ls "):
            return await self._cmd_ls(text[3:].strip())
        if lower.startswith("help "):
            return self._cmd_control_help(text[5:].strip())
        if lower in ("help", "?"):
            return self._cmd_help()

        if "=" in text and not self._looks_like_control(text):
            left, right = text.split("=", 1)
            return await self._cmd_write(left.strip(), right.strip())

        if self._has_path_sep(text) or text.startswith("../") or text == "..":
            try:
                ns_parts, leaf_name = self._resolve_user_path(text)
                if self._is_control_path(ns_parts + [leaf_name]):
                    return await self._cmd_invoke(ns_parts, leaf_name, "")
                display = self._format_path(ns_parts + [leaf_name])
                return await self._cmd_read(ns_parts, leaf_name, display)
            except KeyError:
                pass

        parts = shlex.split(text)
        if parts:
            try:
                node = self._registry._node_at(self._pwd)
                if parts[0] in node.controls:
                    return await self._cmd_invoke(
                        self._pwd, parts[0], text[len(parts[0]) :].strip()
                    )
            except KeyError:
                pass
            if self._has_path_sep(parts[0]) or parts[0].startswith("../"):
                try:
                    ns_parts, leaf_name = self._resolve_user_path(parts[0])
                    if self._is_control_path(ns_parts + [leaf_name]):
                        return await self._cmd_invoke(
                            ns_parts, leaf_name, text[len(parts[0]) :].strip()
                        )
                except KeyError:
                    pass

        try:
            return await self._cmd_read(self._pwd, text, text)
        except (KeyError, PermissionError):
            pass

        resolved = self._resolve_short_in_view(text)
        if resolved is not None:
            ns_parts, leaf_name = resolved
            display = self._format_path(ns_parts + [leaf_name])
            try:
                if self._is_control_path(ns_parts + [leaf_name]):
                    return await self._cmd_invoke(ns_parts, leaf_name, "")
                return await self._cmd_read(ns_parts, leaf_name, display)
            except (KeyError, PermissionError):
                pass

        try:
            node = self._registry._node_at(self._pwd)
            if text in node.controls:
                return self._format_control_usage(node.controls[text], text)
        except KeyError:
            pass

        try:
            if " " in text:
                name, rest = text.split(None, 1)
                if name.lower() not in _BUILTIN:
                    return await self._cmd_invoke(self._pwd, name, rest)
        except (KeyError, ValueError) as exc:
            return f"error: {exc}"

        return self._cmd_fuzzy(text)

    def _cmd_history(self, arg: str) -> str:
        arg = arg.strip()
        if not arg:
            return self._history.format_lines()
        if arg.isdigit():
            return self._history.format_lines(last_k=int(arg))
        return "error: usage: history [n]"

    @staticmethod
    def _looks_like_control(text: str) -> bool:
        left = text.split("=", 1)[0]
        return " " in left.strip()

    def _apply_segments(self, base: List[str], segments: Tuple[str, ...]) -> List[str]:
        out = list(base)
        for seg in segments:
            if seg in ("", "."):
                continue
            if seg == "..":
                if out:
                    out.pop()
                continue
            out.append(seg)
        return out

    def _resolve_theme_segments(
        self,
        segments: Tuple[str, ...],
        *,
        absolute: bool,
    ) -> Optional[List[str]]:
        if absolute:
            cand = self._apply_segments([], segments)
            try:
                self._registry._node_at(cand)
                return cand
            except KeyError:
                return None

        # `..` / `.` 相对导航不得先当成「根上的空路径」命中（WC-RUL-033）
        has_dot = any(s in (".", "..") for s in segments)
        candidates: List[List[str]] = []
        if not has_dot:
            candidates.append(self._apply_segments([], segments))
        candidates.append(self._apply_segments(self._pwd, segments))

        seen = set()
        for cand in candidates:
            key = tuple(cand)
            if key in seen:
                continue
            seen.add(key)
            try:
                self._registry._node_at(cand)
                return cand
            except KeyError:
                continue
        return None

    def _resolve_theme_path(self, raw: str) -> Optional[List[str]]:
        parsed = parse_shell_path(raw)
        return self._resolve_theme_segments(
            parsed.segments, absolute=parsed.absolute or raw.strip().startswith("/")
        )

    def _cmd_cd(self, arg: str) -> str:
        if not arg or arg == "/":
            self._pwd = []
            self._view_plane = None
            return "ok"
        target = self._resolve_theme_path(arg)
        if target is None:
            return f"error: path not found: {arg}"
        self._pwd = target
        return "ok"

    def _node_at_pwd(self) -> _TreeNode:
        return self._registry._node_at(self._pwd) if self._pwd else self._registry._root

    async def _cmd_ls(self, query: str = "") -> str:
        if query.startswith("@"):
            rest = query[1:].strip()
            if not rest:
                return "error: usage: ls @<plane> [filter] (prefer: @ <plane>)"
            parts = rest.split(None, 1)
            plane_name = parts[0].lower()
            if plane_name not in _PLANE_SEGMENTS:
                return f"error: unknown plane: {plane_name}"
            msg = self._cmd_at(plane_name)
            if msg.startswith("error"):
                return msg
            needle = parts[1].lower() if len(parts) > 1 else ""
            listed = await self._ls_filtered(needle=needle)
            return f"{msg}\r\n{listed}" if listed else msg

        try:
            node = self._node_at_pwd()
        except KeyError:
            return "error: invalid pwd"
        if query:
            if self._looks_like_path_query(query):
                target = self._resolve_theme_path(query)
                if target is not None:
                    try:
                        node = (
                            self._registry._node_at(target)
                            if target
                            else self._registry._root
                        )
                    except KeyError:
                        return f"error: path not found: {query}"
                    return await self._format_ls_node(node)
            if query in node.namespaces:
                node = node.namespaces[query]
            else:
                return await self._format_ls_node(node, needle=query.lower())
        return await self._format_ls_node(node)

    async def _ls_filtered(self, *, needle: str = "") -> str:
        """当前主题 cwd 下按平面类型过滤列举（WC-RUL-026）。"""
        try:
            node = self._node_at_pwd()
        except KeyError:
            return "error: invalid pwd"
        return await self._format_ls_node(node, needle=needle)

    def _cmd_at(self, rest: str) -> str:
        """切换平面类型过滤（WC-RUL-026）；与 pwd 无关。"""
        if not rest:
            if self._view_plane:
                return f"view: @{self._view_plane}"
            return "view: @all"
        token = rest.split(None, 1)[0].lower()
        if token in ("all", "off", "none"):
            self._view_plane = None
            return "view: @all"
        if token not in _PLANE_SEGMENTS:
            return f"error: unknown plane: {token} (use state|runtime|config|control|all)"
        self._view_plane = token
        return f"view: @{token}"

    def _resolve_short_in_view(
        self, name: str
    ) -> Optional[Tuple[List[str], str]]:
        if not self._view_plane or self._has_path_sep(name) or not name:
            return None
        matches = self._collect_endpoint_paths_by_plane(self._view_plane, name)
        if len(matches) == 1:
            return matches[0]
        return None

    def _collect_endpoint_paths_by_plane(
        self, plane_name: str, leaf_name: str
    ) -> List[Tuple[List[str], str]]:
        """在当前实例（或单顶层根）子树内按平面类型查找同名端点。"""
        if self._registry.single_root:
            root_parts: List[str] = []
        else:
            instance = self._instance_scope()
            if instance is None:
                return []
            root_parts = [instance]
        try:
            node = (
                self._registry._node_at(root_parts)
                if root_parts
                else self._registry._root
            )
        except KeyError:
            return []
        out: List[Tuple[List[str], str]] = []
        self._walk_plane_matches(node, list(root_parts), leaf_name, plane_name, out)
        return out

    def _walk_plane_matches(
        self,
        node: _TreeNode,
        path: List[str],
        leaf_name: str,
        plane_name: str,
        out: List[Tuple[List[str], str]],
    ) -> None:
        if leaf_name in node.leaves:
            leaf = node.leaves[leaf_name]
            if leaf.plane.value == plane_name:
                out.append((path, leaf_name))
        if leaf_name in node.controls and plane_name == Plane.CONTROL.value:
            out.append((path, leaf_name))
        for ns_name, child in node.namespaces.items():
            self._walk_plane_matches(
                child, path + [ns_name], leaf_name, plane_name, out
            )

    def _instance_scope(self) -> Optional[str]:
        if self._registry.single_root:
            return None
        if not self._pwd:
            return None
        return self._pwd[0]

    def _should_omit_plane_tags(
        self, leaves: List[_DataLeaf], controls: Optional[List[_ControlLeaf]] = None
    ) -> bool:
        """同质省略：当前列举范围内端点平面唯一时省略行级标签（WC-RUL-028）。"""
        planes: set = set()
        for leaf in leaves:
            planes.add(leaf.plane)
        if controls:
            for _ in controls:
                planes.add(Plane.CONTROL)
        return len(planes) == 1

    def _leaf_ls_meta(self, leaf: _DataLeaf, *, omit_plane: bool) -> str:
        bits: List[str] = []
        if not omit_plane:
            bits.append(f"[{leaf.plane.value}]")
        if leaf.plane != Plane.STATE and (leaf.readonly or leaf.setter is None):
            bits.append("ro")
        return " ".join(bits)

    def _namespace_matches_plane(self, node: _TreeNode, plane_name: str) -> bool:
        """子树内是否存在该平面的端点（WC-RUL-026 过滤下列 [dir]）。"""
        for leaf in node.leaves.values():
            if leaf.plane.value == plane_name:
                return True
        if plane_name == Plane.CONTROL.value and node.controls:
            return True
        return any(
            self._namespace_matches_plane(child, plane_name)
            for child in node.namespaces.values()
        )

    async def _gather_ls_rows(
        self,
        node: _TreeNode,
        *,
        needle: str = "",
        omit_plane: Optional[bool] = None,
        name_prefix: str = "",
    ) -> List[tuple[str, str, str, str]]:
        plane_filter = self._view_plane
        visible_leaves = [
            leaf
            for name, leaf in node.leaves.items()
            if (not needle or needle in name.lower())
            and (plane_filter is None or leaf.plane.value == plane_filter)
        ]
        visible_controls = [
            ctrl
            for name, ctrl in node.controls.items()
            if (not needle or needle in name.lower())
            and (plane_filter is None or plane_filter == Plane.CONTROL.value)
        ]
        if omit_plane is None:
            omit_plane = self._should_omit_plane_tags(visible_leaves, visible_controls)

        rows: List[tuple[str, str, str, str]] = []
        for name in node.namespaces:
            if needle and needle not in name.lower():
                continue
            child = node.namespaces[name]
            if plane_filter is not None and not self._namespace_matches_plane(
                child, plane_filter
            ):
                continue
            display = f"{name_prefix}{name}/" if name_prefix else f"{name}/"
            rows.append((display, "[dir]", "", ""))
        for name, leaf in node.leaves.items():
            if needle and needle not in name.lower():
                continue
            if plane_filter is not None and leaf.plane.value != plane_filter:
                continue
            try:
                val = format_value(await resolve_getter_value(leaf.getter), compact=True)
            except Exception as exc:
                val = f"<err:{exc}>"
            display = f"{name_prefix}{name}" if name_prefix else name
            rows.append(
                (
                    display,
                    type_label(leaf.value_type),
                    val,
                    self._leaf_ls_meta(leaf, omit_plane=omit_plane),
                )
            )
        for name, ctrl in node.controls.items():
            if needle and needle not in name.lower():
                continue
            if plane_filter is not None and plane_filter != Plane.CONTROL.value:
                continue
            display = f"{name_prefix}{name}" if name_prefix else name
            rows.append((display, "[control]", control_param_summary(ctrl), ""))
        return rows

    async def _format_ls_node(self, node: _TreeNode, *, needle: str = "") -> str:
        rows = await self._gather_ls_rows(node, needle=needle)
        return self._format_ls_rows(rows)

    @staticmethod
    def _ls_value_is_wide(val: str, *, typ: str = "") -> bool:
        return len(val) > _LS_VALUE_COMPACT_MAX

    @staticmethod
    def _ls_format_value_cell(typ: str, val: str) -> str:
        if not val:
            return " " * _LS_VAL_W
        if typ in ("bool", "int", "float"):
            return f"{val:>{_LS_VAL_W}}"
        if typ == "str":
            return f"{val:<{_LS_VAL_W}}"
        return f"{val:<{_LS_VAL_W}}"

    @staticmethod
    def _format_ls_rows(rows: List[tuple[str, str, str, str]]) -> str:
        if not rows:
            return "(empty)"
        name_w = max(len(name) for name, _, _, _ in rows)
        lines: List[str] = []
        for name, typ, val, meta in rows:
            type_cell = f"{typ:<{_LS_TYPE_W}}"
            if "\n" in val:
                header = f"{name:<{name_w}}  {type_cell}"
                if meta:
                    header += f"  {meta}"
                lines.append(header)
                lines.extend("  " + part for part in val.split("\n"))
                continue
            wide = bool(val) and PlaneShell._ls_value_is_wide(val, typ=typ)
            if wide:
                core = f"{name:<{name_w}}  {type_cell}"
                line = f"{core}  {meta}  {val}" if meta else f"{core}  {val}"
            else:
                val_cell = PlaneShell._ls_format_value_cell(typ, val)
                line = f"{name:<{name_w}}  {type_cell}  {val_cell}"
                if meta:
                    line += f"  {meta}"
            lines.append(line)
        return "\r\n".join(lines)

    def _cmd_control_help(self, arg: str) -> str:
        if not arg:
            return "error: usage: help <control>"
        if self._has_path_sep(arg):
            try:
                ns_parts, name = self._resolve_user_path(arg)
            except KeyError:
                return f"error: path not found: {arg}"
        else:
            ns_parts, name = self._pwd, arg
        try:
            node = self._registry._node_at(ns_parts) if ns_parts else self._registry._root
            if name not in node.controls:
                return f"error: not a control: {arg}"
            return self._format_control_usage(node.controls[name], name)
        except KeyError:
            return f"error: path not found: {arg}"

    @staticmethod
    def _format_control_usage(ctrl: _ControlLeaf, name: str) -> str:
        return format_control_usage(ctrl, name)

    def _cmd_help(self) -> str:
        return (
            "Commands: cd <theme|..|/>, pwd, ls [path|filter], @ [state|runtime|config|control|all], "
            "history [n], !!, !n, help, help <control>, quit\r\n"
            "@ <plane> filters ls/bare names by plane type (does not change pwd)\r\n"
            "Read: <endpoint> or <a/b/c> (dots also accepted)\r\n"
            "Write: <endpoint>=<value> (config/runtime only)\r\n"
            "Control: <name> [k=v ...] — bare name runs when all params optional\r\n"
            "Control help: help <name> | <name> -h | <name> --help (never invokes)\r\n"
            "Line edit: ←/→ Home/End Delete; history ↑/↓; Tab complete; Ctrl+C clears line"
        )

    def _is_control_path(self, parts: List[str]) -> bool:
        try:
            node = self._registry._node_at(parts[:-1]) if len(parts) > 1 else self._registry._root
            return parts[-1] in node.controls
        except KeyError:
            return False

    async def _cmd_read(self, ns_parts: List[str], leaf_name: str, display_path: str) -> str:
        node = self._registry._node_at(ns_parts) if ns_parts else self._registry._root
        if leaf_name in node.controls:
            return f"error: control endpoint not readable: {display_path}"
        leaf = self._registry.get_leaf_for_shell(ns_parts, leaf_name)
        value = await resolve_getter_value(leaf.getter)
        return self._format_read_output(display_path, value, leaf)

    @staticmethod
    def _format_read_output(display_path: str, value: Any, leaf: _DataLeaf) -> str:
        body = format_value(value)
        tag = f"({type_label(leaf.value_type)}) [{leaf.plane.value}]"
        if "\n" in body:
            return f"{display_path} {tag}\r\n{body.replace('\n', '\r\n')}"
        return f"{display_path} = {body} {tag}"

    async def _cmd_write(self, left: str, raw_value: str) -> str:
        if self._has_path_sep(left):
            try:
                ns_parts, leaf_name = self._resolve_user_path(left)
            except KeyError:
                return f"error: path not found: {left}"
            display = self._format_path(ns_parts + [leaf_name])
        else:
            resolved = self._resolve_short_in_view(left)
            if resolved is not None:
                ns_parts, leaf_name = resolved
                display = self._format_path(ns_parts + [leaf_name])
            else:
                ns_parts, leaf_name = self._pwd, left
                display = f"{self.pwd}/{left}" if self.pwd else left

        try:
            leaf = self._registry.get_leaf_for_shell(ns_parts, leaf_name)
        except KeyError:
            return f"error: path not found: {left}"

        if leaf.plane in (Plane.STATE, Plane.CONTROL) or leaf.readonly or leaf.setter is None:
            return self._not_writable_message(leaf)

        try:
            value = coerce_param(raw_value, leaf.value_type)
            result = leaf.setter(value)
            if inspect.isawaitable(result):
                await result
        except (ValueError, PermissionError) as exc:
            return f"error: {exc}"
        except Exception as exc:
            return f"error: {exc}"

        try:
            new_val = format_value(await resolve_getter_value(leaf.getter))
        except Exception:
            new_val = raw_value
        return self._write_ok_message(leaf, display, new_val)

    def _not_writable_message(self, leaf: _DataLeaf) -> str:
        if leaf.plane == Plane.STATE:
            return "error: not writable [state]"
        if leaf.plane == Plane.CONTROL:
            return "error: not writable [control]"
        ro = " ro" if leaf.readonly or leaf.setter is None else ""
        return f"error: not writable [{leaf.plane.value}]{ro}"

    def _write_ok_message(self, leaf: _DataLeaf, display: str, new_val: str) -> str:
        if self._view_plane == leaf.plane.value:
            suffix = ""
        else:
            suffix = f" [{leaf.plane.value}]"
        extra = " (not persisted)" if leaf.plane == Plane.RUNTIME else ""
        if "\n" in new_val:
            return f"ok{suffix}: {display} =\r\n{new_val.replace('\n', '\r\n')}{extra}"
        return f"ok{suffix}: {display} = {new_val}{extra}"

    def _parse_control_params(self, rest: str, param_specs: List[Any]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not rest:
            return out
        positional = [ps.name for ps in param_specs]
        pos_idx = 0
        for token in shlex.split(rest):
            if "=" in token:
                k, v = token.split("=", 1)
                out[k.strip()] = v.strip()
                continue
            while pos_idx < len(positional) and positional[pos_idx] in out:
                pos_idx += 1
            if pos_idx >= len(positional):
                raise ValueError(
                    f"unexpected token {token!r}; use name=value or positional order "
                    f"({', '.join(positional)})"
                )
            out[positional[pos_idx]] = token
            pos_idx += 1
        return out

    async def _cmd_invoke(self, ns_parts: List[str], name: str, rest: str) -> str:
        node = self._registry._node_at(ns_parts) if ns_parts else self._registry._root
        if name not in node.controls:
            raise KeyError(name)
        ctrl = node.controls[name]
        path = "/".join(ns_parts + [name]) if ns_parts else name
        try:
            tokens = shlex.split(rest) if rest.strip() else []
        except ValueError as exc:
            return f"error: {exc}\r\n{self._format_control_usage(ctrl, name)}"
        if any(tok in _HELP_FLAGS for tok in tokens):
            return self._format_control_usage(ctrl, name)
        if not tokens and any(ps.required for ps in ctrl.params):
            return self._format_control_usage(ctrl, name)
        try:
            params = self._parse_control_params(rest, ctrl.params)
        except ValueError as exc:
            return f"error: {exc}\r\n{self._format_control_usage(ctrl, name)}"
        try:
            result = await self._registry.invoke(path, params)
        except ValueError as exc:
            return f"error: {exc}\r\n{self._format_control_usage(ctrl, name)}"
        except Exception as exc:
            return f"error: {exc}"
        status = "ok" if result.ok else "error"
        body = format_value(result.message)
        if "\n" in body:
            return f"{status}: [{result.code}]\r\n{body.replace('\n', '\r\n')}"
        return f"{status}: [{result.code}] {body}"

    def _resolve_user_path(self, raw: str) -> Tuple[List[str], str]:
        parsed = parse_shell_path(raw)
        if parsed.trailing or not parsed.segments:
            raise KeyError(raw)
        leaf = parsed.segments[-1]
        if leaf in (".", ".."):
            raise KeyError(raw)
        parent = self._resolve_theme_segments(
            parsed.segments[:-1],
            absolute=parsed.absolute or raw.strip().startswith("/"),
        )
        if parent is None:
            raise KeyError(raw)
        return parent, leaf

    def _resolve_parent_ns(self, segments: List[str]) -> Optional[List[str]]:
        return self._resolve_theme_segments(tuple(segments), absolute=False)

    def tab_complete(self, line: str) -> TabResult:
        token, candidates = self._gather_tab_candidates(line)
        if not candidates:
            return TabResult("")
        matches = [
            m for m in candidates if m.rstrip("/").lower().startswith(token.lower())
        ]
        if not matches:
            return TabResult("")
        if len(matches) == 1:
            bare = matches[0].rstrip("/")
            return TabResult(bare[len(token) :])
        bare_matches = [m.rstrip("/") for m in matches]
        lcp = self._common_prefix(bare_matches)
        if len(lcp) > len(token):
            return TabResult(lcp[len(token) :], tuple(matches))
        return TabResult("", tuple(matches))

    @staticmethod
    def _common_prefix(strings: List[str]) -> str:
        if not strings:
            return ""
        prefix = strings[0]
        for s in strings[1:]:
            i = 0
            limit = min(len(prefix), len(s))
            while i < limit and prefix[i].lower() == s[i].lower():
                i += 1
            prefix = prefix[:i]
            if not prefix:
                return ""
        return prefix

    def _child_names(self, parts: List[str]) -> List[str]:
        node = self._registry._node_at(parts) if parts else self._registry._root
        names = [f"{n}/" for n in sorted(node.namespaces)]
        names.extend(sorted(node.leaves))
        names.extend(sorted(node.controls))
        return names

    def _namespace_names(self, parts: List[str]) -> List[str]:
        node = self._registry._node_at(parts) if parts else self._registry._root
        return [f"{n}/" for n in sorted(node.namespaces)]

    def _tab_path_context(
        self, path_text: str, *, dirs_only: bool
    ) -> Tuple[str, List[str]]:
        """统一路径 Tab 上下文（WC-RUL-034）。"""
        parsed = parse_shell_path(path_text)
        segs = list(parsed.segments)
        absolute = parsed.absolute or path_text.strip().startswith("/")

        if parsed.trailing or (segs and segs[-1] in (".", "..")):
            parent = self._resolve_theme_segments(tuple(segs), absolute=absolute)
            token = ""
        elif not segs:
            parent = [] if absolute else list(self._pwd)
            token = ""
        else:
            token = segs[-1]
            prefix = tuple(segs[:-1])
            if not prefix and not absolute:
                # 单段相对路径：相对 pwd 补全（与无点号 cd 语义一致）
                parent = list(self._pwd)
            else:
                parent = self._resolve_theme_segments(prefix, absolute=absolute)

        if parent is None:
            return token, []
        try:
            names = (
                self._namespace_names(parent) if dirs_only else self._child_names(parent)
            )
        except KeyError:
            return token, []
        return token, names

    def _gather_tab_candidates(self, line: str) -> Tuple[str, List[str]]:
        text = line
        lower = text.lower()
        if lower.startswith("ls "):
            rest = text[3:]
            stripped = rest.lstrip()
            if stripped.startswith("@"):
                plane_part = stripped[1:].split(None, 1)[0].lower() if stripped[1:] else ""
                if not plane_part:
                    return stripped, sorted(_PLANE_SEGMENTS)
                if plane_part in _PLANE_SEGMENTS and " " not in stripped[1:].strip():
                    return stripped, [plane_part]
                # ls @plane filter — fall through to name filter on current
            if self._looks_like_path_query(stripped) or stripped.endswith("/") or stripped.endswith("."):
                return self._tab_path_context(stripped, dirs_only=False)
            return stripped, self._child_names(self._pwd)
        if text.startswith("@"):
            rest = text[1:].strip()
            token = rest.split(None, 1)[0].lower() if rest else ""
            candidates = sorted(_PLANE_SEGMENTS | {"all", "off"})
            if not token:
                return rest, candidates
            matches = [c for c in candidates if c.startswith(token)]
            return rest, matches
        if lower.startswith("cd "):
            path = text[3:].lstrip()
            return self._tab_path_context(path, dirs_only=True)
        if lower.startswith("history"):
            return text, ["history"]
        if "=" in text:
            left = text.split("=", 1)[0]
            if self._looks_like_path_query(left) or left.startswith("/"):
                return self._tab_path_context(left, dirs_only=False)
            return left, self._child_names(self._pwd)
        if self._looks_like_path_query(text) or text.startswith("/"):
            return self._tab_path_context(text, dirs_only=False)
        if " " not in text:
            token = text
            names = sorted(_BUILTIN)
            if self._pwd:
                names.extend(self._child_names(self._pwd))
            else:
                names.extend(self._namespace_names([]))
            return token, names
        return self._gather_control_tab_candidates(text)

    def _gather_control_tab_candidates(self, text: str) -> Tuple[str, List[str]]:
        try:
            parts = shlex.split(text)
        except ValueError:
            return "", []
        if not parts:
            return "", []
        try:
            node = self._node_at_pwd()
        except KeyError:
            return "", []
        cmd = parts[0]
        if cmd not in node.controls:
            return "", []
        ctrl = node.controls[cmd]
        if len(parts) == 1:
            if text.endswith(" "):
                return "", [f"{ps.name}=" for ps in ctrl.params]
            return cmd, [f"{ps.name}=" for ps in ctrl.params]
        tail = text.split(None, 1)[1]
        if tail.endswith(" "):
            assigned = {p.split("=", 1)[0] for p in parts[1:] if "=" in p}
            pending = [f"{ps.name}=" for ps in ctrl.params if ps.name not in assigned]
            return "", pending
        last = parts[-1]
        if "=" in last:
            pname, _, partial = last.partition("=")
            for ps in ctrl.params:
                if ps.name == pname and ps.value_type is bool:
                    opts = ["true", "false"]
                    matches = [o for o in opts if o.startswith(partial.lower())]
                    return last, matches
            return last, []
        assigned = {p.split("=", 1)[0] for p in parts[1:] if "=" in p}
        names = [ps.name for ps in ctrl.params if ps.name not in assigned]
        return last, names

    def _cmd_fuzzy(self, token: str) -> str:
        needle = token.lower().replace(".", "/")
        matches: List[str] = []
        try:
            node = self._node_at_pwd()
            for name in node.namespaces:
                if needle in name.lower():
                    path = f"{self.pwd}/{name}" if self.pwd else name
                    matches.append(f"{path}/\t[dir]")
            for name, leaf in node.leaves.items():
                if needle in name.lower():
                    path = f"{self.pwd}/{name}" if self.pwd else name
                    matches.append(f"{path}\t[{leaf.plane.value}]")
            for name in node.controls:
                if needle in name.lower():
                    path = f"{self.pwd}/{name}" if self.pwd else name
                    matches.append(f"{path}\t[control]")
        except KeyError:
            pass
        if not matches:
            for path, kind, item in self._registry.iter_shell_entries():
                if needle in path.lower():
                    if kind == "dir":
                        matches.append(f"{path}/\t[dir]")
                    elif kind == "leaf":
                        matches.append(f"{path}\t[{(item).plane.value}]")
                    else:
                        matches.append(f"{path}\t[control]")
        if not matches:
            return f"error: no match for {token!r}"
        if len(matches) > _FUZZY_LIMIT:
            head = matches[:_FUZZY_LIMIT]
            head.append(f"... narrow query (>{_FUZZY_LIMIT})")
            return "\r\n".join(head)
        return "\r\n".join(matches)
