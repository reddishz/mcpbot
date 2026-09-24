"""
WCore 内建 HTTP 通知发送器（与 libs/notice 区分，故命名为 notice_http）。

由原 libs/notice 迁入，WCore 不依赖 notice 包。本模块内仅使用 DEBUG 级别日志，
避免接入日志驱动通知时形成循环或重复通知。
"""

from __future__ import annotations

import json
import logging
import os
import platform
from datetime import datetime
from typing import Optional, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class NotificationSender(Protocol):
    """通知发送器协议：发送单条文本，返回是否成功。"""

    def send(self, message: str) -> bool:
        ...


class HttpNoticeSender:
    """
    内建 HTTP 通知发送器（迁入自 libs/notice）。

    与 notice.Notifier 行为一致：POST JSON {"msg": "【hostname】 message"} 到
    base_url/chat/notice?timestamp=mdY。不依赖 notice 包。
    内部仅使用 DEBUG 级别日志，不触发日志驱动通知。
    """

    def __init__(
        self,
        base_url: str = "http://rush.sys.pub",
        timeout: int = 10,
        _logger: Optional[logging.Logger] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._logger = _logger or logging.getLogger("wcore.notice_http")

    def _timestamp_mdy(self) -> str:
        """PHP date('mdY') 格式，如 03132025"""
        return datetime.now().strftime("%m%d%Y")

    def _build_url(self) -> str:
        return f"{self.base_url}/chat/notice?timestamp={self._timestamp_mdy()}"

    def send(self, message: str) -> bool:
        if not message:
            self._logger.debug("通知消息为空，跳过发送")
            return False
        try:
            url = self._build_url()
            # 消息格式已由上层处理，直接发送
            payload = {"msg": message}
            body = json.dumps(payload).encode("utf-8")
            req = Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    self._logger.debug("通知发送成功: %s", message[:80])
                    return True
                self._logger.debug(
                    "通知发送失败，状态码: %s", resp.status
                )
                return False
        except TimeoutError:
            self._logger.debug(
                "通知发送超时 (>%ss): %s", self.timeout, message[:80]
            )
            return False
        except (HTTPError, URLError) as e:
            self._logger.debug("通知发送连接失败: %s", e)
            return False
        except Exception as e:
            self._logger.debug("通知发送异常: %s", e, exc_info=True)
            return False
