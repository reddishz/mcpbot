# WCore 纯命令行参数使用指南

## 概述

WCore 配置系统支持纯命令行参数（`cli_only` 字段），这些参数仅用于运行时控制，不写入配置文件，适用于一次性操作、调试辅助和临时开关等场景。

## 使用场景

### 1. 操作类参数
用于控制特定操作的执行，通常不需要持久化：
- `--force-rebuild`：强制重建模型
- `--skip-validation`：跳过数据验证
- `--reset-cache`：重置缓存

### 2. 调试类参数
用于开发和调试过程中的临时控制：
- `--debug-port 5678`：调试器端口
- `--profile-output profile.out`：性能分析输出文件
- `--trace-level DEBUG`：跟踪级别

### 3. 开关类参数
用于临时启用/禁用功能：
- `--enable-paper-trading`：启用模拟交易
- `--use-fast-mode`：使用快速模式
- `--dry-run`：模拟运行，不实际执行

## 定义方式

```python
from wcore.config_system import config_class, config_field, cli_only_field
from dataclasses import dataclass

@config_class
@dataclass
class MyConfig:
    # 普通配置参数（可写入配置文件）
    batch_size: int = config_field(default=32, description="批次大小")
    
    # 纯命令行参数（不写入配置文件）
    force_rebuild: bool = cli_only_field(default=False, description="强制重建模型")
    debug_port: int = cli_only_field(default=5678, description="调试器端口")
    dry_run: bool = cli_only_field(default=False, description="模拟运行")
```

## 行为特性

### 命令行解析
```bash
# 查看帮助（包含纯命令行参数）
python cli_only_example.py --help

# 使用纯命令行参数
python cli_only_example.py --force-rebuild --debug-port 9999 --dry-run
```

### 环境变量覆盖
```bash
# 使用环境变量覆盖纯命令行参数
FORCE_REBUILD=true DEBUG_PORT=8888 python cli_only_example.py

# 优先级：命令行 > 环境变量 > 默认值
FORCE_REBUILD=true python cli_only_example.py --force-rebuild=false  # 结果为 false
```

### 配置文件行为
- **不写入**：`config.dump()` 不会包含 `cli_only` 字段
- **不加载**：配置文件中的对应字段会被忽略
- **环境变量**：仍然支持环境变量覆盖

### 示例对比

#### 配置文件内容 (config.yaml)
```yaml
# 这些字段会被正常加载
symbol: "AAPL"
quantity: 100
max_price: 200.0

# 这些字段会被忽略
force_rebuild: true
debug_port: 9999
```

#### dump() 输出
```yaml
symbol: AAPL
quantity: 100
max_price: 200.0
# 注意：不包含 force_rebuild, debug_port 等 cli_only 字段
```

## 命名规范

对于 `cli_only=True` 的参数，建议采用动词或动词短语开头：

- **操作类**：`force_rebuild`, `skip_validation`, `reset_cache`
- **调试类**：`debug_port`, `profile_output`, `trace_level`
- **开关类**：`enable_feature_x`, `use_cache`, `dry_run`

命令行自动转换为 `kebab-case`（如 `--force-rebuild`）。

## 完整示例

参见 `cli_only_example.py` 文件，展示了：
- 混合使用普通配置和纯命令行参数
- 配置文件与命令行参数的交互
- 业务逻辑中如何使用这些参数

## 最佳实践

1. **职责分离**：持久化配置用 `config_field`，临时控制用 `cli_only_field`
2. **明确语义**：使用描述性名称，清楚表达参数用途
3. **合理默认值**：提供安全的默认值，确保无参数时也能正常运行
4. **文档说明**：在 `description` 中清楚说明参数的作用和使用场景

## 与传统方式的对比

### 传统方式（不推荐）
```python
# 需要手动解析 argparse，失去类型安全和 IDE 支持
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--force-rebuild', action='store_true')
args = parser.parse_args()

if args.force_rebuild:
    # 业务逻辑...
```

### WCore 方式（推荐）
```python
# 类型安全，IDE 支持，统一的帮助系统
force_rebuild: bool = cli_only_field(default=False, description="强制重建模型")

# 直接使用，无需额外解析
if config.force_rebuild:
    # 业务逻辑...
```

## 注意事项

1. **初始化依赖**：`cli_only_field` 需要在 `@config_class` 装饰的类中使用
2. **配置文件**：这些字段不会出现在配置文件中，避免混淆
3. **向后兼容**：现有代码无需修改，可以渐进式引入
