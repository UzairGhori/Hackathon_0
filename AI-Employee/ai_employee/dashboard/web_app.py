"""
AI Employee — FastAPI Web Dashboard

Modern dashboard built with FastAPI + Tailwind UI + Alpine.js.

Pages:
    GET /          — Dashboard overview
    GET /tasks     — Task overview with queue, history, AI decisions
    GET /approvals — Approval requests with approve/reject actions
    GET /briefings — Weekly CEO briefing reports
    GET /logs      — System logs with level filtering
    GET /system    — Integration status, health, service monitoring

API Endpoints:
    GET  /api/tasks                      — Task data
    GET  /api/approvals                  — Approval data
    POST /api/approvals/{id}/approve     — Approve a request
    POST /api/approvals/{id}/reject      — Reject a request
    GET  /api/briefings                  — Briefing report data
    POST /api/briefings/generate         — Generate a new briefing
    GET  /api/logs                       — Structured logs
    GET  /api/system                     — System health + services
    GET  /api/decisions                  — AI decision history
"""

import logging
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

log = logging.getLogger("ai_employee.dashboard.web")

TEMPLATES_DIR = Path(__file__).parent / "templates"

# ── Module state (set by create_dashboard_app) ───────────────────────────

_memory = None
_analytics = None
_approval_manager = None
_status_aggregator = None
_system_logger = None
_health_check = None
_health_monitor = None
_planner = None
_decision_engine = None
_settings = None
_audit_agent = None


def create_dashboard_app(
    memory=None,
    analytics=None,
    approval_manager=None,
    status_aggregator=None,
    system_logger=None,
    health_check=None,
    health_monitor=None,
    planner=None,
    decision_engine=None,
    settings=None,
    audit_agent=None,
) -> FastAPI:
    """Create and configure the FastAPI dashboard application."""
    global _memory, _analytics, _approval_manager, _status_aggregator
    global _system_logger, _health_check, _health_monitor
    global _planner, _decision_engine, _settings, _audit_agent

    _memory = memory
    _analytics = analytics
    _approval_manager = approval_manager
    _status_aggregator = status_aggregator
    _system_logger = system_logger
    _health_check = health_check
    _health_monitor = health_monitor
    _planner = planner
    _decision_engine = decision_engine
    _settings = settings
    _audit_agent = audit_agent

    return app


# ── FastAPI App ──────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Employee Dashboard",
    description="Gold Tier AI Employee — Monitoring Dashboard",
    version="2.0.0",
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── Custom Jinja2 filters ───────────────────────────────────────────────

def _format_dt(value) -> str:
    if not value:
        return "N/A"
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value)
        elif isinstance(value, datetime):
            dt = value
        else:
            return str(value)
        return dt.strftime("%b %d, %H:%M:%S")
    except Exception:
        return str(value)


def _time_ago(value) -> str:
    if not value:
        return ""
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value)
        elif isinstance(value, datetime):
            dt = value
        else:
            return ""
        diff = datetime.now() - dt
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"
    except Exception:
        return ""


def _format_uptime(seconds) -> str:
    try:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m {s % 60}s"
        h = s // 3600
        m = (s % 3600) // 60
        return f"{h}h {m}m"
    except Exception:
        return str(seconds)


templates.env.filters["format_dt"] = _format_dt
templates.env.filters["time_ago"] = _time_ago
templates.env.filters["uptime"] = _format_uptime


# ── Helpers ──────────────────────────────────────────────────────────────

def _nav_context(current_page: str) -> dict:
    pending_count = 0
    if _approval_manager:
        try:
            stats = _approval_manager.get_stats()
            pending_count = stats.get("pending", 0)
        except Exception:
            pass

    health_ok = True
    health_status = "UNKNOWN"
    if _status_aggregator:
        try:
            overall = _status_aggregator.overall_health()
            health_ok = overall.value in ("healthy", "unknown")
            health_status = overall.value.upper()
        except Exception:
            pass
    elif _health_check:
        try:
            h = _health_check.run()
            health_ok = h.overall
            health_status = "HEALTHY" if h.overall else "DEGRADED"
        except Exception:
            pass

    uptime = 0
    if _health_check:
        try:
            uptime = _health_check.uptime
        except Exception:
            pass

    return {
        "current_page": current_page,
        "pending_approvals": pending_count,
        "health_ok": health_ok,
        "health_status": health_status,
        "uptime": uptime,
    }


def _safe_analytics() -> dict:
    if _analytics:
        try:
            return _analytics.compute()
        except Exception:
            pass
    return {
        "overview": {
            "total_tasks": 0, "auto_completed": 0,
            "approved": 0, "rejected": 0,
        },
        "queues": {
            "inbox": 0, "needs_action": 0,
            "done": 0, "pending_approval": 0,
        },
        "category_distribution": {},
        "priority_breakdown": {},
        "recent_tasks": [],
    }


