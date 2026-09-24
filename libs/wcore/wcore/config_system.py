import logging
import warnings

"""
WCore 配置系统
基于 Python dataclass 的标准配置管理框架

遵循 ued/core/config_system.md 设计规范：
1. RuntimeConfig 处理环境信息与框架级公共基础参数
2. 业务配置 dataclass 处理子系统特有的业务参数
3. 命令行参数：点号分隔层级，下划线转连字符
"""

import argparse
import os
import platform
import sys
from dataclasses import dataclass, field, fields, is_dataclass, MISSING
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    ClassVar,
    Dict,
    List,
    Optional,
    Type,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

import yaml

try:
    from collections.abc import Mapping
except ImportError:
    Mapping = dict  # pragma: no cover

# 定义泛型类型变量
T = TypeVar('T')


def _strip_optional(t: Any) -> Any:
    """Optional[T] / T | None -> T（单层）。"""
    origin = get_origin(t)
    if origin is Union:
        args = tuple(a for a in get_args(t) if a is not type(None))
        if len(args) == 1:
            return args[0]
    return t


def _resolve_dataclass_ref(t: Any) -> Optional[Type]:
    if t is None:
        return None
    if is_dataclass(t) and isinstance(t, type):
        return t
    if callable(t) and not isinstance(t, type):
        try:
            v = t()
        except Exception:
            return None
        if is_dataclass(v) and isinstance(v, type):
            return v
    return None


def _dict_value_dataclass_type(field_type: Any) -> Optional[Type]:
    """若 field_type 为 Mapping[str, V] 且 V 为 dataclass 类，则返回 V。"""
    t = _strip_optional(field_type)
    origin = get_origin(t)
    args = get_args(t)
    if origin is None:
        return None
    if origin in (dict, Dict, Mapping) and len(args) >= 2:
        val_t = _strip_optional(args[1])
        if is_dataclass(val_t) and isinstance(val_t, type):
            return val_t
    return None


def _list_element_dataclass_type(field_type: Any) -> Optional[Type]:
    """若 field_type 为 list[T] 且 T 为 dataclass 类，则返回 T。"""
    t = _strip_optional(field_type)
    origin = get_origin(t)
    args = get_args(t)
    if origin in (list, List) and len(args) >= 1:
        el_t = _strip_optional(args[0])
        if is_dataclass(el_t) and isinstance(el_t, type):
            return el_t
    return None


def _coerce_partial_dict_to_dataclass(dc_cls: Type, value: Any) -> Any:
    """
    将「部分键的 dict」或已是实例的值，整理为完整的 dataclass 实例，供 dump 展示默认值。
    加载阶段若仍保留为 dict，此处用 _override_from_dict 与类默认值对齐。
    """
    if value is None:
        return dc_cls()
    if is_dataclass(value) and isinstance(value, dc_cls):
        return value
    if isinstance(value, dict):
        inst = dc_cls()
        if hasattr(inst, "_override_from_dict"):
            inst._override_from_dict(value)
        else:
            for k, v in value.items():
                nk = _resolve_yaml_key_to_field_name(inst, k)
                if nk is not None:
                    setattr(inst, nk, v)
        return inst
    return value


def _inherit_dataclass_defaults(child: Any, parent: Any) -> Any:
    return child


def _field_metadata_for(obj: Any, field_name: str) -> Dict[str, Any]:
    if not is_dataclass(obj):
        return {}
    for f in fields(obj):
        if f.name == field_name:
            return dict(f.metadata or {})
    return {}


def _resolve_yaml_key_to_field_name(obj: Any, raw_key: str) -> Optional[str]:
    """
    YAML 键可与 dataclass 字段名一致，或为 kebab-case（连字符，与 CLI 风格一致）。
    例如 anchor-threshold-pct -> anchor_threshold_pct。
    无法匹配已知字段时返回 None。
    """
    if not is_dataclass(obj):
        return raw_key if hasattr(obj, raw_key) else None
    name_set = {f.name for f in fields(obj)}
    if raw_key in name_set:
        return raw_key
    normalized = raw_key.replace("-", "_")
    if normalized in name_set:
        return normalized
    return None


def _effective_dump_type_hint(field_type: Any, metadata: Dict[str, Any]) -> Any:
    """合并 config_field 的 dict_value_dataclass / list_element_dataclass，供 dump 识别。"""
    if metadata.get("dict_value_dataclass"):
        return Dict[str, metadata["dict_value_dataclass"]]
    if metadata.get("list_element_dataclass"):
        return List[metadata["list_element_dataclass"]]
    return field_type


