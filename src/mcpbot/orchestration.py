"""对话编排（CMP-002：FLW-001 状态机、CON-009 名额、CON-010 预算、IF-003 事件）。

一次执行持有自己的预算与阶段事实；名额只在本地工作与有界收尾实际结束后释放，
不以结束反馈是否送达浏览器为前提。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Dict, List, Optional, Tuple

from .config import McpBotConfig
from .history import HistoryStore
from .ollama import OllamaClient, TurnResult

logger = logging.getLogger(__name__)

STAGE_GENERATING = "生成"
STAGE_WRAPUP = "收尾"

OUTCOME_DONE = "完成"
OUTCOME_FAILED = "失败"
OUTCOME_TERMINATED = "已终止"

CONTEXT_TAIL_MESSAGES = 8


@dataclass
class Execution:
    execution_id: str
    request_id: str
    deadline_seconds: float
    started_at: float
    rounds_limit: int = 1
    tool_calls_limit: int = 0
    rounds_used: int = 0
    tool_calls_used: int = 0
    lifecycle: str = "进行中"
    stage: str = ""
    client_gone: bool = False
    wrapup_reason: str = "正常生成完毕"
    uncertain_calls: List[str] = field(default_factory=list)
    task: Optional["asyncio.Task"] = None

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_seconds - (time.monotonic() - self.started_at))

    def can_start_upstream(self) -> bool:
        """进行中且预算未耗尽、客户端未放弃时才允许发起新的上游操作。"""

        return self.lifecycle == "进行中" and not self.client_gone and self.remaining_seconds() > 0

    def to_wrapup(self, reason: str) -> None:
        self.lifecycle = "收尾中"
        self.stage = STAGE_WRAPUP
        self.wrapup_reason = reason


class SlotBusy(Exception):
    """已有执行占用名额（CON-009 固定为 1，不排队）。"""


class ExecutionSlot:
    def __init__(self) -> None:
        self._current: Optional[Execution] = None
        self._lock = asyncio.Lock()

    async def try_acquire(self, execution: Execution) -> None:
        async with self._lock:
            if self._current is not None:
                raise SlotBusy()
            self._current = execution

    async def release(self, execution_id: str) -> None:
        async with self._lock:
            if self._current and self._current.execution_id == execution_id:
                self._current = None

    def remaining_of_current(self) -> float:
        """在途执行的剩余时限；供名额拒绝时给出 Retry-After，不看新执行的预算。"""

        current = self._current
        return current.remaining_seconds() if current is not None else 1.0


class ChatOrchestrator:
    def __init__(self, cfg: McpBotConfig, history: HistoryStore, ollama: OllamaClient) -> None:
        self._cfg = cfg
        self._history = history
        self._ollama = ollama
        self.slot = ExecutionSlot()

    def new_execution(self, request_id: str) -> Execution:
        budget = self._cfg.budget
        return Execution(
            execution_id=uuid.uuid4().hex,
            request_id=request_id,
            deadline_seconds=float(budget.deadline_seconds),
            started_at=time.monotonic(),
            rounds_limit=budget.max_rounds,
            tool_calls_limit=budget.max_tool_calls,
        )

    def start(self, execution: Execution, user_text: str) -> asyncio.Queue:
        """把一次执行跑成独立任务，事件按序进队列。

        响应流被取消时该任务不受牵连：名额与结束记录由任务自身在收尾实际结束后交出，
        不以结束反馈是否送达浏览器为前提（IF-002）。
        """

        queue: asyncio.Queue = asyncio.Queue()

        async def pump() -> None:
            try:
                async for event in self.run(execution, user_text):
                    queue.put_nowait(event)
            finally:
                self._force_end_if_open(execution)
                queue.put_nowait(None)
                await self.slot.release(execution.execution_id)

        execution.task = asyncio.create_task(pump())
        return queue

    def _force_end_if_open(self, execution: Execution) -> None:
        """任务被取消或提前结束时补记唯一的结束事实，不把未完成写成完成。"""

        if execution.lifecycle == "已结束":
            return
        reason = "客户端放弃" if execution.client_gone else "执行未走到结束点即被打断"
        self._history.append_end(
            execution.execution_id,
            outcome=OUTCOME_TERMINATED,
            reason=reason,
            answer_complete=False,
            rounds_used=execution.rounds_used,
            tool_calls_used=execution.tool_calls_used,
            uncertain_calls=execution.uncertain_calls,
        )
        execution.lifecycle = "已结束"

    def build_context(self, user_text: str) -> List[dict]:
        """最近已保存事实 + 当前输入。token 级裁剪与记忆候选由后续里程碑落实。"""

        messages: List[dict] = []
        for item in reversed(self._history.all_executions()):
            if len(messages) >= CONTEXT_TAIL_MESSAGES:
                break
            for record in reversed(item.assistant_messages()):
                if record.get("complete"):
                    messages.append({"role": "assistant", "content": record["text"]})
                    break
            if item.accepted and item.end:
                messages.append({"role": "user", "content": item.user_text})
        messages.reverse()
        messages.append({"role": "user", "content": user_text})
        return messages

    async def run(self, execution: Execution, user_text: str) -> AsyncIterator[Dict[str, object]]:
        """产出 IF-003 事件序列；调用方负责把事件映射为响应流帧。

        内部异常同样收敛为故障事件与唯一的执行结束事实：结局不由流是否自然关闭表达。
        """

        seq = 0

        def emit(kind: str, payload: Dict[str, object]) -> Dict[str, object]:
            nonlocal seq
            seq += 1
            event: Dict[str, object] = {"execution_id": execution.execution_id, "seq": seq, "kind": kind}
            event.update(payload)
            return event

        try:
            async for event in self._execute(execution, user_text, emit):
                yield event
        except Exception as exc:
            logger.exception("执行 %s 出现内部故障", execution.execution_id)
            execution.to_wrapup("本站内部故障")
            yield emit("故障", {"boundary": "本站", "category": "内部故障", "detail": f"{type(exc).__name__}: {exc}"})

        if execution.lifecycle == "收尾中":
            yield emit("阶段", {"stage": STAGE_WRAPUP, "reason": execution.wrapup_reason})

        outcome, reason = self._settle(execution)
        end_saved = self._history.append_end(
            execution.execution_id,
            outcome=outcome,
            reason=reason,
            answer_complete=outcome == OUTCOME_DONE,
            rounds_used=execution.rounds_used,
            tool_calls_used=execution.tool_calls_used,
            uncertain_calls=execution.uncertain_calls,
        )
        execution.lifecycle = "已结束"
        yield emit(
            "执行结束",
            {
                "outcome": outcome,
                "reason": reason,
                "answer_complete": outcome == OUTCOME_DONE,
                "saved_confirmed": end_saved,
                "uncertain_calls": execution.uncertain_calls,
            },
        )

    async def _execute(
        self,
        execution: Execution,
        user_text: str,
        emit: Callable[[str, Dict[str, object]], Dict[str, object]],
    ) -> AsyncIterator[Dict[str, object]]:
        message_id = uuid.uuid4().hex
        if not self._history.accept(execution.execution_id, execution.request_id, user_text, message_id):
            execution.to_wrapup("接纳保存未确认")
            # RUL-005：未确认即不得据其启动依赖它的下一外部操作
            yield emit("故障", {"boundary": "本站持久化", "category": "保存未确认", "detail": "用户消息未能确认保存"})
            return

        yield emit("接纳", {"request_id": execution.request_id, "message_id": message_id, "saved_confirmed": True})

        if not execution.can_start_upstream():
            execution.to_wrapup("客户端已放弃或时限用尽")
            yield emit("故障", {"boundary": "本站", "category": "连接失效", "detail": "响应连接已断开，未发起推理"})
            return

        execution.stage = STAGE_GENERATING
        yield emit("阶段", {"stage": STAGE_GENERATING})
        turn = await self._generate(execution, user_text)
        if turn.fault:
            execution.to_wrapup(turn.fault)
            yield emit(
                "故障",
                {
                    "boundary": "Ollama 接入",
                    "category": turn.fault,
                    "detail": "未取得可用的推理结果",
                    "partial": turn.content,
                },
            )
            return

        assistant_id = uuid.uuid4().hex
        saved_answer = self._history.save_assistant(
            execution.execution_id, assistant_id, turn.content, turn.completed
        )
        yield emit(
            "回答增量",
            {
                "message_id": assistant_id,
                "text": turn.content,
                "position": 0,
                "complete": turn.completed,
                "saved_confirmed": saved_answer,
            },
        )
        if not saved_answer:
            execution.to_wrapup("回答保存未确认")
            yield emit(
                "故障",
                {"boundary": "本站持久化", "category": "保存未确认", "detail": "回答已生成但未能确认保存"},
            )
        elif not turn.completed:
            execution.to_wrapup(turn.end_reason)

    async def _generate(self, execution: Execution, user_text: str) -> TurnResult:
        execution.rounds_used += 1
        remaining = max(1, int(execution.remaining_seconds()))
        if remaining <= 1:
            return TurnResult("", False, "预算耗尽", "单次执行总时限用尽")
        try:
            async with asyncio.timeout(remaining):
                return await self._ollama.generate(self.build_context(user_text), remaining)
        except TimeoutError:
            return TurnResult("", False, "预算耗尽", "单次执行总时限用尽")

    @staticmethod
    def _settle(execution: Execution) -> Tuple[str, str]:
        reason = execution.wrapup_reason
        if execution.client_gone:
            return OUTCOME_TERMINATED, "客户端放弃请求"
        if reason == "正常生成完毕":
            return OUTCOME_DONE, reason
        return OUTCOME_FAILED, reason
