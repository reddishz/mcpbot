"""
WCore AppContext - 基础应用框架入口

提供配置管理、日志管理、路径管理等通用基础设施。
无业务语义，无ML依赖，可独立用于任意Python应用。
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Type, TypeVar, ClassVar, Dict

from .config_system import config_class, config_field
from .logging_utils import CompactNameFormatter, ConsoleBriefFormatter, EnhancedLogger


def _handler_targets_log_path(handler: Any, log_path: str) -> bool:
    """判断 handler 是否写入 log_path（FileHandler 与 TimedRotatingFileHandler 均有 baseFilename）。"""
    bf = getattr(handler, "baseFilename", None)
    if bf is None:
        return False
    return os.path.abspath(str(bf)) == os.path.abspath(log_path)


@config_class
@dataclass
class NotificationConfig:
    """日志驱动通知配置（可从 <subsystem>.yaml 的 notification 段读取）"""

    enabled: bool = config_field(default=True, description="是否启用日志驱动通知")
    sysname: str = config_field(default="", description="通知中的实例名（空则取子系统短名，如 gridtrade）")
    digest_interval_sec: float = config_field(default=300.0, description="WARNING 摘要周期(秒)")
    max_message_chars: int = config_field(default=2000, description="单条消息最大字符数")


@config_class
@dataclass
class RuntimeConfig:
    """运行时环境配置（WCore Layer 1）
    
    仅包含运行时环境信息，不包含业务参数。
    训练公共参数（max_epochs/years/sampling_ratio/random_state）由 WTrain 的 TrainRuntime 管理。
    
    分类：
    - 运行时只读变量（ClassVar）：系统初始化后不可修改
    - 可配置参数：参与配置文件、环境变量、命令行参数的统一处理
    """
    
    # ===== 运行时只读变量（ClassVar - 不参与配置系统） =====
    # 这些变量在AppContext初始化时设置，之后只读
    project_root: ClassVar[Optional[Path]] = None
    subsystem_root: ClassVar[Optional[Path]] = None
    workdir: ClassVar[Optional[Path]] = None
    subsystem_name: ClassVar[Optional[str]] = None
    start_time: ClassVar[Optional[datetime]] = None
    python_version: ClassVar[Optional[str]] = None
    platform: ClassVar[Optional[str]] = None
    environment: ClassVar[Optional[Dict[str, str]]] = None
    config_file: ClassVar[Optional[Path]] = None
    project_root_env: ClassVar[Optional[Dict[str, str]]] = None  # 从 project_root.yaml 加载的环境变量补充
    
    # ===== 可配置参数（运行模式） =====
    test: bool = config_field(
        default=False,
        description="测试/联调标记：当前仅用于日志级别（DEBUG）等观测差异；业务规则（交易日、QMT 撤单等）不得据此分支",
    )
    verbose: bool = config_field(
        default=False,
        description="详细输出：--help --verbose 等价 --help-all；应用可用来打启动核对 INFO。不改变日志级别",
    )
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    log_rotate_daily: bool = config_field(
        default=False,
        description="日志是否按本地时区每日午夜轮转（TimedRotatingFileHandler）；False 时仍为单文件 FileHandler",
    )
    log_backup_count: int = config_field(
        default=7,
        description="按日轮转时保留的历史文件个数；0 表示不删除旧文件（仅当 log_rotate_daily 为 True 时生效）",
    )
    log_console_brief: bool = config_field(
        default=True,
        description="控制台仅输出消息正文（无时间戳/模块/INFO）；文件 Handler 仍为完整行。WARNING+ 带级别前缀",
    )

    # ===== 派生属性 =====
    @property
    def data_dir(self) -> Path:
        """数据目录路径"""
        return self.project_root / "data"
    
    @property
    def dct_data_dir(self) -> Path:
        """DCT数据目录路径"""
        return self.data_dir / "daily_all_dct"
    
    @property
    def models_dir(self) -> Path:
        """模型输出目录路径"""
        return self.subsystem_root / "models"
    
    @property
    def log_file(self) -> Path:
        """运行时日志文件路径"""
        short_name = (self.subsystem_name or "").split(".")[-1]
        name = short_name or (self.subsystem_name or "app")
        return self.workdir / f"{name}.log"


T = TypeVar('T')


class AppContext:
    """统一应用程序上下文（WCore 入口）
    
    提供：
    - runtime_config: 运行时环境配置
    - config: 应用层配置实例
    - logger: 日志器
    
    注意：工件管理（artifacts）由 WTrain 框架提供，不属于 WCore 职责。
    """
    
    # 静态属性，用于访问全局组件
    logger: Optional[EnhancedLogger] = None
    config: Optional[Any] = None  # 存储应用层配置实例
    runtime_config: Optional[RuntimeConfig] = None
    _remaining_args: Optional[list] = None  # 供 TrainContext 解析 L2 (TrainRuntime) 命令行
    
    def __init__(
        self,
        config_cls: Optional[Type[T]],
        subsystem_name: str,
        extra_args: Optional[list] = None,
        extra_help_classes: Optional[list] = None,
    ):
        """初始化应用程序上下文
        
        Args:
            config_cls: 应用层配置类，可为 None（仅使用 RuntimeConfig）
            subsystem_name: 子系统名称
            extra_args: 额外命令行参数
            extra_help_classes: 可选，用于 --help/--help-all（及 --help --verbose 别名）的额外配置类列表
        """
        import platform
        from importlib import resources
        
        # ========================================
        # 阶段1：解析控制参数（help、dump、workdir、test）
        # ========================================
        control_parser = argparse.ArgumentParser(add_help=False)
        control_parser.add_argument("--help", "-h", action="store_true", help="显示基本帮助信息")
        control_parser.add_argument("--help-all", action="store_true", help="显示所有配置项的帮助信息")
        control_parser.add_argument("--workdir", "-w", type=Path, help="工作目录路径")
        control_parser.add_argument("--dump", action="store_true", help="输出完整配置并退出")
        control_parser.add_argument("--test", action="store_true", help="测试模式")
        control_parser.add_argument("--verbose", "-v", action="store_true", help="详细输出模式")
        
        # 解析控制参数
        control_args, remaining_args = control_parser.parse_known_args(extra_args)
        
        # 初始化运行时只读变量（设置到 RuntimeConfig 的 ClassVar）
        from .config_system import _resolve_project_root, _find_config_file, _load_project_root_yaml

        # 解析子系统根目录
        RuntimeConfig.subsystem_root = Path(resources.files(subsystem_name))

        # 解析项目根目录
        RuntimeConfig.project_root = _resolve_project_root(RuntimeConfig.subsystem_root)

        # 加载 project_root.yaml 环境变量补充配置
        RuntimeConfig.project_root_env = _load_project_root_yaml(RuntimeConfig.project_root)

        # 确定工作目录：指定了 --workdir 则使用并 chdir；否则使用当前 cwd，不 chdir
        if hasattr(control_args, 'workdir') and control_args.workdir:
            RuntimeConfig.workdir = Path(control_args.workdir).resolve()
            os.chdir(str(RuntimeConfig.workdir))
        else:
            RuntimeConfig.workdir = Path.cwd()

        # 查找配置文件
        RuntimeConfig.config_file = _find_config_file(RuntimeConfig.workdir, subsystem_name)
        
        # 设置其他运行时信息
        RuntimeConfig.subsystem_name = subsystem_name
        RuntimeConfig.start_time = datetime.now()
        RuntimeConfig.python_version = platform.python_version()
        RuntimeConfig.platform = platform.system()
        RuntimeConfig.environment = dict(os.environ)
        
        # ========================================
        # 阶段2：处理框架配置（RuntimeConfig）
        # ========================================
        framework_parser = argparse.ArgumentParser(
            description="框架级配置参数",
            add_help=False
        )
        RuntimeConfig.add_arguments(framework_parser)
        
        # 解析框架配置的命令行参数
        framework_args, remaining_args = framework_parser.parse_known_args(remaining_args)
        
        # 统一加载流程：配置文件 -> project_root.yaml -> 环境变量 -> 命令行参数
        runtime_config = RuntimeConfig()
        if RuntimeConfig.config_file:
            try:
                runtime_config = RuntimeConfig.from_file(RuntimeConfig.config_file)
            except Exception as e:
                import logging
                logging.getLogger("wcore.app_context").warning(
                    "config file load failed, using defaults: %s", RuntimeConfig.config_file, exc_info=True
                )
                runtime_config = RuntimeConfig()
        runtime_config._override_from_env(RuntimeConfig.project_root_env)
        # 注入控制参数
        if control_args.test:
            framework_args.test = True
        if control_args.verbose:
            framework_args.verbose = True
        runtime_config.override_from_args(framework_args)
        
        # ========================================
        # 阶段3：处理子系统配置（config_cls）
        # ========================================
        config_instance = None
        if config_cls is not None:
            config_parser = argparse.ArgumentParser(
                description=f"{subsystem_name} 子系统配置参数",
                add_help=False
            )
            config_cls.add_arguments(config_parser)
            
            # 解析子系统配置的命令行参数
            config_args, remaining_args = config_parser.parse_known_args(remaining_args)
            
            # 统一加载流程：配置文件 -> project_root.yaml -> 环境变量 -> 命令行参数
            config_instance = config_cls()
            if RuntimeConfig.config_file:
                try:
                    config_instance = config_cls.from_file(RuntimeConfig.config_file)
                except Exception as e:
                    import logging
                    logging.getLogger("wcore.app_context").warning(
                        "config file load failed, using defaults: %s", RuntimeConfig.config_file, exc_info=True
                    )
                    config_instance = config_cls()
            config_instance._override_from_env(RuntimeConfig.project_root_env)
            config_instance.override_from_args(config_args)
        
        # 供 TrainContext 解析 L2 (TrainRuntime) 命令行使用；不在此处报“未识别”，由 TrainContext 解析后若有剩余再告警
        AppContext._remaining_args = remaining_args
        
        # ========================================
        # 处理帮助和配置输出请求
        # ========================================
        if control_args.help or control_args.help_all:
            show_nested = bool(
                control_args.help_all or (control_args.help and control_args.verbose)
            )
            help_parts = []
            help_parts.append("=== 运行期参数 ===")
            help_parts.append("--help, -h  显示基本帮助信息")
            help_parts.append("--help-all  展开嵌套配置（--help --verbose 等价）")
            help_parts.append("--workdir, -w <Path>  工作目录路径")
            help_parts.append("--dump  输出完整配置并退出")
            help_parts.append("--test / --no-test  联调：本子系统 logger 为 DEBUG")
            help_parts.append("--verbose / -v / --no-verbose  详细输出（不改变日志级别）")
            help_parts.append(RuntimeConfig.help(show_nested=show_nested))
            
            if extra_help_classes:
                for help_cls in extra_help_classes:
                    if hasattr(help_cls, "help") and callable(help_cls.help):
                        help_parts.append("")
                        help_parts.append(f"=== {help_cls.__name__} ===")
                        help_parts.append(help_cls.help(show_nested=show_nested))
            
            if config_cls is not None:
                help_parts.append("")
                help_parts.append("=== 子系统配置 ===")
                help_parts.append(config_cls.help(show_nested=show_nested))
            
            print("\n".join(help_parts))
            sys.exit(0)
        
        if control_args.dump:
            # 始终完整输出（含 advance）；不因 --verbose 变短；打印后退出
            dump_parts = []
            dump_parts.append("=== 运行期参数 ===")
            dump_parts.append(runtime_config.dump(verbose=True).strip())
            
            if config_instance is not None:
                dump_parts.append("")
                dump_parts.append("=== 子系统配置 ===")
                dump_parts.append(config_instance.dump(verbose=True).strip())
            
            print("\n".join(dump_parts))
            sys.exit(0)
        
        # ========================================
        # 设置静态属性
        # ========================================
        AppContext.runtime_config = runtime_config
        AppContext.config = config_instance

        # 初始化日志器
        AppContext.logger = self._create_logger(subsystem_name)
        AppContext.logger.info("workdir=%s", str(RuntimeConfig.workdir))
        if RuntimeConfig.config_file:
            AppContext.logger.info("config_file=%s", str(RuntimeConfig.config_file))
        else:
            AppContext.logger.info("config_file not found, using defaults")
        rc = AppContext.runtime_config
        AppContext.logger.info(
            "log_file=%s log_rotate_daily=%s log_backup_count=%s log_console_brief=%s",
            str(rc.log_file),
            rc.log_rotate_daily,
            rc.log_backup_count,
            rc.log_console_brief,
        )
        # 日志驱动通知：ERROR/CRITICAL 即时发送，由 rc.notification 控制，业务无需再调 setup_notification
        from .notification import setup_notification
        setup_notification()

    def _create_logger(self, subsystem_name: str) -> EnhancedLogger:
        """创建日志器"""
        import logging
        from logging.handlers import TimedRotatingFileHandler

        logger = logging.getLogger(subsystem_name)
        # 日志级别只看 test；verbose 不改变 level
        logger.setLevel(logging.DEBUG if self.runtime_config.test else logging.INFO)
        
        # 默认到秒即可；毫秒对秒级防抖/分钟节流主路径可读性收益低
        formatter = CompactNameFormatter(
            "%(asctime)s - %(compact_logger_name)s - %(levelname)s - %(message)s",
            "%Y-%m-%d %H:%M:%S",
            strip_prefix=subsystem_name,
        )
        if not logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.DEBUG if self.runtime_config.test else logging.INFO)
            if self.runtime_config.log_console_brief:
                console_handler.setFormatter(ConsoleBriefFormatter())
            else:
                console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        # 避免子 logger（如 app.qutrade.consumer）的日志传播到 root 后再次被 root 的控制台 handler 输出，导致控制台重复打印
        logger.propagate = False
        log_file = self.runtime_config.log_file
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_path = str(log_file)
            rc = self.runtime_config
            bc = rc.log_backup_count
            if bc < 0:
                raise ValueError(f"log_backup_count must be >= 0, got {bc}")
            has_file_handler = any(
                _handler_targets_log_path(h, log_path) for h in logger.handlers
            )
            if not has_file_handler:
                if rc.log_rotate_daily:
                    file_handler = TimedRotatingFileHandler(
                        log_path,
                        when="midnight",
                        interval=1,
                        backupCount=bc,
                        encoding="utf-8",
                    )
                else:
                    file_handler = logging.FileHandler(log_path, encoding="utf-8")
                file_handler.setLevel(logging.DEBUG if self.runtime_config.test else logging.INFO)
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)

        return EnhancedLogger(logger)

    @classmethod
    def is_test(cls) -> bool:
        """联调标记：本子系统 logger 为 DEBUG。未初始化时 False。业务不得据此分支。"""
        rc = cls.runtime_config
        return bool(rc is not None and rc.test)

    @classmethod
    def is_verbose(cls) -> bool:
        """详细输出。未初始化时 False。不改变日志级别。"""
        rc = cls.runtime_config
        return bool(rc is not None and rc.verbose)
    
    @classmethod
    def ensure_initialized(cls) -> bool:
        """确保AppContext已初始化"""
        if cls.runtime_config is None:
            raise RuntimeError("AppContext not initialized. Please call AppContext() constructor first.")
        return True