def _try_coerce_dict_or_list_field(obj: Any, field_name: str, value: Any) -> Optional[Any]:
    """
    若字段为 Dict[str, Dataclass] 或 List[Dataclass]（类型注解或 metadata 指明元素类型），
    将 YAML 来的 dict/list 转为 dataclass 实例。成功则返回新值，否则 None。
    """
    try:
        hints = get_type_hints(type(obj))
    except Exception:
        hints = {}
    meta = _field_metadata_for(obj, field_name)
    target_type = hints.get(field_name, type(getattr(obj, field_name)))

    dv = _dict_value_dataclass_type(target_type) or meta.get("dict_value_dataclass")
    if dv is not None and isinstance(value, dict):
        return {k: _coerce_partial_dict_to_dataclass(dv, v) for k, v in value.items()}

    le = _list_element_dataclass_type(target_type) or meta.get("list_element_dataclass")
    if le is not None and isinstance(value, list):
        return [_coerce_partial_dict_to_dataclass(le, item) for item in value]

    return None


def _convert_value(value: Any, target_type: Type) -> Any:
    """
    将 value 转为 target_type。

    - 对 bool 做显式解析，避免 bool("False") 为 True 的坑：
      - 若已是 bool，直接返回；
      - 若是字符串，按 true/false/1/0/yes/no/on/off 等解析；
      - 其它类型走 bool(value)；
    - 其它类型沿用原有 target_type(value) 逻辑。
    """
    origin = get_origin(target_type)
    if origin is list or target_type is list:
        item_types = get_args(target_type)
        item_type = item_types[0] if item_types else Any
        if isinstance(value, tuple):
            seq = list(value)
        elif isinstance(value, list):
            seq = value
        else:
            raise TypeError(f"cannot convert {type(value).__name__} to list")
        if item_type is Any:
            return list(seq)
        return [_convert_value(v, item_type) for v in seq]

    if origin is dict or target_type is dict:
        key_val_types = get_args(target_type)
        key_type = key_val_types[0] if len(key_val_types) >= 1 else Any
        val_type = key_val_types[1] if len(key_val_types) >= 2 else Any
        if not isinstance(value, dict):
            raise TypeError(f"cannot convert {type(value).__name__} to dict")
        if key_type is Any and val_type is Any:
            return dict(value)
        out: dict[Any, Any] = {}
        for k, v in value.items():
            kk = k if key_type is Any else _convert_value(k, key_type)
            vv = v if val_type is Any else _convert_value(v, val_type)
            out[kk] = vv
        return out

    # 专门处理布尔类型
    if target_type is bool:
        # 已经是 bool，直接使用
        if isinstance(value, bool):
            return value
        # 字符串形式的布尔
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("true", "1", "yes", "y", "on"):
                return True
            if v in ("false", "0", "no", "n", "off", ""):
                return False
        # 其它类型按 Python 习惯：非 0 / 非空 为真
        return bool(value)

    # 其它常见类型可按需要扩展，这里保持原有行为
    if target_type is str:
        return str(value)
    if target_type is int:
        return int(value)
    if target_type is float:
        return float(value)

    # 回退：直接调用目标类型构造
    if target_type != type(value):
        return target_type(value)
    return value


def config_class(cls: Type) -> Type:
    """
    配置类装饰器

    增强标准dataclass，提供配置管理功能。

    Args:
        cls: 需要装饰的dataclass类

    Returns:
        增强后的配置类

    Usage:
        @config_class
        @dataclass
        class MyConfig:
            host: str = field(default="localhost", metadata={"description": "服务器地址"})
    """
    # 确保类已经是dataclass
    if not is_dataclass(cls):
        cls = dataclass(cls)

    # 添加配置类方法
    cls = _add_config_methods(cls)

    return cls


# 保持向后兼容
ConfigClass = config_class


