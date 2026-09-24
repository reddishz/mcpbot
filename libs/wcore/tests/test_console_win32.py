"""console_win32 单元测试（WC-D010）。"""

from __future__ import annotations

import ctypes
import importlib
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

import wcore.console_win32 as console_win32


@pytest.fixture(autouse=True)
def reset_console_win32_state():
    console_win32._attempted = False
    yield
    console_win32._attempted = False


@contextmanager
def _win32_console(kernel32: MagicMock):
    fake_windll = MagicMock(kernel32=kernel32)
    with patch.object(sys, "platform", "win32"), patch.object(
        ctypes, "windll", fake_windll, create=True
    ):
        yield


def test_skips_on_non_windows():
    with patch.object(sys, "platform", "linux"):
        assert console_win32.disable_legacy_console_quick_edit() is False


def test_skips_on_windows_terminal(monkeypatch):
    monkeypatch.setenv("WT_SESSION", "1")
    with patch.object(sys, "platform", "win32"):
        assert console_win32.disable_legacy_console_quick_edit() is False


def test_skips_on_conemu(monkeypatch):
    monkeypatch.setenv("ConEmuANSI", "ON")
    with patch.object(sys, "platform", "win32"):
        assert console_win32.disable_legacy_console_quick_edit() is False


def test_skips_when_no_console_window():
    fake_kernel32 = MagicMock()
    fake_kernel32.GetConsoleWindow.return_value = 0

    with _win32_console(fake_kernel32):
        assert console_win32.disable_legacy_console_quick_edit() is False
    fake_kernel32.GetConsoleMode.assert_not_called()


def test_skips_when_quick_edit_already_disabled():
    fake_kernel32 = MagicMock()
    fake_kernel32.GetConsoleWindow.return_value = 1
    fake_kernel32.GetStdHandle.return_value = 7
    fake_kernel32.GetConsoleMode.side_effect = lambda handle, mode: _set_mode(mode, 0x0080)

    with _win32_console(fake_kernel32):
        assert console_win32.disable_legacy_console_quick_edit() is False
    fake_kernel32.SetConsoleMode.assert_not_called()


def test_disables_quick_edit_on_legacy_console():
    fake_kernel32 = MagicMock()
    fake_kernel32.GetConsoleWindow.return_value = 1
    fake_kernel32.GetStdHandle.return_value = 7
    fake_kernel32.GetConsoleMode.side_effect = lambda handle, mode: _set_mode(mode, 0x00C0)
    fake_kernel32.SetConsoleMode.return_value = 1

    with _win32_console(fake_kernel32):
        assert console_win32.disable_legacy_console_quick_edit() is True

    fake_kernel32.SetConsoleMode.assert_called_once()
    _, new_mode = fake_kernel32.SetConsoleMode.call_args[0]
    assert new_mode == 0x0080


def test_silent_when_get_console_mode_fails():
    fake_kernel32 = MagicMock()
    fake_kernel32.GetConsoleWindow.return_value = 1
    fake_kernel32.GetStdHandle.return_value = 7
    fake_kernel32.GetConsoleMode.return_value = 0

    with _win32_console(fake_kernel32):
        assert console_win32.disable_legacy_console_quick_edit() is False


def test_idempotent_second_call_returns_false():
    fake_kernel32 = MagicMock()
    fake_kernel32.GetConsoleWindow.return_value = 1
    fake_kernel32.GetStdHandle.return_value = 7
    fake_kernel32.GetConsoleMode.side_effect = lambda handle, mode: _set_mode(mode, 0x00C0)
    fake_kernel32.SetConsoleMode.return_value = 1

    with _win32_console(fake_kernel32):
        assert console_win32.disable_legacy_console_quick_edit() is True
        assert console_win32.disable_legacy_console_quick_edit() is False

    fake_kernel32.SetConsoleMode.assert_called_once()


def test_wcore_import_triggers_disable_once():
    import wcore

    with patch.object(console_win32, "disable_legacy_console_quick_edit") as mocked:
        importlib.reload(wcore)
        mocked.assert_called_once()


def _set_mode(mode_ref, value: int) -> int:
    mode_ref._obj.value = value
    return 1
