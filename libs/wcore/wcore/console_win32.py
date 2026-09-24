"""Windows legacy console Quick Edit mitigation (WC-D010)."""

from __future__ import annotations

import os
import sys

_STD_INPUT_HANDLE = -10
_ENABLE_EXTENDED_FLAGS = 0x0080
_ENABLE_QUICK_EDIT_MODE = 0x0040

_attempted = False


def _is_modern_terminal_host() -> bool:
    if os.environ.get("WT_SESSION"):
        return True
    if os.environ.get("ConEmuANSI") or os.environ.get("ANSICON"):
        return True
    return False


def disable_legacy_console_quick_edit() -> bool:
    """Disable Quick Edit on legacy Windows console hosts.

    Returns True when Quick Edit was disabled, False otherwise.
    Safe to call multiple times; only the first call attempts modification.
    """
    global _attempted
    if _attempted:
        return False
    _attempted = True

    if sys.platform != "win32":
        return False
    if _is_modern_terminal_host():
        return False

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if kernel32.GetConsoleWindow() == 0:
            return False

        handle = kernel32.GetStdHandle(_STD_INPUT_HANDLE)
        if handle in (0, -1):
            return False

        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False

        if not (mode.value & _ENABLE_QUICK_EDIT_MODE):
            return False

        new_mode = (mode.value | _ENABLE_EXTENDED_FLAGS) & ~_ENABLE_QUICK_EDIT_MODE
        if not kernel32.SetConsoleMode(handle, new_mode):
            return False
        return True
    except Exception:
        return False