def _add_config_methods(cls: Type) -> Type:
    """为配置类添加配置管理方法"""

    @classmethod
    def get_instance(cls) -> Any:
        """
        返回当前配置类的单例实例。

        - 由框架在 AppContext 初始化完成后设置；
        - 业务代码可通过 XxxConfig.get_instance() 获取强类型配置实例；
        - 禁止直接使用 init_framework；若尚未初始化，将抛出 RuntimeError。
        """
        instance = getattr(cls, "_instance", None)
        if instance is None:
            raise RuntimeError(
                f"{cls.__name__} 配置未初始化，请先在入口处调用 init_framework 或 load_config"
            )
        return instance

    @classmethod
    def help(cls, show_nested: bool = False, indent: int = 0) -> str:
        """
        生成层次化的配置帮助信息

        Args:
            show_nested: 是否递归显示嵌套配置（True=全部展开，False=只显示顶层）
            indent: 当前缩进层级（用于嵌套配置的递归显示）

        Returns:
            格式化的帮助文本
        """
        lines = []
        prefix = "  " * indent

        # 获取类型提示（处理字符串类型注解）
        try:
            hints = get_type_hints(cls)
        except Exception as e:
            logging.getLogger(__name__).debug("get_type_hints(%s): %s", cls, e)
            hints = {}
        
        for f in cls.__dataclass_fields__.values():
            field_name = f.name
            # 使用类型提示获取实际类型，否则使用字段的原始类型
            field_type = hints.get(field_name, f.type)
            metadata = f.metadata or {}
            description = metadata.get("description", "")

            # 检查是否为 ClassVar 类型，如果是则跳过（不在帮助中显示）
            # ClassVar 类型的字段是运行时只读变量，不能通过命令行参数设置
            if hasattr(field_type, '__origin__') and field_type.__origin__ is ClassVar:
                continue

            # 获取字段类型名称
            type_name = getattr(field_type, "__name__", str(field_type))

            # 获取默认值
            if f.default is not MISSING:
                default_val = f.default
            elif f.default_factory is not MISSING:
                default_val = "<factory>"
            else:
                default_val = "<required>"

            dv = _dict_value_dataclass_type(field_type) or _resolve_dataclass_ref(metadata.get("dict_value_dataclass"))
            le = _list_element_dataclass_type(field_type) or _resolve_dataclass_ref(metadata.get("list_element_dataclass"))

            # 检查是否为嵌套的 dataclass
            if is_dataclass(field_type) and isinstance(field_type, type):
                # 嵌套配置：显示分组标题
                if description:
                    lines.append(f"{prefix}[{field_name}]  说明: {description}")
                else:
                    lines.append(f"{prefix}[{field_name}]")
            
                # 根据 show_nested 决定是否递归显示
                if show_nested:
                    # 递归显示嵌套字段，增加缩进
                    nested_help = field_type.help(show_nested=True, indent=indent + 1)
                    if nested_help and nested_help.strip():
                        for line in nested_help.strip().split('\r\n'):
                            # 每个嵌套行增加2个空格的缩进
                            lines.append(f"  {line}")
                else:
                    # 不显示嵌套内容，只显示提示
                    lines.append(f"{prefix}  (使用 --help-all 或 --help --verbose 查看详细配置)")
            elif show_nested and dv is not None:
                cli_name = field_name.replace("_", "-").lower()
                if description:
                    lines.append(f"{prefix}--{cli_name} <Dict>  默认值: {default_val}  说明: {description}")
                else:
                    lines.append(f"{prefix}--{cli_name} <Dict>  默认值: {default_val}")
                lines.append(f"{prefix}  (YAML 结构：{field_name}: {{ <SYMBOL>: {{...}} }})")
                for sf in dv.__dataclass_fields__.values():
                    sm = sf.metadata or {}
                    sdesc = sm.get("description", "")
                    st = get_type_hints(dv).get(sf.name, sf.type)
                    st_name = getattr(_strip_optional(st), "__name__", str(_strip_optional(st)))
                    if sf.default is not MISSING:
                        sdef = sf.default
                    elif sf.default_factory is not MISSING:
                        sdef = "<factory>"
                    else:
                        sdef = "<required>"
                    s_cli = sf.name.replace("_", "-").lower()
                    if sdesc:
                        lines.append(f"{prefix}  - {s_cli} <{st_name}>  默认值: {sdef}  说明: {sdesc}")
                    else:
                        lines.append(f"{prefix}  - {s_cli} <{st_name}>  默认值: {sdef}")
            elif show_nested and le is not None:
                cli_name = field_name.replace("_", "-").lower()
                if description:
                    lines.append(f"{prefix}--{cli_name} <List>  默认值: {default_val}  说明: {description}")
                else:
                    lines.append(f"{prefix}--{cli_name} <List>  默认值: {default_val}")
                lines.append(f"{prefix}  (YAML 结构：{field_name}: [ {{...}}, ... ])")
                for sf in le.__dataclass_fields__.values():
                    sm = sf.metadata or {}
                    sdesc = sm.get("description", "")
                    st = get_type_hints(le).get(sf.name, sf.type)
                    st_name = getattr(st, "__name__", str(st))
                    if sf.default is not MISSING:
                        sdef = sf.default
                    elif sf.default_factory is not MISSING:
                        sdef = "<factory>"
                    else:
                        sdef = "<required>"
                    s_cli = sf.name.replace("_", "-").lower()
                    if sdesc:
                        lines.append(f"{prefix}  - {s_cli} <{st_name}>  默认值: {sdef}  说明: {sdesc}")
                    else:
                        lines.append(f"{prefix}  - {s_cli} <{st_name}>  默认值: {sdef}")
            else:
                # 普通字段：命令行格式
                cli_name = field_name.replace("_", "-").lower()
            
                if description:
                    lines.append(f"{prefix}--{cli_name} <{type_name}>  默认值: {default_val}  说明: {description}")
                else:
                    lines.append(f"{prefix}--{cli_name} <{type_name}>  默认值: {default_val}")

        return "\r\n".join(lines)

    @classmethod
    def from_file(cls, file_path: Path) -> Any:
        """
        从YAML文件加载配置

        Args:
            file_path: 配置文件路径

        Returns:
            配置实例
        """
        if not file_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f) or {}

        # 创建实例并覆盖配置值
        config = cls()
        return config._override_from_dict(config_data)

    def override_from_args(self, args: argparse.Namespace) -> None:
        """
        使用命令行参数覆盖配置实例的字段值

        Args:
            args: argparse.Namespace 对象，包含解析后的命令行参数

        实现方式：
        - 递归遍历 dataclass 字段结构
        - 对每一层构造匹配的 dest 名（prefix + field_name → database_dbname）
        - 从 args 中取值并覆盖
        """
        self._override_from_args_recursive(self, args, "")

    def _override_from_args_recursive(self, obj: Any, args: argparse.Namespace, prefix: str) -> None:
        """
        递归处理 dataclass 字段，从 args 中覆盖值

        Args:
            obj: 当前 dataclass 对象
            args: argparse.Namespace 对象
            prefix: 当前层级的 dest 前缀（如 "database_" 或 ""）
        """
        if not is_dataclass(obj):
            return

        for field in fields(obj):
            field_name = field.name
            # 构造 dest 名：prefix + field_name（点号转下划线）
            dest = prefix + field_name

            # 从 args 中获取值（None 表示未传参，跳过）
            value = getattr(args, dest, None)
            if value is not None:
                # 设置字段值
                if hasattr(obj, field_name):
                    try:
                        hints = get_type_hints(type(obj))
                    except Exception as e:
                        logging.getLogger(__name__).debug("get_type_hints: %s", e)
                        hints = {}
                    target_type = hints.get(field_name, type(getattr(obj, field_name)))

                    try:
                        if target_type != type(value):
                            converted = target_type(value)
                            setattr(obj, field_name, converted)
                        else:
                            setattr(obj, field_name, value)
                    except (ValueError, TypeError):
                        logging.debug(f"警告: 无法将 '{value}' 转换为 {target_type.__name__}，保持原值")
            # 递归处理嵌套 dataclass
            field_value = getattr(obj, field_name)
            if is_dataclass(field_value):
                # 递归进入下一层，prefix 加上 field_name + "_"
                self._override_from_args_recursive(field_value, args, dest + "_")

    def _override_from_dict(self, data: Dict[str, Any]) -> Any:
        """
        从字典覆盖配置值（内部方法）

        Args:
            data: 包含配置值的字典，键为字段名（支持嵌套字典结构）

        Returns:
            更新后的配置实例

        设计说明：
        - 主要用于从 YAML 文件加载配置（嵌套字典结构）
        - 递归处理嵌套 dataclass 字段
        - 跳过 cli_only 字段，不从配置文件加载
        - 键名支持 kebab-case（含连字符），会解析为字段的 snake_case 名
        """
        for raw_key, value in data.items():
            if value is None:
                continue

            key = _resolve_yaml_key_to_field_name(self, raw_key)
            if key is None:
                continue

            # 获取字段元信息，检查是否为 cli_only
            field_obj = None
            for f in fields(self):
                if f.name == key:
                    field_obj = f
                    break

            # 跳过 cli_only 字段
            if field_obj and field_obj.metadata.get("cli_only", False):
                continue

            # 如果字段是 dataclass 且值是字典，递归处理
            if isinstance(value, dict) and is_dataclass(getattr(self, key)):
                nested_config = getattr(self, key)
                if hasattr(nested_config, "_override_from_dict"):
                    nested_config._override_from_dict(value)
            else:
                # 普通字段直接设置
                self._set_field_value(key, value)

        return self

    def _override_from_env(self, project_root_env: Optional[Dict[str, str]] = None) -> None:
        """
        从环境变量和 project_root.yaml 覆盖配置值

        环境变量命名规则（统一规范）：
        - 顶层字段：{FIELD_NAME}，如 YEARS=5
        - 嵌套字段：{SECTION}_{FIELD_NAME}，如 OPTUNA_N_TRIALS=200

        优先级：
        - 环境变量 > project_root.yaml

        设计原则：
        - 不使用 SUBSYSTEM 前缀，保持简洁，支持跨子系统配置共享
        - 只处理非 None 的环境变量值

        Args:
            project_root_env: 从 project_root.yaml 加载的环境变量补充配置
        """
        project_root_env = project_root_env or {}

        def _apply_env(obj, prefix_tokens):
            if not is_dataclass(obj):
                return
            for f in fields(obj):
                field_name = f.name
                current_value = getattr(obj, field_name)
                is_nested = is_dataclass(current_value)

                # 构造环境变量键：嵌套字段使用 SECTION_FIELD，避免与系统变量（如 PATH）冲突
                if prefix_tokens:
                    env_key = "_".join(prefix_tokens + [field_name]).upper()
                else:
                    env_key = field_name.upper()

                # 仅对非嵌套字段应用环境变量覆盖；嵌套字段递归处理其子字段
                if not is_nested:
                    # 优先级1: 环境变量
                    env_value = os.environ.get(env_key)
                    if env_value is not None:
                        self._set_field_value_from_path(obj, field_name, env_value)
                    else:
                        # 优先级2: project_root.yaml
                        pr_env_value = project_root_env.get(env_key)
                        if pr_env_value is not None:
                            self._set_field_value_from_path(obj, field_name, pr_env_value)

                # 递归处理嵌套 dataclass
                if is_nested:
                    _apply_env(current_value, prefix_tokens + [field_name])

        _apply_env(self, [])

    def _set_field_value(self, field_name: str, value: Any) -> None:
        """设置单个字段值，包含类型转换"""
        if not hasattr(self, field_name):
            return

        coerced = _try_coerce_dict_or_list_field(self, field_name, value)
        if coerced is not None:
            setattr(self, field_name, coerced)
            return

        # 使用类型注解获取目标类型，而非当前值类型
        try:
            hints = get_type_hints(type(self))
        except Exception as e:
            logging.getLogger(__name__).debug("get_type_hints: %s", e)
            hints = {}
        target_type = hints.get(field_name, type(getattr(self, field_name)))

        try:
            converted = _convert_value(value, target_type)
            setattr(self, field_name, converted)
        except (ValueError, TypeError):
            current_value = getattr(self, field_name)
            logging.debug(f"警告: 无法将 '{value}' 转换为 {target_type.__name__}，保持原值: {current_value}")

    def _set_nested_value(self, parts: List[str], value: Any) -> None:
        """设置嵌套配置值"""
        current_obj = self
        for part in parts[:-1]:
            if not hasattr(current_obj, part):
                return
            current_obj = getattr(current_obj, part)

        final_part = parts[-1]
        if hasattr(current_obj, final_part):
            self._set_field_value_from_path(current_obj, final_part, value)

    def _set_field_value_from_path(self, obj: Any, field_name: str, value: Any) -> None:
        """从路径设置字段值"""
        if not hasattr(obj, field_name):
            return

        coerced = _try_coerce_dict_or_list_field(obj, field_name, value)
        if coerced is not None:
            setattr(obj, field_name, coerced)
            return

        # 使用类型注解获取目标类型
        try:
            hints = get_type_hints(type(obj))
        except Exception as e:
            logging.getLogger(__name__).debug("get_type_hints: %s", e)
            hints = {}
        target_type = hints.get(field_name, type(getattr(obj, field_name)))

        try:
            converted = _convert_value(value, target_type)
            setattr(obj, field_name, converted)
        except (ValueError, TypeError):
            getattr(obj, field_name)
            logging.debug(f"警告: 无法将 '{value}' 转换为 {target_type.__name__}")

    def dump(self, verbose: bool = False) -> str:
        """
        将配置序列化为YAML格式

        Args:
            verbose: 是否包含高级配置项（advance=True）

        Returns:
            YAML格式的配置字符串
            
        注意：
        - 自动过滤 cli_only 字段，不写入配置文件
        - 对 Dict[str, Dataclass] / List[Dataclass]（类型注解或 config_field 的 dict_value_dataclass /
          list_element_dataclass）：若值为「部分键的 dict」或嵌套仍为 dict，dump 会先按 dataclass 默认值
          补全再序列化，便于 --dump 看到全部可配置项。
        """
        def _to_dict(
            obj: Any,
            include_all: bool,
            type_hint: Any = None,
            field_meta: Optional[Dict[str, Any]] = None,
            parent_obj: Any = None,
        ) -> Any:
            if is_dataclass(obj):
                try:
                    hints = get_type_hints(type(obj))
                except Exception as e:
                    logging.getLogger(__name__).debug("get_type_hints in dump: %s", e)
                    hints = {}
                result = {}
                for f in fields(obj):
                    metadata = f.metadata or {}
                    is_advance = metadata.get("advance", False)
                    is_cli_only = metadata.get("cli_only", False)

                    # 跳过 cli_only 字段
                    if is_cli_only:
                        continue

                    # 非 verbose 模式下跳过高级配置
                    if not include_all and is_advance:
                        continue

                    ft = _effective_dump_type_hint(hints.get(f.name, f.type), metadata)
                    value = getattr(obj, f.name)
                    result[f.name] = _to_dict(value, include_all, ft, metadata, obj)
                return result
            elif isinstance(obj, (list, tuple)):
                el_dc = _list_element_dataclass_type(type_hint)
                if el_dc is not None:
                    return [
                        _to_dict(_coerce_partial_dict_to_dataclass(el_dc, item), include_all, el_dc, None, None)
                        for item in obj
                    ]
                return [_to_dict(item, include_all, None, None, None) for item in obj]
            elif isinstance(obj, dict):
                val_dc = _dict_value_dataclass_type(type_hint)
                if val_dc is not None:
                    return {
                        k: _to_dict(
                            _coerce_partial_dict_to_dataclass(val_dc, v),
                            include_all,
                            val_dc,
                            None,
                            None,
                        )
                        for k, v in obj.items()
                    }
                return {k: _to_dict(v, include_all, None, None, None) for k, v in obj.items()}
            else:
                return obj

        config_dict = _to_dict(self, verbose, type(self), None, None)
        return yaml.dump(config_dict, allow_unicode=True, indent=2)

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser, prefix: str = "") -> None:
        """
        向argparse解析器递归添加配置参数

        Args:
            parser: argparse.ArgumentParser 实例
            prefix: 当前层级的参数前缀（如 "database." 或 ""）

        处理逻辑:
            1. 遍历当前类的所有字段
            2. 对于普通字段：注册为 --{prefix}{field_name}
            3. 对于嵌套 dataclass 字段：递归调用子类的 add_arguments
            4. 使用 metadata["description"] 作为参数帮助信息
            5. 根据字段类型推断 argparse 的 type 和 action
        """
        # 获取类型提示（处理字符串类型注解）
        try:
            hints = get_type_hints(cls)
        except Exception as e:
            logging.getLogger(__name__).debug("get_type_hints(%s): %s", cls, e)
            hints = {}

        for f in cls.__dataclass_fields__.values():
            field_name = f.name
            field_type = f.type
            metadata = f.metadata or {}
            description = metadata.get("description", "")

            # 构造完整的参数名（层级用点号，字段名下划线转连字符）
            full_name = f"{prefix}{field_name}"
            # 转换为命令行格式：下划线转连字符，全小写
            cli_name = full_name.replace("_", "-").lower()

            # 获取默认值
            if f.default is not MISSING:
                default_value = f.default
            elif f.default_factory is not MISSING:
                default_value = None
            else:
                default_value = None

            # 获取真实的字段类型（处理字符串类型注解）
            actual_type = hints.get(field_name, field_type)
            dv = _dict_value_dataclass_type(actual_type) or _resolve_dataclass_ref(metadata.get("dict_value_dataclass"))
            le = _list_element_dataclass_type(actual_type) or _resolve_dataclass_ref(metadata.get("list_element_dataclass"))
            # 检查是否为嵌套的 dataclass（使用 actual_type 处理字符串类型注解）
            if is_dataclass(actual_type) and isinstance(actual_type, type):
                # 递归处理嵌套配置
                nested_prefix = f"{full_name}."
                actual_type.add_arguments(parser, prefix=nested_prefix)
            elif dv is not None or le is not None:
                continue
            else:
                # 普通字段：注册到 argparse
                # 关键修复：default 设为 None，只有用户传参时才会覆盖 YAML
                kwargs = {"help": description, "default": None}

                # dest 保持原始字段名格式（点号分隔层级，下划线连接）
                dest = full_name.replace('.', '_')

                # 布尔字段使用标准开关形式
                if actual_type is bool:
                    # 添加启用参数 --p995
                    parser.add_argument(
                        f"--{cli_name}",
                        action="store_true",
                        dest=dest,
                        default=None,
                        help=description,
                    )
                    # 添加禁用参数 --no-p995
                    parser.add_argument(
                        f"--no-{cli_name}",
                        action="store_false",
                        dest=dest,
                        default=None,
                        help=f"禁用{description}" if description else f"禁用{field_name}",
                    )
                else:
                    parser.add_argument(
                        f"--{cli_name}",
                        type=actual_type,
                        dest=dest,
                        **kwargs
                    )

    # 将方法添加到类中
    # 单例访问接口
    setattr(cls, "_instance", None)
    cls.get_instance = get_instance

    # 配置管理接口
    cls.help = help
    cls.from_file = from_file
    cls.override_from_args = override_from_args
    cls._override_from_args_recursive = _override_from_args_recursive
    cls._override_from_dict = _override_from_dict
    cls._override_from_env = _override_from_env
    cls._set_field_value = _set_field_value
    cls._set_nested_value = _set_nested_value
    cls._set_field_value_from_path = _set_field_value_from_path
    cls.dump = dump
    cls.add_arguments = add_arguments

    return cls


