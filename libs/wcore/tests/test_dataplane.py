"""平面 Registry / Shell 主题树测试（WC-D009 v2）。"""

import asyncio
from typing import Any, Dict

from wcore.dataplane import CatalogNode, ControlSpec, LeafSpec, ParamSpec, Plane, PlaneRegistry
from wcore.dataplane.coerce import format_float, format_value, type_label
from wcore.dataplane.types import ControlResult


def test_plane_registry_read_write_invoke():
    store = {"x": 1}

    async def _run():
        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="x",
                plane=Plane.CONFIG,
                value_type=int,
                getter=lambda: store["x"],
                setter=lambda v: store.__setitem__("x", int(v)),
            )
        )
        grid.add_leaf(
            LeafSpec(
                name="y",
                plane=Plane.STATE,
                value_type=int,
                readonly=True,
                getter=lambda: 42,
            )
        )
        grid.add_control(
            ControlSpec(
                name="ping",
                handler=lambda p: ControlResult(ok=True, code="OK", message="pong"),
            )
        )

        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)

        entries = reg.list_entries("")
        assert any(e.name == "grid" for e in entries)

        data = await reg.read("grid/x")
        assert data["value"] == 1

        await reg.write("grid/x", 9)
        assert store["x"] == 9

        result = await reg.invoke("grid/ping", {})
        assert result.ok and result.message == "pong"

    asyncio.run(_run())


