"""平面 Shell 与行编辑（自 WC-D007 迁移）。"""

import asyncio

from wcore.dataplane import (
    CatalogNode,
    ControlSpec,
    LeafSpec,
    ParamSpec,
    Plane,
    PlaneRegistry,
    PlaneShell,
)
from wcore.dataplane.coerce import coerce_value
from wcore.dataplane.line_edit import CsiDecoder, InputAction, LineEditor
from wcore.dataplane.port import bind_localhost_port
from wcore.dataplane.telnet import _line_text, _one_char
from wcore.dataplane.types import ControlResult


def _shell_with_control():
    catalog = CatalogNode()
    ctrl_ns = catalog.add_node("control", plane=Plane.CONTROL)

    async def _lot_handler(params):
        return ControlResult(ok=True, code="OK", message=f"lot={params.get('quote')}")

    ctrl_ns.add_control(
        ControlSpec(
            name="lot",
            description="relock lot",
            params=[ParamSpec("quote", float, required=True, description="quote amount")],
            handler=_lot_handler,
        )
    )
    ctrl_ns.add_control(
        ControlSpec(
            name="buy",
            description="manual buy",
            params=[ParamSpec("follow", bool, required=False)],
            handler=lambda p: ControlResult(ok=True, code="OK", message="buy"),
        )
    )
    reg = PlaneRegistry(single_root=True)
    reg.set_single_root_tree(catalog)
    shell = PlaneShell(reg)
    return shell


def test_shell_control_ls_summary():
    shell = _shell_with_control()

    async def _run():
        await shell.execute_async("cd control")
        out = await shell.execute_async("ls")
        assert "lot" in out and "quote" in out
        assert "buy" in out and "follow?" in out

    asyncio.run(_run())


def test_shell_control_usage_on_bare_name():
    shell = _shell_with_control()

    async def _run():
        await shell.execute_async("cd control")
        out = await shell.execute_async("lot")
        assert "usage:" in out
        assert "quote" in out

    asyncio.run(_run())


def test_shell_control_help():
    """WC-RUL-009：help <control> / -h / --help 出用法且不 invoke。"""
    calls = {"n": 0}
    catalog = CatalogNode()
    ctrl_ns = catalog.add_node("control", plane=Plane.CONTROL)

    async def _buy(params):
        calls["n"] += 1
        return ControlResult(ok=True, code="OK", message="buy")

    ctrl_ns.add_control(
        ControlSpec(
            name="buy",
            description="manual buy",
            params=[ParamSpec("follow", bool, required=False)],
            handler=_buy,
        )
    )
    reg = PlaneRegistry(single_root=True)
    reg.set_single_root_tree(catalog)
    shell = PlaneShell(reg)

    async def _run():
        await shell.execute_async("cd control")
        for line in ("help buy", "buy -h", "buy --help", "buy follow=true --help"):
            out = await shell.execute_async(line)
            assert "manual buy" in out
            assert "follow" in out
            assert "usage:" in out
        assert calls["n"] == 0
        out = await shell.execute_async("buy")
        assert out.startswith("ok:")
        assert calls["n"] == 1
        global_help = await shell.execute_async("help")
        assert "help <control>" in global_help
        assert "describe" not in global_help
        missing = await shell.execute_async("describe buy")
        assert "error" in missing

    asyncio.run(_run())


def test_shell_control_buy_no_args():
    shell = _shell_with_control()

    async def _run():
        await shell.execute_async("cd control")
        out = await shell.execute_async("buy")
        assert out.startswith("ok:")

    asyncio.run(_run())


def test_registry_read_write():
    store = {"x": 1}

    async def _run():
        catalog = CatalogNode()
        cfg = catalog.add_node("config", plane=Plane.CONFIG)
        cfg.add_leaf(
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
        await reg.write("config/x", 5)
        assert store["x"] == 5

    asyncio.run(_run())


def test_shell_cd_ls():
    store = {"a": 1}

    async def _run():
        catalog = CatalogNode()
        cfg = catalog.add_node("config", plane=Plane.CONFIG)
        cfg.add_leaf(
            LeafSpec(
                name="a",
                plane=Plane.CONFIG,
                value_type=int,
                getter=lambda: store["a"],
                setter=lambda v: store.__setitem__("a", int(v)),
            )
        )
        reg = PlaneRegistry(single_root=True)
        reg.set_single_root_tree(catalog)
        shell = PlaneShell(reg)
        assert await shell.execute_async("cd config") == "ok"
        out = await shell.execute_async("ls")
        assert "a" in out
        out2 = await shell.execute_async("a=9")
        assert out2.startswith("ok")
        assert store["a"] == 9

    asyncio.run(_run())


def test_shell_ls_subdir_without_cd():
    shell = _shell_with_control()

    async def _run():
        out = await shell.execute_async("ls control")
        assert "lot" in out and "buy" in out
        assert "control/" not in out

        await shell.execute_async("cd control")
        out2 = await shell.execute_async("ls")
        assert "lot" in out2 and "buy" in out2
        assert await shell.execute_async("pwd") == "control"

    asyncio.run(_run())


def test_shell_ls_fuzzy_filter():
    shell = _shell_with_control()

    async def _run():
        await shell.execute_async("cd control")
        out = await shell.execute_async("ls lot")
        assert "lot" in out
        assert "buy" not in out

    asyncio.run(_run())


def test_coerce_and_line_edit():
    assert coerce_value("true", bool) is True
    ed = LineEditor(prompt="> ")
    assert ed.begin_line()
    dec = CsiDecoder()
    assert dec.feed("\r").action == InputAction.SUBMIT


def test_port_bind_localhost():
    host, port, sock = bind_localhost_port(35555, 3)
    assert host == "127.0.0.1"
    sock.close()


def test_telnet_line_helpers():
    assert _line_text(b"hi\n") == "hi"
    assert _one_char(b"x") == "x"


def test_runtime_info_format():
    from datetime import datetime, timedelta

    from wcore.dataplane.runtime_info import (
        format_runtime_lines,
        format_uptime,
        start_time_text,
        uptime_text,
    )

    start = datetime(2026, 6, 25, 10, 0, 0)
    now = start + timedelta(days=1, hours=2, minutes=3, seconds=4)
    assert format_uptime(now - start) == "1d 02:03:04"
    assert start_time_text(start) == "2026-06-25 10:00:00"
    assert uptime_text(start, now=now) == "1d 02:03:04"
    assert format_runtime_lines(start, now=now) == [
        "started: 2026-06-25 10:00:00",
        "uptime: 1d 02:03:04",
    ]


def test_shell_greeting_includes_runtime():
    from datetime import datetime

    shell = _shell_with_control()
    shell = PlaneShell(
        shell._registry,
        welcome="test shell",
        start_time=datetime(2026, 6, 25, 8, 30, 0),
    )
    greeting = shell.greeting()
    assert "test shell" in greeting
    assert "started: 2026-06-25 08:30:00" in greeting
    assert "uptime:" in greeting
    assert "Type 'help' for commands" in greeting