def resolve_runtime_config(
    args: argparse.Namespace,
    subsystem_name: str,
) -> Any:
    """
    [DEPRECATED] 解析运行时环境配置信息
    
    .. deprecated::
        此函数已废弃。请直接使用 AppContext 类进行初始化，
        RuntimeConfig 的 ClassVar 属性会在 AppContext.__init__ 中自动设置。
        
        替代方案:
            from wcore import AppContext
            
            # 方式1: 使用 AppContext 获取配置
            app_context = AppContext(ConfigClass, "subsystem_name")
            runtime_config = AppContext.runtime_config
            
            # 方式2: 直接访问 RuntimeConfig 的 ClassVar
            from wcore import RuntimeConfig
            project_root = RuntimeConfig.project_root
    
    Args:
        args: 解析后的命令行参数（包含 --workdir 等）
        subsystem_name: 子系统名称，用于定位配置文件

    Returns:
        RuntimeConfig 实例，包含所有运行时环境信息
    """
    warnings.warn(
        "resolve_runtime_config is deprecated, use AppContext class instead",
        DeprecationWarning,
        stacklevel=2
    )
    
    # 使用标准 RuntimeConfig 类
    from .app_context import RuntimeConfig

    # 解析子系统根目录（更安全的查找方式）
    from importlib import resources

    subsystem_root = Path(resources.files(subsystem_name))

    # 解析项目根目录（向上查找）
    project_root = _resolve_project_root(subsystem_root)

    # 确定工作目录
    if hasattr(args, 'workdir') and args.workdir:
        workdir = Path(args.workdir).resolve()
    else:
        workdir = Path.cwd()

    # 查找配置文件
    config_file = _find_config_file(workdir, subsystem_name)

    # 收集环境信息
    environment = dict(os.environ)

    # 设置运行时只读变量（ClassVar）- 设置到类本身
    RuntimeConfig.project_root = project_root
    RuntimeConfig.subsystem_root = subsystem_root
    RuntimeConfig.workdir = workdir
    RuntimeConfig.config_file = config_file
    RuntimeConfig.subsystem_name = subsystem_name
    RuntimeConfig.start_time = datetime.now()
    RuntimeConfig.python_version = platform.python_version()
    RuntimeConfig.platform = platform.system()
    RuntimeConfig.environment = environment
    
    # 创建 RuntimeConfig 实例（只包含可配置参数）
    runtime_config = RuntimeConfig()

    return runtime_config


