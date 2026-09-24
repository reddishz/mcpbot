#!/usr/bin/env python3
"""
WCore 纯命令行参数使用示例

演示如何使用 cli_only_field 定义仅命令行参数，这些参数
不写入配置文件，仅用于运行时控制。
"""

import logging
from wcore import AppContext
from wcore.config_system import config_class, config_field, cli_only_field
from dataclasses import dataclass, field


@config_class
@dataclass
class TradingSystemConfig:
    """交易系统配置示例"""
    
    # ===== 普通配置参数（可写入配置文件） =====
    symbol: str = config_field(default="AAPL", description="交易标的")
    quantity: int = config_field(default=100, description="交易数量")
    max_price: float = config_field(default=200.0, description="最高价格限制")
    
    # ===== 纯命令行参数（不写入配置文件） =====
    # 操作类参数
    force_rebuild: bool = cli_only_field(default=False, description="强制重建模型")
    skip_validation: bool = cli_only_field(default=False, description="跳过数据验证")
    reset_cache: bool = cli_only_field(default=False, description="重置缓存")
    
    # 调试类参数
    debug_port: int = cli_only_field(default=5678, description="调试器端口")
    profile_output: str = cli_only_field(default="", description="性能分析输出文件")
    trace_level: str = cli_only_field(default="INFO", description="跟踪级别")
    
    # 开关类参数
    enable_paper_trading: bool = cli_only_field(default=False, description="启用模拟交易")
    use_fast_mode: bool = cli_only_field(default=False, description="使用快速模式")
    dry_run: bool = cli_only_field(default=False, description="模拟运行，不实际执行")


def main():
    """主函数演示纯命令行参数的使用"""
    
    # 初始化应用上下文
    app = AppContext(TradingSystemConfig, "cli_only_example")
    
    # 获取配置实例
    config = AppContext.config
    logger = AppContext.logger
    
    logger.info("=== 配置参数演示 ===")
    logger.info("交易标的: %s", config.symbol)
    logger.info("交易数量: %d", config.quantity)
    logger.info("最高价格: %.2f", config.max_price)
    
    logger.info("=== 纯命令行参数演示 ===")
    logger.info("强制重建模型: %s", config.force_rebuild)
    logger.info("跳过数据验证: %s", config.skip_validation)
    logger.info("重置缓存: %s", config.reset_cache)
    
    logger.info("调试器端口: %d", config.debug_port)
    logger.info("性能分析输出: %s", config.profile_output or "无")
    logger.info("跟踪级别: %s", config.trace_level)
    
    logger.info("模拟交易: %s", config.enable_paper_trading)
    logger.info("快速模式: %s", config.use_fast_mode)
    logger.info("模拟运行: %s", config.dry_run)
    
    # 演示配置文件内容（不包含 cli_only 字段）
    logger.info("=== 配置文件内容演示 ===")
    config_dump = config.dump(verbose=True)
    logger.info("dump() 输出（不包含纯命令行参数）:\n%s", config_dump)
    
    # 演示业务逻辑
    if config.force_rebuild:
        logger.info("执行强制重建逻辑...")
    
    if config.skip_validation:
        logger.info("跳过数据验证步骤")
    else:
        logger.info("执行数据验证...")
    
    if config.enable_paper_trading:
        logger.info("启用模拟交易模式")
    
    if config.dry_run:
        logger.info("模拟运行：不会执行实际交易")
    else:
        logger.info("执行实际交易逻辑")
    
    logger.info("演示完成")


if __name__ == "__main__":
    main()
