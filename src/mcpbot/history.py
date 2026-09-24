"""历史组件的记录域（CMP-005：IF-008 保存、IF-009 查询）。

一次对话执行对应一个追加文件，文件名前缀是执行创建时刻的微秒时间戳，因此按名排序即按
时间排序，不需要另外维护索引文件。接纳提交把执行事实与用户消息写成同一条记录，使
IF-008 要求的「共同成立后确认」成为一次追加的成与不成。
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .durable import PersistenceUnavailable, append_record, read_records

_TERMINATED_INTERRUPT = "进程中断后按 FLW-001 异常分支标为已终止"


@dataclass
class ExecutionRecord:
    execution_id: str
    request_id: str
    file: Path
    records: List[dict]
    torn_tail: bool

    @property
    def accepted(self) -> bool:
        return any(r.get("t") == "accept" for r in self.records)

    @property
    def end(self) -> Optional[dict]:
        for record in reversed(self.records):
            if record.get("t") == "end":
                return record
        return None

    @property
    def user_text(self) -> str:
        for record in self.records:
            if record.get("t") == "accept":
                return record.get("user_text", "")
        return ""

    def assistant_messages(self) -> List[dict]:
        return [r for r in self.records if r.get("t") == "assistant"]


def _execution_file(prefix_us: int, execution_id: str) -> str:
    return f"{prefix_us:016d}__{execution_id}.jsonl"


class HistoryStore:
    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "executions"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ---------- 读取 ----------

    def _files(self) -> List[Path]:
        # 用 listdir 而不是 glob：glob 把「读不到目录」吞成空结果，IF-004 不接受伪装成空历史
        try:
            names = [name for name in os.listdir(self._dir) if name.endswith(".jsonl")]
        except OSError as exc:
            raise PersistenceUnavailable(str(exc)) from exc
        return sorted(self._dir / name for name in names)

    def load(self, path: Path) -> ExecutionRecord:
        try:
            records, torn = read_records(path)
        except OSError as exc:
            raise PersistenceUnavailable(str(exc)) from exc
        request_id = ""
        execution_id = path.name.split("__", 1)[1][: -len(".jsonl")]
        for record in records:
            if record.get("t") == "accept":
                request_id = record.get("request_id", "")
                execution_id = record.get("execution_id", execution_id)
                break
        return ExecutionRecord(execution_id, request_id, path, records, torn)

    def all_executions(self) -> List[ExecutionRecord]:
        return [self.load(path) for path in self._files()]

    def by_request_id(self, request_id: str) -> Optional[ExecutionRecord]:
        for item in reversed(self._files()):
            record = self.load(item)
            if record.request_id == request_id:
                return record
        return None

    # ---------- 保存 ----------

    def accept(self, execution_id: str, request_id: str, user_text: str, message_id: str) -> bool:
        """接纳提交：执行记录与用户消息共同写入，返回是否已确认保存。"""

        prefix = int(time.time() * 1_000_000)
        record = {
            "t": "accept",
            "execution_id": execution_id,
            "request_id": request_id,
            "message_id": message_id,
            "user_text": user_text,
            "role": "user",
            "created_at_us": prefix,
            "lifecycle": "进行中",
        }
        return append_record(self._dir / _execution_file(prefix, execution_id), record)

    def _path_for(self, execution_id: str) -> Optional[Path]:
        for path in self._files():
            if path.name.endswith(f"__{execution_id}.jsonl"):
                return path
        return None

    def save_assistant(self, execution_id: str, message_id: str, text: str, complete: bool) -> bool:
        path = self._path_for(execution_id)
        if path is None:
            return False
        return append_record(
            path,
            {
                "t": "assistant",
                "execution_id": execution_id,
                "message_id": message_id,
                "role": "assistant",
                "text": text,
                "complete": complete,
                "at_us": int(time.time() * 1_000_000),
            },
        )

    def append_end(
        self,
        execution_id: str,
        outcome: str,
        reason: str,
        answer_complete: bool,
        rounds_used: int,
        tool_calls_used: int,
        uncertain_calls: Optional[List[str]] = None,
    ) -> bool:
        path = self._path_for(execution_id)
        if path is None:
            return False
        return append_record(
            path,
            {
                "t": "end",
                "execution_id": execution_id,
                "outcome": outcome,
                "reason": reason,
                "answer_complete": answer_complete,
                "rounds_used": rounds_used,
                "tool_calls_used": tool_calls_used,
                "uncertain_calls": uncertain_calls or [],
                "at_us": int(time.time() * 1_000_000),
            },
        )

    # ---------- 重启恢复 ----------

    def recover(self) -> int:
        """把没有结束记录的遗留执行标为已终止并保留中断原因，不补写为完成。"""

        recovered = 0
        for item in self.all_executions():
            if item.end is not None:
                continue
            if not item.accepted:
                continue
            if self.append_end(
                item.execution_id,
                outcome="已终止",
                reason=_TERMINATED_INTERRUPT,
                answer_complete=False,
                rounds_used=0,
                tool_calls_used=0,
                uncertain_calls=[],
            ):
                recovered += 1
        return recovered

    # ---------- IF-009 查询（供 IF-004 映射） ----------

    @staticmethod
    def _view(item: ExecutionRecord) -> dict:
        """已保存事实的对外视图；同一形态供快照与分页查询复用。"""

        end = item.end or {}
        messages: List[dict] = []
        if item.accepted:
            accept = next(r for r in item.records if r.get("t") == "accept")
            messages.append(
                {
                    "message_id": accept["message_id"],
                    "role": "user",
                    "text": accept["user_text"],
                    "complete": True,
                    "saved": True,
                }
            )
        for record in item.assistant_messages():
            messages.append(
                {
                    "message_id": record["message_id"],
                    "role": "assistant",
                    "text": record["text"],
                    "complete": bool(record.get("complete")),
                    "saved": True,
                }
            )
        return {
            "execution_id": item.execution_id,
            "lifecycle": "已结束" if item.end else "进行中",
            "outcome": end.get("outcome"),
            "end_reason": end.get("reason"),
            "answer_complete": end.get("answer_complete"),
            "torn_tail_unconfirmed": item.torn_tail,
            "uncertain_calls": end.get("uncertain_calls", []),
            "messages": messages,
            "created_at_us": item.records[0].get("created_at_us") if item.records else None,
        }

    def query(self, cursor: Optional[str], limit: int, ceiling_bytes: int) -> Tuple[List[dict], Optional[str], str]:
        """返回按确定顺序排列的已保存事实、后续游标与读取边界。

        游标是不透明的文件名标记，指向本片段最早一条之前，保证同一片段内顺序稳定。
        响应超过上限时从最早一条起回退，游标随之收在保留的最早一条上，
        使「本次少给」与「下次接着给」不会丢掉任何已保存事实。
        """

        files = self._files()
        upper = len(files)
        if cursor:
            try:
                name = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                raise ValueError("无效游标")
            names = [path.name for path in files]
            if name not in names:
                raise ValueError("无效游标")
            upper = names.index(name)

        window = files[max(0, upper - limit) : upper]
        pairs = [(path.name, self._view(self.load(path))) for path in reversed(window)]
        while len(pairs) > 1:
            size = len(json.dumps([p[1] for p in pairs], ensure_ascii=False).encode("utf-8"))
            if size <= ceiling_bytes:
                break
            pairs.pop()
        if not pairs:
            return [], None, "已到最早记录"
        exhausted = pairs[-1][0] == files[0].name
        next_cursor = None if exhausted else base64.urlsafe_b64encode(pairs[-1][0].encode("utf-8")).decode("ascii")
        return [p[1] for p in pairs], next_cursor, "已到最早记录" if exhausted else "部分历史"

    def snapshot_of(self, execution_id: str) -> Optional[dict]:
        """IF-002 重复提交时返回的一次性状态快照。"""

        path = self._path_for(execution_id)
        if path is None:
            return None
        item = self.load(path)
        view = self._view(item)
        view["unconfirmed_items"] = [{"kind": "尾部记录未确认"}] if item.torn_tail else []
        view["tool_results"] = [r for r in item.records if r.get("t") == "tool_result"]
        return view