def _resolve_project_root(subsystem_root: Path) -> Path:
    """解析项目根目录（向上查找包含 project_root.yaml 标记文件的目录）

    设计规范：
    - 在项目根目录放置一个名为 'project_root.yaml' 的文件作为标记
    - 从 subsystem_root 向上最多查找 5 层
    - 未找到时回退到 subsystem_root
    """
    current = subsystem_root
    for _ in range(5):  # 最多向上5层
        if (current / "project_root.yaml").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    # 回退：未找到标记文件时返回 subsystem_root
    return subsystem_root


def _load_project_root_yaml(project_root: Path) -> Dict[str, str]:
    """
    加载项目根目录的环境变量补充配置

    Args:
        project_root: 项目根目录路径

    Returns:
        环境变量补充配置字典
    """
    yaml_path = project_root / "project_root.yaml"
    if not yaml_path.exists():
        return {}

    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
            # 保留 YAML 原始类型，由 _convert_value 负责按目标类型做统一转换
            return {k: v for k, v in data.items() if v is not None}
    except Exception as e:
        logging.getLogger(__name__).warning("load project_root.yaml %s: %s", yaml_path, e)
        return {}


def _find_config_file(workdir: Path, subsystem_name: str) -> Optional[Path]:
    """查找配置文件
    
    兼容子系统包名包含层级（如 app.testqmt、app.qutrade）的情况：
    - 优先按短名（最后一段，如 testqmt、qutrade）查找
    - 再尝试完整包名（app.testqmt.yaml 等）
    - 最后回退到通用文件名（config.yaml）
    """
    short_name = subsystem_name.split(".")[-1] if "." in subsystem_name else subsystem_name
    possible_files = [
        workdir / f"{short_name}.yaml",
        workdir / f"{short_name}.yml",
        workdir / f"{subsystem_name}.yaml",
        workdir / f"{subsystem_name}.yml",
    ]

    for file_path in possible_files:
        if file_path.exists():
            return file_path

    return None


