"""进程运行时信息（启动时间、运行时长）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional


def format_uptime(delta: timedelta) -> str:
    total = max(0, int(delta.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_runtime_lines(
    start_time: datetime,
    *,
    now: Optional[datetime] = None,
) -> List[str]:
    at = now or datetime.now()
    return [
        f"started: {start_time_text(start_time)}",
        f"uptime: {uptime_text(start_time, now=at)}",
    ]


def start_time_text(start_time: Optional[datetime] = None) -> str:
    st = start_time if start_time is not None else resolve_start_time()
    if st is None:
        return ""
    return st.strftime("%Y-%m-%d %H:%M:%S")


def uptime_text(
    start_time: Optional[datetime] = None,
    *,
    now: Optional[datetime] = None,
) -> str:
    st = start_time if start_time is not None else resolve_start_time()
    if st is None:
        return ""
    at = now or datetime.now()
    return format_uptime(at - st)


def resolve_start_time() -> Optional[datetime]:
    try:
        from wcore.app_context import RuntimeConfig
    except ImportError:
        return None
    return RuntimeConfig.start_time
