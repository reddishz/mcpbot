"""
WCore 日志驱动通知：NotificationHandler + setup_notification。

根据日志级别（CRITICAL/NOTICE/ERROR 即时发送，WARNING 摘要待 P1）触发通知。
本模块内仅使用 DEBUG 级别日志，避免循环触发通知。

说明：Python logging 无 "exception" 级别。logger.exception() 等价于
logger.error(..., exc_info=True)，即 ERROR 级别并附带异常栈，会触发即时通知。

新增 NOTICE 级别（45）：用于重要业务事件通知，如成交结果等。
"""

from __future__ import annotations

import logging
import os
import platform
import socket
from typing import Any, Optional

from .notice_http import HttpNoticeSender, NotificationSender


def _host_label_for_notification() -> str:
    """通知中的主机段：优先 socket 主机名，为空再回退（Windows COMPUTERNAME、platform.node）。"""
    try:
        hn = socket.gethostname().strip()
        if hn:
            return hn
    except Exception:
        pass
    if platform.system() == "Windows":
        cn = os.environ.get("COMPUTERNAME", "").strip()
        if cn:
            return cn
    node = (platform.node() or "").strip()
    return node if node else "unknown"


def _process_label_for_notification() -> str:
    """从 RuntimeConfig.notification.sysname 读取实例名；空则取 subsystem_name 短名（如 'gridtrade'）。"""
    from .app_context import AppContext

    rc = AppContext.runtime_config
    if rc is None:
        return ""
    if rc.notification.sysname:
        return rc.notification.sysname
    return rc.subsystem_name.rsplit(".", 1)[-1] if rc.subsystem_name else ""


# 新增 NOTICE 日志级别（45），介于 ERROR(40) 和 CRITICAL(50) 之间
NOTICE = 45
logging.addLevelName(NOTICE, "NOTICE")

_TRUNCATE_SUFFIX = "...(已截断)"
_LEVEL_NAMES = {
    logging.CRITICAL: "CRITICAL",
    NOTICE: "NOTICE",
    logging.ERROR: "ERROR",
    logging.WARNING: "WARNING",
}


def _notification_envelope(level: int) -> str:
    """即时通知统一前缀，仅此处维护【级别】【主机】【proc】形态。"""
    level_name = _LEVEL_NAMES.get(level, "ERROR")
    host = _host_label_for_notification()
    proc = _process_label_for_notification()
    proc_seg = f"{proc}" if proc else ""
    return f"【{level_name} {host} {proc_seg}】"


def _format_immediate(level: int, message: str, max_chars: int) -> str:
    """前缀 + 日志原文；超长截断。"""
    body = _notification_envelope(level) + message
    if len(body) <= max_chars:
        return body
    return body[: max_chars - len(_TRUNCATE_SUFFIX)] + _TRUNCATE_SUFFIX


class _NotificationHandler(logging.Handler):
    """处理 CRITICAL/NOTICE/ERROR 即时发送；WARNING 暂不发送（P1 摘要）。"""

    def __init__(
        self,
        enabled: bool,
        max_message_chars: int,
        sender: NotificationSender,
        _logger: Optional[logging.Logger] = None,
    ):
        super().__init__()
        self._enabled = enabled
        self._max_message_chars = max_message_chars
        self._sender = sender
        self._log = _logger or logging.getLogger("wcore.notification")
        self.setLevel(logging.WARNING)  # 只接收 WARNING 及以上，INFO/DEBUG 不进入

    def emit(self, record: logging.LogRecord) -> None:
        if not self._enabled:
            return
        try:
            level = record.levelno
            if level >= logging.CRITICAL:
                self._send_immediate(logging.CRITICAL, record.getMessage())
            elif level >= NOTICE:
                self._send_immediate(NOTICE, record.getMessage())
            elif level >= logging.ERROR:
                self._send_immediate(logging.ERROR, record.getMessage())
            # WARNING: P1 再做摘要，此处不发送
        except Exception as e:
            self._log.debug("通知 emit 异常: %s", e)

    def _send_immediate(self, level: int, message: str) -> None:
        text = _format_immediate(level, message, self._max_message_chars)
        try:
            self._sender.send(text)
        except Exception as e:
            self._log.debug("通知发送异常: %s", e)


def notice(message: str) -> None:
    """
    发送重要业务事件通知。
    
    用法：
        from wcore import notice
        notice("订单成交: AAPL 100股 @ 150.25")
    
    内部自动映射到 IMPORT 日志级别，触发即时通知。
    外部系统无需关心日志级别细节。
    """
    from .app_context import AppContext

    # 回测/单测等未挂 AppContext 时静默跳过（与 order_executor._safe_notice 一致）
    rc = AppContext.runtime_config
    if not rc or not rc.subsystem_name:
        return

    logger = logging.getLogger(rc.subsystem_name)
    logger.log(NOTICE, message)


_handler_instance: Optional[_NotificationHandler] = None


def setup_notification(
    *,
    enabled: bool = True,
    digest_interval_sec: float = 300.0,
    max_message_chars: int = 2000,
    sender: Optional[Any] = None,
) -> None:
    """
    启用日志驱动通知：将 NotificationHandler 挂到当前子系统的 logger 上。

    应在 AppContext 构造完成后调用。enabled / digest_interval_sec / max_message_chars
    从 AppContext.runtime_config.notification 读取（RuntimeConfig 已定义该字段）。
    若未传 sender，使用内建 HttpNoticeSender。
    """
    global _handler_instance
    from .app_context import AppContext

    AppContext.ensure_initialized()
    rc = AppContext.runtime_config
    subsystem_name = rc.subsystem_name
    if not subsystem_name:
        return

    # 从 RuntimeConfig.notification 明确读取（该字段已在 RuntimeConfig 上定义）
    enabled = rc.notification.enabled
    digest_interval_sec = rc.notification.digest_interval_sec
    max_message_chars = rc.notification.max_message_chars

    if sender is None:
        sender = HttpNoticeSender()

    if _handler_instance is not None:
        logger = logging.getLogger(subsystem_name)
        logger.removeHandler(_handler_instance)
        _handler_instance = None

    handler = _NotificationHandler(
        enabled=enabled,
        max_message_chars=max_message_chars,
        sender=sender,
    )
    _handler_instance = handler
    logger = logging.getLogger(subsystem_name)
    logger.addHandler(handler)