# 配置字段辅助函数
def config_field(
    default: Any = None,
    *,
    description: str = "",
    advance: bool = False,
    default_factory: Any = None,
    dict_value_dataclass: Any = None,
    list_element_dataclass: Any = None,
    **kwargs
):
    """
    配置字段辅助函数，简化字段定义

    Args:
        default: 字段默认值（与default_factory二选一）
        description: 字段描述信息
        advance: 是否为高级配置项
        default_factory: 默认值工厂函数（与default二选一）
        dict_value_dataclass: 当类型为 dict / 裸 Mapping 时，声明值为该 dataclass，
            YAML 加载与 --dump 会按此类合并默认值（等价于 Dict[str, 该类] 的语义）。
        list_element_dataclass: 当类型为 list 时，声明元素为该 dataclass，加载与 dump 同理。
        **kwargs: 其他dataclass.field参数
    """
    metadata = kwargs.pop('metadata', {})
    metadata.update({
        'description': description,
        'advance': advance
    })
    if dict_value_dataclass is not None:
        metadata['dict_value_dataclass'] = dict_value_dataclass
    if list_element_dataclass is not None:
        metadata['list_element_dataclass'] = list_element_dataclass

    # 处理default_factory情况
    if default_factory is not None:
        return field(default_factory=default_factory, metadata=metadata, **kwargs)
    else:
        return field(default=default, metadata=metadata, **kwargs)


def cli_only_field(
    default: Any = None,
    *,
    description: str = "",
    default_factory: Any = None,
    **kwargs
):
    """
    纯命令行字段辅助函数，定义仅命令行参数

    Args:
        default: 字段默认值（与default_factory二选一）
        description: 字段描述信息
        default_factory: 默认值工厂函数（与default二选一）
        **kwargs: 其他dataclass.field参数
    """
    metadata = kwargs.pop('metadata', {})
    metadata.update({
        'description': description,
        'cli_only': True  # 标记为仅命令行参数
    })

    # 处理default_factory情况
    if default_factory is not None:
        return field(default_factory=default_factory, metadata=metadata, **kwargs)
    else:
        return field(default=default, metadata=metadata, **kwargs)
