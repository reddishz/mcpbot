#!/usr/bin/env python3
"""
WCore 通知系统使用示例

演示如何使用新增的 NOTICE 级别通知功能，用于重要业务事件通知。
"""

import logging
from wcore import AppContext, notice
from wcore.config_system import config_class, config_field
from dataclasses import dataclass


@config_class
@dataclass
class ExampleConfig:
    """示例配置"""
    notification_enabled: bool = config_field(default=True, description="是否启用通知")
    test_mode: bool = config_field(default=True, description="测试模式")


def main():
    """主函数演示通知使用"""
    
    # 1. 初始化应用上下文（AppContext 内部自动启用通知）
    app = AppContext(ExampleConfig, "notification_example")
    
    # 2. 方式1：使用 notice() 接口（推荐）
    print("=== 方式1：使用 notice() 接口 ===")
    
    # 成交结果通知
    notice("订单成交: AAPL 100股 @ 150.25")
    notice("订单成交: TSLA 50股 @ 245.80")
    
    # 策略状态通知
    notice("策略启动: 动量策略 v2.1")
    notice("策略暂停: 市场波动过大")
    
    # 风险预警通知
    notice("风险预警: 单日亏损超过5%")
    notice("风险预警: 持仓集中度过高")
    
    print("notice() 通知已发送")
    
    # 3. 方式2：直接使用日志系统
    print("\n=== 方式2：直接使用日志系统 ===")
    
    from wcore.notification import NOTICE
    logger = logging.getLogger("notification_example")
    
    # 使用 NOTICE 级别
    logger.log(NOTICE, "数据更新: 获取最新行情数据成功")
    logger.log(NOTICE, "模型更新: 重新训练模型完成")
    
    print("logger.log(NOTICE, ...) 通知已发送")
    
    # 4. 对比：其他日志级别
    print("\n=== 其他日志级别对比 ===")
    
    # 不触发通知的级别
    logger.info("这是 INFO 日志，不会触发通知")
    logger.debug("这是 DEBUG 日志，不会触发通知")
    
    # 触发摘要通知的级别
    logger.warning("这是 WARNING 日志，会触发摘要通知")
    
    # 触发即时通知的级别
    logger.error("这是 ERROR 日志，会触发即时通知")
    logger.critical("这是 CRITICAL 日志，会触发即时通知")
    
    print("所有日志级别演示完成")


if __name__ == "__main__":
    main()
