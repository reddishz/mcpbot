"""部署配置分段与结构校验（FLW-004、CON-009 至 CON-012）。

取值区间逐项来自 L5-001 的 CON-010 与 CON-011 约束表；wcore 的配置装载对未知键与
转换失败只作静默处理，因此本站自行做「必填齐全 + 落在允许区间」的判定，
使无效配置在启动阶段就能定位到段，而不是等到运行期以默认值放行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from wcore.config_system import config_class, config_field

# CON-010 执行预算：默认值与允许区间
BUDGET_RANGES = {
    "max_rounds": (8, 1, 16),
    "max_tool_calls": (12, 1, 32),
    "deadline_seconds": (180, 30, 600),
    "input_context_tokens": (8000, 4000, 32000),
    "max_tool_result_kb": (32, 1, 128),
}

# CON-011 入口资源限额：默认值与允许区间
ENTRY_RANGES = {
    "auth_attempts_per_minute": (10, 1, 60),
    "max_body_kb": (256, 16, 1024),
    "history_page_max_items": (100, 10, 200),
    "history_response_max_kb": (512, 64, 2048),
}


@config_class
@dataclass
class SiteSection:
    """站点凭据与入口限额（CON-012：仅随进程重启生效）。"""

    key: str = config_field(default="", description="站点 Key；空值视为配置缺失并拒绝启动")
    auth_attempts_per_minute: int = config_field(
        default=ENTRY_RANGES["auth_attempts_per_minute"][0], description="Key 校验尝试频率上限（次/分钟·来源）"
    )
    max_body_kb: int = config_field(default=ENTRY_RANGES["max_body_kb"][0], description="请求体大小上限（KB）")
    history_page_max_items: int = config_field(
        default=ENTRY_RANGES["history_page_max_items"][0], description="历史读取分页条数上限"
    )
    history_response_max_kb: int = config_field(
        default=ENTRY_RANGES["history_response_max_kb"][0], description="历史读取响应大小上限（KB）"
    )


@config_class
@dataclass
class BudgetSection:
    """执行预算取值（CON-010；CON-012：仅随进程重启生效）。"""

    max_rounds: int = config_field(default=BUDGET_RANGES["max_rounds"][0], description="推理轮数上限")
    max_tool_calls: int = config_field(default=BUDGET_RANGES["max_tool_calls"][0], description="工具调用数上限")
    deadline_seconds: int = config_field(default=BUDGET_RANGES["deadline_seconds"][0], description="单次执行总时限（秒）")
    input_context_tokens: int = config_field(
        default=BUDGET_RANGES["input_context_tokens"][0], description="输入上下文预算（tokens）"
    )
    max_tool_result_kb: int = config_field(
        default=BUDGET_RANGES["max_tool_result_kb"][0], description="单个工具结果大小上限（KB）"
    )


@config_class
@dataclass
class OllamaSection:
    """Ollama 服务段（FLW-004；CON-012：仅随进程重启生效）。"""

    base_url: str = config_field(default="http://127.0.0.1:11434", description="服务地址")
    model: str = config_field(default="", description="模型标识；空值视为配置缺失并拒绝启动")
    api_key: str = config_field(default="", description="对应连接凭据，可空；只在接入边界发送给该地址")
    timeout_seconds: int = config_field(default=120, description="单次请求超时（秒）")
    think: bool = config_field(
        default=False,
        description="思考模式开关，默认关闭：实测小模型思考输出可达数千 token，单次推理即耗尽执行总时限",
    )


@config_class
@dataclass
class McpService:
    """一个业务 MCP 服务条目（FLW-004）。"""

    service_id: str = config_field(default="", description="稳定服务标识")
    url: str = config_field(default="", description="Streamable HTTP 地址")
    api_key: str = config_field(default="", description="连接凭据，可空")
    allowed_tools: List[str] = field(default_factory=list)


@config_class
@dataclass
class McpSection:
    """业务 MCP 服务段（CON-012：运行期受控刷新；集合可为空）。"""

    services: List[McpService] = field(default_factory=list)


@config_class
@dataclass
class StorageSection:
    """记录域位置：配置与运行日志由 wcore 承担，此处只给业务记录域目录。"""

    data_dir: str = config_field(default="data", description="五类记录的文件域根目录")


@config_class
@dataclass
class WebSection:
    listen_host: str = config_field(default="127.0.0.1", description="应用监听地址，须受控（NFR-002）")
    listen_port: int = config_field(default=8100, description="应用监听端口")
    public_mode: bool = config_field(default=False, description="公网访问模式：传输保护依赖反向代理，本站不实现 TLS")
    trust_forwarded_for: bool = config_field(
        default=False, description="是否采信 X-Forwarded-For 判定来源；仅在部署于可信反向代理之后开启"
    )


@config_class
@dataclass
class McpBotConfig:
    """本站应用层配置（AppContext 的唯一 config_cls）。"""

    site: SiteSection = field(default_factory=SiteSection)
    budget: BudgetSection = field(default_factory=BudgetSection)
    ollama: OllamaSection = field(default_factory=OllamaSection)
    mcp: McpSection = field(default_factory=McpSection)
    storage: StorageSection = field(default_factory=StorageSection)
    web: WebSection = field(default_factory=WebSection)


def _range_check(section: str, name: str, value: int, table: dict, faults: List[str]) -> None:
    _default, low, high = table[name]
    if not low <= value <= high:
        faults.append(f"{section}.{name}={value} 超出允许区间 [{low}, {high}]")


def validate_config(cfg: McpBotConfig) -> List[str]:
    """逐段结构校验，返回可定位到段的诊断（FLW-004 关键步骤）。"""

    faults: List[str] = []

    if not cfg.site.key.strip():
        faults.append("site.key 缺失：站点凭据为空会让 wcore 的权限门自行生成 Key 并写入日志，本站拒绝该形态")
    for name in ENTRY_RANGES:
        _range_check("site", name, getattr(cfg.site, name), ENTRY_RANGES, faults)
    for name in BUDGET_RANGES:
        _range_check("budget", name, getattr(cfg.budget, name), BUDGET_RANGES, faults)

    if not cfg.ollama.base_url.strip():
        faults.append("ollama.base_url 缺失：Ollama 段结构无效判为致命")
    if not cfg.ollama.model.strip():
        faults.append("ollama.model 缺失：Ollama 段结构无效判为致命")

    seen = set()
    for svc in cfg.mcp.services:
        # 单条目结构无效只判该服务不可用，不判致命（FLW-004 异常分支）
        if not svc.service_id.strip() or svc.service_id in seen:
            faults.append(f"mcp 条目 service_id 缺失或重复：{svc.service_id!r}（该条目跳过）")
            continue
        seen.add(svc.service_id)
        if not svc.url.strip():
            faults.append(f"mcp 条目 {svc.service_id} 的 url 缺失（该条目跳过）")

    if not cfg.storage.data_dir.strip():
        faults.append("storage.data_dir 缺失")

    return faults


def unavailable_services(cfg: McpBotConfig) -> List[str]:
    """按 FLW-004 异常分支筛出结构无效、只能判不可用的服务标识。"""

    bad = []
    seen = set()
    for svc in cfg.mcp.services:
        if not svc.service_id.strip() or not svc.url.strip() or svc.service_id in seen:
            bad.append(svc.service_id or "<未命名>")
            continue
        seen.add(svc.service_id)
    return bad


def allowed_tools_for(cfg: McpBotConfig, service_id: str) -> Optional[List[str]]:
    """服务标识到允许工具集合；未配置该服务时返回 None 表示不开放任何调用。"""

    for svc in cfg.mcp.services:
        if svc.service_id == service_id:
            return list(svc.allowed_tools)
    return None
