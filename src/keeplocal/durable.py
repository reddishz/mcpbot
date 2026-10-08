"""记录域的提交点原语（ADR-002 持久化形态与写入保护）。

域内只有两种提交动作：追加一条记录，或用临时文件原子替换整份内容。两者的「已确认保存」
都定义为持久化同步成功，失败一律按未确认返回，不由调用方推测。
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
from pathlib import Path
from typing import List, Optional, Tuple


class DomainLockHeld(RuntimeError):
    """数据目录已被另一个进程独占。"""


class PersistenceUnavailable(RuntimeError):
    """记录域读不到：按 IF-004 口径必须与「没有历史」分开表达，不得伪装成空历史。"""


class DomainLock:
    """启动时取得的数据目录独占锁，进程存活期间持有，不写锁文件内容也不删除。"""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / ".domain.lock"
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise DomainLockHeld(f"数据目录已被其他进程占用：{self._path}") from exc
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def _fsync_dir(directory: Path) -> None:
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def append_record(path: Path, record: dict) -> bool:
    """追加一条 JSON 记录并完成持久化同步；返回是否已确认保存。"""

    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not path.exists()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if new_file:
            _fsync_dir(path.parent)
        return True
    except OSError:
        return False


def replace_json(path: Path, document) -> bool:
    """临时文件同步后原子改名；改名前的任何失败都不改变原内容。"""

    tmp = path.parent / f".{path.name}.tmp"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def read_records(path: Path) -> Tuple[List[dict], bool]:
    """读取追加域；尾部不完整记录丢弃并置 torn_tail，由调用方按未确认处理。"""

    records: List[dict] = []
    torn = False
    if not path.exists():
        return records, torn
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    for index, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                torn = True
    return records, torn


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default
