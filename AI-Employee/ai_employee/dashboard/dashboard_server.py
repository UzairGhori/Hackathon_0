"""
AI Employee — Dashboard Server

Lightweight HTTP dashboard for monitoring the AI Employee system.
Built on Python's http.server — no external web framework needed.

Endpoints:
    GET /                   — HTML dashboard with live stats
    GET /api/health         — JSON health check
    GET /api/stats          — JSON analytics data
    GET /api/service-status — JSON service status from StatusAggregator
    GET /api/logs           — JSON recent structured logs
"""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

from ai_employee.dashboard.analytics import AnalyticsEngine
from ai_employee.dashboard.approval_api import ApprovalAPIHandler, set_approval_manager
from ai_employee.monitoring.health_check import HealthCheck

log = logging.getLogger("ai_employee.dashboard")

# Module-level references set by DashboardServer.start()
_analytics: AnalyticsEngine | None = None
_health_check: HealthCheck | None = None
_status_aggregator = None  # StatusAggregator
_system_logger = None       # SystemLogger


class _DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the dashboard."""

    def do_GET(self):
        if ApprovalAPIHandler.can_handle(self.path):
            ApprovalAPIHandler.handle_get(self, self.path)
        elif self.path == "/":
            self._serve_dashboard()
        elif self.path == "/api/health":
            self._serve_json(self._get_health())
        elif self.path == "/api/stats":
            self._serve_json(self._get_stats())
        elif self.path == "/api/service-status":
            self._serve_json(self._get_service_status())
        elif self.path == "/api/logs":
            self._serve_json(self._get_logs())
        else:
            self.send_error(404)

    def do_POST(self):
        if ApprovalAPIHandler.can_handle(self.path):
            ApprovalAPIHandler.handle_post(self, self.path)
        else:
            self.send_error(404)

    def _serve_dashboard(self):
        health = self._get_health()
        stats = self._get_stats()

        status_color = "#22c55e" if health.get("overall") else "#ef4444"
        status_text = "HEALTHY" if health.get("overall") else "DEGRADED"

        queues = stats.get("queues", {})
        overview = stats.get("overview", {})

        components_html = ""
        for c in health.get("components", []):
            icon = "&#9989;" if c["healthy"] else "&#10060;"
            components_html += f"<tr><td>{icon}</td><td>{c['name']}</td><td>{c['message']}</td></tr>"

        recent_html = ""
        for t in stats.get("recent_tasks", [])[-10:]:
            recent_html += (
                f"<tr><td>{t.get('title', 'N/A')}</td>"
                f"<td>{t.get('category', 'N/A')}</td>"
                f"<td>{t.get('priority', 'N/A')}</td>"
                f"<td>{t.get('status', 'N/A')}</td></tr>"
            )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="30">
    <title>AI Employee — Gold Tier Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0f172a; color: #e2e8f0; padding: 2rem; }}
        .header {{ text-align: center; margin-bottom: 2rem; }}
        .header h1 {{ font-size: 2rem; color: #f59e0b; }}
        .header .tier {{ color: #fbbf24; font-size: 0.9rem; letter-spacing: 2px; }}
        .status {{ display: inline-block; padding: 0.3rem 1rem; border-radius: 999px;
                   background: {status_color}; color: white; font-weight: bold; margin: 0.5rem 0; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin: 1.5rem 0; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 1.5rem; text-align: center; }}
        .card .number {{ font-size: 2.5rem; font-weight: bold; color: #f59e0b; }}
        .card .label {{ color: #94a3b8; font-size: 0.85rem; margin-top: 0.3rem; }}
        table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
        th, td {{ padding: 0.75rem 1rem; text-align: left; border-bottom: 1px solid #334155; }}
        th {{ background: #1e293b; color: #f59e0b; }}
        .section {{ margin: 2rem 0; }}
        .section h2 {{ color: #f59e0b; margin-bottom: 1rem; font-size: 1.3rem; }}
        .footer {{ text-align: center; color: #64748b; margin-top: 3rem; font-size: 0.8rem; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>AI Employee</h1>
        <div class="tier">GOLD TIER — AUTONOMOUS SYSTEM</div>
        <div class="status">{status_text}</div>
        <div style="color: #64748b; margin-top: 0.5rem;">
            Uptime: {health.get('uptime_seconds', 0):.0f}s |
            Last refresh: {datetime.now().strftime('%H:%M:%S')}
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <div class="number">{overview.get('total_tasks', 0)}</div>
            <div class="label">Total Tasks</div>
        </div>
        <div class="card">
            <div class="number">{overview.get('auto_completed', 0)}</div>
            <div class="label">Auto-Completed</div>
        </div>
        <div class="card">
            <div class="number">{overview.get('approved', 0)}</div>
            <div class="label">Approved</div>
        </div>
        <div class="card">
            <div class="number">{overview.get('rejected', 0)}</div>
            <div class="label">Rejected</div>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <div class="number">{queues.get('inbox', 0)}</div>
            <div class="label">Inbox</div>
        </div>
        <div class="card">
            <div class="number">{queues.get('needs_action', 0)}</div>
            <div class="label">Needs Action</div>
        </div>
        <div class="card">
            <div class="number">{queues.get('done', 0)}</div>
            <div class="label">Done</div>
        </div>
        <div class="card">
            <div class="number">{queues.get('pending_approval', 0)}</div>
            <div class="label">Pending Approval</div>
        </div>
    </div>

    <div class="section">
        <h2>System Components</h2>
        <table>
            <tr><th></th><th>Component</th><th>Status</th></tr>
            {components_html}
        </table>
    </div>

    <div class="section">
        <h2>Recent Tasks</h2>
        <table>
            <tr><th>Title</th><th>Category</th><th>Priority</th><th>Status</th></tr>
            {recent_html if recent_html else "<tr><td colspan='4' style='color:#64748b;'>No tasks processed yet</td></tr>"}
        </table>
    </div>

    <div style="text-align:center;margin:2rem 0;">
        <a href="/approvals" style="display:inline-block;background:#f59e0b;color:#0f172a;
           padding:0.8rem 2rem;border-radius:8px;font-weight:bold;text-decoration:none;
           font-size:1.1rem;">
            Open Approval Dashboard &rarr;
        </a>
    </div>

    <div class="footer">
        AI Employee Gold Tier &mdash; Agent Factory Hackathon &mdash; Auto-refreshes every 30s
    </div>
</body>
</html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _serve_json(self, data: dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2, default=str).encode("utf-8"))

    @staticmethod
    def _get_health() -> dict:
        if _health_check is None:
            return {"overall": False, "error": "Health check not initialized"}
        h = _health_check.run()
        return {
            "overall": h.overall,
            "uptime_seconds": h.uptime_seconds,
            "components": [
                {"name": c.name, "healthy": c.healthy, "message": c.message}
                for c in h.components
            ],
            "queue_depths": h.queue_depths,
            "timestamp": h.timestamp,
        }

    @staticmethod
    def _get_stats() -> dict:
        if _analytics is None:
            return {"error": "Analytics not initialized"}
        return _analytics.compute()

    @staticmethod
    def _get_service_status() -> dict:
        if _status_aggregator is None:
            return {"error": "Status aggregator not initialized"}
        return _status_aggregator.summary()

    @staticmethod
    def _get_logs() -> list:
        if _system_logger is None:
            return []
        return _system_logger.query().recent(limit=100)

    def log_message(self, format, *args):
        """Suppress default HTTP logs, use our logger instead."""
        log.debug("Dashboard: %s", format % args)


class DashboardServer:
    """Manages the dashboard HTTP server lifecycle."""

    def __init__(self, analytics: AnalyticsEngine, health_check: HealthCheck,
                 host: str = "127.0.0.1", port: int = 8080,
                 approval_manager=None,
                 status_aggregator=None,
                 system_logger=None):
        self._analytics = analytics
        self._health_check = health_check
        self._host = host
        self._port = port
        self._approval_manager = approval_manager
        self._status_aggregator = status_aggregator
        self._system_logger = system_logger
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the dashboard server in a background thread."""
        global _analytics, _health_check, _status_aggregator, _system_logger
        _analytics = self._analytics
        _health_check = self._health_check
        _status_aggregator = self._status_aggregator
        _system_logger = self._system_logger

        if self._approval_manager:
            set_approval_manager(self._approval_manager)

        self._server = HTTPServer((self._host, self._port), _DashboardHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info("Dashboard running at http://%s:%d", self._host, self._port)

    def stop(self) -> None:
        """Shut down the dashboard server."""
        if self._server:
            self._server.shutdown()
            log.info("Dashboard stopped")

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"
