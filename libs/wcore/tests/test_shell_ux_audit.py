"""Shell UX 对照 WC-DEC-006 / WC-RUL-033~036 的审查测试。"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from wcore.dataplane import (
    CatalogNode,
    CommandHistory,
    LeafSpec,
    Plane,
    PlaneRegistry,
    PlaneShell,
)
from wcore.dataplane.line_edit import FeedResult, InputAction, LineEditor
from wcore.dataplane.shell import parse_shell_path


def _tree() -> tuple[PlaneRegistry, dict]:
    store = {"ax": 1, "bx": 2}
    catalog = CatalogNode()
    alpha = catalog.add_node("alpha")
    ag = alpha.add_node("grid")
    ag.add_leaf(
        LeafSpec(
            name="x",
            plane=Plane.CONFIG,
            value_type=int,
            getter=lambda: store["ax"],
            setter=lambda v: store.__setitem__("ax", int(v)),
        )
    )
    beta = catalog.add_node("beta")
    bg = beta.add_node("grid")
    bg.add_leaf(
        LeafSpec(
            name="x",
            plane=Plane.CONFIG,
            value_type=int,
            getter=lambda: store["bx"],
            setter=lambda v: store.__setitem__("bx", int(v)),
        )
    )
    reg = PlaneRegistry(single_root=True)
    reg.set_single_root_tree(catalog)
    return reg, store


def test_parse_must_preserve_dotdot():
    assert parse_shell_path("..").segments == ("..",)
    assert parse_shell_path("../foo").segments == ("..", "foo")
    assert parse_shell_path("a.b").segments == ("a", "b")
    # registry-style replace would destroy these
    assert ".." in parse_shell_path("../x").segments


def test_rul033_cd_ls_dotdot_and_trailing():
    async def _run():
        reg, _ = _tree()
        sh = PlaneShell(reg, persist_history=False)
        assert await sh.execute_async("cd alpha/grid") == "ok"
        assert sh.pwd == "alpha/grid"
        assert await sh.execute_async("cd ..") == "ok"
        assert sh.pwd == "alpha"
        assert await sh.execute_async("cd ../beta/grid") == "ok"
        assert sh.pwd == "beta/grid"
        assert await sh.execute_async("cd .") == "ok"
        assert sh.pwd == "beta/grid"
        # ls path peek must not change pwd
        out = await sh.execute_async("ls ../../alpha/grid")
        assert "x" in out
        assert sh.pwd == "beta/grid"
        out2 = await sh.execute_async("ls ..")
        assert "grid/" in out2
        # trailing slash peek
        await sh.execute_async("cd /")
        out3 = await sh.execute_async("ls alpha/")
        assert "grid/" in out3
        assert sh.pwd == "" or sh.pwd == "/"

    asyncio.run(_run())


def test_rul034_relative_and_full_path_tab():
    async def _run():
        reg, _ = _tree()
        sh = PlaneShell(reg, persist_history=False)
        await sh.execute_async("cd alpha")
        # regression: single-segment relative cd tab against pwd
        tab = sh.tab_complete("cd gri")
        assert tab.suffix == "d" or any(c.rstrip("/") == "grid" for c in tab.candidates)
        # full path
        tab2 = sh.tab_complete("beta/g")
        assert "rid" in tab2.suffix or any("grid" in c for c in tab2.candidates)
        tab3 = sh.tab_complete("alpha/")
        assert (
            any(c.rstrip("/") == "grid" for c in tab3.candidates)
            or tab3.suffix in ("", "grid")
        )
        # ../ tab
        await sh.execute_async("cd alpha/grid")
        tab4 = sh.tab_complete("cd ../../be")
        assert "ta" in tab4.suffix or any("beta" in c for c in tab4.candidates)
        # write left
        tab5 = sh.tab_complete("alpha/grid/x")
        assert tab5.suffix == "" or "x" in tab5.suffix or not tab5.candidates
        # ls path tab
        tab6 = sh.tab_complete("ls ../../beta/")
        assert any("grid" in c for c in tab6.candidates) or tab6.suffix != ""

    asyncio.run(_run())


def test_rul034_tab_uses_cursor_left_semantics():
    """光标左侧补全：模拟 editor.text[:cursor]。"""
    reg, _ = _tree()
    sh = PlaneShell(reg, persist_history=False)
    # 右侧噪声不得参与
    left = "cd al"
    full_wrong = "cd alXXXX"
    t_left = sh.tab_complete(left)
    t_full = sh.tab_complete(full_wrong)
    assert t_left.suffix.startswith("pha") or any("alpha" in c for c in t_left.candidates)
    assert t_full.suffix == "" and not t_full.candidates


def test_rul035_history_bang_renumber_persist():
    async def _run():
        reg, store = _tree()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".plane_history"
            sh = PlaneShell(reg, history_path=path, persist_history=True, history_max=3)
            await sh.execute_async("cd alpha")
            await sh.execute_async("cd grid")
            await sh.execute_async("x")
            # capacity 3: oldest dropped after more cmds
            await sh.execute_async("pwd")
            hist = await sh.execute_async("history")
            # numbers restart from 1 in buffer
            assert hist.split("\r\n")[0].startswith("1  ")
            assert "cd alpha" not in hist  # dropped
            assert "pwd" in hist

            err = await sh.execute_async("!!")
            assert err.startswith("history\r\n") or "1  " in err

            # !n bounds
            bad = await sh.execute_async("!99")
            assert bad.startswith("error:")
            empty = PlaneShell(reg, persist_history=False)
            assert (await empty.execute_async("!!")).startswith("error:")

            # persist reload
            sh2 = PlaneShell(reg, history_path=path, persist_history=True, history_max=3)
            assert len(sh2.command_history) >= 1
            assert path.is_file()

            await sh.execute_async("cd /")
            await sh.execute_async("pwd")
            out = await sh.execute_async("!!")
            assert out.startswith("pwd\r\n") or out.split("\r\n", 1)[0] == "pwd"

    asyncio.run(_run())


def test_rul035_bang_appends_expanded_not_bang():
    async def _run():
        reg, _ = _tree()
        sh = PlaneShell(reg, persist_history=False)
        await sh.execute_async("cd alpha")
        await sh.execute_async("!!")  # expands to cd alpha
        items = list(sh.command_history.items)
        assert "!!" not in items
        assert items[-1] == "cd alpha"

    asyncio.run(_run())


def test_rul036_session_pwd_independent_history_shared():
    async def _run():
        reg, _ = _tree()
        tmpl = PlaneShell(reg, persist_history=False)
        s1 = tmpl.new_session()
        s2 = tmpl.new_session()
        await s1.execute_async("cd alpha")
        await s2.execute_async("@ state")
        await s2.execute_async("cd beta")
        assert s1.pwd == "alpha"
        assert s2.pwd == "beta"
        assert s1.view_plane is None
        assert s2.view_plane == "state"
        assert s1.command_history is s2.command_history
        ed1 = LineEditor(history=s1.command_history)
        ed2 = LineEditor(history=s2.command_history)
        ed1.begin_line()
        ed1.apply(FeedResult(InputAction.UP))
        assert ed1.text in list(s1.command_history.items)

    asyncio.run(_run())


def test_rul036_line_editor_draft_and_no_submit_append():
    h = CommandHistory(maxlen=10)
    h.append("one")
    ed = LineEditor(history=h)
    ed.begin_line()
    for ch in "draft":
        ed.apply(FeedResult(InputAction.CHAR, ch))
    ed.apply(FeedResult(InputAction.UP))
    assert ed.text == "one"
    ed.apply(FeedResult(InputAction.DOWN))
    assert ed.text == "draft"
    submitted = ed.submit()
    assert submitted == "draft"
    assert list(h.items) == ["one"]  # submit 不入史


def test_help_and_greeting_mention_history():
    reg, _ = _tree()
    sh = PlaneShell(reg, persist_history=False, welcome="hi")
    g = sh.greeting()
    assert "history" in g.lower() and ("!!" in g or "!n" in g)
    h = sh._cmd_help()
    assert "history" in h and "!!" in h


def test_command_history_hard_cap():
    h = CommandHistory(maxlen=500)
    assert h._items.maxlen == 200
