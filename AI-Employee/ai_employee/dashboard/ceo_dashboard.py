"""
AI Employee — CEO Executive Dashboard

Registers CEO-specific routes on the existing FastAPI app:

    GET /ceo              — CEO executive dashboard page
    GET /api/ceo/snapshot — Full executive snapshot JSON

Usage:
    from ai_employee.dashboard.ceo_dashboard import register_ceo_routes
    register_ceo_routes(app, ceo_engine)
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ai_employee.dashboard.analytics_engine import CEOAnalyticsEngine

log = logging.getLogger("ai_employee.dashboard.ceo")

# Module-level state
_ceo_engine: CEOAnalyticsEngine | None = None


def register_ceo_routes(app: FastAPI, ceo_engine: CEOAnalyticsEngine) -> None:
    """Register CEO dashboard routes on the existing FastAPI app."""
    global _ceo_engine
    _ceo_engine = ceo_engine

    from ai_employee.dashboard.web_app import templates, _nav_context

    @app.get("/ceo", response_class=HTMLResponse)
    async def page_ceo(request: Request):
        snapshot = {}
        if _ceo_engine:
            try:
                snapshot = _ceo_engine.full_snapshot()
            except Exception as exc:
                log.error("CEO snapshot failed: %s", exc)

        return templates.TemplateResponse("ceo.html", {
            "request": request,
            **_nav_context("ceo"),
            "snapshot": snapshot,
            "overview": snapshot.get("business_overview", {}),
            "tasks": snapshot.get("task_status", {}),
            "social": snapshot.get("social_media", {}),
            "accounting": snapshot.get("accounting", {}),
            "report": snapshot.get("weekly_report", {}),
            "approvals": snapshot.get("approvals", {}),
            "agents": snapshot.get("agent_performance", {}),
            "errors": snapshot.get("error_health", {}),
            "audit": snapshot.get("audit_trail", {}),
            "generated_at": snapshot.get("generated_at", ""),
        })

    @app.get("/api/ceo/snapshot")
    async def api_ceo_snapshot():
        if not _ceo_engine:
            return JSONResponse(
                {"error": "CEO analytics engine not initialized"},
                status_code=503,
            )
        try:
            return _ceo_engine.full_snapshot()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/ceo/overview")
    async def api_ceo_overview():
        if not _ceo_engine:
            return JSONResponse(
                {"error": "CEO analytics engine not initialized"},
                status_code=503,
            )
        try:
            return _ceo_engine.business_overview()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/ceo/social")
    async def api_ceo_social():
        if not _ceo_engine:
            return JSONResponse(
                {"error": "CEO analytics engine not initialized"},
                status_code=503,
            )
        try:
            return _ceo_engine.social_media_metrics()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/ceo/accounting")
    async def api_ceo_accounting():
        if not _ceo_engine:
            return JSONResponse(
                {"error": "CEO analytics engine not initialized"},
                status_code=503,
            )
        try:
            return _ceo_engine.accounting_summary()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    log.info("CEO dashboard routes registered at /ceo")
