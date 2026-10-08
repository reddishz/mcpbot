"""MCP 接入（CMP-004：IF-006 能力发现、IF-007 工具调用）。

只用官方 SDK 的客户端侧，以 Streamable HTTP 连接业务端 MCP Server（CON-005）；
本站不提供自己的业务 MCP Server，也不复用 wcore 的服务端适配。

- 连接与凭据只取自部署配置，地址与 Key 不接受浏览器或模型输入（FR-015、NFR-003）。
- 接入不自行重试；未取得结构化结果的调用一律标为结果不确定（NFR-007、RSK-004）。
- 上游故障只按传输层请求处理结果、协议消息层错误响应与工具结果内的执行标记三类标准证据归类（RUL-010）；
  不解析业务端私有编码，也不按错误文本措辞分流。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from jsonschema import Draft202012Validator, SchemaError
from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND, PARSE_ERROR

from .config import KeepLocalConfig, McpService

logger = logging.getLogger(__name__)

STATUS_OK = "可用"
STATUS_UNCHECKED = "未检查"
STATUS_NOT_CONFIGURED = "未配置该服务"
STATUS_CONNECTION = "连接失败"
STATUS_AUTH = "上游鉴权失败"
STATUS_PROTOCOL = "协议不兼容"
STATUS_UNAVAILABLE = "服务不可用"

REJECT_UNKNOWN_SERVICE = "未知服务标识"
REJECT_TOOL_NOT_ALLOWED = "工具不在允许范围"
REJECT_TOOL_NOT_DISCOVERED = "工具未完成发现"
REJECT_BAD_ARGUMENTS = "参数结构不合法"
REJECT_BUDGET = "剩余时限不足"
REJECT_INTENT_UNSAVED = "发送意图未确认保存"

OUTCOME_SUCCESS = "成功"
OUTCOME_BUSINESS_REJECT = "业务拒绝"
OUTCOME_FAULT = "故障"
OUTCOME_NOT_SENT = "未发送"

# 协议消息层中表明方法不存在、协议不被支持或请求内容不合法的取值（RUL-010）
_PROTOCOL_ERROR_CODES = frozenset({METHOD_NOT_FOUND, PARSE_ERROR, INVALID_REQUEST, INVALID_PARAMS})

# 传输层拒收形态：凭据被拒只有这一层载体，请求本身不被接受的形态单列（RUL-010）
_TRANSPORT_AUTH = frozenset({401, 403})
_TRANSPORT_PROTOCOL = frozenset({400, 404, 405, 406, 411, 414, 415, 417, 422, 426, 501, 505})

# 建连阶段就失败，请求未发出；连接已建立后的中断按服务不可用记（RUL-010）
_PRESEND_TRANSPORT_NAMES = {
    "ConnectError",
    "ConnectTimeout",
    "ConnectionError",
    "ConnectionRefusedError",
    "PoolTimeout",
}

# 归类派生的两个事实：只有建连失败的请求可判定未发出，只有服务不可用需要标结果不确定（RUL-010 映射表）
_PRESUMED_SENT_STATUSES = {STATUS_AUTH, STATUS_PROTOCOL, STATUS_UNAVAILABLE}
_UNCERTAIN_STATUSES = {STATUS_UNAVAILABLE}

# 诊断说明长度上限：上游错误文本常夹带业务结果正文，不整段进运行记录（RUL-009）
_DETAIL_LIMIT = 300


@dataclass(frozen=True)
class ToolDescriptor:
    """一个可编排使用的工具；mapping 在同部署内唯一，同名工具靠它区分（IF-006、AC-009）。

    display_name 取上游自述的展示名称，与 description、input_schema 同属不可信数据，只用于呈现。
    """

    service_id: str
    tool_name: str
    display_name: str
    description: str
    input_schema: Dict[str, Any]
    mapping: str


@dataclass
class DiscoveryResult:
    service_id: str
    status: str
    tools: List[ToolDescriptor] = field(default_factory=list)
    detail: str = ""


@dataclass
class CallResult:
    call_id: str
    service_id: str
    tool_name: str
    sent: bool
    outcome: str
    text: str = ""
    structured: Optional[Dict[str, Any]] = None
    detail: str = ""
    truncated: bool = False
    uncertain: bool = False
    elapsed_seconds: float = 0.0
    sent_categories: List[str] = field(default_factory=list)
    sent_bytes: int = 0


class McpAccess:
    """按服务持有连接状态与发现快照；发现 lazy 到该服务首次被使用（FLW-004）。"""

    def __init__(self, cfg: KeepLocalConfig) -> None:
        self._cfg = cfg
        self._cache: Dict[str, Tuple[str, List[ToolDescriptor]]] = {}
        self._status: Dict[str, Tuple[str, str]] = {}

    # ---- 配置侧（FR-015：地址、凭据、允许范围只来自部署配置） ----

    def services(self) -> Dict[str, McpService]:
        return {svc.service_id: svc for svc in self._cfg.mcp.services if svc.service_id.strip()}

    @staticmethod
    def _fingerprint(svc: McpService) -> str:
        # 允许范围或地址一变即视为新配置：旧发现结果不得扩大新配置的权限（IF-006）
        return f"{svc.url}|{svc.api_key}|{','.join(sorted(svc.allowed_tools))}"

    def allowed(self, service_id: str, tool_name: str) -> bool:
        svc = self.services().get(service_id)
        return bool(svc and tool_name in svc.allowed_tools)

    def status_of(self, service_id: str) -> Tuple[str, str]:
        return self._status.get(service_id, (STATUS_UNCHECKED, "该服务尚未被检查，不描述为正常"))

    def statuses(self) -> Dict[str, str]:
        result = {sid: STATUS_UNCHECKED for sid in self.services()}
        result.update({sid: state for sid, (state, _) in self._status.items()})
        return result

    def invalidate(self, service_id: Optional[str] = None) -> None:
        """受控刷新后作废旧能力清单（FLW-004）。"""

        if service_id is None:
            self._cache.clear()
        else:
            self._cache.pop(service_id, None)

    # ---- IF-006 能力发现 ----

    async def discover(self, service_id: str, remaining_seconds: float) -> DiscoveryResult:
        svc = self.services().get(service_id)
        if svc is None:
            return self._record(DiscoveryResult(service_id, STATUS_NOT_CONFIGURED, detail=REJECT_UNKNOWN_SERVICE))
        if not svc.allowed_tools:
            # 空集合即不开放（FLW-004）：不建连，也不向编排声明任何可用工具
            return self._record(DiscoveryResult(service_id, STATUS_OK, detail="允许工具范围为空"))

        cached = self._cache.get(service_id)
        if cached and cached[0] == self._fingerprint(svc):
            return DiscoveryResult(service_id, STATUS_OK, tools=list(cached[1]))

        if remaining_seconds <= 0:
            return self._record(DiscoveryResult(service_id, STATUS_UNAVAILABLE, detail=REJECT_BUDGET))

        statuses: List[int] = []
        try:
            async with self._session(svc, remaining_seconds, statuses) as client:
                listed = await client.list_tools()
        except Exception as exc:  # 上游故障按服务分别标记，不拖垮其他服务（IF-006）
            return self._record(self._failure(service_id, exc, statuses))

        tools: List[ToolDescriptor] = []
        for tool in listed.tools:
            if tool.name not in svc.allowed_tools:
                continue  # 超出允许范围的能力不向编排声明可用（CMP-004）
            schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
            tools.append(
                ToolDescriptor(
                    service_id=service_id,
                    tool_name=tool.name,
                    display_name=tool.title or tool.name,
                    description=tool.description or "",
                    input_schema=schema,
                    mapping=f"{service_id}/{tool.name}",
                )
            )
        self._cache[service_id] = (self._fingerprint(svc), tools)
        return self._record(DiscoveryResult(service_id, STATUS_OK, tools=tools))

    # ---- IF-007 工具调用 ----

    async def call(
        self,
        *,
        call_id: str,
        service_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        data_categories: List[str],
        remaining_seconds: float,
        max_result_bytes: int,
        send_intent_confirmed: bool,
    ) -> CallResult:
        started = time.monotonic()
        reject = self._precheck(service_id, tool_name, arguments, remaining_seconds, send_intent_confirmed)
        if reject is not None:
            return CallResult(call_id, service_id, tool_name, False, OUTCOME_NOT_SENT, detail=reject,
                              sent_categories=list(data_categories), elapsed_seconds=time.monotonic() - started)

        svc = self.services()[service_id]
        payload = dict(arguments)
        sent_bytes = _measure(payload)

        statuses: List[int] = []
        try:
            async with self._session(svc, remaining_seconds, statuses) as client:
                result = await client.call_tool(tool_name, payload)
        except Exception as exc:
            # 未取得工具结果就没有执行标记可判，按归类映射决定不确定标记与发送证据（RUL-010）
            failure = self._failure(service_id, exc, statuses)
            self._record(failure)
            uncertain = failure.status in _UNCERTAIN_STATUSES
            sent = failure.status in _PRESUMED_SENT_STATUSES
            return CallResult(
                call_id, service_id, tool_name, sent, OUTCOME_FAULT,
                detail=f"{failure.status}：{failure.detail}",
                uncertain=uncertain, sent_categories=list(data_categories),
                elapsed_seconds=time.monotonic() - started, sent_bytes=sent_bytes,
            )

        # 取得工具结果就以内部的执行标记与内容为准：HTTP 层与连接状态不再单独作为结论依据（IF-007）
        evidence = {"sent_categories": list(data_categories), "sent_bytes": sent_bytes,
                    "elapsed_seconds": time.monotonic() - started}
        text, text_truncated = _collect_text(result.content, max_result_bytes)
        structured, struct_truncated = _fit_structured(result.structured_content, max_result_bytes)
        truncated = text_truncated or struct_truncated
        detail = f"结果超过 {max_result_bytes} 字节上限，已截断" if truncated else ""
        if not (structured or text):
            # 执行标记之外没有可判定内容：按服务不可用记并标不确定（RUL-010）
            self._record(DiscoveryResult(service_id, STATUS_UNAVAILABLE, detail="工具结果无可判定内容"))
            return CallResult(call_id, service_id, tool_name, True, OUTCOME_FAULT, text=text, structured=structured,
                              detail="服务不可用：工具结果无可判定内容", truncated=truncated, uncertain=True,
                              **evidence)
        self._status[service_id] = (STATUS_OK, "")
        # 已取得可判定内容时按执行标记归类，截断本身不改变判定（RUL-010）
        if result.is_error:
            return CallResult(call_id, service_id, tool_name, True, OUTCOME_BUSINESS_REJECT, text=text,
                              structured=structured, detail=detail or "业务侧拒绝", truncated=truncated,
                              **evidence)
        return CallResult(call_id, service_id, tool_name, True, OUTCOME_SUCCESS, text=text, structured=structured,
                          detail=detail, truncated=truncated, **evidence)

    def _precheck(
        self, service_id: str, tool_name: str, arguments: Any, remaining_seconds: float, saved: bool
    ) -> Optional[str]:
        """本地拒绝在发送之前完成，任一侧未通过都不发送（IF-007）。"""

        if service_id not in self.services():
            return REJECT_UNKNOWN_SERVICE
        if not self.allowed(service_id, tool_name):
            return REJECT_TOOL_NOT_ALLOWED
        if not saved:
            return REJECT_INTENT_UNSAVED
        if remaining_seconds <= 0:
            return REJECT_BUDGET
        if not isinstance(arguments, dict):
            return REJECT_BAD_ARGUMENTS

        cached = self._cache.get(service_id)
        if not cached or cached[0] != self._fingerprint(self.services()[service_id]):
            return REJECT_TOOL_NOT_DISCOVERED
        if tool_name not in {t.tool_name for t in cached[1]}:
            return REJECT_TOOL_NOT_DISCOVERED
        if not _schema_ok(arguments, self._schema_of(service_id, tool_name) or {}):
            return REJECT_BAD_ARGUMENTS
        return None

    def _schema_of(self, service_id: str, tool_name: str) -> Optional[Dict[str, Any]]:
        cached = self._cache.get(service_id)
        if not cached:
            return None
        for tool in cached[1]:
            if tool.tool_name == tool_name:
                return tool.input_schema
        return None

    @asynccontextmanager
    async def _session(self, svc: McpService, remaining_seconds: float, statuses: List[int]):
        headers = {"Authorization": f"Bearer {svc.api_key}"} if svc.api_key else {}
        # 只把该服务的凭据发给该服务的地址；站点 Key 不出本站入口（NFR-003）
        http = create_mcp_http_client(headers=headers)

        async def record(response) -> None:
            # 记传输层请求处理结果：SDK 会把非 2xx 复述成协议层错误，状态码在异常里丢失
            statuses.append(response.status_code)

        http.event_hooks["response"].append(record)
        # 不设等待下限：剩余预算多小就等多久，远程等待不绕过调用方的剩余时限（IF-006、NFR-006）
        try:
            async with asyncio.timeout(remaining_seconds):
                async with Client(streamable_http_client(svc.url, http_client=http),
                                  raise_exceptions=False) as client:
                    yield client
        finally:
            await http.aclose()

    def _failure(self, service_id: str, exc: BaseException, statuses: List[int]) -> DiscoveryResult:
        status, detail = _classify(exc, statuses)
        logger.warning("MCP 服务 %s 故障（已发送=%s，结果不确定=%s）：%s", service_id,
                       status in _PRESUMED_SENT_STATUSES, status in _UNCERTAIN_STATUSES, detail)
        return DiscoveryResult(service_id, status, detail=detail)

    def _record(self, result: DiscoveryResult) -> DiscoveryResult:
        self._status[result.service_id] = (result.status, result.detail)
        return result


def _collect_text(blocks: List[Any], max_result_bytes: int) -> Tuple[str, bool]:
    """按内容块顺序取文；超过上限保留已取到的部分并标截断（IF-007）。"""

    parts: List[str] = []
    used = 0
    truncated = False
    for block in blocks:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        encoded = text.encode("utf-8")
        room = max_result_bytes - used
        if len(encoded) > room:
            truncated = True
            if room > 0:
                parts.append(encoded[:room].decode("utf-8", errors="ignore"))
            break
        parts.append(text)
        used += len(encoded)
    return "".join(parts), truncated


def _fit_structured(structured: Any, max_result_bytes: int) -> Tuple[Optional[Dict[str, Any]], bool]:
    if not isinstance(structured, dict):
        return None, False
    if len(json.dumps(structured, ensure_ascii=False, default=str).encode("utf-8")) > max_result_bytes:
        return None, True
    return structured, False


def _schema_ok(arguments: Dict[str, Any], schema: Dict[str, Any]) -> bool:
    # Schema 是不可信数据（IF-006）：只用于判定参数结构，不据其扩展发送范围
    if not schema:
        return False
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return False
    return Draft202012Validator(schema).is_valid(arguments)


def _measure(payload: Dict[str, Any]) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _exception_chain(exc: BaseException) -> List[BaseException]:
    """沿异常链与任务组子异常收集证据；真正的故障原因常藏在 ExceptionGroup 里。"""

    chain: List[BaseException] = []
    pending: List[BaseException] = [exc]
    while pending and len(chain) <= 16:
        node = pending.pop()
        if any(node is known for known in chain):
            continue
        chain.append(node)
        pending.extend(x for x in (node.__cause__, node.__context__) if x is not None)
        pending.extend(getattr(node, "exceptions", None) or ())
    return chain


def _is_connect_phase(node: BaseException) -> bool:
    return type(node).__name__ in _PRESEND_TRANSPORT_NAMES


def _is_timeout(node: BaseException) -> bool:
    return isinstance(node, TimeoutError) or type(node).__name__.endswith("Timeout")


def _bound(detail: str) -> str:
    return detail if len(detail) <= _DETAIL_LIMIT else detail[:_DETAIL_LIMIT] + "…（已截断）"


def _describe(chain: List[BaseException]) -> str:
    """只取异常形态，不取异常正文：上游与客户端校验错误的正文常夹带业务结果内容（RUL-009）。"""

    return " | ".join(type(node).__name__ for node in chain)


def _by_transport(status: int, detail: str) -> Tuple[str, str]:
    """传输层请求处理结果到类别（RUL-010）。"""
    if status in _TRANSPORT_PROTOCOL:
        return STATUS_PROTOCOL, _bound(f"发生阶段=传输层拒收 HTTP {status}：{detail}")
    return STATUS_UNAVAILABLE, _bound(f"发生阶段=传输层失败 HTTP {status}：{detail}")


def _classify(exc: BaseException, statuses: List[int]) -> Tuple[str, str]:
    """按 RUL-010 归一：工具结果的执行标记由调用处判定，其次协议消息层错误响应，最后传输层请求处理结果。"""

    chain = _exception_chain(exc)
    failing = [status for status in statuses if status >= 400]
    # 202 没有响应体：请求被接受而未取得结果，属传输层证据（RUL-010）
    accepted_without_result = 202 in statuses

    # 凭据被拒只有传输层载体，取得该证据就不向其他层推定（RUL-010 鉴权证据的唯一性）
    refused = next((status for status in statuses if status in _TRANSPORT_AUTH), None)
    if refused is not None:
        return STATUS_AUTH, f"发生阶段=传输层：上游以 HTTP {refused} 拒收凭据"

    for node in chain:
        if not isinstance(node, MCPError):
            continue
        message = node.error.message or ""
        if node.error.code == INTERNAL_ERROR and failing:
            # SDK 用同一个协议层错误复述非 2xx 响应，此时可用证据只有状态码
            return _by_transport(failing[-1], message)
        if node.error.code == INVALID_REQUEST and accepted_without_result:
            # SDK 也用请求不合法复述 202，此处不能据它判为可判定未执行
            return STATUS_UNAVAILABLE, "发生阶段=传输层：上游以 HTTP 202 接受请求但未返回结果"
        if node.error.code in _PROTOCOL_ERROR_CODES:
            return STATUS_PROTOCOL, _bound(f"发生阶段=协议消息层：{message}")
        return STATUS_UNAVAILABLE, _bound(f"发生阶段=协议消息层：{message}")

    # 连接类先于超时判定：建连阶段的失败即使在措辞里带 timeout，也不是请求等满时限
    if any(_is_connect_phase(node) for node in chain):
        return STATUS_CONNECTION, _bound("发生阶段=建连：" + _describe(chain))
    if any(_is_timeout(node) for node in chain):
        # 超时不单设类别，按服务不可用记录并保留发生阶段（RUL-010）
        return STATUS_UNAVAILABLE, _bound("发生阶段=等待响应：" + _describe(chain))
    if failing:
        return _by_transport(failing[-1], _describe(chain))
    return STATUS_UNAVAILABLE, _bound(
        f"发生阶段=证据不可判定（传输层 HTTP {sorted(set(statuses)) or '未取得'}）：" + _describe(chain))