# ── Page Routes ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    data = _safe_analytics()
    recent = data.get("recent_tasks", [])[:5]

    # Service count
    service_count = 0
    if _status_aggregator:
        try:
            service_count = len(_status_aggregator.all_services())
        except Exception:
            pass

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        **_nav_context("dashboard"),
        "overview": data.get("overview", {}),
        "queues": data.get("queues", {}),
        "recent_tasks": recent,
        "service_count": service_count,
    })


@app.get("/tasks", response_class=HTMLResponse)
async def page_tasks(request: Request):
    data = _safe_analytics()

    queue_summary = {"total": 0, "by_status": {}}
    queue_tasks = []
    if _planner:
        try:
            queue_summary = _planner.queue_summary()
            queue_tasks = [t.to_dict() for t in _planner.queue.all_tasks()]
        except Exception:
            pass

    decisions = []
    if _memory:
        try:
            decisions = _memory.get_recent_decisions(limit=20)
        except Exception:
            pass

    return templates.TemplateResponse("tasks.html", {
        "request": request,
        **_nav_context("tasks"),
        "overview": data.get("overview", {}),
        "categories": data.get("category_distribution", {}),
        "priorities": data.get("priority_breakdown", {}),
        "recent_tasks": data.get("recent_tasks", []),
        "queue_summary": queue_summary,
        "queue_tasks": queue_tasks,
        "decisions": decisions,
    })


@app.get("/approvals", response_class=HTMLResponse)
async def page_approvals(request: Request):
    stats = {}
    pending = []
    history = []

    if _approval_manager:
        try:
            stats = _approval_manager.get_stats()
            pending = _approval_manager.get_pending()
            all_req = _approval_manager.get_all()
            history = [r for r in all_req if r.get("status") != "pending"]
        except Exception:
            pass

    return templates.TemplateResponse("approvals.html", {
        "request": request,
        **_nav_context("approvals"),
        "stats": stats,
        "pending": pending,
        "history": history,
    })


@app.get("/logs", response_class=HTMLResponse)
async def page_logs(request: Request):
    logs = []
    if _system_logger:
        try:
            logs = _system_logger.query().recent(limit=200)
        except Exception:
            pass

    return templates.TemplateResponse("logs.html", {
        "request": request,
        **_nav_context("logs"),
        "logs": logs,
    })


@app.get("/system", response_class=HTMLResponse)
async def page_system(request: Request):
    health = {}
    if _health_check:
        try:
            h = _health_check.run()
            health = {
                "overall": h.overall,
                "uptime_seconds": h.uptime_seconds,
                "components": [
                    {"name": c.name, "healthy": c.healthy, "message": c.message}
                    for c in h.components
                ],
                "queue_depths": h.queue_depths,
            }
        except Exception:
            pass

    services = {}
    if _status_aggregator:
        try:
            services = _status_aggregator.summary()
        except Exception:
            pass

    monitoring = {}
    if _health_monitor:
        try:
            snap = _health_monitor.get_snapshot()
            monitoring = {
                "snapshot": snap.to_dict() if snap else None,
                "history_count": len(_health_monitor.get_history()),
                "is_running": _health_monitor.is_running,
            }
        except Exception:
            pass

    decisions = []
    if _memory:
        try:
            decisions = _memory.get_recent_decisions(limit=20)
        except Exception:
            pass

    settings_info = {}
    if _settings:
        try:
            settings_info = {
                "project_root": str(_settings.project_root),
                "dashboard_port": _settings.dashboard_port,
                "health_check_interval": _settings.health_check_interval,
                "circuit_breaker_threshold": _settings.circuit_breaker_threshold,
                "circuit_breaker_timeout": _settings.circuit_breaker_timeout,
                "ai_enabled": bool(_settings.anthropic_api_key),
            }
        except Exception:
            pass

    return templates.TemplateResponse("system.html", {
        "request": request,
        **_nav_context("system"),
        "health": health,
        "services": services,
        "monitoring": monitoring,
        "decisions": decisions,
        "settings_info": settings_info,
    })


