"""
统一日志工具模块
提供增强的日志功能，包括性能追踪、结构化日志等
"""

import logging
import time
import functools
from typing import Callable, Any, Optional
from pathlib import Path
from contextlib import contextmanager


class CompactNameFormatter(logging.Formatter):
    """
    控制台/文件日志中缩短 logger 名：去掉与子系统根一致的前缀后，仅保留最后一段。

    例：prefix ``app.w3trade`` 时 ``app.w3trade.actors.components.follow_engine`` → ``follow_engine``；
    恰为根名 ``app.w3trade`` 时 → ``w3trade``。无前缀匹配则保持原名。
    """

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        *,
        strip_prefix: str = "",
        style: str = "%",
    ) -> None:
        super().__init__(fmt, datefmt, style)
        self._strip_prefix = (strip_prefix or "").strip().rstrip(".")

    def format(self, record: logging.LogRecord) -> str:
        record.compact_logger_name = self._compact_name(record.name)  # type: ignore[attr-defined]
        return super().format(record)

    def _compact_name(self, name: str) -> str:
        p = self._strip_prefix
        if not p:
            return name
        if name == p:
            return p.rsplit(".", 1)[-1]
        dotted = p + "."
        if name.startswith(dotted):
            rest = name[len(dotted) :]
            if not rest:
                return p.rsplit(".", 1)[-1]
            return rest.rsplit(".", 1)[-1]
        return name


class ConsoleBriefFormatter(logging.Formatter):
    """
    控制台精简：不打印时间戳、logger 名；INFO/DEBUG 仅消息体（与文件完整行对照）。

    WARNING 及以上带 ``LEVELNAME: `` 前缀；``exc_info`` / ``stack_info`` 追加方式与标准 Formatter 一致。
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        if record.levelno >= logging.WARNING:
            s = f"{record.levelname}: {record.message}"
        else:
            s = record.message
        if record.exc_info:
            if record.exc_text is None:
                record.exc_text = self.formatException(record.exc_info)
            s = f"{s}\n{record.exc_text}"
        if record.stack_info:
            s = f"{s}\n{self.formatStack(record.stack_info)}"
        return s


class EnhancedLogger:
    """增强日志记录器"""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
    
    def __getattr__(self, name):
        """代理所有未定义的方法到底层logger"""
        return getattr(self.logger, name)
    
    def time_it(self, func_name: str = None):
        """装饰器：记录函数执行时间"""
        def decorator(func: Callable) -> Callable:
            fname = func_name or func.__name__
            
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.time()
                self.logger.info(f"🚀 开始执行 {fname}...")
                
                try:
                    result = func(*args, **kwargs)
                    duration = time.time() - start_time
                    self.logger.info(f"✅ {fname} 执行完成，耗时: {duration:.2f}秒")
                    return result
                except Exception as e:
                    duration = time.time() - start_time
                    self.logger.error(f"❌ {fname} 执行失败，耗时: {duration:.2f}秒，错误: {str(e)}")
                    raise
            
            return wrapper
        return decorator
    
    @contextmanager
    def timer(self, operation: str):
        """上下文管理器：记录操作耗时"""
        start_time = time.time()
        self.logger.info(f"⏱️  开始 {operation}...")
        try:
            yield
            duration = time.time() - start_time
            self.logger.info(f"✅ {operation} 完成，耗时: {duration:.2f}秒")
        except Exception as e:
            duration = time.time() - start_time
            self.logger.error(f"❌ {operation} 失败，耗时: {duration:.2f}秒，错误: {str(e)}")
            raise
    
    def log_progress(self, current: int, total: int, operation: str = "处理"):
        """记录进度信息"""
        percentage = (current / total) * 100
        self.logger.info(f"📊 {operation}: {current}/{total} ({percentage:.1f}%)")
    
    def log_batch_info(self, batch_idx: int, total_batches: int, **kwargs):
        """记录批量处理信息"""
        extra_info = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
        self.logger.info(f"🔄 批次 {batch_idx}/{total_batches} | {extra_info}")


def get_enhanced_logger(name: str) -> EnhancedLogger:
    """获取增强日志记录器"""
    base_logger = logging.getLogger(name)
    return EnhancedLogger(base_logger)