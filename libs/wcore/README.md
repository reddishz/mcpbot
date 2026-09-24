# WCore - 基础应用框架

## 🎯 简介

WCore 是基础应用框架，提供配置、日志、路径等通用基础设施。

**设计原则**：
- 无业务语义
- 无ML依赖
- 可独立用于任意Python应用

## 📦 安装

```bash
cd /data/mworks/quant/strategy/libs/wcore
pip install -e .
```

## 📝 使用方法

```python
from wcore import AppContext, RuntimeConfig
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
```

## 📁 结构

```
wcore/
├── pyproject.toml         # 现代项目配置文件
├── README.md              # 本文件
└── wcore/
    ├── __init__.py        # 库初始化
    ├── app_context.py     # 应用上下文
    ├── config_system.py   # 配置系统
    ├── logging_utils.py   # 日志工具
    └── dataplane/         # 平面框架（WC-D009，可选 telnet）
```

设计文档见 `libs/wcore/ued/dataplane.md`。

## ⚙️ 项目配置

使用现代 `pyproject.toml` 格式，包含：

- **构建系统**: setuptools + wheel
- **项目元数据**: 名称、版本、描述、作者等
- **依赖管理**: pyyaml>=6.0
- **开发依赖**: pytest, black, flake8, mypy
- **工具配置**: black, pytest, mypy

## 🎉 核心功能

- **AppContext**: 应用上下文管理，整合配置、日志、运行时路径
- **ConfigSystem**: 声明式配置系统，支持 dataclass + 装饰器
- **LoggingUtils**: 增强日志工具，支持结构化输出
- **RuntimeConfig**: 运行时路径管理（workdir、logdir、datadir）
- **Plane（平面框架）**: 四子平面统一路径，可选 Telnet Shell（`pip install wcore[telnet]`，见 WC-D009）

## ⚠️ 重要约束

### 配置系统约束
- **严格禁止**：使用 `getattr()` 或字符串为 key 访问配置
- **必须要求**：明确定义所需配置项，使用类属性访问
- **设计目的**：避免项目中使用任意的 `getattr` 之类的混乱无约束用法

```python
# ❌ 禁止
config_value = getattr(config, "some_key")
config_value = config.__dict__.get("some_key")
config_value = config["some_key"]

# ✅ 正确
config_value = config.some_key  # 类属性访问
```

### 通用约束
- **无业务语义**：WCore 必须保持通用性，不包含任何业务逻辑
- **无 ML 依赖**：保持轻量级，无 PyTorch/sklearn 依赖
- **禁止 print**：统一使用 logger 输出日志
- **路径管理**：通过 RuntimeConfig 获取路径，禁止硬编码

## 🔧 开发环境

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 代码格式化
black wcore/

# 运行测试
pytest

# 类型检查
mypy wcore/
```

---

**状态**: 🚀 可用  
**版本**: 1.0.0  
**格式**: pyproject.toml（现代标准）
