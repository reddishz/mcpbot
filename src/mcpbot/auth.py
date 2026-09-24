"""Web 与鉴权组件的认证与入口限额（CMP-001：IF-001、CON-011、RSK-008）。

站点 Key 的比对复用 wcore 的 PrivilegeGate，并按 bind_scope="network" 使用——即不使用
它按来源地址免密钥的 local 豁免，鉴权判定只以凭据为依据。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import HTTPException, Request
from wcore.dataplane.privilege import PRIVILEGE_QUERY, Allow, PrivilegeGate, PrivilegeSpec

from .config import McpBotConfig


class SiteAuth:
    def __init__(self, cfg: McpBotConfig) -> None:
        # 期望值非空是启动校验的前提，否则 PrivilegeGate 会自行生成 Key 并写入日志
        self._gate = PrivilegeGate(
            [PrivilegeSpec(name=PRIVILEGE_QUERY, expected=cfg.site.key, header="")],
            bind_scope="network",
        )

    def verify(self, token: Optional[str]) -> bool:
        # 本站只用 Bearer 一种呈现方式，因此直接给权限门 CredentialMap，不走它的 Header/Bearer 抽取
        credentials = {PRIVILEGE_QUERY: token}
        return self._gate.require(self._gate.resolve(credentials), PRIVILEGE_QUERY) is Allow


class AuthAttemptLimiter:
    """按来源滑动窗口计数（CON-011 Key 校验尝试频率）；返回可检查的恢复秒数。"""

    def __init__(self, per_minute: int) -> None:
        self._limit = per_minute
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def check(self, source: str) -> Optional[int]:
        """未超限返回 None；超限返回建议等待秒数。"""

        now = time.monotonic()
        window = self._hits[source]
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= self._limit:
            return max(1, int(60.0 - (now - window[0])) + 1)
        window.append(now)
        return None


def client_source(request: Request, trust_forwarded_for: bool) -> str:
    peer = request.client.host if request.client else "unknown"
    if trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return peer


async def read_limited_body(request: Request, max_bytes: int) -> bytes:
    """读取请求体并在超限时中断；无 Content-Length 的流式上传同样受限。"""

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(status_code=413, detail="请求体超出上限")
    collected = bytearray()
    async for chunk in request.stream():
        collected.extend(chunk)
        if len(collected) > max_bytes:
            raise HTTPException(status_code=413, detail="请求体超出上限")
    if len(collected) > max_bytes:
        raise HTTPException(status_code=413, detail="请求体超出上限")
    return bytes(collected)


def bearer_of(request: Request) -> Optional[str]:
    parts = (request.headers.get("authorization") or "").split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    return parts[1].strip()
