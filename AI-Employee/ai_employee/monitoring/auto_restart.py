"""
AI Employee — Auto-Restart Manager (Platinum Tier)

Manages automatic service recovery for all restartable components:
  - Cloud Watchers (Gmail, WhatsApp, LinkedIn, Twitter, Instagram, Odoo)
  - MCP Servers (meta-social, twitter-social, odoo-accounting, communication)
  - Dashboard Server
  - Inbox Watcher

Recovery strategy:
  1. Detect failure (called by HealthMonitor)
  2. Check restart budget (max retries per service, global budget)
  3. Apply backoff (exponential: 5s → 10s → 20s → 40s)
  4. Execute restart via the service's own restart API
  5. Verify recovery
  6. Fire alert if recovery fails
  7. Log everything to AuditLogger

Cooldown:
  - After max_retries exhausted, enters cooldown (default 10 min)
  - After cooldown, retries are reset and the service can be retried
  - Manual reset available via reset_service()

Usage:
    restarter = AutoRestartManager(
        alert_system=alerts,
        audit_logger=audit,
        system_logger=syslog,
    )
    restarter.register_watcher("gmail", gmail_watcher_instance)
    restarter.register_mcp_server("meta-social", mcp_manager)
    restarter.register_service("dashboard_server", dashboard_instance)

    # Called by HealthMonitor when a failure is detected
    result = restarter.restart("gmail")
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

log = logging.getLogger("ai_employee.auto_restart")


# ── Enums ────────────────────────────────────────────────────────────────

class ServiceType(str, Enum):
    WATCHER = "watcher"
    MCP_SERVER = "mcp_server"
    DASHBOARD = "dashboard"
    INBOX_WATCHER = "inbox_watcher"
    GENERIC = "generic"


class RestartStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"         # already running
    BUDGET_EXHAUSTED = "budget_exhausted"
    COOLDOWN = "cooldown"
    UNKNOWN_SERVICE = "unknown_service"


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class RestartRecord:
    """Record of a single restart attempt."""
    service_name: str
    service_type: str
    status: str
    attempt: int
    max_retries: int
    backoff_seconds: float
    duration_ms: int = 0
    error: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RestartResult:
    """Returned by restart() to inform the caller."""
    service_name: str
    status: RestartStatus
    attempts_used: int
    attempts_remaining: int
    recovered: bool
    record: Optional[RestartRecord] = None
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class _ServiceEntry:
    """Internal registry entry for a managed service."""
    name: str
    service_type: ServiceType
    restart_fn: Callable[[], bool]      # returns True on success
    verify_fn: Callable[[], bool]       # returns True if alive
    instance: Any = None
    max_retries: int = 3
    backoff_base: float = 5.0           # seconds
    cooldown_seconds: float = 600.0     # 10 min after exhaustion

    # Tracking
    attempts: int = 0
    total_restarts: int = 0
    total_failures: int = 0
    last_restart: str = ""
    last_error: str = ""
    exhausted_at: float = 0.0           # monotonic time when budget exhausted


# ── Auto-Restart Manager ────────────────────────────────────────────────

class AutoRestartManager:
    """
    Centralized auto-restart orchestrator for all system services.

    Thread-safe.  Provides per-service restart budgets, exponential
    backoff, cooldown periods, and multi-channel alert integration.
    """

    def __init__(
        self,
        alert_system=None,
        audit_logger=None,
        system_logger=None,
        max_retries: int = 3,
        backoff_base: float = 5.0,
        cooldown_seconds: float = 600.0,
    ):
        self._alerts = alert_system
        self._audit = audit_logger
        self._syslog = system_logger
        self._default_max_retries = max_retries
        self._default_backoff = backoff_base
        self._default_cooldown = cooldown_seconds

        self._lock = threading.Lock()
        self._services: dict[str, _ServiceEntry] = {}
        self._history: list[RestartRecord] = []

    # ── Registration ─────────────────────────────────────────────────

    def register_watcher(self, name: str, watcher) -> None:
        """Register a BaseWatcher instance for auto-restart."""
        def _restart() -> bool:
            watcher.stop()
            time.sleep(1)
            watcher.start()
            return True

        def _verify() -> bool:
            return watcher.running

        self._register(name, ServiceType.WATCHER, _restart, _verify, watcher)

    def register_mcp_server(self, name: str, mcp_manager) -> None:
        """Register an MCP server (via MCPServerManager) for auto-restart."""
        def _restart() -> bool:
            return mcp_manager.restart_server(name)

        def _verify() -> bool:
            health = mcp_manager.health_check(name)
            return health.get("alive", False)

        self._register(name, ServiceType.MCP_SERVER, _restart, _verify, mcp_manager)

    def register_dashboard(self, name: str, dashboard) -> None:
        """Register the DashboardServer for auto-restart."""
        def _restart() -> bool:
            dashboard.stop()
            time.sleep(1)
            dashboard.start()
            return True

        def _verify() -> bool:
            thread = getattr(dashboard, "_thread", None)
            return thread is not None and thread.is_alive()

        self._register(name, ServiceType.DASHBOARD, _restart, _verify, dashboard)

    def register_inbox_watcher(self, name: str, watcher) -> None:
        """Register the InboxWatcher for auto-restart."""
        def _restart() -> bool:
            watcher.stop()
            time.sleep(1)
            watcher.start()
            return True

        def _verify() -> bool:
            observer = getattr(watcher, "_observer", None)
            return observer is not None and observer.is_alive()

        self._register(name, ServiceType.INBOX_WATCHER, _restart, _verify, watcher)

    def register_generic(
        self,
        name: str,
        restart_fn: Callable[[], bool],
        verify_fn: Callable[[], bool],
        instance: Any = None,
    ) -> None:
        """Register a generic restartable service."""
        self._register(name, ServiceType.GENERIC, restart_fn, verify_fn, instance)

    def _register(
        self,
        name: str,
        stype: ServiceType,
        restart_fn: Callable[[], bool],
        verify_fn: Callable[[], bool],
        instance: Any = None,
    ) -> None:
        with self._lock:
            self._services[name] = _ServiceEntry(
                name=name,
                service_type=stype,
                restart_fn=restart_fn,
                verify_fn=verify_fn,
                instance=instance,
                max_retries=self._default_max_retries,
                backoff_base=self._default_backoff,
                cooldown_seconds=self._default_cooldown,
            )
        log.info("Registered for auto-restart: %s (%s)", name, stype.value)

    # ── Core restart API ─────────────────────────────────────────────

    def restart(self, name: str) -> RestartResult:
        """
        Attempt to restart a registered service.

        Applies backoff, respects retry budget, verifies recovery.
        Returns a RestartResult with status and metadata.
        """
        with self._lock:
            entry = self._services.get(name)

        if not entry:
            return RestartResult(
                service_name=name, status=RestartStatus.UNKNOWN_SERVICE,
                attempts_used=0, attempts_remaining=0, recovered=False,
                error=f"Service '{name}' not registered",
            )

        # Check cooldown
        if entry.exhausted_at > 0:
            elapsed = time.monotonic() - entry.exhausted_at
            if elapsed < entry.cooldown_seconds:
                remaining = int(entry.cooldown_seconds - elapsed)
                log.info("Service '%s' in cooldown (%ds remaining)", name, remaining)
                return RestartResult(
                    service_name=name, status=RestartStatus.COOLDOWN,
                    attempts_used=entry.attempts, attempts_remaining=0,
                    recovered=False,
                    error=f"Cooldown: {remaining}s remaining",
                )
            else:
                # Cooldown expired — reset budget
                entry.attempts = 0
                entry.exhausted_at = 0.0
                log.info("Service '%s' cooldown expired — budget reset", name)

        # Check if already healthy
        try:
            if entry.verify_fn():
                return RestartResult(
                    service_name=name, status=RestartStatus.SKIPPED,
                    attempts_used=entry.attempts,
                    attempts_remaining=entry.max_retries - entry.attempts,
                    recovered=True,
                )
        except Exception:
            pass  # Assume unhealthy if verify fails

        # Check budget
        if entry.attempts >= entry.max_retries:
            entry.exhausted_at = time.monotonic()
            self._fire_alert(
                name, "CRITICAL",
                f"Service '{name}' restart budget exhausted ({entry.max_retries} attempts)",
                f"Entering {int(entry.cooldown_seconds)}s cooldown",
            )
            return RestartResult(
                service_name=name, status=RestartStatus.BUDGET_EXHAUSTED,
                attempts_used=entry.attempts, attempts_remaining=0,
                recovered=False,
                error=f"Max retries ({entry.max_retries}) exhausted",
            )

        # Calculate backoff
        backoff = entry.backoff_base * (2 ** entry.attempts)
        entry.attempts += 1
        attempt_num = entry.attempts

        log.warning(
            "Restarting '%s' (attempt %d/%d, backoff %.1fs)",
            name, attempt_num, entry.max_retries, backoff,
        )

        # Wait backoff
        time.sleep(backoff)

        # Execute restart
        start = time.monotonic()
        try:
            success = entry.restart_fn()
            duration_ms = int((time.monotonic() - start) * 1000)
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            success = False
            entry.last_error = str(exc)
            log.error("Restart '%s' raised: %s", name, exc)

        # Verify
        recovered = False
        if success:
            time.sleep(2)  # Give service time to stabilize
            try:
                recovered = entry.verify_fn()
            except Exception:
                recovered = False

        # Record
        record = RestartRecord(
            service_name=name,
            service_type=entry.service_type.value,
            status="success" if recovered else "failed",
            attempt=attempt_num,
            max_retries=entry.max_retries,
            backoff_seconds=backoff,
            duration_ms=duration_ms,
            error="" if recovered else (entry.last_error or "Verification failed"),
        )
        entry.last_restart = record.timestamp
        self._history.append(record)

        if recovered:
            entry.attempts = 0  # Reset on success
            entry.total_restarts += 1
            log.info("Service '%s' recovered (%dms)", name, duration_ms)
            self._log_audit("service_restarted", name, record)
            self._fire_alert(
                name, "INFO",
                f"Service '{name}' recovered after restart",
                f"Attempt {attempt_num}, took {duration_ms}ms",
            )
        else:
            entry.total_failures += 1
            log.error("Service '%s' restart failed (attempt %d/%d)",
                       name, attempt_num, entry.max_retries)
            self._fire_alert(
                name, "ERROR",
                f"Service '{name}' restart failed",
                f"Attempt {attempt_num}/{entry.max_retries}: {record.error}",
            )

        return RestartResult(
            service_name=name,
            status=RestartStatus.SUCCESS if recovered else RestartStatus.FAILED,
            attempts_used=entry.attempts,
            attempts_remaining=max(0, entry.max_retries - entry.attempts),
            recovered=recovered,
            record=record,
        )

    def restart_all_failed(self, failed_services: list[str]) -> dict[str, RestartResult]:
        """Restart a list of failed services. Returns {name: result}."""
        results = {}
        for name in failed_services:
            results[name] = self.restart(name)
        return results

    # ── Service management ───────────────────────────────────────────

    def reset_service(self, name: str) -> bool:
        """Manually reset a service's restart budget and cooldown."""
        with self._lock:
            entry = self._services.get(name)
        if not entry:
            return False
        entry.attempts = 0
        entry.exhausted_at = 0.0
        entry.last_error = ""
        log.info("Service '%s' restart budget reset", name)
        return True

    def reset_all(self) -> None:
        """Reset all services' restart budgets."""
        with self._lock:
            for entry in self._services.values():
                entry.attempts = 0
                entry.exhausted_at = 0.0
                entry.last_error = ""
        log.info("All restart budgets reset")

    # ── Queries ──────────────────────────────────────────────────────

    def get_service_status(self, name: str) -> Optional[dict]:
        """Get restart status for a specific service."""
        with self._lock:
            entry = self._services.get(name)
        if not entry:
            return None

        in_cooldown = (
            entry.exhausted_at > 0
            and (time.monotonic() - entry.exhausted_at) < entry.cooldown_seconds
        )

        return {
            "name": entry.name,
            "type": entry.service_type.value,
            "attempts": entry.attempts,
            "max_retries": entry.max_retries,
            "total_restarts": entry.total_restarts,
            "total_failures": entry.total_failures,
            "last_restart": entry.last_restart,
            "last_error": entry.last_error,
            "in_cooldown": in_cooldown,
            "cooldown_remaining": max(
                0, int(entry.cooldown_seconds - (time.monotonic() - entry.exhausted_at))
            ) if in_cooldown else 0,
        }

    def get_all_status(self) -> dict[str, dict]:
        """Get restart status for all registered services."""
        with self._lock:
            names = list(self._services.keys())
        return {name: self.get_service_status(name) for name in names}

    @property
    def history(self) -> list[dict]:
        """Return all restart records."""
        return [r.to_dict() for r in self._history]

    @property
    def recent_history(self) -> list[dict]:
        """Return last 50 restart records."""
        return [r.to_dict() for r in self._history[-50:]]

    @property
    def stats(self) -> dict:
        """Aggregated restart statistics."""
        total = len(self._history)
        successes = sum(1 for r in self._history if r.status == "success")
        failures = total - successes
        with self._lock:
            services = list(self._services.values())
        in_cooldown = sum(
            1 for e in services
            if e.exhausted_at > 0
            and (time.monotonic() - e.exhausted_at) < e.cooldown_seconds
        )
        return {
            "total_restarts": total,
            "successes": successes,
            "failures": failures,
            "success_rate": round(successes / total, 3) if total else 0.0,
            "registered_services": len(services),
            "in_cooldown": in_cooldown,
        }

    # ── Internal helpers ─────────────────────────────────────────────

    def _fire_alert(self, source: str, level: str, title: str, detail: str) -> None:
        if self._alerts:
            try:
                self._alerts.fire(
                    level=level, source=source,
                    title=title, detail=detail,
                )
            except Exception as exc:
                log.warning("Failed to fire restart alert: %s", exc)

    def _log_audit(self, event: str, source: str, record: RestartRecord) -> None:
        if self._audit:
            try:
                self._audit.log_system_event(
                    event="system_restart",
                    summary=f"Auto-restart: {source} ({record.status})",
                    metadata={
                        "service": source,
                        "attempt": record.attempt,
                        "duration_ms": record.duration_ms,
                    },
                )
            except Exception:
                pass

        if self._syslog:
            try:
                self._syslog.info(
                    "auto_restart",
                    f"Restart {record.status}: {source} (attempt {record.attempt}/{record.max_retries})",
                    {"duration_ms": record.duration_ms},
                )
            except Exception:
                pass
