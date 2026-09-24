"""LineEditor — 行缓冲、光标、历史浏览（WC-RUL-036）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional, Sequence

from .history import CommandHistory


class InputAction(Enum):
    NONE = auto()
    CHAR = auto()
    SUBMIT = auto()
    BACKSPACE = auto()
    TAB = auto()
    INTERRUPT = auto()
    UP = auto()
    DOWN = auto()
    LEFT = auto()
    RIGHT = auto()
    HOME = auto()
    END = auto()
    DELETE = auto()
    IGNORE = auto()


@dataclass(frozen=True)
class FeedResult:
    action: InputAction
    char: str = ""


class CsiDecoder:
    """CSI / SS3 escape 序列解码。"""

    def __init__(self) -> None:
        self._state = "normal"
        self._params = ""

    def reset(self) -> None:
        self._state = "normal"
        self._params = ""

    def feed(self, ch: str) -> FeedResult:
        if self._state == "normal":
            return self._feed_normal(ch)
        if self._state == "esc":
            return self._feed_esc(ch)
        if self._state == "csi":
            return self._feed_csi(ch)
        if self._state == "ss3":
            return self._feed_ss3(ch)
        self.reset()
        return FeedResult(InputAction.IGNORE)

    def _feed_normal(self, ch: str) -> FeedResult:
        if ch == "\x1b":
            self._state = "esc"
            return FeedResult(InputAction.NONE)
        if ch in "\r\n":
            return FeedResult(InputAction.SUBMIT)
        if ch in ("\x7f", "\x08"):
            return FeedResult(InputAction.BACKSPACE)
        if ch == "\t":
            return FeedResult(InputAction.TAB)
        if ch == "\x03":
            return FeedResult(InputAction.INTERRUPT)
        if ch < " ":
            return FeedResult(InputAction.IGNORE)
        return FeedResult(InputAction.CHAR, ch)

    def _feed_esc(self, ch: str) -> FeedResult:
        if ch == "[":
            self._state = "csi"
            self._params = ""
            return FeedResult(InputAction.NONE)
        if ch == "O":
            self._state = "ss3"
            return FeedResult(InputAction.NONE)
        self.reset()
        return FeedResult(InputAction.IGNORE)

    def _feed_csi(self, ch: str) -> FeedResult:
        if ch.isdigit():
            self._params += ch
            return FeedResult(InputAction.NONE)
        if ch == "~":
            action = self._csi_tilde_action(self._params)
            self.reset()
            return action
        action = self._csi_letter_action(ch)
        self.reset()
        return action

    def _feed_ss3(self, ch: str) -> FeedResult:
        action = self._csi_letter_action(ch)
        self.reset()
        return action

    @staticmethod
    def _csi_letter_action(ch: str) -> FeedResult:
        mapping = {
            "A": InputAction.UP,
            "B": InputAction.DOWN,
            "C": InputAction.RIGHT,
            "D": InputAction.LEFT,
            "H": InputAction.HOME,
            "F": InputAction.END,
        }
        return FeedResult(mapping.get(ch, InputAction.IGNORE))

    @staticmethod
    def _csi_tilde_action(params: str) -> FeedResult:
        mapping = {
            "1": InputAction.HOME,
            "4": InputAction.END,
            "3": InputAction.DELETE,
        }
        return FeedResult(mapping.get(params, InputAction.IGNORE))


class LineEditor:
    """传输无关行编辑器；Telnet 层消费 redraw 输出。

    历史浏览绑定进程级 :class:`CommandHistory`；入史由 Shell 在展开后完成。
    """

    def __init__(
        self,
        *,
        prompt: str = "> ",
        history: Optional[CommandHistory] = None,
        history_max: int = 50,
    ) -> None:
        self._prompt = prompt
        self._chars: List[str] = []
        self._cursor = 0
        self._cmd_history = history if history is not None else CommandHistory(maxlen=history_max)
        self._hist_idx = -1
        self._draft = ""

    def set_prompt(self, prompt: str) -> None:
        self._prompt = prompt

    @property
    def text(self) -> str:
        return "".join(self._chars)

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def history(self) -> Sequence[str]:
        return self._cmd_history.items

    @property
    def command_history(self) -> CommandHistory:
        return self._cmd_history

    def begin_line(self) -> str:
        """新行开始：清空缓冲，保留历史，返回初始重绘。"""
        self._chars = []
        self._cursor = 0
        self._hist_idx = -1
        self._draft = ""
        return self.redraw()

    def redraw(self) -> str:
        text = self.text
        tail = len(text) - self._cursor
        moves = f"\x1b[{tail}D" if tail else ""
        return f"\r{self._prompt}{text}\x1b[K{moves}"

    def submit(self) -> str:
        """返回当前行文本；不入史（由 PlaneShell 在 ! 展开后入史）。"""
        return self.text

    def apply(self, result: FeedResult) -> Optional[str]:
        action = result.action
        if action in (InputAction.NONE, InputAction.IGNORE):
            return None
        if action == InputAction.CHAR:
            return self._insert(result.char)
        if action == InputAction.BACKSPACE:
            return self._backspace()
        if action == InputAction.DELETE:
            return self._delete_forward()
        if action == InputAction.LEFT:
            return self._move_left()
        if action == InputAction.RIGHT:
            return self._move_right()
        if action == InputAction.HOME:
            return self._home()
        if action == InputAction.END:
            return self._end()
        if action == InputAction.UP:
            return self._history_up()
        if action == InputAction.DOWN:
            return self._history_down()
        return None

    def insert_text(self, suffix: str) -> Optional[str]:
        if not suffix:
            return None
        self._exit_history_browse()
        for ch in suffix:
            self._chars.insert(self._cursor, ch)
            self._cursor += 1
        return self.redraw()

    def _exit_history_browse(self) -> None:
        if self._hist_idx != -1:
            self._hist_idx = -1
            self._draft = ""

    def _insert(self, ch: str) -> str:
        self._exit_history_browse()
        self._chars.insert(self._cursor, ch)
        self._cursor += 1
        return self.redraw()

    def _backspace(self) -> Optional[str]:
        if self._cursor == 0:
            return None
        self._exit_history_browse()
        self._chars.pop(self._cursor - 1)
        self._cursor -= 1
        return self.redraw()

    def _delete_forward(self) -> Optional[str]:
        if self._cursor >= len(self._chars):
            return None
        self._exit_history_browse()
        self._chars.pop(self._cursor)
        return self.redraw()

    def _move_left(self) -> Optional[str]:
        if self._cursor == 0:
            return None
        self._cursor -= 1
        return self.redraw()

    def _move_right(self) -> Optional[str]:
        if self._cursor >= len(self._chars):
            return None
        self._cursor += 1
        return self.redraw()

    def _home(self) -> Optional[str]:
        if self._cursor == 0:
            return None
        self._cursor = 0
        return self.redraw()

    def _end(self) -> Optional[str]:
        end = len(self._chars)
        if self._cursor == end:
            return None
        self._cursor = end
        return self.redraw()

    def _history_up(self) -> Optional[str]:
        items = self._cmd_history.items
        if not items:
            return None
        if self._hist_idx == -1:
            self._draft = self.text
            self._hist_idx = len(items) - 1
        elif self._hist_idx > 0:
            self._hist_idx -= 1
        else:
            return None
        self._set_text(items[self._hist_idx])
        return self.redraw()

    def _history_down(self) -> Optional[str]:
        items = self._cmd_history.items
        if self._hist_idx == -1:
            return None
        if self._hist_idx < len(items) - 1:
            self._hist_idx += 1
            self._set_text(items[self._hist_idx])
        else:
            self._hist_idx = -1
            self._set_text(self._draft)
        return self.redraw()

    def _set_text(self, text: str) -> None:
        self._chars = list(text)
        self._cursor = len(self._chars)
