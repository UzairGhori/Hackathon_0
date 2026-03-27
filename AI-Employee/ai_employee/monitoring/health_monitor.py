"""
AI Employee — System Health Monitor (Platinum Tier)

Comprehensive background daemon that continuously monitors all system
components and triggers auto-restart + alerts on failure.

Monitored subsystems:
  1. WATCHERS    — Gmail, WhatsApp, LinkedIn, Twitter, Instagram, Odoo
  2. MCP SERVERS — meta-social, twitter-social, odoo-accounting, communication
  3. ODOO        — JSON-RPC connectivity and response time
  4. DISK SPACE  — vault, logs, project root free-space thresholds
  5. INTERNET    — outbound HTTPS connectivity to key API endpoints
  6. API LIMITS  — per-service call counts approaching rate limits

On failure:
  - AutoRestartManager restarts the service (backoff, budget, cooldown)
  - AlertSystem fires multi-channel alerts (memory, file, vault, email)
  - SystemLogger + AuditLogger record structured logs

Features:
  - Configurable check interval (default 60s)
  - Snapshot history (last 200 checks) for trend analysis
  - Dashboard API: get_snapshot(), get_history(), get_full_report()
  - Health endpoint data for /api/system
  - Per-probe timing and success tracking
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ai_employee.monitoring.service_status import (
    HealthState, StatusAggregator, ServiceStatus,
)
from ai_employee.monitoring.system_logs import SystemLogger

log = logging.getLogger("ai_employee.monitoring.health")


# ── Thresholds ───────────────────────────────────────────────────────────

DISK_WARN_MB = 500       # Warn below 500 MB free
DISK_CRIT_MB = 100       # Critical below 100 MB free
INTERNET_TIMEOUT = 10    # seconds for connectivity probe
ODOO_TIMEOUT = 15        # seconds for Odoo health probe

# API rate limit thresholds (% of max before warning)
API_LIMIT_WARN_PCT = 80
API_LIMIT_CRIT_PCT = 95


# ── Probe result ─────────────────────────────────────────────────────────

@dataclass
class ProbeResult:
    """Result of a single health probe."""
    name: str
    category: str           # "watcher", "mcp_server", "odoo", "disk", "internet", "api_limit"
    healthy: bool
    message: str = ""
    duration_ms: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "healthy": self.healthy,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


# ── Snapshot ─────────────────────────────────────────────────────────────

@dataclass
class MonitoringSnapshot:
    """Point-in-time capture of full system health."""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    overall_healthy: bool = True
    probes: list[ProbeResult] = field(default_factory=list)
    system_metrics: dict[str, Any] = field(default_factory=dict)
    failures_detected: list[str] = field(default_factory=list)
    restarts_attempted: list[str] = field(default_factory=list)
    restart_results: dict[str, dict] = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "overall_healthy": self.overall_healthy,
            "probes": [p.to_dict() for p in self.probes],
            "system_metrics": self.system_metrics,
            "failures_detected": self.failures_detected,
            "restarts_attempted": self.restarts_attempted,
            "restart_results": self.restart_results,
            "duration_ms": self.duration_ms,
            "probe_count": len(self.probes),
            "healthy_count": sum(1 for p in self.probes if p.healthy),
            "unhealthy_count": sum(1 for p in self.probes if not p.healthy),
        }


# ── Health Monitor ───────────────────────────────────────────────────────

class HealthMonitor:
    """
    Platinum-tier continuous health monitor.

    Spawns a daemon thread that periodically:
      1. Probes all watchers (thread liveness + circuit breaker state)
      2. Probes all MCP servers (process alive check)
      3. Probes Odoo connectivity (JSON-RPC ping)
      4. Probes disk space (vault, logs, root)
      5. Probes internet connectivity (HTTPS to key endpoints)
      6. Checks API rate limit proximity
      7. Auto-restarts failed services via AutoRestartManager
      8. Fires alerts via AlertSystem
    """

    MAX_HISTORY = 200

    def __init__(
        self,
        settings,
        memory,
        health_check=None,
        dashboard_server=None,
        inbox_watcher=None,
        agent_map: dict | None = None,
        status_aggregator: StatusAggregator | None = None,
        system_logger: SystemLogger | None = None,
        # Platinum
        alert_system=None,
        auto_restart=None,
        audit_logger=None,
        cloud_watcher_manager=None,
        mcp_server_manager=None,
    ):
        self._settings = settings
        self._memory = memory
        self._health_check = health_check
        self._dashboard = dashboard_server
        self._watcher = inbox_watcher
        self._agent_map = agent_map or {}
        self._aggregator = status_aggregator
        self._syslog = system_logger

        # Platinum subsystems
        self._alerts = alert_system
        self._restarter = auto_restart
        self._audit = audit_logger
        self._cloud_watchers = cloud_watcher_manager
        self._mcp_manager = mcp_server_manager

        self._interval = getattr(settings, "health_check_interval", 60)
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._history: deque[MonitoringSnapshot] = deque(maxlen=self.MAX_HISTORY)
        self._latest_snapshot: MonitoringSnapshot | None = None
        self._check_count = 0

    # ── Lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the health monitor background thread."""
        if self._running:
            log.warning("HealthMonitor already running")
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="HealthMonitor",
            daemon=True,
        )
        self._thread.start()
        log.info("HealthMonitor started (interval: %ds)", self._interval)
        if self._syslog:
            self._syslog.info("health_monitor", "Monitor started",
                              {"interval": self._interval})

    def stop(self) -> None:
        """Gracefully stop the health monitor."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        log.info("HealthMonitor stopped")
        if self._syslog:
            self._syslog.info("health_monitor", "Monitor stopped")

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    # ── Monitor loop ─────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        """Main monitoring loop — runs until stop() is called."""
        log.debug("Monitor loop started")
        while not self._stop_event.is_set():
            try:
                snapshot = self._run_all_probes()
                self._latest_snapshot = snapshot
                self._history.append(snapshot)
                self._check_count += 1

                if snapshot.failures_detected:
                    self._handle_failures(snapshot)

            except Exception as exc:
                log.error("Monitor loop error: %s", exc, exc_info=True)
                if self._syslog:
                    self._syslog.error(
                        "health_monitor", f"Monitor loop error: {exc}",
                    )

            self._stop_event.wait(timeout=self._interval)

        log.debug("Monitor loop exited")

    # ── Probe orchestrator ───────────────────────────────────────────

    def _run_all_probes(self) -> MonitoringSnapshot:
        """Execute all health probes and build a snapshot."""
        start = time.monotonic()
        snapshot = MonitoringSnapshot()

        # 1. Cloud Watchers
        snapshot.probes.extend(self._probe_watchers())

        # 2. MCP Servers
        snapshot.probes.extend(self._probe_mcp_servers())

        # 3. Odoo
        snapshot.probes.append(self._probe_odoo())

        # 4. Disk Space
        snapshot.probes.extend(self._probe_disk_space())

        # 5. Internet Connectivity
        snapshot.probes.append(self._probe_internet())

        # 6. API Limits
        snapshot.probes.extend(self._probe_api_limits())

        # 7. Legacy checks (dashboard, inbox_watcher, agents)
        snapshot.probes.extend(self._probe_legacy_services())

        # Collect failures
        for probe in snapshot.probes:
            if not probe.healthy:
                snapshot.failures_detected.append(probe.name)

        # System metrics
        snapshot.system_metrics = self._collect_metrics()
        snapshot.overall_healthy = len(snapshot.failures_detected) == 0
        snapshot.duration_ms = int((time.monotonic() - start) * 1000)

        return snapshot

    # ── Probe 1: Cloud Watchers ──────────────────────────────────────

    def _probe_watchers(self) -> list[ProbeResult]:
        """Check all cloud watchers (thread liveness + circuit state)."""
        results = []
        if not self._cloud_watchers:
            return results

        watchers = getattr(self._cloud_watchers, "_watchers", None)
        if not watchers:
            # Try get_all_health if available
            get_health = getattr(self._cloud_watchers, "get_all_health", None)
            if get_health:
                try:
                    healths = get_health()
                    for h in healths:
                        name = h.get("name", "unknown") if isinstance(h, dict) else getattr(h, "name", "unknown")
                        running = h.get("running", False) if isinstance(h, dict) else getattr(h, "running", False)
                        enabled = h.get("enabled", True) if isinstance(h, dict) else getattr(h, "enabled", True)
                        circuit = h.get("circuit_state", "closed") if isinstance(h, dict) else getattr(h, "circuit_state", "closed")

                        healthy = running or not enabled
                        results.append(ProbeResult(
                            name=f"watcher_{name}",
                            category="watcher",
                            healthy=healthy,
                            message="running" if running else ("disabled" if not enabled else "stopped"),
                            metadata={"circuit_state": circuit, "enabled": enabled},
                        ))
                except Exception as exc:
                    results.append(ProbeResult(
                        name="cloud_watchers",
                        category="watcher",
                        healthy=False,
                        message=f"Health check failed: {exc}",
                    ))
            return results

        for watcher in watchers:
            t0 = time.monotonic()
            name = getattr(watcher, "name", "unknown")
            enabled = getattr(watcher, "enabled", True)

            if not enabled:
                results.append(ProbeResult(
                    name=f"watcher_{name}",
                    category="watcher",
                    healthy=True,
                    message="disabled (OK)",
                ))
                continue

            running = getattr(watcher, "running", False)
            breaker = getattr(watcher, "_breaker", None)
            circuit_state = breaker.state.value if breaker else "unknown"
            consec = getattr(watcher, "_consecutive_failures", 0)
            elapsed = int((time.monotonic() - t0) * 1000)

            healthy = running and circuit_state != "open"
            msg = f"running, circuit={circuit_state}" if running else "thread dead"

            results.append(ProbeResult(
                name=f"watcher_{name}",
                category="watcher",
                healthy=healthy,
                message=msg,
                duration_ms=elapsed,
                metadata={
                    "circuit_state": circuit_state,
                    "consecutive_failures": consec,
                },
            ))

        return results

    # ── Probe 2: MCP Servers ─────────────────────────────────────────

    def _probe_mcp_servers(self) -> list[ProbeResult]:
        """Check all MCP server processes."""
        results = []
        if not self._mcp_manager:
            return results

        try:
            health_all = self._mcp_manager.health_check_all()
        except Exception as exc:
            results.append(ProbeResult(
                name="mcp_servers",
                category="mcp_server",
                healthy=False,
                message=f"Health check failed: {exc}",
            ))
            return results

        for name, health in health_all.items():
            alive = health.get("alive", False)
            status = health.get("status", "unknown")
            crash_count = health.get("crash_count", 0)

            results.append(ProbeResult(
                name=f"mcp_{name}",
                category="mcp_server",
                healthy=alive,
                message=f"status={status}" + (f", crashes={crash_count}" if crash_count else ""),
                metadata={
                    "pid": health.get("pid"),
                    "status": status,
                    "crash_count": crash_count,
                    "start_count": health.get("start_count", 0),
                },
            ))

        return results

    # ── Probe 3: Odoo ────────────────────────────────────────────────

    def _probe_odoo(self) -> ProbeResult:
        """Check Odoo JSON-RPC connectivity."""
        odoo_url = getattr(self._settings, "odoo_url", "")
        if not odoo_url:
            return ProbeResult(
                name="odoo",
                category="odoo",
                healthy=True,
                message="not configured (OK)",
            )

        t0 = time.monotonic()
        try:
            import urllib.request
            import urllib.error
            # Try a lightweight version_info call
            url = f"{odoo_url.rstrip('/')}/web/webclient/version_info"
            req = urllib.request.Request(
                url, method="POST",
                headers={"Content-Type": "application/json"},
                data=b'{"jsonrpc":"2.0","method":"call","id":1,"params":{}}',
            )
            with urllib.request.urlopen(req, timeout=ODOO_TIMEOUT) as resp:
                data = resp.read()
                elapsed = int((time.monotonic() - t0) * 1000)
                return ProbeResult(
                    name="odoo",
                    category="odoo",
                    healthy=True,
                    message=f"reachable ({elapsed}ms)",
                    duration_ms=elapsed,
                    metadata={"url": odoo_url},
                )
        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            return ProbeResult(
                name="odoo",
                category="odoo",
                healthy=False,
                message=f"unreachable: {exc}",
                duration_ms=elapsed,
                metadata={"url": odoo_url},
            )

    # ── Probe 4: Disk Space ──────────────────────────────────────────

    def _probe_disk_space(self) -> list[ProbeResult]:
        """Check free disk space on key directories."""
        results = []
        paths = {
            "disk_vault": getattr(self._settings, "vault_dir", None),
            "disk_logs": getattr(self._settings, "log_dir", None),
            "disk_root": getattr(self._settings, "project_root", None),
        }

        for label, path in paths.items():
            if not path or not Path(path).exists():
                continue
            try:
                usage = shutil.disk_usage(str(path))
                free_mb = usage.free / (1024 * 1024)
                total_mb = usage.total / (1024 * 1024)
                used_pct = ((usage.total - usage.free) / usage.total) * 100

                if free_mb < DISK_CRIT_MB:
                    healthy = False
                    msg = f"CRITICAL: {free_mb:.0f}MB free ({used_pct:.0f}% used)"
                elif free_mb < DISK_WARN_MB:
                    healthy = False
                    msg = f"LOW: {free_mb:.0f}MB free ({used_pct:.0f}% used)"
                else:
                    healthy = True
                    msg = f"{free_mb:.0f}MB free ({used_pct:.0f}% used)"

                results.append(ProbeResult(
                    name=label,
                    category="disk",
                    healthy=healthy,
                    message=msg,
                    metadata={
                        "free_mb": round(free_mb, 1),
                        "total_mb": round(total_mb, 1),
                        "used_pct": round(used_pct, 1),
                        "path": str(path),
                    },
                ))
            except Exception as exc:
                results.append(ProbeResult(
                    name=label,
                    category="disk",
                    healthy=False,
                    message=f"check failed: {exc}",
                ))

        return results

    # ── Probe 5: Internet Connectivity ───────────────────────────────

    def _probe_internet(self) -> ProbeResult:
        """Check outbound HTTPS connectivity."""
        targets = [
            ("googleapis.com", 443),
            ("graph.facebook.com", 443),
            ("api.twitter.com", 443),
        ]

        t0 = time.monotonic()
        reachable = 0
        errors = []

        for host, port in targets:
            try:
                sock = socket.create_connection((host, port), timeout=INTERNET_TIMEOUT)
                sock.close()
                reachable += 1
            except Exception as exc:
                errors.append(f"{host}: {exc}")

        elapsed = int((time.monotonic() - t0) * 1000)
        total = len(targets)
        healthy = reachable > 0  # At least one endpoint reachable
        msg = f"{reachable}/{total} endpoints reachable ({elapsed}ms)"
        if errors:
            msg += f" — failures: {'; '.join(errors[:3])}"

        return ProbeResult(
            name="internet",
            category="internet",
            healthy=healthy,
            message=msg,
            duration_ms=elapsed,
            metadata={
                "reachable": reachable,
                "total": total,
                "errors": errors,
            },
        )

    # ── Probe 6: API Rate Limits ─────────────────────────────────────

    def _probe_api_limits(self) -> list[ProbeResult]:
        """Check per-service API call counts approaching rate limits."""
        results = []

        # LinkedIn limits from settings
        limits = {
            "linkedin_messages": {
                "max": getattr(self._settings, "linkedin_max_messages_per_day", 80),
                "label": "LinkedIn messages/day",
            },
            "linkedin_connections": {
                "max": getattr(self._settings, "linkedin_max_connections_per_day", 25),
                "label": "LinkedIn connections/day",
            },
        }

        # Try to read actual counts from agents
        for limit_name, config in limits.items():
            max_val = config["max"]
            label = config["label"]
            current = 0

            # Attempt to read from agent rate limiter
            agent_name = limit_name.split("_")[0] + "_agent"
            agent = self._agent_map.get(agent_name)
            if agent:
                rl = getattr(agent, "_rate_limiter", None) or getattr(agent, "rate_limiter", None)
                if rl:
                    current = getattr(rl, "current_count", 0)

            pct = (current / max_val * 100) if max_val > 0 else 0
            if pct >= API_LIMIT_CRIT_PCT:
                healthy = False
                msg = f"CRITICAL: {current}/{max_val} ({pct:.0f}%) — {label}"
            elif pct >= API_LIMIT_WARN_PCT:
                healthy = False
                msg = f"WARNING: {current}/{max_val} ({pct:.0f}%) — {label}"
            else:
                healthy = True
                msg = f"{current}/{max_val} ({pct:.0f}%) — {label}"

            results.append(ProbeResult(
                name=f"api_{limit_name}",
                category="api_limit",
                healthy=healthy,
                message=msg,
                metadata={
                    "current": current,
                    "max": max_val,
                    "pct": round(pct, 1),
                },
            ))

        return results

    # ── Legacy service probes ────────────────────────────────────────

    def _probe_legacy_services(self) -> list[ProbeResult]:
        """Check dashboard, inbox watcher, and agent health."""
        results = []

        # Dashboard
        if self._dashboard:
            thread = getattr(self._dashboard, "_thread", None)
            alive = thread is not None and thread.is_alive() if thread else True
            results.append(ProbeResult(
                name="dashboard_server",
                category="service",
                healthy=alive or thread is None,
                message="running" if alive else ("not started" if thread is None else "thread dead"),
            ))

        # Inbox Watcher
        if self._watcher:
            observer = getattr(self._watcher, "_observer", None)
            alive = observer is not None and observer.is_alive() if observer else True
            results.append(ProbeResult(
                name="inbox_watcher",
                category="service",
                healthy=alive or observer is None,
                message="running" if alive else ("not started" if observer is None else "thread dead"),
            ))

        # Agents
        for name, agent in self._agent_map.items():
            enabled = getattr(agent, "enabled", True)
            results.append(ProbeResult(
                name=name,
                category="agent",
                healthy=True,  # Agents are always "healthy" if enabled; failures tracked elsewhere
                message="enabled" if enabled else "disabled",
                metadata={"enabled": enabled},
            ))

        return results

    # ── System metrics ───────────────────────────────────────────────

    def _collect_metrics(self) -> dict:
        """Gather system-wide metrics for the snapshot."""
        metrics: dict[str, Any] = {
            "check_number": self._check_count + 1,
            "uptime_seconds": self._health_check.uptime if self._health_check else 0,
            "total_tasks": self._memory.total_tasks if self._memory else 0,
        }

        if self._aggregator:
            metrics["aggregator_health"] = self._aggregator.overall_health().value
            metrics["unhealthy_services"] = len(self._aggregator.get_unhealthy())

        if self._restarter:
            metrics["restart_stats"] = self._restarter.stats

        if self._alerts:
            metrics["alert_stats"] = self._alerts.stats

        return metrics

    # ── Failure handling ─────────────────────────────────────────────

    def _handle_failures(self, snapshot: MonitoringSnapshot) -> None:
        """Handle detected failures: restart services and fire alerts."""
        restartable = []
        alert_only = []

        for name in snapshot.failures_detected:
            # Determine if this is a restartable service
            probe = next((p for p in snapshot.probes if p.name == name), None)
            if not probe:
                continue

            if probe.category in ("watcher", "mcp_server", "service"):
                restartable.append(name)
            else:
                alert_only.append(name)

        # Auto-restart restartable services
        if self._restarter and restartable:
            # Map probe names back to registered service names
            for probe_name in restartable:
                svc_name = self._map_probe_to_service(probe_name)
                if svc_name:
                    snapshot.restarts_attempted.append(svc_name)
                    result = self._restarter.restart(svc_name)
                    snapshot.restart_results[svc_name] = result.to_dict()

        # Fire alerts for non-restartable failures
        if self._alerts:
            for name in alert_only:
                probe = next((p for p in snapshot.probes if p.name == name), None)
                if probe:
                    level = "CRITICAL" if probe.category == "disk" and "CRITICAL" in probe.message else "ERROR"
                    self._alerts.fire(
                        level=level,
                        source=name,
                        title=f"Health check failed: {name}",
                        detail=probe.message,
                        metadata=probe.metadata,
                    )

            # Also alert for restart failures
            for svc_name, result in snapshot.restart_results.items():
                if not result.get("recovered", False):
                    self._alerts.fire(
                        level="ERROR",
                        source=svc_name,
                        title=f"Auto-restart failed: {svc_name}",
                        detail=result.get("error", "Unknown"),
                    )

        # Log to syslog
        if self._syslog:
            self._syslog.warning(
                "health_monitor",
                f"Failures detected: {', '.join(snapshot.failures_detected)}",
                {
                    "failures": snapshot.failures_detected,
                    "restarts": snapshot.restarts_attempted,
                },
            )

    def _map_probe_to_service(self, probe_name: str) -> Optional[str]:
        """Map a probe name (e.g. 'watcher_gmail') to a registered restart service name."""
        if not self._restarter:
            return None

        # Direct lookup
        status = self._restarter.get_service_status(probe_name)
        if status:
            return probe_name

        # Strip prefix
        for prefix in ("watcher_", "mcp_"):
            if probe_name.startswith(prefix):
                bare = probe_name[len(prefix):]
                status = self._restarter.get_service_status(bare)
                if status:
                    return bare

        return None

    # ── Snapshot access ──────────────────────────────────────────────

    def get_snapshot(self) -> MonitoringSnapshot | None:
        """Return the latest monitoring snapshot."""
        return self._latest_snapshot

    def get_history(self, limit: int = 20) -> list[dict]:
        """Return recent snapshots as dicts."""
        snapshots = list(self._history)
        if limit:
            snapshots = snapshots[-limit:]
        return [s.to_dict() for s in snapshots]

    def get_full_report(self) -> dict:
        """Comprehensive health report for dashboard/API."""
        latest = self._latest_snapshot
        return {
            "monitor_running": self.is_running,
            "check_interval": self._interval,
            "total_checks": self._check_count,
            "latest": latest.to_dict() if latest else None,
            "history_count": len(self._history),
            "restart_stats": self._restarter.stats if self._restarter else {},
            "alert_stats": self._alerts.stats if self._alerts else {},
        }
