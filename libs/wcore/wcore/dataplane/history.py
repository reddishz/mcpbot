"""进程级 Shell 命令历史（WC-DEC-006 / WC-RUL-035）。"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Deque, Optional, Sequence


_DEFAULT_MAX = 50
_HARD_CAP = 200


class CommandHistory:
    """共享命令历史：内存 deque + 可选 `.plane_history` 落盘。"""

    def __init__(
        self,
        *,
        maxlen: int = _DEFAULT_MAX,
        path: Optional[Path] = None,
    ) -> None:
        cap = max(1, min(int(maxlen), _HARD_CAP))
        self._items: Deque[str] = deque(maxlen=cap)
        self._path = path
        if path is not None:
            self.load()

    @property
    def items(self) -> Sequence[str]:
        return self._items

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def clear(self) -> None:
        self._items.clear()

    def append(self, text: str) -> None:
        line = text.strip("\r\n")
        if not line:
            return
        if self._items and self._items[-1] == line:
            return
        self._items.append(line)
        self.save()

    def last(self) -> str:
        if not self._items:
            raise IndexError("history empty")
        return self._items[-1]

    def get(self, n: int) -> str:
        """1-based index within the current buffer (oldest = 1)."""
        if n < 1 or n > len(self._items):
            raise IndexError(f"history {n} not found")
        return self._items[n - 1]

    def format_lines(self, *, last_k: Optional[int] = None) -> str:
        items = list(self._items)
        if not items:
            return "(empty)"
        if last_k is not None:
            if last_k < 1:
                return "error: history count must be positive"
            start = max(0, len(items) - last_k)
            pairs = [(i + 1, items[i]) for i in range(start, len(items))]
        else:
            pairs = [(i + 1, cmd) for i, cmd in enumerate(items)]
        return "\r\n".join(f"{n}  {cmd}" for n, cmd in pairs)

    def load(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return
        lines = [ln.rstrip("\r\n") for ln in raw.splitlines() if ln.strip()]
        self._items.clear()
        limit = self._items.maxlen or len(lines)
        for ln in lines[-limit:]:
            self._items.append(ln)

    def save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            body = "\n".join(self._items)
            if body:
                body += "\n"
            self._path.write_text(body, encoding="utf-8")
        except OSError:
            pass