def test_shell_control_positional_params():
    calls: list = []

    async def _trade(params: Dict[str, Any]) -> ControlResult:
        calls.append(params)
        return ControlResult(ok=True, code="OK", message="done")

    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_control(
            ControlSpec(
                name="trade",
                params=[
                    ParamSpec("side", str, required=True),
                    ParamSpec("follow", bool, required=False),
                ],
                handler=_trade,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        out = await shell.execute_async("trade buy")
        assert out.startswith("ok:")
        assert calls[-1]["side"] == "buy"
        out2 = await shell.execute_async("trade sell follow=true")
        assert out2.startswith("ok:")
        assert calls[-1]["side"] == "sell"
        assert calls[-1]["follow"] is True
        err = await shell.execute_async("trade")
        assert "usage:" in err

    asyncio.run(_run())


def test_invoke_rejects_unknown_params_with_hint():
    calls: list = []

    async def _buy(params: Dict[str, Any]) -> ControlResult:
        calls.append(params)
        return ControlResult(ok=True, code="OK", message="bought")

    async def _run():
        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_control(
            ControlSpec(
                name="buy",
                params=[
                    ParamSpec("follow", bool, required=False),
                    ParamSpec("limit", float, required=False),
                ],
                handler=_buy,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)

        try:
            await reg.invoke("grid/buy", {"limt": 1000})
        except ValueError as exc:
            msg = str(exc)
            assert "unknown param 'limt'" in msg
            assert "did you mean limit" in msg
        else:
            raise AssertionError("expected ValueError for unknown param")
        assert calls == []

        ok = await reg.invoke("grid/buy", {"limit": 1000})
        assert ok.ok and calls[-1]["limit"] == 1000.0
        try:
            await reg.invoke("grid/buy", {"limit_price": 99})
        except ValueError as exc:
            msg = str(exc)
            assert "unknown param 'limit_price'" in msg
            assert "did you mean limit" in msg
        else:
            raise AssertionError("expected ValueError for unknown param")

    asyncio.run(_run())


def test_shell_buy_unknown_param_limit_shows_hint():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_control(
            ControlSpec(
                name="buy",
                description="manual one-lot BUY",
                params=[
                    ParamSpec("follow", bool, required=False),
                    ParamSpec("limit", float, required=False),
                    ParamSpec("book", str, required=False),
                ],
                handler=lambda p: ControlResult(ok=True, code="OK", message="should-not-run"),
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        out = await shell.execute_async("buy limt=1000")
        assert out.startswith("error:")
        assert "unknown param 'limt'" in out
        assert "did you mean limit" in out
        assert "usage:" in out
        assert "should-not-run" not in out
        ok = await shell.execute_async("buy limit=1000")
        assert ok.startswith("ok:")
        old = await shell.execute_async("buy limit_price=1000")
        assert old.startswith("error:")
        assert "unknown param 'limit_price'" in old
        assert "did you mean limit" in old

    asyncio.run(_run())


def test_multi_instance():
    store = {"g": 0}

    def _global_tree():
        cat = CatalogNode()
        cat.add_leaf(
            LeafSpec(
                name="g",
                plane=Plane.CONFIG,
                value_type=int,
                getter=lambda: store["g"],
                setter=lambda v: store.__setitem__("g", int(v)),
            )
        )
        return cat

    reg = PlaneRegistry()
    reg.add_instance_tree("global", _global_tree())
    entries = reg.list_entries("global")
    assert any(e.name == "g" for e in entries)
    g_entry = next(e for e in entries if e.name == "g")
    assert g_entry.kind == "leaf"
    assert g_entry.plane == "config"
    assert g_entry.path == "global/g"
    assert reg.inspect_path("global") == "dir"
    assert reg.inspect_path("global/state") == "missing"
    assert "unknown segment" in reg.path_hint("global/state", "state")


def test_read_subtree_marks_intermediate_dirs():
    async def _run():
        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        nested = grid.add_node("metrics")
        nested.add_leaf(
            LeafSpec(
                name="x",
                plane=Plane.STATE,
                value_type=int,
                readonly=True,
                getter=lambda: 1,
            )
        )

        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)

        tree = await reg.read("grid", depth=2)
        assert tree["kind"] == "dir"
        metrics = tree["children"]["metrics"]
        assert metrics["kind"] == "dir"
        assert metrics["path"] == "grid/metrics"
        assert metrics["children"]["x"]["kind"] == "leaf"

    asyncio.run(_run())


def test_shell_async_state_getter():
    async def _open_view():
        return {"orders": [{"order_id": "1"}], "warnings": []}

    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="orders_open",
                plane=Plane.STATE,
                value_type=dict,
                readonly=True,
                getter=_open_view,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        ls_out = await shell.execute_async("ls")
        assert "<coroutine" not in ls_out
        assert "orders_open" in ls_out
        read_out = await shell.execute_async("orders_open")
        assert "<coroutine" not in read_out
        assert "order_id" in read_out
        data = await reg.read("grid/orders_open")
        assert data["value"]["orders"][0]["order_id"] == "1"

    asyncio.run(_run())


def test_format_value_none():
    assert format_value(None) == "-"
    assert format_float(405.86963906365594) == "405.87"
    assert format_float(0.007970903893904329) == "0.007971"
    assert format_float(0.75) == "0.75"
    assert format_float(0.0) == "0"
    assert format_float(0.11714285714285522) == "0.117143"
    assert format_value(0.3375) == "0.3375"


def test_type_label_containers():
    assert type_label(dict) == "dict"
    assert type_label(list) == "list"
    assert type_label(str) == "str"


def test_format_value_leaf_container_stays_one_line():
    assert "\n" not in format_value({"count": 2, "ok": True})
    assert "count: 2" in format_value({"count": 2, "ok": True})
    assert format_value([1, 2, 3]) == "[1, 2, 3]"
    assert format_value([]) == "[]"
    assert format_value({}) == "{}"


def test_format_value_nested_expands_for_single_item():
    payload = {
        "items": [
            {"id": "a", "n": 1, "ok": True},
            {"id": "b", "n": 2, "ok": False},
        ],
        "meta": {"count": 2, "label": "x"},
        "tags": [],
        "note": "plain",
    }
    text = format_value(payload)
    lines = text.split("\n")
    assert lines[0] == "{"
    assert lines[-1] == "}"
    assert any("items: [" in line for line in lines)
    assert "{id: a, n: 1, ok: true}" in text
    assert "{id: b, n: 2, ok: false}" in text
    assert "meta: {count: 2, label: x}" in text
    assert "tags: []" in text
    assert "note: plain" in text
    assert len(lines) <= 10


def test_format_value_compact_listing_is_one_line():
    payload = {
        "items": [{"id": "a", "n": 1}, {"id": "b", "n": 2}],
        "meta": {"count": 2},
    }
    compact = format_value(payload, compact=True)
    assert "\n" not in compact
    assert "id: a" in compact and "id: b" in compact
    assert compact.startswith("{") and compact.endswith("}")


def test_shell_ls_compact_read_expanded():
    payload = {
        "items": [
            {"id": "a", "n": 1},
            {"id": "b", "n": 2},
        ],
        "meta": {"count": 2},
    }

    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="snapshot",
                plane=Plane.STATE,
                value_type=dict,
                readonly=True,
                getter=lambda: payload,
            )
        )
        grid.add_leaf(
            LeafSpec(
                name="rows",
                plane=Plane.STATE,
                value_type=list,
                readonly=True,
                getter=lambda: payload["items"],
            )
        )
        grid.add_leaf(
            LeafSpec(
                name="enabled",
                plane=Plane.STATE,
                value_type=bool,
                readonly=True,
                getter=lambda: True,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        ls_out = await shell.execute_async("ls")
        read_out = await shell.execute_async("snapshot")
        list_out = await shell.execute_async("rows")
        ls_lines = [ln for ln in ls_out.split("\r\n") if ln]
        assert len(ls_lines) == 3
        snap_ls = next(ln for ln in ls_lines if ln.startswith("snapshot"))
        assert "\n" not in snap_ls
        assert "id: a" in snap_ls
        expanded = format_value(payload).replace("\n", "\r\n")
        assert expanded in read_out
        assert read_out.startswith("snapshot (dict) [state]\r\n")
        assert format_value(payload["items"]).replace("\n", "\r\n") in list_out
        assert list_out.startswith("rows (list) [state]\r\n")

    asyncio.run(_run())


def test_shell_ls_column_alignment():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="boundary_buy_px",
                plane=Plane.STATE,
                value_type=float,
                readonly=True,
                getter=lambda: 405.86963906365594,
            )
        )
        grid.add_leaf(
            LeafSpec(
                name="enabled",
                plane=Plane.STATE,
                value_type=bool,
                readonly=True,
                getter=lambda: True,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        ls_out = await shell.execute_async("ls")
        lines = ls_out.split("\r\n")
        assert len(lines) == 2
        assert "405.87" in ls_out
        assert "[state]" not in ls_out
        assert "readonly" not in ls_out
        assert " ro" not in ls_out
        assert lines[0].index("float") == lines[1].index("bool")
        assert "\t" not in ls_out

    asyncio.run(_run())


def test_shell_ls_wide_value_after_meta():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="enabled",
                plane=Plane.STATE,
                value_type=bool,
                readonly=True,
                getter=lambda: True,
            )
        )
        grid.add_leaf(
            LeafSpec(
                name="orders_open",
                plane=Plane.STATE,
                value_type=str,
                readonly=True,
                getter=lambda: (
                    '{"orders":[],"summary":{"count":0},'
                    '"synced_at":"2026-06-25T16:22:47.446564"}'
                ),
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        ls_out = await shell.execute_async("ls")
        lines = ls_out.split("\r\n")
        wide = next(l for l in lines if l.startswith("orders_open"))
        compact = next(l for l in lines if l.startswith("enabled"))
        assert "[state]" not in wide
        assert "[state]" not in compact
        assert "readonly" not in wide
        idx_json = wide.index("{")
        assert idx_json > 0

    asyncio.run(_run())


def test_shell_ls_preserves_catalog_order():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="zebra",
                plane=Plane.STATE,
                value_type=int,
                readonly=True,
                getter=lambda: 1,
            )
        )
        grid.add_leaf(
            LeafSpec(
                name="apple",
                plane=Plane.STATE,
                value_type=int,
                readonly=True,
                getter=lambda: 2,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        names = [e.name for e in reg.list_entries("grid") if e.kind == "leaf"]
        assert names == ["zebra", "apple"]
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        ls_out = await shell.execute_async("ls")
        lines = [ln for ln in ls_out.split("\r\n") if ln.strip()]
        assert lines[0].startswith("zebra")
        assert lines[1].startswith("apple")

    asyncio.run(_run())


def test_shell_ls_homogeneous_omits_plane_tag():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="coefficient",
                plane=Plane.CONFIG,
                value_type=float,
                getter=lambda: 0.9,
                setter=lambda v: None,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        ls_out = await shell.execute_async("ls")
        assert "coefficient" in ls_out
        assert "[config]" not in ls_out

    asyncio.run(_run())


def test_shell_ls_plane_filter():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="x",
                plane=Plane.CONFIG,
                value_type=int,
                getter=lambda: 1,
                setter=lambda v: None,
            )
        )
        grid.add_leaf(
            LeafSpec(
                name="y",
                plane=Plane.STATE,
                value_type=int,
                readonly=True,
                getter=lambda: 2,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        ls_out = await shell.execute_async("ls @config")
        assert "view: @config" in ls_out
        assert "x" in ls_out
        body = ls_out.split("\r\n", 1)[-1]
        assert "y" not in body
        assert await shell.execute_async("pwd") == "grid"
        assert shell.view_plane == "config"
        assert shell.prompt_line() == "grid@config> "

    asyncio.run(_run())


def test_shell_ls_plane_filter_lists_dirs_with_matching_descendants():
    """@state 时实例根 ls 仍列含 state 的主题，不含仅 control/config 的空壳。"""

    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="y",
                plane=Plane.STATE,
                value_type=int,
                readonly=True,
                getter=lambda: 2,
            )
        )
        grid.add_leaf(
            LeafSpec(
                name="x",
                plane=Plane.CONFIG,
                value_type=int,
                getter=lambda: 1,
                setter=lambda v: None,
            )
        )
        follow = catalog.add_node("follow")
        follow.add_leaf(
            LeafSpec(
                name="open",
                plane=Plane.STATE,
                value_type=bool,
                readonly=True,
                getter=lambda: True,
            )
        )
        reservoir = catalog.add_node("reservoir")
        reservoir.add_control(
            ControlSpec(name="fill", description="fill", handler=lambda p: None)
        )
        cfg_only = catalog.add_node("meta")
        cfg_only.add_leaf(
            LeafSpec(
                name="label",
                plane=Plane.CONFIG,
                value_type=str,
                getter=lambda: "z",
                setter=lambda v: None,
            )
        )
        reg = PlaneRegistry()
        reg.add_instance_tree("ZEC_USDT", catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd ZEC_USDT") == "ok"
        assert await shell.execute_async("@ state") == "view: @state"
        ls_out = await shell.execute_async("ls")
        assert "(empty)" not in ls_out
        assert "grid/" in ls_out
        assert "follow/" in ls_out
        assert "reservoir/" not in ls_out
        assert "meta/" not in ls_out
        assert await shell.execute_async("cd grid") == "ok"
        grid_ls = await shell.execute_async("ls")
        assert "y" in grid_ls
        assert not any(line.split()[:1] == ["x"] for line in grid_ls.splitlines())

    asyncio.run(_run())


def test_shell_sticky_at_view():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="y",
                plane=Plane.STATE,
                value_type=int,
                readonly=True,
                getter=lambda: 2,
            )
        )
        grid.add_leaf(
            LeafSpec(
                name="x",
                plane=Plane.CONFIG,
                value_type=int,
                getter=lambda: 1,
                setter=lambda v: None,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        assert await shell.execute_async("@ state") == "view: @state"
        ls_out = await shell.execute_async("ls")
        assert "y" in ls_out
        assert "x" not in ls_out or ls_out.count("x") == 0
        assert await shell.execute_async("@ all") == "view: @all"
        assert shell.view_plane is None
        assert shell.prompt_line() == "grid> "

    asyncio.run(_run())


def test_shell_write_not_writable_state():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="y",
                plane=Plane.STATE,
                value_type=int,
                readonly=True,
                getter=lambda: 42,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        out = await shell.execute_async("y=1")
        assert out == "error: not writable [state]"
        assert "readonly" not in out

    asyncio.run(_run())


def test_shell_config_readonly_shows_ro():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="locked",
                plane=Plane.CONFIG,
                value_type=float,
                readonly=True,
                getter=lambda: 1.0,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        ls_out = await shell.execute_async("ls")
        assert " ro" in ls_out
        assert "[config]" not in ls_out
        out = await shell.execute_async("locked=2")
        assert out == "error: not writable [config] ro"

    asyncio.run(_run())


def test_shell_ls_heterogeneous_shows_plane_tags():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="x",
                plane=Plane.CONFIG,
                value_type=int,
                getter=lambda: 1,
                setter=lambda v: None,
            )
        )
        grid.add_leaf(
            LeafSpec(
                name="y",
                plane=Plane.STATE,
                value_type=int,
                readonly=True,
                getter=lambda: 2,
            )
        )
        reg = PlaneRegistry()
        reg.add_instance_tree("SYM", catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd SYM.grid") == "ok"
        ls_out = await shell.execute_async("ls")
        assert "[config]" in ls_out
        assert "[state]" in ls_out
        filtered = await shell.execute_async("ls @state")
        assert "view: @state" in filtered
        assert "y" in filtered
        assert await shell.execute_async("pwd") == "SYM/grid"
        assert shell.prompt_line() == "SYM/grid@state> "
        assert shell.view_plane == "state"

    asyncio.run(_run())


def test_shell_blocked_config_shows_ro():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="blocked",
                plane=Plane.CONFIG,
                value_type=int,
                readonly=True,
                getter=lambda: 1,
                setter=None,
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd grid") == "ok"
        ls_out = await shell.execute_async("ls")
        assert " ro" in ls_out
        out = await shell.execute_async("blocked=2")
        assert out == "error: not writable [config] ro"

    asyncio.run(_run())


def test_shell_slash_and_dot_paths_equivalent():
    """WC-RUL-009：展示为斜杠；点分输入与斜杠指向同一节点。"""

    async def _run():
        from wcore.dataplane import PlaneShell

        store = {"x": 1}
        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="x",
                plane=Plane.CONFIG,
                value_type=int,
                getter=lambda: store["x"],
                setter=lambda v: store.__setitem__("x", int(v)),
            )
        )
        reg = PlaneRegistry()
        reg.add_instance_tree("SYM", catalog)
        shell = PlaneShell(reg)

        assert await shell.execute_async("cd SYM/grid") == "ok"
        assert await shell.execute_async("pwd") == "SYM/grid"
        assert shell.prompt_line() == "SYM/grid> "

        assert await shell.execute_async("cd /") == "ok"
        assert await shell.execute_async("cd SYM.grid") == "ok"
        assert await shell.execute_async("pwd") == "SYM/grid"

        read_slash = await shell.execute_async("SYM/grid/x")
        read_dot = await shell.execute_async("SYM.grid.x")
        assert "SYM/grid/x =" in read_slash
        assert read_slash == read_dot

        write_out = await shell.execute_async("SYM/grid/x=7")
        assert write_out.startswith("ok")
        assert "SYM/grid/x = 7" in write_out
        assert store["x"] == 7

        write_dot = await shell.execute_async("SYM.grid.x=9")
        assert "SYM/grid/x = 9" in write_dot
        assert store["x"] == 9

    asyncio.run(_run())


