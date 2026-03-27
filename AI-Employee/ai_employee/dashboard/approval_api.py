"""
AI Employee — Approval REST API

HTTP request handler for approval operations via the dashboard.
Built on Python's http.server — no external framework needed.

Endpoints:
    GET  /api/approvals           — List all pending approval requests
    GET  /api/approvals/all       — List all requests (any status)
    GET  /api/approvals/stats     — Approval statistics
    GET  /api/approvals/{id}      — Get full details of a request
    POST /api/approvals/{id}/approve  — Approve a request
    POST /api/approvals/{id}/reject   — Reject a request

Dashboard page:
    GET  /approvals               — Interactive approval dashboard (HTML)

All responses are JSON except the HTML dashboard page.
"""

import json
import logging
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler

log = logging.getLogger("ai_employee.approval_api")

# Module-level reference set by DashboardServer
_approval_manager = None


def set_approval_manager(manager) -> None:
    """Set the approval manager instance (called by DashboardServer.start)."""
    global _approval_manager
    _approval_manager = manager


class ApprovalAPIHandler:
    """
    Mixin handler for approval API endpoints.
    Designed to be called from the main dashboard request handler.
    """

    @staticmethod
    def can_handle(path: str) -> bool:
        """Check if this path belongs to the approval API."""
        return path.startswith("/api/approvals") or path == "/approvals"

    @staticmethod
    def handle_get(handler: BaseHTTPRequestHandler, path: str) -> None:
        """Route GET requests to the appropriate handler."""
        if _approval_manager is None:
            _send_json(handler, 503, {"error": "Approval manager not initialized"})
            return

        if path == "/approvals":
            _serve_approval_dashboard(handler)
        elif path == "/api/approvals" or path == "/api/approvals/":
            _handle_list_pending(handler)
        elif path == "/api/approvals/all":
            _handle_list_all(handler)
        elif path == "/api/approvals/stats":
            _handle_stats(handler)
        elif path.startswith("/api/approvals/"):
            # Extract request ID from path
            parts = path.rstrip("/").split("/")
            if len(parts) == 4:
                _handle_get_request(handler, parts[3])
            else:
                _send_json(handler, 404, {"error": "Not found"})
        else:
            _send_json(handler, 404, {"error": "Not found"})

    @staticmethod
    def handle_post(handler: BaseHTTPRequestHandler, path: str) -> None:
        """Route POST requests to the appropriate handler."""
        if _approval_manager is None:
            _send_json(handler, 503, {"error": "Approval manager not initialized"})
            return

        # Parse path: /api/approvals/{id}/approve or /api/approvals/{id}/reject
        parts = path.rstrip("/").split("/")
        if len(parts) == 5 and parts[4] in ("approve", "reject"):
            request_id = parts[3]
            action = parts[4]

            # Read POST body for optional reason
            content_length = int(handler.headers.get("Content-Length", 0))
            body = {}
            if content_length > 0:
                raw = handler.rfile.read(content_length)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    body = {}

            by = body.get("by", "manager (dashboard)")
            reason = body.get("reason", "")

            if action == "approve":
                _handle_approve(handler, request_id, by, reason)
            else:
                _handle_reject(handler, request_id, by, reason)
        else:
            _send_json(handler, 404, {"error": "Not found"})


# ── API endpoint handlers ────────────────────────────────────────────────

def _handle_list_pending(handler: BaseHTTPRequestHandler) -> None:
    """GET /api/approvals — list pending requests."""
    pending = _approval_manager.get_pending()
    _send_json(handler, 200, {
        "count": len(pending),
        "requests": pending,
    })


def _handle_list_all(handler: BaseHTTPRequestHandler) -> None:
    """GET /api/approvals/all — list all requests."""
    all_requests = _approval_manager.get_all()
    _send_json(handler, 200, {
        "count": len(all_requests),
        "requests": all_requests,
    })


