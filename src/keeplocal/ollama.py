"""Ollama 接入（CMP-003：IF-005）。

每次调用只代表一轮推理，不自行重试、不重置总时限，也不在内部发起 MCP 调用。完成证据
来自上游响应中的结束标记，不以 HTTP 200 或连接关闭代替。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import httpx

from .config import OllamaSection

# 故障类别与 RUL-004 的本站响应对应；上游鉴权失败不映射成本站 401
FAULT_CONNECTION = "上游连接故障"
FAULT_UPSTREAM_AUTH = "上游鉴权失败"
FAULT_UNAVAILABLE = "模型或服务不可用"
FAULT_BAD_RESPONSE = "上游响应格式错误"
FAULT_TIMEOUT = "上游请求超时"


@dataclass
class TurnResult:
    content: str
    completed: bool
    end_reason: str
    fault: Optional[str] = None


class OllamaClient:
    def __init__(self, section: OllamaSection) -> None:
        self._section = section
        self._http: Optional[httpx.AsyncClient] = None

    def _client(self) -> httpx.AsyncClient:
        # 在使用它的事件循环内惰性创建：主循环建立前构造会让连接池绑到别的循环
        if self._http is None:
            section = self._section
            headers = {"Content-Type": "application/json"}
            if section.api_key:
                headers["Authorization"] = f"Bearer {section.api_key}"
            self._http = httpx.AsyncClient(
                base_url=section.base_url.rstrip("/"),
                headers=headers,
                timeout=httpx.Timeout(section.timeout_seconds, connect=min(10.0, section.timeout_seconds)),
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def generate(self, messages: List[dict], deadline_seconds: int) -> TurnResult:
        """单轮非流式推理。messages 由编排构建，本接入不自行追加历史或记忆。"""

        payload = {
            "model": self._section.model,
            "messages": messages,
            "stream": False,
            "think": self._section.think,
        }
        try:
            response = await self._client().post("/api/chat", json=payload, timeout=deadline_seconds + 5)
        except httpx.TimeoutException:
            return TurnResult("", False, FAULT_TIMEOUT, FAULT_TIMEOUT)
        except httpx.HTTPError:
            return TurnResult("", False, FAULT_CONNECTION, FAULT_CONNECTION)

        if response.status_code in (401, 403):
            return TurnResult("", False, FAULT_UPSTREAM_AUTH, FAULT_UPSTREAM_AUTH)
        if response.status_code == 404:
            return TurnResult("", False, FAULT_UNAVAILABLE, FAULT_UNAVAILABLE)
        if response.status_code >= 400:
            return TurnResult("", False, FAULT_UNAVAILABLE, FAULT_UNAVAILABLE)

        try:
            document = response.json()
            message = document.get("message") or {}
            content = message.get("content")
            done = document.get("done")
        except (ValueError, AttributeError):
            return TurnResult("", False, FAULT_BAD_RESPONSE, FAULT_BAD_RESPONSE)

        if not isinstance(content, str) or not isinstance(done, bool):
            return TurnResult("", False, FAULT_BAD_RESPONSE, FAULT_BAD_RESPONSE)
        if not done:
            return TurnResult(content, False, "上游未给出完成证据", None)
        reason = str(document.get("done_reason") or "stop")
        if reason == "length":
            # 达长度上限的截断：有内容不等于有完成证据，不能标为完整回答
            return TurnResult(content, False, "上游输出达长度上限截断", None)
        return TurnResult(content, True, reason, None)

    async def probe(self) -> Optional[str]:
        """启动阶段的可达性检查（FLW-004）；返回故障类别，None 表示可达。

        用独立的短生命周期客户端：它在服务循环建立之前运行，不得让连接池绑到这个临时循环上。
        """

        try:
            async with httpx.AsyncClient(base_url=self._section.base_url.rstrip("/")) as client:
                response = await client.get("/api/tags", timeout=10)
        except httpx.TimeoutException:
            return FAULT_TIMEOUT
        except httpx.HTTPError:
            return FAULT_CONNECTION
        if response.status_code in (401, 403):
            return FAULT_UPSTREAM_AUTH
        if response.status_code >= 400:
            return FAULT_UNAVAILABLE
        return None
