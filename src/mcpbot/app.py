"""Web 与鉴权组件的入口装配（CMP-001 路由与页面外壳、IF-001 至 IF-004 的 HTTP 映射）。

页面外壳与登录页可匿名加载但不含任何业务数据；所有业务入口在读取业务数据或触发处理前
逐次验证本站凭据。增量呈现走普通 HTTP 响应流，由页面带 Authorization 请求头自行读取并
解析分帧（ADR-002）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from wcore.app_context import AppContext

from .auth import AuthAttemptLimiter, SiteAuth, bearer_of, client_source, read_limited_body
from .config import McpBotConfig, unavailable_services, validate_config
from .durable import DomainLock, DomainLockHeld, PersistenceUnavailable
from .history import HistoryStore
from .ollama import OllamaClient
from .orchestration import ChatOrchestrator, SlotBusy

SUBSYSTEM = "mcpbot"
_PKG_DIR = Path(__file__).resolve().parent


class AppState:
    def __init__(self, cfg: McpBotConfig, history: HistoryStore, ollama: OllamaClient, orchestrator: ChatOrchestrator, auth: SiteAuth):
        self.cfg = cfg
        self.history = history
        self.ollama = ollama
        self.orchestrator = orchestrator
        self.auth = auth
        self.limiter = AuthAttemptLimiter(cfg.site.auth_attempts_per_minute)
        self.log = logging.getLogger(SUBSYSTEM)


def _asset_version() -> str:
    # 升级后浏览器会按 URL 复用旧脚本；用静态文件更新时间做版本标记随外壳一起刷新
    static_dir = _PKG_DIR / "static"
    try:
        stamp = max(path.stat().st_mtime for path in static_dir.iterdir())
    except (OSError, ValueError):
        return "0"
    return str(int(stamp))


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="mcpbot", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(_PKG_DIR / "static")), name="static")
    templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))
    asset_version = _asset_version()

    def current_state(request: Request) -> AppState:
        return request.app.state.mcpbot

    async def require_site_key(request: Request) -> None:
        state = current_state(request)
        if not state.auth.verify(bearer_of(request)):
            # 本站 401：页面据此清理当前标签页 Key 并关闭敏感视图
            raise HTTPException(status_code=401, detail="本站凭据校验未通过")

    @app.get("/", response_class=HTMLResponse)
    async def shell(request: Request) -> HTMLResponse:
        # 外壳只承载登录与呈现骨架，不内嵌聊天、记忆、配置、凭据或运行状态
        state = current_state(request)
        return templates.TemplateResponse(
            request, "index.html", {"public_mode": state.cfg.web.public_mode, "asset_v": asset_version}
        )

    @app.post("/api/auth/check")
    async def auth_check(request: Request) -> JSONResponse:
        state = current_state(request)
        source = client_source(request, state.cfg.web.trust_forwarded_for)
        retry_after = state.limiter.check(source)
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={"detail": "校验尝试频率超限", "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        if not state.auth.verify(bearer_of(request)):
            raise HTTPException(status_code=401, detail="本站凭据校验未通过")
        return JSONResponse({"result": "校验成功"})

    @app.post("/api/chat", dependencies=[Depends(require_site_key)])
    async def chat(request: Request) -> StreamingResponse:
        state = current_state(request)
        body = await _json_body(request, state.cfg.site.max_body_kb)
        text = body.get("text")
        request_id = body.get("request_id")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=400, detail="用户文本为空或格式不符")
        if not isinstance(request_id, str) or not request_id.strip():
            raise HTTPException(status_code=400, detail="缺少请求标识")
        # 输入上下文预算由 ALG-001 在装配推理输入时判定并拒绝执行；入口只受 CON-011 的请求体体积限额

        # 尚未开流的阶段：持久化不可用按 IF-002 的 503 口径表达；开流后结局一律由事件表达
        try:
            existing = state.history.by_request_id(request_id)
            if existing is not None and existing.user_text != text:
                raise HTTPException(status_code=409, detail="请求标识与已保存文本冲突")
            snapshot = state.history.snapshot_of(existing.execution_id) if existing else None
        except PersistenceUnavailable:
            raise HTTPException(status_code=503, detail="记录域当前不可读，无法核实已保存状态")
        if existing is not None:
            if snapshot is None:
                raise HTTPException(status_code=503, detail="已保存状态无法核实")
            # 同标识同文本：返回一次性状态快照，不创建新执行、不另开反馈流
            return JSONResponse(status_code=200, content={"snapshot": snapshot})

        execution = state.orchestrator.new_execution(request_id)
        try:
            await state.orchestrator.slot.try_acquire(execution)
        except SlotBusy:
            # CON-009 固定并发为 1 且不排队；剩余时限取在途执行，不取新执行的预算
            waiting = state.orchestrator.slot.remaining_of_current()
            raise HTTPException(
                status_code=429,
                detail="已有对话执行在途",
                headers={"Retry-After": str(int(waiting) + 1)},
            )

        queue = state.orchestrator.start(execution, text)

        async def frames() -> AsyncIterator[bytes]:
            # 只负责把事件投递给客户端；执行本体在独立任务里跑完本地工作与有界收尾
            while True:
                event = await queue.get()
                if event is None:
                    return
                if execution.client_gone:
                    continue
                yield _frame(event)
                if await request.is_disconnected():
                    execution.client_gone = True

        return StreamingResponse(frames(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})

    @app.get("/api/history", dependencies=[Depends(require_site_key)])
    async def history_read(request: Request, cursor: Optional[str] = None, limit: int = 20) -> JSONResponse:
        state = current_state(request)
        page_limit = max(1, min(limit, state.cfg.site.history_page_max_items))
        ceiling = state.cfg.site.history_response_max_kb * 1024
        try:
            items, next_cursor, boundary = state.history.query(cursor, page_limit, ceiling)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效游标或参数")
        except PersistenceUnavailable:
            # IF-004：持久化不可用不得伪装成「首次使用的空历史」
            raise HTTPException(status_code=503, detail="记录域当前不可读，历史未能确认")
        return JSONResponse(
            {"items": items, "cursor": next_cursor, "boundary": boundary},
            headers={"Cache-Control": "no-store"},
        )

    return app


async def _json_body(request: Request, max_kb: int) -> Dict[str, object]:
    raw = await read_limited_body(request, max_kb * 1024)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="请求体不是有效 JSON")
    if not isinstance(document, dict):
        raise HTTPException(status_code=400, detail="请求体不是 JSON 对象")
    return document


def _frame(event: Dict[str, object]) -> bytes:
    # IF-003 只钉事件类别与公共字段，封装形态由本接口定为 ndjson：每行一条裸 JSON
    return (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


# ---------- 启动装配 ----------


def build_state(cfg: McpBotConfig) -> AppState:
    data_dir = Path(cfg.storage.data_dir).expanduser().resolve()
    history = HistoryStore(data_dir)
    ollama = OllamaClient(cfg.ollama)
    orchestrator = ChatOrchestrator(cfg, history, ollama)
    return AppState(cfg, history, ollama, orchestrator, SiteAuth(cfg))


def startup_checks(cfg: McpBotConfig, log: logging.Logger) -> List[str]:
    """启动核对项：返回致命诊断，空列表表示通过；MCP 单条目无效只告警跳过。"""

    runtime = AppContext.runtime_config
    faults: List[str] = []
    if runtime is None or runtime.config_file is None:
        # wcore 在找不到配置文件时静默使用默认值，本站不接受以默认值放行的形态
        faults.append("未在工作目录找到部署配置文件，拒绝以默认配置启动")
    if runtime is not None and getattr(getattr(runtime, "notification", None), "enabled", False):
        faults.append("日志驱动通知出站通道未显式关闭（notification.enabled 必须为 false）")
    validate = validate_config(cfg)
    faults.extend(line for line in validate if not line.startswith("mcp 条目"))
    for line in [f for f in validate if f.startswith("mcp 条目")]:
        log.warning("MCP 服务条目判为不可用并跳过：%s", line)
    return faults


def _refuse(lines: List[str]) -> int:
    # 不走 logger：本站把 ERROR 日志本身当作通知触发点，通知通道尚未确认关闭时必须避免外发
    for line in lines:
        print("部署核对失败：" + line, file=sys.stderr)
    return 1


def main() -> int:
    # extra_args 留空即由 AppContext 取 sys.argv[1:]；传 [] 会让 --help/--dump/--workdir 全部失效
    AppContext(McpBotConfig, SUBSYSTEM)
    cfg: McpBotConfig = AppContext.config
    log = AppContext.logger
    faults = startup_checks(cfg, log)
    if faults:
        return _refuse(faults)
    leftovers = [str(token) for token in (AppContext._remaining_args or [])]
    if leftovers:
        log.error("未识别的命令行参数：%s", " ".join(leftovers))
        return 4

    data_dir = Path(cfg.storage.data_dir).expanduser().resolve()
    try:
        lock = DomainLock(data_dir)
        lock.acquire()
    except DomainLockHeld as exc:
        log.error("写入保护生效：拒绝启动。%s", exc)
        return 2

    state = build_state(cfg)
    fault = asyncio.run(state.ollama.probe())
    if fault:
        log.error("Ollama 段可达性检查失败（%s），拒绝进入可服务状态", fault)
        return 3
    recovered = state.history.recover()
    if recovered:
        log.warning("遗留未结束执行已标为已终止：%s 条", recovered)
    if unavailable_services(cfg):
        log.warning("以下业务 MCP 服务配置结构无效，启动阶段跳过：%s", unavailable_services(cfg))

    app = create_app(state)
    app.state.mcpbot = state
    log.info(
        "mcpbot 启动：监听 %s:%s，记录域 %s，公网模式 %s",
        cfg.web.listen_host,
        cfg.web.listen_port,
        data_dir,
        cfg.web.public_mode,
    )
    try:
        # 单进程固定：同事务域与在途快照语义不支撑多 worker（ADR-002 横向扩展不作承诺）
        uvicorn.run(app, host=cfg.web.listen_host, port=cfg.web.listen_port, log_level="warning", access_log=False)
    finally:
        asyncio.run(state.ollama.aclose())
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
