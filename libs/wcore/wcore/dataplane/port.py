"""本地 loopback 端口绑定。"""

from __future__ import annotations

import socket
from typing import Optional, Tuple

LOOPBACK_HOST = "127.0.0.1"


def bind_localhost_port(start: int = 3333, max_tries: int = 10) -> Tuple[str, int, socket.socket]:
    """
    在 127.0.0.1 上绑定首个可用端口。

    Returns:
        (host, port, bound_socket) — 调用方负责 close socket 后由服务端 re-bind，
        或传递 port 给 telnetlib3。
    """
    last_err: Optional[Exception] = None
    for port in range(start, start + max_tries):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((LOOPBACK_HOST, port))
            return LOOPBACK_HOST, port, sock
        except OSError as exc:
            last_err = exc
            sock.close()
    raise OSError(f"no free port in {start}-{start + max_tries - 1}: {last_err}")