def _handle_stats(handler: BaseHTTPRequestHandler) -> None:
    """GET /api/approvals/stats — approval statistics."""
    stats = _approval_manager.get_stats()
    _send_json(handler, 200, stats)


def _handle_get_request(handler: BaseHTTPRequestHandler,
                        request_id: str) -> None:
    """GET /api/approvals/{id} — get full request details."""
    req = _approval_manager.get_request(request_id)
    if req:
        _send_json(handler, 200, req)
    else:
        _send_json(handler, 404, {"error": f"Request '{request_id}' not found"})


def _handle_approve(handler: BaseHTTPRequestHandler,
                    request_id: str, by: str, reason: str) -> None:
    """POST /api/approvals/{id}/approve — approve a request."""
    result = _approval_manager.approve(request_id, by, reason)
    status_code = 200 if result.get("status") != "error" else 404
    _send_json(handler, status_code, result)


def _handle_reject(handler: BaseHTTPRequestHandler,
                   request_id: str, by: str, reason: str) -> None:
    """POST /api/approvals/{id}/reject — reject a request."""
    result = _approval_manager.reject(request_id, by, reason)
    status_code = 200 if result.get("status") != "error" else 404
    _send_json(handler, status_code, result)


# ── JSON response helper ─────────────────────────────────────────────────

def _send_json(handler: BaseHTTPRequestHandler, status: int,
               data: dict) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(
        json.dumps(data, indent=2, default=str).encode("utf-8")
    )


# ── Approval Dashboard HTML ──────────────────────────────────────────────