@app.get("/briefings", response_class=HTMLResponse)
async def page_briefings(request: Request):
    briefings = []
    latest_content = ""
    briefing_dir = None

    if _settings:
        briefing_dir = _settings.briefing_dir

    if briefing_dir and briefing_dir.exists():
        md_files = sorted(briefing_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in md_files:
            stat = f.stat()
            briefings.append({
                "name": f.name,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "size": stat.st_size,
            })
        if md_files:
            latest_content = md_files[0].read_text(encoding="utf-8")

    return templates.TemplateResponse("briefings.html", {
        "request": request,
        **_nav_context("briefings"),
        "briefings": briefings,
        "latest_content": latest_content,
        "audit_enabled": _audit_agent is not None and getattr(_audit_agent, "enabled", False),
    })


# ── API Routes ───────────────────────────────────────────────────────────

@app.get("/api/tasks")
async def api_tasks():
    data = _safe_analytics()
    queue_summary = {}
    queue_tasks = []
    if _planner:
        try:
            queue_summary = _planner.queue_summary()
            queue_tasks = [t.to_dict() for t in _planner.queue.all_tasks()]
        except Exception:
            pass
    return {
        **data,
        "queue_summary": queue_summary,
        "queue_tasks": queue_tasks,
    }


@app.get("/api/approvals")
async def api_approvals():
    if not _approval_manager:
        return {"stats": {}, "pending": [], "all": []}
    try:
        return {
            "stats": _approval_manager.get_stats(),
            "pending": _approval_manager.get_pending(),
            "all": _approval_manager.get_all(),
        }
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/approvals/{request_id}/approve")
async def api_approve(request_id: str, request: Request):
    if not _approval_manager:
        return JSONResponse(
            {"error": "Approval manager not initialized"}, status_code=503,
        )
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    reason = body.get("reason", "Approved via dashboard")
    try:
        result = _approval_manager.approve(
            request_id, by="dashboard_user", reason=reason,
        )
        if result:
            return {"status": "approved", "result": result}
        return JSONResponse(
            {"error": "Request not found or already processed"},
            status_code=404,
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/approvals/{request_id}/reject")
async def api_reject(request_id: str, request: Request):
    if not _approval_manager:
        return JSONResponse(
            {"error": "Approval manager not initialized"}, status_code=503,
        )
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    reason = body.get("reason", "Rejected via dashboard")
    try:
        result = _approval_manager.reject(
            request_id, by="dashboard_user", reason=reason,
        )
        if result:
            return {"status": "rejected", "result": result}
        return JSONResponse(
            {"error": "Request not found or already processed"},
            status_code=404,
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/briefings")
async def api_briefings():
    briefings = []
    latest_content = ""
    briefing_dir = None

    if _settings:
        briefing_dir = _settings.briefing_dir

    if briefing_dir and briefing_dir.exists():
        md_files = sorted(briefing_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in md_files:
            stat = f.stat()
            briefings.append({
                "name": f.name,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "size": stat.st_size,
            })
        if md_files:
            latest_content = md_files[0].read_text(encoding="utf-8")

    return {
        "briefings": briefings,
        "latest_content": latest_content,
        "count": len(briefings),
    }


@app.post("/api/briefings/generate")
async def api_briefings_generate():
    if not _audit_agent:
        return JSONResponse(
            {"error": "Audit agent not initialized"}, status_code=503,
        )
    if not _audit_agent.enabled:
        return JSONResponse(
            {"error": "Audit agent disabled — no data sources available"},
            status_code=503,
        )
    try:
        path = _audit_agent.generate_weekly_briefing()
        return {
            "status": "success",
            "file": path.name,
            "path": str(path),
        }
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/logs")
async def api_logs(
    limit: int = Query(100, ge=1, le=500),
    level: str | None = Query(None),
    source: str | None = Query(None),
):
    if not _system_logger:
        return {"logs": [], "count": 0}
    try:
        logs = _system_logger.query().recent(
            limit=limit, level=level, source=source,
        )
        return {"logs": logs, "count": len(logs)}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/system")
async def api_system():
    result = {}
    if _health_check:
        try:
            h = _health_check.run()
            result["health"] = {
                "overall": h.overall,
                "uptime_seconds": h.uptime_seconds,
                "components": [
                    {"name": c.name, "healthy": c.healthy, "message": c.message}
                    for c in h.components
                ],
                "queue_depths": h.queue_depths,
            }
        except Exception:
            pass

    if _status_aggregator:
        try:
            result["services"] = _status_aggregator.summary()
        except Exception:
            pass

    if _health_monitor:
        try:
            snap = _health_monitor.get_snapshot()
            result["monitoring"] = {
                "snapshot": snap.to_dict() if snap else None,
                "history": _health_monitor.get_history(limit=10),
            }
        except Exception:
            pass

    return result


@app.get("/api/decisions")
async def api_decisions(limit: int = Query(20, ge=1, le=100)):
    if not _memory:
        return {"decisions": []}
    try:
        return {"decisions": _memory.get_recent_decisions(limit=limit)}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Server lifecycle ─────────────────────────────────────────────────────

class WebDashboardServer:
    """Manages the FastAPI dashboard server in a background thread."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8080, **app_kwargs):
        self._host = host
        self._port = port
        self._app = create_dashboard_app(**app_kwargs)
        self._server = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import uvicorn
        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, daemon=True, name="WebDashboard",
        )
        self._thread.start()
        log.info("Web dashboard at http://%s:%d", self._host, self._port)

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
            log.info("Web dashboard stopped")

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"