def test_parse_shell_path_preserves_dotdot():
    from wcore.dataplane.shell import parse_shell_path

    assert parse_shell_path("..").segments == ("..",)
    assert parse_shell_path("../x").segments == ("..", "x")
    assert parse_shell_path("a.b.c").segments == ("a", "b", "c")
    assert parse_shell_path("a/b/").trailing is True
    assert parse_shell_path("a.b.").trailing is True


def test_shell_cd_ls_dotdot_and_full_path_tab():
    async def _run():
        from wcore.dataplane import PlaneShell

        store = {"x": 1, "y": 2}
        catalog = CatalogNode()
        a = catalog.add_node("alpha")
        g = a.add_node("grid")
        g.add_leaf(
            LeafSpec(
                name="x",
                plane=Plane.CONFIG,
                value_type=int,
                getter=lambda: store["x"],
                setter=lambda v: store.__setitem__("x", int(v)),
            )
        )
        b = catalog.add_node("beta")
        bg = b.add_node("grid")
        bg.add_leaf(
            LeafSpec(
                name="y",
                plane=Plane.CONFIG,
                value_type=int,
                getter=lambda: store["y"],
                setter=lambda v: store.__setitem__("y", int(v)),
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg, persist_history=False)

        assert await shell.execute_async("cd alpha/grid") == "ok"
        assert await shell.execute_async("pwd") == "alpha/grid"
        assert await shell.execute_async("cd ..") == "ok"
        assert await shell.execute_async("pwd") == "alpha"
        assert await shell.execute_async("cd ../beta/grid") == "ok"
        assert await shell.execute_async("pwd") == "beta/grid"

        ls_up = await shell.execute_async("ls ..")
        assert "grid/" in ls_up
        ls_path = await shell.execute_async("ls ../../alpha/grid")
        assert "x" in ls_path

        # full path tab
        tab = shell.tab_complete("alpha/g")
        assert "rid" in tab.suffix or any("grid" in c for c in tab.candidates)
        tab2 = shell.tab_complete("alpha/")
        assert any(c.rstrip("/") == "grid" for c in (tab2.candidates or ("grid/",)))
        assert await shell.execute_async("cd beta/grid") == "ok"
        tab4 = shell.tab_complete("cd ../../al")
        assert "pha" in tab4.suffix or any("alpha" in c for c in tab4.candidates)

    asyncio.run(_run())


def test_shell_history_bang_and_shared():
    async def _run():
        from pathlib import Path
        import tempfile

        from wcore.dataplane import PlaneShell

        store = {"x": 1}
        catalog = CatalogNode()
        grid = catalog.add_node("grid")
        grid.add_leaf(
            LeafSpec(
                name="x",
                plane=Plane.CONFIG,
                value_type=int,
                getter=lambda: store["x"],
                setter=lambda v: store.__setitem__("x", int(v)),
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)

        with tempfile.TemporaryDirectory() as td:
            hist = Path(td) / ".plane_history"
            shell = PlaneShell(reg, history_path=hist, persist_history=True)
            assert await shell.execute_async("cd grid") == "ok"
            read_out = await shell.execute_async("x")
            assert "x =" in read_out
            out = await shell.execute_async("history")
            assert "1  cd grid" in out
            assert "2  x" in out

            bang = await shell.execute_async("!!")
            assert bang.startswith("history\r\n") or "1  cd grid" in bang

            n1 = await shell.execute_async("!1")
            assert n1.split("\r\n", 1)[0] == "cd grid"

            sess2 = shell.new_session()
            assert len(sess2.command_history) >= 2
            hist_txt = hist.read_text(encoding="utf-8")
            assert "cd grid" in hist_txt

            empty_shell = PlaneShell(reg, persist_history=False)
            err = await empty_shell.execute_async("!!")
            assert err.startswith("error:")

    asyncio.run(_run())


def test_shell_sessions_independent_pwd_shared_history():
    async def _run():
        from wcore.dataplane import PlaneShell

        catalog = CatalogNode()
        catalog.add_node("a")
        catalog.add_node("b")
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        template = PlaneShell(reg, persist_history=False)
        s1 = template.new_session()
        s2 = template.new_session()
        assert await s1.execute_async("cd a") == "ok"
        assert await s2.execute_async("cd b") == "ok"
        assert s1.pwd == "a"
        assert s2.pwd == "b"
        assert s1.command_history is s2.command_history
        assert "cd a" in list(s1.command_history.items)
        assert "cd b" in list(s2.command_history.items)

    asyncio.run(_run())
