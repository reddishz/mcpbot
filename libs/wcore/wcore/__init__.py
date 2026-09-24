"""
WCore - 基础应用框架

提供配置、日志、路径等通用基础设施。
无业务语义，无ML依赖，可独立用于任意Python应用。

使用示例：
    from wcore import AppContext
    from dataclasses import dataclass
    from wcore import config_class, config_field
    
    @config_class
    @dataclass
    class MyConfig:
        input_path: str = config_field(default="./data", description="输入路径")
    
    # 初始化应用上下文
    app = AppContext(MyConfig, "my_app")
    
    # 访问配置
    print(app.config.input_path)
    print(app.runtime_config.workdir)
    
    # 使用日志
    app.logger.info("应用启动")
"""

from .console_win32 import disable_legacy_console_quick_edit

disable_legacy_console_quick_edit()

from .app_context import AppContext, RuntimeConfig
from .config_system import config_class, config_field, cli_only_field
from .logging_utils import CompactNameFormatter, ConsoleBriefFormatter, EnhancedLogger, get_enhanced_logger
from .notice_http import HttpNoticeSender
from .notification import setup_notification, notice

from .dataplane import (
    CatalogNode,
    ControlResult,
    LeafSpec,
    Plane,
    PlaneRegistry,
    PlaneShell,
)

__version__ = "1.0.0"
__all__ = [
    # 核心组件
    "AppContext",
    "RuntimeConfig",
    # 配置系统
    "config_class",
    "config_field",
    "cli_only_field",
    # 日志
    "CompactNameFormatter",
    "ConsoleBriefFormatter",
    "EnhancedLogger",
    "get_enhanced_logger",
    # 通知
    "HttpNoticeSender",
    "setup_notification",
    "notice",
    # 平面框架 (WC-D009)
    "CatalogNode",
    "ControlResult",
    "LeafSpec",
    "Plane",
    "PlaneRegistry",
    "PlaneShell",
]
