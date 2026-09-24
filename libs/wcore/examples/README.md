# WCore 通知系统使用指南

## 概述

WCore 通知系统基于日志驱动，支持多个级别的自动通知触发。新增的 NOTICE 级别专门用于重要业务事件通知。

## 日志级别与通知行为

| 日志级别 | 数值 | 通知行为 | 使用场景 |
|----------|------|----------|----------|
| CRITICAL | 50 | 立即发送 | 系统级致命错误 |
| NOTICE | 45 | 立即发送 | 重要业务事件（成交结果等） |
| ERROR | 40 | 立即发送 | 功能错误 |
| WARNING | 30 | 批量摘要 | 警告信息 |
| INFO/DEBUG | 20/10 | 不通知 | 一般信息 |

## 快速开始

### 方式1：使用 notice() 接口（推荐）

```python
from wcore import AppContext, notice
from wcore.config_system import config_class, config_field
from dataclasses import dataclass

@config_class
@dataclass
class MyConfig:
    notification_enabled: bool = config_field(default=True)

# 初始化应用（AppContext 内部自动启用通知）
app = AppContext(MyConfig, "my_trading_system")

# 直接使用 notice() 发送重要业务事件通知
notice("订单成交: AAPL 100股 @ 150.25")
notice("策略启动: 动量策略 v2.1")
notice("风险预警: 单日亏损超过5%")
```

### 方式2：直接使用日志系统

```python
from wcore import AppContext
from wcore.notification import NOTICE
from wcore.config_system import config_class, config_field
from dataclasses import dataclass
import logging

@config_class
@dataclass
class MyConfig:
    notification_enabled: bool = config_field(default=True)

# 初始化应用（AppContext 内部自动启用通知）
app = AppContext(MyConfig, "my_trading_system")

# 获取 wcore 日志器（与 AppContext 一致）
logger = logging.getLogger("my_trading_system")

# 直接使用 NOTICE 级别
logger.log(NOTICE, "订单成交: AAPL 100股 @ 150.25")
logger.log(NOTICE, "策略启动: 动量策略 v2.1")
logger.log(NOTICE, "风险预警: 单日亏损超过5%")
```

## 使用场景示例

### 交易相关通知
```python
# 成交结果
notice("订单成交: AAPL 100股 @ 150.25")
notice("订单成交: TSLA 50股 @ 245.80")
notice("订单拒绝: 余额不足")

# 策略状态
notice("策略启动: 动量策略 v2.1")
notice("策略暂停: 市场波动过大")
notice("策略停止: 达到止损条件")
```

### 风险管理通知
```python
# 风险预警
notice("风险预警: 单日亏损超过5%")
notice("风险预警: 持仓集中度过高")
notice("风险预警: 杠杆率超过限制")
```

### 系统重要事件
```python
# 数据和模型
notice("数据更新: 获取最新行情数据成功")
notice("模型更新: 重新训练模型完成")
notice("信号生成: 新的买卖信号已生成")
```

## 配置选项

在 `RuntimeConfig.notification` 中可配置：

```yaml
notification:
  enabled: true                    # 是否启用通知
  digest_interval_sec: 300.0      # WARNING 摘要周期（秒）
  max_message_chars: 2000         # 单条消息最大字符数
```

## 最佳实践

1. **语义清晰**：使用 NOTICE 级别发送真正的业务重要事件，避免与系统错误混淆
2. **推荐使用 notice()**：大部分场景使用 `notice()` 接口，简单直接
3. **日志器一致性**：使用 `logger.getLogger("subsystem_name")` 确保与 AppContext 一致
4. **适度使用**：避免过度发送通知，确保通知的有效性

## 与传统日志的区别

### 方式1：使用 notice() 接口（推荐）

```python
from wcore import notice

# 重要业务事件 - 触发通知
notice("订单成交: AAPL 100股 @ 150.25")
```

### 方式2：直接使用日志系统

```python
import logging
from wcore.notification import NOTICE

# 获取与 AppContext 一致的日志器
logger = logging.getLogger("my_trading_system")

# 重要业务事件 - 触发通知
logger.log(NOTICE, "订单成交: AAPL 100股 @ 150.25")

# 系统错误 - 触发通知
logger.error("系统错误")

# 普通日志 - 不触发通知
logger.info("普通信息")
logger.debug("调试信息")
```

### 两种方式对比

| 方式 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| `notice()` | 简单直接，无需了解日志级别 | 灵活性较低 | 大部分业务场景 |
| `logger.log(NOTICE, ...)` | 灵活性高，可与日志系统集成 | 需要了解日志级别 | 需要动态控制或复杂日志处理 |

## 注意事项

1. **初始化依赖**：`notice()` 函数需要在 AppContext 初始化后使用
2. **自动启用**：AppContext 内部自动调用 `setup_notification()`，无需手动调用
3. **日志器一致性**：使用 `logging.getLogger("subsystem_name")` 确保与 AppContext 的子系统名称一致
4. **发送模式**：NOTICE 级别的通知会立即发送，不同于 WARNING 的摘要模式
