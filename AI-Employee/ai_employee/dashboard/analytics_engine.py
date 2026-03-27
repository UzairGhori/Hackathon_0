"""
AI Employee — CEO Analytics Engine

Aggregates data from every subsystem into a single executive snapshot:

  - Business overview (tasks, approvals, agent activity)
  - Weekly audit reports (from AuditAgent briefings)
  - Social media metrics (from MetaAgent)
  - Accounting summary (from OdooAgent)
  - Task status (from Memory + TaskQueue + Scheduler)
  - Error / recovery health (from AuditLogger + ErrorHandler)

Every method returns a plain dict — ready for JSON serialisation or
template rendering.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("ai_employee.ceo_analytics")


class CEOAnalyticsEngine:
    """
    Single entry point for all CEO-level business intelligence.

    All constructor arguments are optional — the engine gracefully
    degrades when a subsystem is unavailable.
    """

    def __init__(
        self,
        memory=None,
        settings=None,
        approval_manager=None,
        audit_agent=None,
        meta_agent=None,
        odoo_agent=None,
        gmail_agent=None,
        planner=None,
        status_aggregator=None,
        audit_logger=None,
        error_handler=None,
        retry_manager=None,
        fallback_system=None,
    ):
        self._memory = memory
        self._settings = settings
        self._approval_mgr = approval_manager
        self._audit_agent = audit_agent
        self._meta_agent = meta_agent
        self._odoo_agent = odoo_agent
        self._gmail_agent = gmail_agent
        self._planner = planner
        self._aggregator = status_aggregator
        self._audit_logger = audit_logger
        self._error_handler = error_handler
        self._retry_manager = retry_manager
        self._fallback_system = fallback_system

    # ══════════════════════════════════════════════════════════════════
    #  FULL EXECUTIVE SNAPSHOT
    # ══════════════════════════════════════════════════════════════════

    def full_snapshot(self) -> dict:
        """Return the complete CEO dashboard payload."""
        return {
            "generated_at": datetime.now().isoformat(),
            "business_overview": self.business_overview(),
            "task_status": self.task_status(),
            "social_media": self.social_media_metrics(),
            "accounting": self.accounting_summary(),
            "weekly_report": self.weekly_report(),
            "approvals": self.approval_summary(),
            "agent_performance": self.agent_performance(),
            "error_health": self.error_health(),
            "audit_trail": self.audit_trail_summary(),
        }

    # ══════════════════════════════════════════════════════════════════
    #  1. BUSINESS OVERVIEW
    # ══════════════════════════════════════════════════════════════════

    def business_overview(self) -> dict:
        """High-level KPIs for the entire system."""
        tasks = self._safe(lambda: self._memory.stats, {})
        queues = self._queue_depths()
        approvals = self._safe(
            lambda: self._approval_mgr.get_stats(), {},
        )
        agents = self._agent_statuses()

        total_tasks = tasks.get("total_tasks", 0)
        auto_completed = tasks.get("auto_completed", 0)
        automation_rate = (
            round(auto_completed / total_tasks * 100, 1)
            if total_tasks > 0 else 0.0
        )

        return {
            "total_tasks": total_tasks,
            "auto_completed": auto_completed,
            "approved": tasks.get("approved", 0),
            "rejected": tasks.get("rejected", 0),
            "automation_rate": automation_rate,
            "pending_approvals": approvals.get("pending", 0),
            "queues": queues,
            "active_agents": sum(1 for a in agents if a["enabled"]),
            "total_agents": len(agents),
            "agents": agents,
        }

    # ══════════════════════════════════════════════════════════════════
    #  2. TASK STATUS
    # ══════════════════════════════════════════════════════════════════

    def task_status(self) -> dict:
        """Task queue state, recent history, and category/priority breakdown."""
        recent = self._safe(lambda: self._memory.recent_tasks, [])

        queue_summary = {"total": 0, "by_status": {}}
        queue_tasks = []
        if self._planner:
            queue_summary = self._safe(
                lambda: self._planner.queue_summary(), queue_summary,
            )
            queue_tasks = self._safe(
                lambda: [t.to_dict() for t in self._planner.queue.all_tasks()], [],
            )

        categories = dict(Counter(t.get("category", "Unknown") for t in recent))
        priorities = dict(Counter(t.get("priority", "Unknown") for t in recent))

        return {
            "recent_tasks": recent[-10:],
            "queue_summary": queue_summary,
            "queue_tasks": queue_tasks[:20],
            "category_distribution": categories,
            "priority_breakdown": priorities,
            "total_in_queue": queue_summary.get("total", 0),
            "pending_in_queue": queue_summary.get("by_status", {}).get("pending", 0),
        }

    # ══════════════════════════════════════════════════════════════════
    #  3. SOCIAL MEDIA METRICS
    # ══════════════════════════════════════════════════════════════════

    def social_media_metrics(self) -> dict:
        """Fetch live metrics from the Meta agent."""
        if not self._meta_agent or not getattr(self._meta_agent, "enabled", False):
            return {"available": False, "reason": "Meta agent not configured"}

        metrics = self._safe(lambda: self._meta_agent.get_metrics(), {})
        weekly = self._safe(lambda: self._meta_agent.get_weekly_summary(), {})

        fb = metrics.get("facebook", {})
        ig = metrics.get("instagram", {})
        combined = weekly.get("combined", {})

        return {
            "available": True,
            "facebook": {
                "posts": len(fb.get("recent_posts", [])),
                "engagements": fb.get("total_engagements", 0),
                "likes": fb.get("total_likes", 0),
                "comments": fb.get("total_comments", 0),
                "shares": fb.get("total_shares", 0),
            },
            "instagram": {
                "posts": len(ig.get("recent_media", [])),
                "engagements": ig.get("total_engagements", 0),
                "likes": ig.get("total_likes", 0),
                "comments": ig.get("total_comments", 0),
            },
            "weekly_summary": {
                "total_posts": combined.get("total_posts", 0),
                "total_engagements": combined.get("total_engagements", 0),
                "period": weekly.get("period", {}),
            },
        }

    # ══════════════════════════════════════════════════════════════════
    #  4. ACCOUNTING SUMMARY
    # ══════════════════════════════════════════════════════════════════

    def accounting_summary(self) -> dict:
        """Fetch financial summary from the Odoo agent."""
        if not self._odoo_agent or not getattr(self._odoo_agent, "enabled", False):
            return {"available": False, "reason": "Odoo agent not configured"}

        summary = self._safe(lambda: self._odoo_agent.get_financial_summary(), {})
        overdue = self._safe(lambda: self._odoo_agent.check_overdue_invoices(), [])

        pl = summary.get("profit_loss", {})
        bs = summary.get("balance_sheet", {})

        return {
            "available": True,
            "profit_loss": {
                "income": pl.get("income_total", 0),
                "expenses": pl.get("expense_total", 0),
                "net_profit": pl.get("net", 0),
            },
            "balance_sheet": {
                "assets": bs.get("assets", 0),
                "liabilities": bs.get("liabilities", 0),
                "equity": bs.get("equity", 0),
            },
            "overdue_invoices": {
                "count": len(overdue),
                "total_amount": sum(inv.get("amount_due", 0) for inv in overdue),
                "items": overdue[:5],
            },
            "generated_at": summary.get("generated_at", ""),
        }

    # ══════════════════════════════════════════════════════════════════
    #  5. WEEKLY AUDIT REPORT
    # ══════════════════════════════════════════════════════════════════

    def weekly_report(self) -> dict:
        """Load the most recent weekly briefing from disk."""
        if not self._settings:
            return {"available": False, "reason": "Settings not loaded"}

        briefing_dir = self._settings.briefing_dir
        if not briefing_dir or not briefing_dir.exists():
            return {"available": False, "reason": "No briefings directory"}

        md_files = sorted(
            briefing_dir.glob("*.md"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        if not md_files:
            return {"available": False, "reason": "No briefings generated yet"}

        latest = md_files[0]
        stat = latest.stat()

        reports = []
        for f in md_files[:10]:
            s = f.stat()
            reports.append({
                "name": f.name,
                "modified": datetime.fromtimestamp(s.st_mtime).isoformat(),
                "size_kb": round(s.st_size / 1024, 1),
            })

        content = ""
        try:
            content = latest.read_text(encoding="utf-8")
        except Exception:
            pass

        return {
            "available": True,
            "latest_file": latest.name,
            "latest_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "content": content,
            "total_reports": len(md_files),
            "reports": reports,
            "audit_enabled": (
                self._audit_agent is not None
                and getattr(self._audit_agent, "enabled", False)
            ),
        }

    # ══════════════════════════════════════════════════════════════════
    #  6. APPROVAL SUMMARY
    # ══════════════════════════════════════════════════════════════════

    def approval_summary(self) -> dict:
        if not self._approval_mgr:
            return {"available": False}

        stats = self._safe(lambda: self._approval_mgr.get_stats(), {})
        pending = self._safe(lambda: self._approval_mgr.get_pending(), [])

        return {
            "available": True,
            "pending_count": stats.get("pending", 0),
            "approved_count": stats.get("approved", 0),
            "rejected_count": stats.get("rejected", 0),
            "expired_count": stats.get("expired", 0),
            "total": stats.get("total", 0),
            "pending_items": pending[:5],
        }

    # ══════════════════════════════════════════════════════════════════
    #  7. AGENT PERFORMANCE
    # ══════════════════════════════════════════════════════════════════

    def agent_performance(self) -> dict:
        """Per-agent health and success rates from the StatusAggregator."""
        if not self._aggregator:
            return {"available": False}

        summary = self._safe(lambda: self._aggregator.summary(), {})
        services = summary.get("services", [])

        agents = [s for s in services if s.get("type") == "agent"]
        healthy = sum(1 for a in agents if a.get("health") == "healthy")

        return {
            "available": True,
            "overall_health": summary.get("overall_health", "unknown"),
            "agents": agents,
            "healthy_count": healthy,
            "total_count": len(agents),
        }

    # ══════════════════════════════════════════════════════════════════
    #  8. ERROR HEALTH
    # ══════════════════════════════════════════════════════════════════

    def error_health(self) -> dict:
        """Error/retry/fallback statistics."""
        err = self._safe(
            lambda: self._error_handler.stats, {},
        ) if self._error_handler else {}

        retry = self._safe(
            lambda: self._retry_manager.stats, {},
        ) if self._retry_manager else {}

        fb = self._safe(
            lambda: self._fallback_system.stats, {},
        ) if self._fallback_system else {}

        return {
            "errors": err,
            "retries": retry,
            "fallbacks": fb,
        }

    # ══════════════════════════════════════════════════════════════════
    #  9. AUDIT TRAIL SUMMARY
    # ══════════════════════════════════════════════════════════════════

    def audit_trail_summary(self) -> dict:
        """Summary from the enterprise audit logger."""
        if not self._audit_logger:
            return {"available": False}

        stats = self._safe(lambda: self._audit_logger.stats, {})
        recent = self._safe(lambda: self._audit_logger.recent(20), [])
        errors = self._safe(lambda: self._audit_logger.query_errors(10), [])

        return {
            "available": True,
            "total_entries": stats.get("total_entries", 0),
            "error_count": stats.get("error_count", 0),
            "by_event": stats.get("by_event", {}),
            "by_source": stats.get("by_source", {}),
            "recent": recent,
            "recent_errors": errors,
        }

    # ══════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════

    def _queue_depths(self) -> dict[str, int]:
        if not self._settings:
            return {}

        def _count(path: Path) -> int:
            try:
                return sum(1 for f in path.iterdir() if f.suffix == ".md") if path.exists() else 0
            except Exception:
                return 0

        return {
            "inbox": _count(self._settings.inbox_dir),
            "needs_action": _count(self._settings.needs_action_dir),
            "done": _count(self._settings.done_dir),
            "pending_approval": _count(self._settings.approval_dir),
        }

    def _agent_statuses(self) -> list[dict]:
        """Return name + enabled for every known agent."""
        if not self._aggregator:
            return []

        result = []
        for name, svc in self._aggregator.all_services().items():
            if svc.service_type != "agent":
                continue
            result.append({
                "name": name,
                "enabled": svc.enabled,
                "health": svc.health.value,
                "success_rate": round(svc.metrics.success_rate * 100, 1),
                "total_calls": svc.metrics.total_calls,
                "failed_calls": svc.metrics.failed_calls,
                "circuit_state": svc.breaker.state.value,
            })
        return result

    @staticmethod
    def _safe(fn, default=None):
        """Call fn() and swallow exceptions, returning default."""
        try:
            return fn()
        except Exception as exc:
            log.debug("Analytics safe-call failed: %s", exc)
            return default
