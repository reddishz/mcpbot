"""Web 与鉴权组件的认证与入口限额（CMP-001：IF-001、CON-011、RSK-008）。

站点凭据的载体与判定沿用底层框架的凭证模型：一枚 Bearer 凭据对应一组由维护者在凭证文件
中配置的权限范围，本站不自建凭据集合，也不签发或轮换凭据。权限门按 bind_scope="network"
使用，即不启用它按来源地址免凭据的 local 豁免，鉴权判定只以凭据为依据。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, Optional, Sequence

from fastapi import HTTPException, Request
from wcore.dataplane.privilege import AccessContext, Allow, Deny, PrivilegeGate, TokenStore

from .config import McpBotConfig

# 凭证文件词干：框架据此得到 {stem}.tokens.yaml 与 {stem}.token-status.yaml
TOKEN_STEM = "mcpbot"


class SiteAuth:
    def __init__(self, cfg: McpBotConfig) -> None:
        data_dir = Path(cfg.storage.data_dir).expanduser().resolve()
        # 凭证文件缺失时由框架生成；内容非法时框架按致命处理，不在请求期才暴露
        self.store = TokenStore(data_dir, stem=TOKEN_STEM)
        self._gate = PrivilegeGate(self.store, bind_scope="network")

    @property
    def tokens_path(self) -> Path:
        return self.store.tokens_path

    def verify(self, authorization: Optional[str]) -> bool:
        """只判凭据有效性，不要求任何范围——凭据校验入口按此口径。"""
        held = self._gate.resolve(authorization)
        if isinstance(held, Deny):
            return False
        return self._gate.require(held) is Allow

    def authorize(
        self,
        authorization: Optional[str],
        required: Sequence[str],
        access: AccessContext,
    ) -> Optional[str]:
        """放行返回 None，否则返回拒绝原因码。原因码不含凭据内容，只可入日志。"""
        outcome = self._gate.enforce(authorization, required, access=access)
        return None if outcome is Allow else outcome.reason_code


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
