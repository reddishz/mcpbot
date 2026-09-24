"""Telnet 传输层（WC-R012 / WC-RUL-036）。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Type

from wcore.dataplane.line_edit import CsiDecoder, InputAction, LineEditor
from wcore.dataplane.port import bind_localhost_port

from .history import CommandHistory
from .registry import PlaneRegistry
from .runtime_info import resolve_start_time
from .shell import PlaneShell, TabResult, _HISTORY_FILE

logger = logging.getLogger(__name__)


def _line_text(line: Any) -> str:
    if isinstance(line, str):
        return line.rstrip("\r\n")
    if isinstance(line, (bytes, bytearray)):
        return bytes(line).decode("utf-8", errors="replace").rstrip("\r\n")
    return str(line).rstrip("\r\n")


def _one_char(chunk: Any) -> str:
    if isinstance(chunk, str):
        return chunk[:1]
    if isinstance(chunk, (bytes, bytearray)):
        return bytes(chunk)[:1].decode("utf-8", errors="replace")
    return str(chunk)[:1]


async def _read_command_line(
    reader: Any,
    writer: Any,
    shell: PlaneShell,
    editor: LineEditor,
) -> Optional[str]:
    """读一行。返回 None 表示对端断开。Ctrl+C 清行后继续读，不返回。"""
    decoder = CsiDecoder()
    writer.write(editor.begin_line())
    await writer.drain()
    while True:
        chunk = await reader.read(1)
        if not chunk:
            return None
        ch = _one_char(chunk)
        if not ch:
            continue
        result = decoder.feed(ch)
        if result.action == InputAction.NONE:
            continue
        if result.action == InputAction.SUBMIT:
            writer.write("\r\n")
            await writer.drain()
            return editor.submit()
        if result.action == InputAction.INTERRUPT:
            # WC-RUL-036：清行重绘，不结束会话
            writer.write("^C\r\n")
            await writer.drain()
            writer.write(editor.begin_line())
            await writer.drain()
            continue
        if result.action == InputAction.TAB:
            await _handle_tab(writer, shell, editor)
            continue
        redraw = editor.apply(result)
        if redraw:
            writer.write(redraw)
            await writer.drain()


async def _handle_tab(writer: Any, shell: PlaneShell, editor: LineEditor) -> None:
    # WC-RUL-034：仅光标左侧参与补全
    left = editor.text[: editor.cursor]
    result: TabResult = shell.tab_complete(left)
    redraw = editor.insert_text(result.suffix)
    if redraw:
        writer.write(redraw)
        await writer.drain()
    if result.candidates:
        listing = "\r\n".join(result.candidates)
        writer.write(f"\r\n{listing}\r\n{editor.redraw()}")
        await writer.drain()


async def _session(template: PlaneShell, reader: Any, writer: Any) -> None:
    shell = template.new_session()
    editor = LineEditor(prompt=shell.prompt_line(), history=shell.command_history)
    writer.write(shell.greeting())
    await writer.drain()
    try:
        while True:
            text = await _read_command_line(reader, writer, shell, editor)
            if text is None:
                break
            if text == "":
                continue
            try:
                out = await shell.execute_async(text)
            except Exception as exc:
                out = f"error: {exc}"
            editor.set_prompt(shell.prompt_line())
            if out == "__QUIT__":
                writer.write("bye\r\n")
                await writer.drain()
                break
            if out:
                if not out.endswith("\r\n"):
                    out += "\r\n"
                writer.write(out)
                await writer.drain()
    finally:
        writer.close()


async def start_plane_telnet_server(
    registry: PlaneRegistry,
    *,
    port_start: int = 3333,
    max_tries: int = 10,
    welcome: str = "WCore Plane Shell",
    workdir: Optional[Path] = None,
    port_file_name: str = ".plane_port",
    history_file_name: str = _HISTORY_FILE,
    persist_history: bool = True,
    history_max: int = 50,
    timeout: int = 300,
    start_time: Optional[datetime] = None,
    log: Optional[logging.Logger] = None,
    shell_class: Type[PlaneShell] = PlaneShell,
) -> Optional[asyncio.AbstractServer]:
    out = log or logger
    try:
        import telnetlib3
    except ImportError:
        out.warning("telnetlib3 not installed; plane telnet server skipped")
        return None

    host, port, probe = bind_localhost_port(port_start, max_tries)
    probe.close()

    hist_path: Optional[Path] = None
    if persist_history and workdir is not None:
        hist_path = workdir / history_file_name
    history = CommandHistory(maxlen=history_max, path=hist_path)

    shell = shell_class(
        registry,
        welcome=welcome,
        start_time=start_time if start_time is not None else resolve_start_time(),
        history=history,
    )

    async def shell_cb(reader: Any, writer: Any) -> None:
        await _session(shell, reader, writer)

    server = await telnetlib3.create_server(
        host=host,
        port=port,
        shell=shell_cb,
        timeout=timeout,
    )
    if workdir is not None:
        try:
            (workdir / port_file_name).write_text(str(port), encoding="utf-8")
        except OSError as exc:
            out.debug("could not write %s: %s", port_file_name, exc)
    return server