def _serve_approval_dashboard(handler: BaseHTTPRequestHandler) -> None:
    """Serve the interactive approval dashboard page."""
    stats = _approval_manager.get_stats()
    pending = _approval_manager.get_pending()

    # Build pending requests HTML
    rows_html = ""
    for req in pending:
        flags = req.get("safety_flags", [])
        flags_str = ", ".join(flags[:3]) if flags else "None"
        category = req.get("category", "general").upper()
        priority = req.get("priority", "MEDIUM")

        priority_color = {
            "CRITICAL": "#ef4444", "HIGH": "#f97316",
            "MEDIUM": "#eab308", "LOW": "#22c55e",
        }.get(priority, "#94a3b8")

        rid = req.get("request_id", "")
        title = req.get("title", "Untitled")
        desc = req.get("description", "")[:80]
        created = req.get("created_at", "")[:16]
        expires = req.get("expires_at", "")[:16] if req.get("expires_at") else "Never"

        rows_html += f"""
        <tr>
            <td><code>{rid[:12]}</code></td>
            <td><strong>{title}</strong><br><small style="color:#94a3b8">{desc}</small></td>
            <td>{category}</td>
            <td style="color:{priority_color};font-weight:bold">{priority}</td>
            <td>{req.get('source', '')}</td>
            <td><small>{flags_str}</small></td>
            <td><small>{created}</small></td>
            <td><small>{expires}</small></td>
            <td>
                <button onclick="decide('{rid}','approve')"
                    style="background:#22c55e;color:white;border:none;padding:6px 16px;
                    border-radius:6px;cursor:pointer;font-weight:bold;margin:2px">
                    APPROVE
                </button>
                <button onclick="decide('{rid}','reject')"
                    style="background:#ef4444;color:white;border:none;padding:6px 16px;
                    border-radius:6px;cursor:pointer;font-weight:bold;margin:2px">
                    REJECT
                </button>
            </td>
        </tr>"""

    if not rows_html:
        rows_html = """<tr><td colspan="9" style="text-align:center;color:#64748b;padding:2rem">
            No pending approvals. All clear!</td></tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="15">
    <title>AI Employee — Approval Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0f172a; color: #e2e8f0; padding: 2rem; }}
        .header {{ text-align: center; margin-bottom: 2rem; }}
        .header h1 {{ font-size: 2rem; color: #f59e0b; }}
        .header .sub {{ color: #94a3b8; font-size: 0.9rem; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1rem; margin: 1.5rem 0; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 1.2rem; text-align: center; }}
        .card .number {{ font-size: 2.2rem; font-weight: bold; color: #f59e0b; }}
        .card .label {{ color: #94a3b8; font-size: 0.8rem; margin-top: 0.3rem; }}
        table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
        th, td {{ padding: 0.6rem 0.8rem; text-align: left; border-bottom: 1px solid #334155; font-size: 0.9rem; }}
        th {{ background: #1e293b; color: #f59e0b; position: sticky; top: 0; }}
        .section {{ margin: 2rem 0; }}
        .section h2 {{ color: #f59e0b; margin-bottom: 1rem; }}
        .nav {{ text-align: center; margin-bottom: 1rem; }}
        .nav a {{ color: #f59e0b; text-decoration: none; margin: 0 1rem; }}
        .nav a:hover {{ text-decoration: underline; }}
        .toast {{ display: none; position: fixed; top: 1rem; right: 1rem; padding: 1rem 2rem;
                  border-radius: 8px; color: white; font-weight: bold; z-index: 1000; }}
        .footer {{ text-align: center; color: #64748b; margin-top: 2rem; font-size: 0.8rem; }}
    </style>
</head>
<body>
    <div id="toast" class="toast"></div>

    <div class="header">
        <h1>Approval Dashboard</h1>
        <div class="sub">AI Employee Gold Tier — Manager Approval System</div>
    </div>

    <div class="nav">
        <a href="/">Main Dashboard</a>
        <a href="/approvals">Approvals</a>
        <a href="/api/approvals/stats">API: Stats</a>
        <a href="/api/approvals">API: Pending</a>
    </div>

    <div class="grid">
        <div class="card">
            <div class="number">{stats.get('pending', 0)}</div>
            <div class="label">Pending</div>
        </div>
        <div class="card">
            <div class="number">{stats.get('approved', 0)}</div>
            <div class="label">Approved</div>
        </div>
        <div class="card">
            <div class="number">{stats.get('rejected', 0)}</div>
            <div class="label">Rejected</div>
        </div>
        <div class="card">
            <div class="number">{stats.get('expired', 0)}</div>
            <div class="label">Expired</div>
        </div>
        <div class="card">
            <div class="number">{stats.get('total', 0)}</div>
            <div class="label">Total</div>
        </div>
    </div>

    <div class="section">
        <h2>Pending Approvals ({stats.get('pending', 0)})</h2>
        <table>
            <tr>
                <th>ID</th><th>Title</th><th>Category</th><th>Priority</th>
                <th>Source</th><th>Flags</th><th>Created</th><th>Expires</th><th>Action</th>
            </tr>
            {rows_html}
        </table>
    </div>

    <div class="footer">
        AI Employee Gold Tier — Approval Dashboard — Auto-refreshes every 15s
    </div>

    <script>
    async function decide(requestId, action) {{
        const reason = prompt(
            action === 'approve'
                ? 'Approve this action? Enter optional reason:'
                : 'Reject this action? Enter reason:',
            ''
        );
        if (reason === null) return;

        try {{
            const resp = await fetch('/api/approvals/' + requestId + '/' + action, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ by: 'manager (dashboard)', reason: reason }})
            }});
            const data = await resp.json();
            showToast(
                action === 'approve' ? 'Approved!' : 'Rejected!',
                action === 'approve' ? '#22c55e' : '#ef4444'
            );
            setTimeout(() => location.reload(), 1000);
        }} catch(e) {{
            showToast('Error: ' + e.message, '#ef4444');
        }}
    }}

    function showToast(msg, color) {{
        const t = document.getElementById('toast');
        t.textContent = msg;
        t.style.background = color;
        t.style.display = 'block';
        setTimeout(() => {{ t.style.display = 'none'; }}, 3000);
    }}
    </script>
</body>
</html>"""

    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(html.encode("utf-8"))
