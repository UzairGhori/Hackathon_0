"""
AI Employee — Production System Manager (Platinum Tier)

Centralized orchestrator for the full production runtime. Manages the
7-step startup sequence and coordinated shutdown of all subsystems:

    1. Cloud Watchers   — Gmail, WhatsApp, LinkedIn, Twitter, Instagram, Odoo
    2. MCP Servers      — meta-social, twitter-social, odoo-accounting, communication
    3. Odoo Connection  — XML-RPC connectivity validation + warm-up
    4. Ralph Loop       — Autonomous error-fixing loop (standby)
    5. Health Monitor   — Continuous probes, auto-restart, alerts
    6. Git Sync         — Periodic vault ↔ git synchronisation
    7. Dashboard        — FastAPI web UI + CEO analytics

Each subsystem reports health via a unified interface.  The manager
aggregates status into a single production readiness snapshot.

Usage:
    from ai_employee.system_manager import SystemManager
    manager = SystemManager(settings, log)
    manager.startup()    # 7-phase boot
    manager.run()        # main loop
    manager.shutdown()   # graceful teardown
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from ai_employee.config.settings import Settings


# ── Enums ────────────────────────────────────────────────────────────────

class SubsystemState(str, Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    FAILED = "failed"


class StartupPhase(str, Enum):
    CLOUD_WATCHERS = "cloud_watchers"
    MCP_SERVERS = "mcp_servers"
    ODOO_CONNECTION = "odoo_connection"
    RALPH_LOOP = "ralph_loop"
    HEALTH_MONITOR = "health_monitor"
    GIT_SYNC = "git_sync"
    DASHBOARD = "dashboard"


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class SubsystemStatus:
    """Health snapshot of a single subsystem."""
    name: str
    state: SubsystemState = SubsystemState.PENDING
    message: str = ""
    started_at: str = ""
    duration_ms: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "message": self.message,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


@dataclass
class StartupResult:
    """Outcome of the full 7-phase startup sequence."""
    success: bool = True
    total_duration_ms: int = 0
    phases: list[SubsystemStatus] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "total_duration_ms": self.total_duration_ms,
            "phases": [p.to_dict() for p in self.phases],
            "warnings": self.warnings,
            "errors": self.errors,
        }


@dataclass
class ProductionHealth:
    """Full production system health snapshot."""
    healthy: bool = True
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    subsystems: dict[str, dict] = field(default_factory=dict)
    uptime_seconds: float = 0.0
    cycle_count: int = 0
    git_sync_count: int = 0

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "timestamp": self.timestamp,
            "subsystems": self.subsystems,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "cycle_count": self.cycle_count,
            "git_sync_count": self.git_sync_count,
        }


# ── Git Sync Worker ──────────────────────────────────────────────────────

class GitSyncWorker:
    """
    Background daemon that periodically syncs the vault/ directory
    with git (add + commit + pull + push).

    Ensures local task files, reports, and audit logs are backed up
    and accessible across machines.
    """

    def __init__(
        self,
        project_root: Path,
        interval: int = 300,
        log: logging.Logger | None = None,
    ):
        self._root = project_root
        self._interval = interval
        self._log = log or logging.getLogger("ai_employee.git_sync")
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sync_count = 0
        self._last_sync: str = ""
        self._last_error: str = ""
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._stop.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="git-sync", daemon=True,
        )
        self._thread.start()
        self._log.info("  GIT     | Sync worker started (interval=%ds)", self._interval)

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._log.info("  GIT     | Sync worker stopped (%d syncs)", self._sync_count)

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def sync_count(self) -> int:
        return self._sync_count

    def _loop(self) -> None:
        # Initial delay to let other services boot
        self._stop.wait(10)
        while not self._stop.is_set():
            self._sync_once()
            self._stop.wait(self._interval)

    def _sync_once(self) -> bool:
        """Execute a git add/commit/pull/push cycle."""
        try:
            cwd = str(self._root)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Stage vault changes
            self._run_git(["git", "add", "vault/", "AI_Employee_Vault/"], cwd)

            # Check if there are staged changes
            result = self._run_git(
                ["git", "diff", "--cached", "--quiet"], cwd, check=False,
            )
            if result.returncode == 0:
                # Nothing to commit
                return True

            # Commit
            msg = f"[AI Employee] Auto-sync vault — {timestamp}"
            self._run_git(["git", "commit", "-m", msg], cwd)

            # Pull with rebase to stay linear
            self._run_git(["git", "pull", "--rebase", "--autostash"], cwd, check=False)

            # Push
            self._run_git(["git", "push"], cwd, check=False)

            self._sync_count += 1
            self._last_sync = datetime.now(timezone.utc).isoformat()
            self._last_error = ""
            self._log.info("  GIT     | Sync #%d complete", self._sync_count)
            return True

        except Exception as exc:
            self._last_error = str(exc)
            self._log.warning("  GIT     | Sync failed: %s", exc)
            return False

    @staticmethod
    def _run_git(cmd: list[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=60, check=check,
        )

    def get_health(self) -> dict:
        return {
            "running": self.running,
            "sync_count": self._sync_count,
            "last_sync": self._last_sync,
            "last_error": self._last_error,
            "interval_seconds": self._interval,
        }


# ═════════════════════════════════════════════════════════════════════════
#  SYSTEM MANAGER
# ═════════════════════════════════════════════════════════════════════════

class SystemManager:
    """
    Production-grade orchestrator for the AI Employee runtime.

    Manages the full 7-phase startup sequence and coordinated shutdown.
    Provides unified health reporting across all subsystems.
    """

    STARTUP_SEQUENCE = [
        StartupPhase.CLOUD_WATCHERS,
        StartupPhase.MCP_SERVERS,
        StartupPhase.ODOO_CONNECTION,
        StartupPhase.RALPH_LOOP,
        StartupPhase.HEALTH_MONITOR,
        StartupPhase.GIT_SYNC,
        StartupPhase.DASHBOARD,
    ]

    def __init__(
        self,
        settings: Settings,
        log: logging.Logger,
        *,
        git_sync_interval: int = 300,
    ):
        self.settings = settings
        self.log = log
        self._git_sync_interval = git_sync_interval

        # Subsystem statuses
        self._statuses: dict[str, SubsystemStatus] = {
            phase.value: SubsystemStatus(name=phase.value)
            for phase in self.STARTUP_SEQUENCE
        }

        # Runtime state
        self._started_at: float = 0.0
        self._cycle_count = 0
        self._running = False
        self._stop_event = threading.Event()

        # Subsystem references (populated during startup)
        self._employee = None       # AIEmployee instance
        self._cloud_mgr = None      # CloudWatcherManager
        self._git_sync = None       # GitSyncWorker

    # ── Startup ──────────────────────────────────────────────────────

    def startup(self) -> StartupResult:
        """
        Execute the 7-phase production startup sequence.

        Each phase is timed and logged. Non-critical failures are recorded
        as warnings; the system continues in degraded mode. Critical
        failures (dashboard) mark the startup as failed.

        Returns a StartupResult with per-phase status.
        """
        result = StartupResult()
        total_start = time.monotonic()

        self.log.info("")
        self.log.info("=" * 60)
        self.log.info("  PRODUCTION STARTUP SEQUENCE")
        self.log.info("=" * 60)

        # Build the core AIEmployee (initialises all components)
        self.log.info("")
        self.log.info("  INIT    | Building AI Employee core...")
        from ai_employee.main import AIEmployee
        self._employee = AIEmployee(self.settings, self.log)
        self._employee.boot()

        # Phase 1: Cloud Watchers
        status = self._phase_cloud_watchers()
        result.phases.append(status)
        if status.state == SubsystemState.FAILED:
            result.warnings.append(f"Cloud watchers: {status.message}")

        # Phase 2: MCP Servers
        status = self._phase_mcp_servers()
        result.phases.append(status)
        if status.state == SubsystemState.FAILED:
            result.warnings.append(f"MCP servers: {status.message}")

        # Phase 3: Odoo Connection
        status = self._phase_odoo_connection()
        result.phases.append(status)
        if status.state == SubsystemState.FAILED:
            result.warnings.append(f"Odoo connection: {status.message}")

        # Phase 4: Ralph Loop (standby)
        status = self._phase_ralph_loop()
        result.phases.append(status)

        # Phase 5: Health Monitor
        status = self._phase_health_monitor()
        result.phases.append(status)
        if status.state == SubsystemState.FAILED:
            result.warnings.append(f"Health monitor: {status.message}")

        # Phase 6: Git Sync
        status = self._phase_git_sync()
        result.phases.append(status)
        if status.state == SubsystemState.FAILED:
            result.warnings.append(f"Git sync: {status.message}")

        # Phase 7: Dashboard
        status = self._phase_dashboard()
        result.phases.append(status)
        if status.state == SubsystemState.FAILED:
            result.errors.append(f"Dashboard: {status.message}")
            result.success = False

        result.total_duration_ms = int((time.monotonic() - total_start) * 1000)
        self._started_at = time.monotonic()
        self._running = True

        # Print startup summary
        self.log.info("")
        self.log.info("=" * 60)
        self.log.info("  STARTUP COMPLETE")
        self.log.info("=" * 60)

        running = sum(
            1 for p in result.phases
            if p.state in (SubsystemState.RUNNING, SubsystemState.DEGRADED)
        )
        total = len(result.phases)
        self.log.info(
            "  %d/%d subsystems online | %dms total",
            running, total, result.total_duration_ms,
        )
        for phase in result.phases:
            icon = {
                SubsystemState.RUNNING: "[OK]",
                SubsystemState.DEGRADED: "[~~]",
                SubsystemState.FAILED: "[!!]",
                SubsystemState.PENDING: "[..]",
            }.get(phase.state, "[??]")
            self.log.info(
                "    %s %-20s %s (%dms)",
                icon, phase.name, phase.message, phase.duration_ms,
            )

        for w in result.warnings:
            self.log.warning("  WARN    | %s", w)
        for e in result.errors:
            self.log.error("  ERROR   | %s", e)

        self.log.info("")

        return result

    # ── Main loop ────────────────────────────────────────────────────

    def run(self, interval_minutes: int = 5) -> None:
        """
        Main production loop — run AI Employee cycles until stopped.

        Calls the existing AIEmployee.run_cycle() for each cycle, with
        graceful shutdown on Ctrl+C or stop() signal.
        """
        if not self._employee:
            raise RuntimeError("Call startup() before run()")

        self._stop_event.clear()

        # Log startup status
        self._employee._log_startup(interval_minutes)
        self.log.info("  ONLINE  | Production system running. Press Ctrl+C to stop.")
        self.log.info("")

        # Initial sweep
        self._employee.run_cycle(0)

        # Main loop
        try:
            while self._running:
                self.log.info(
                    "  IDLE    | Next cycle in %d min ...", interval_minutes,
                )
                stopped = self._stop_event.wait(timeout=interval_minutes * 60)
                if stopped or not self._running:
                    break
                self._cycle_count += 1
                self._employee.run_cycle(self._cycle_count)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def stop(self) -> None:
        """Signal the main loop to stop (non-blocking)."""
        self._running = False
        self._stop_event.set()

    # ── Shutdown ─────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """
        Graceful production shutdown — reverse of startup order.

        7. Dashboard
        6. Git Sync (final sync)
        5. Health Monitor
        4. Ralph Loop (no-op, standby)
        3. Odoo Connection (no-op)
        2. MCP Servers
        1. Cloud Watchers
        """
        if not self._running and not self._employee:
            return
        self._running = False
        self._stop_event.set()

        self.log.info("")
        self.log.info("=" * 60)
        self.log.info("  PRODUCTION SHUTDOWN")
        self.log.info("=" * 60)

        emp = self._employee

        # 7. Dashboard
        self.log.info("  STOP    | [7] Dashboard")
        if emp:
            try:
                emp.dashboard.stop()
            except Exception as exc:
                self.log.error("  STOP    |     Dashboard: %s", exc)

        # 6. Git Sync (final sync before stopping)
        self.log.info("  STOP    | [6] Git Sync")
        if self._git_sync:
            try:
                self._git_sync._sync_once()  # One last sync
                self._git_sync.stop()
            except Exception as exc:
                self.log.error("  STOP    |     Git sync: %s", exc)

        # 5. Health Monitor
        self.log.info("  STOP    | [5] Health Monitor")
        if emp:
            try:
                emp.health_monitor.stop()
            except Exception as exc:
                self.log.error("  STOP    |     Health monitor: %s", exc)

        # 4. Ralph Loop — standby, no-op

        # 3. Odoo — no persistent connection to close

        # 2. MCP Servers
        self.log.info("  STOP    | [2] MCP Servers")
        if emp:
            try:
                emp.mcp_manager.stop_all()
            except Exception as exc:
                self.log.error("  STOP    |     MCP servers: %s", exc)

        # 1. Cloud Watchers
        self.log.info("  STOP    | [1] Cloud Watchers")
        if self._cloud_mgr:
            try:
                self._cloud_mgr.stop()
            except Exception as exc:
                self.log.error("  STOP    |     Cloud watchers: %s", exc)

        # Inbox watcher
        self.log.info("  STOP    | Inbox Watcher")
        if emp:
            try:
                emp.watcher.stop()
            except Exception as exc:
                self.log.error("  STOP    |     Inbox watcher: %s", exc)

        # Persist state
        if emp:
            try:
                emp.approval_manager.save_audit_log()
            except Exception:
                pass

        uptime = ""
        if self._started_at:
            total_s = time.monotonic() - self._started_at
            hours, remainder = divmod(int(total_s), 3600)
            mins, secs = divmod(remainder, 60)
            uptime = f"{hours}h {mins}m {secs}s"

        git_syncs = self._git_sync.sync_count if self._git_sync else 0

        self.log.info("")
        self.log.info(
            "  EXIT    | Cycles: %d | Git syncs: %d | Uptime: %s",
            self._cycle_count, git_syncs, uptime or "N/A",
        )
        self.log.info("=" * 60)

        # Audit
        if emp:
            from ai_employee.monitoring.audit_logger import AuditEvent
            emp.audit_logger.log_system_event(
                AuditEvent.SYSTEM_SHUTDOWN,
                "Production system stopped",
                {
                    "cycles": self._cycle_count,
                    "git_syncs": git_syncs,
                    "uptime": uptime,
                },
            )

    # ── Health ───────────────────────────────────────────────────────

    def get_health(self) -> ProductionHealth:
        """Build a unified health snapshot across all subsystems."""
        health = ProductionHealth()
        health.cycle_count = self._cycle_count
        health.git_sync_count = self._git_sync.sync_count if self._git_sync else 0

        if self._started_at:
            health.uptime_seconds = time.monotonic() - self._started_at

        any_failed = False

        for name, status in self._statuses.items():
            health.subsystems[name] = status.to_dict()
            if status.state == SubsystemState.FAILED:
                any_failed = True

        # Live checks
        if self._cloud_mgr:
            cw = self._cloud_mgr.get_health()
            health.subsystems["cloud_watchers"]["live"] = cw.get("summary", {})
            if not cw.get("healthy", True):
                any_failed = True

        if self._git_sync:
            health.subsystems["git_sync"]["live"] = self._git_sync.get_health()

        if self._employee:
            monitor = self._employee.health_monitor
            snap = monitor.get_snapshot()
            if snap:
                health.subsystems["health_monitor"]["live"] = {
                    "overall_healthy": snap.overall_healthy,
                    "failures": snap.failures_detected,
                    "probe_count": len(snap.probes),
                }
                if not snap.overall_healthy:
                    any_failed = True

        health.healthy = not any_failed
        return health

    # ══════════════════════════════════════════════════════════════════
    #  STARTUP PHASES
    # ══════════════════════════════════════════════════════════════════

    def _phase_cloud_watchers(self) -> SubsystemStatus:
        """Phase 1: Start all cloud watchers."""
        status = self._statuses[StartupPhase.CLOUD_WATCHERS.value]
        status.state = SubsystemState.STARTING
        t0 = time.monotonic()

        self.log.info("")
        self.log.info("  [1/7]   CLOUD WATCHERS")

        try:
            from ai_employee.cloud_watchers import CloudWatcherManager
            self._cloud_mgr = CloudWatcherManager(self.settings)
            self._cloud_mgr.start()

            health = self._cloud_mgr.get_health()
            summary = health.get("summary", {})
            running = summary.get("running", 0)
            enabled = summary.get("enabled", 0)

            elapsed = int((time.monotonic() - t0) * 1000)
            status.duration_ms = elapsed
            status.started_at = datetime.now(timezone.utc).isoformat()
            status.metadata = {"running": running, "enabled": enabled}

            if running > 0:
                status.state = SubsystemState.RUNNING
                status.message = f"{running}/{enabled} watchers online"
            elif enabled == 0:
                status.state = SubsystemState.RUNNING
                status.message = "no watchers enabled (OK)"
            else:
                status.state = SubsystemState.DEGRADED
                status.message = f"0/{enabled} watchers running"

            self.log.info(
                "  [1/7]   %s (%dms)", status.message, elapsed,
            )

        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            status.state = SubsystemState.FAILED
            status.message = str(exc)
            status.duration_ms = elapsed
            self.log.error("  [1/7]   FAILED: %s (%dms)", exc, elapsed)

        return status

    def _phase_mcp_servers(self) -> SubsystemStatus:
        """Phase 2: Start MCP server processes."""
        status = self._statuses[StartupPhase.MCP_SERVERS.value]
        status.state = SubsystemState.STARTING
        t0 = time.monotonic()

        self.log.info("  [2/7]   MCP SERVERS")

        try:
            emp = self._employee

            # Discover tools
            tool_count = emp.tool_registry.discover_all()

            # Start all servers
            mcp_results = emp.mcp_manager.start_all()
            running = sum(1 for ok in mcp_results.values() if ok)
            total = len(mcp_results)

            # Start health monitor for MCP processes
            emp.mcp_manager.start_health_monitor(interval=60)

            elapsed = int((time.monotonic() - t0) * 1000)
            status.duration_ms = elapsed
            status.started_at = datetime.now(timezone.utc).isoformat()
            status.metadata = {
                "tools_discovered": tool_count,
                "servers_running": running,
                "servers_total": total,
            }

            if running == total:
                status.state = SubsystemState.RUNNING
                status.message = f"{running}/{total} servers, {tool_count} tools"
            elif running > 0:
                status.state = SubsystemState.DEGRADED
                status.message = f"{running}/{total} servers ({tool_count} tools)"
            else:
                status.state = SubsystemState.FAILED
                status.message = f"0/{total} servers started"

            self.log.info("  [2/7]   %s (%dms)", status.message, elapsed)

        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            status.state = SubsystemState.FAILED
            status.message = str(exc)
            status.duration_ms = elapsed
            self.log.error("  [2/7]   FAILED: %s (%dms)", exc, elapsed)

        return status

    def _phase_odoo_connection(self) -> SubsystemStatus:
        """Phase 3: Validate Odoo XML-RPC connectivity."""
        status = self._statuses[StartupPhase.ODOO_CONNECTION.value]
        status.state = SubsystemState.STARTING
        t0 = time.monotonic()

        self.log.info("  [3/7]   ODOO CONNECTION")

        try:
            emp = self._employee
            odoo_url = self.settings.odoo_url
            odoo_password = self.settings.odoo_password

            if not odoo_url or not odoo_password:
                elapsed = int((time.monotonic() - t0) * 1000)
                status.state = SubsystemState.RUNNING
                status.message = "not configured (OK)"
                status.duration_ms = elapsed
                self.log.info("  [3/7]   %s (%dms)", status.message, elapsed)
                return status

            # Test connectivity via a lightweight call
            uid = emp.odoo.authenticate()
            elapsed = int((time.monotonic() - t0) * 1000)
            status.duration_ms = elapsed
            status.started_at = datetime.now(timezone.utc).isoformat()

            if uid:
                status.state = SubsystemState.RUNNING
                status.message = f"authenticated (uid={uid})"
                status.metadata = {"uid": uid, "url": odoo_url}
            else:
                status.state = SubsystemState.DEGRADED
                status.message = "auth returned no uid"

            self.log.info("  [3/7]   %s (%dms)", status.message, elapsed)

        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            status.state = SubsystemState.FAILED
            status.message = str(exc)
            status.duration_ms = elapsed
            self.log.error("  [3/7]   FAILED: %s (%dms)", exc, elapsed)

        return status

    def _phase_ralph_loop(self) -> SubsystemStatus:
        """Phase 4: Initialise Ralph Loop in standby mode."""
        status = self._statuses[StartupPhase.RALPH_LOOP.value]
        t0 = time.monotonic()

        self.log.info("  [4/7]   RALPH LOOP")

        try:
            emp = self._employee
            # Ralph loop (LoopController) is already initialised in AIEmployee.__init__
            # It runs on-demand, not as a background service.
            has_controller = emp.loop_controller is not None

            elapsed = int((time.monotonic() - t0) * 1000)
            status.duration_ms = elapsed
            status.started_at = datetime.now(timezone.utc).isoformat()

            if has_controller:
                status.state = SubsystemState.RUNNING
                status.message = "standby (on-demand)"
                status.metadata = {
                    "max_iterations": 10,
                    "timeout_seconds": 300,
                }
            else:
                status.state = SubsystemState.DEGRADED
                status.message = "controller not available"

            self.log.info("  [4/7]   %s (%dms)", status.message, elapsed)

        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            status.state = SubsystemState.FAILED
            status.message = str(exc)
            status.duration_ms = elapsed
            self.log.error("  [4/7]   FAILED: %s (%dms)", exc, elapsed)

        return status

    def _phase_health_monitor(self) -> SubsystemStatus:
        """Phase 5: Start the continuous health monitor."""
        status = self._statuses[StartupPhase.HEALTH_MONITOR.value]
        status.state = SubsystemState.STARTING
        t0 = time.monotonic()

        self.log.info("  [5/7]   HEALTH MONITOR")

        try:
            emp = self._employee

            # Inject cloud watcher manager into health monitor
            if self._cloud_mgr:
                emp.health_monitor._cloud_watchers = self._cloud_mgr

            emp.health_monitor.start()

            # Also start the inbox watcher
            emp.watcher.start()

            elapsed = int((time.monotonic() - t0) * 1000)
            status.duration_ms = elapsed
            status.started_at = datetime.now(timezone.utc).isoformat()
            status.state = SubsystemState.RUNNING
            status.message = f"interval={self.settings.health_check_interval}s"
            status.metadata = {
                "interval": self.settings.health_check_interval,
                "inbox_watcher": "running",
            }

            self.log.info("  [5/7]   %s (%dms)", status.message, elapsed)

        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            status.state = SubsystemState.FAILED
            status.message = str(exc)
            status.duration_ms = elapsed
            self.log.error("  [5/7]   FAILED: %s (%dms)", exc, elapsed)

        return status

    def _phase_git_sync(self) -> SubsystemStatus:
        """Phase 6: Start the git sync worker."""
        status = self._statuses[StartupPhase.GIT_SYNC.value]
        status.state = SubsystemState.STARTING
        t0 = time.monotonic()

        self.log.info("  [6/7]   GIT SYNC")

        try:
            self._git_sync = GitSyncWorker(
                project_root=self.settings.project_root,
                interval=self._git_sync_interval,
                log=self.log,
            )
            self._git_sync.start()

            elapsed = int((time.monotonic() - t0) * 1000)
            status.duration_ms = elapsed
            status.started_at = datetime.now(timezone.utc).isoformat()
            status.state = SubsystemState.RUNNING
            status.message = f"interval={self._git_sync_interval}s"
            status.metadata = {"interval": self._git_sync_interval}

            self.log.info("  [6/7]   %s (%dms)", status.message, elapsed)

        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            status.state = SubsystemState.FAILED
            status.message = str(exc)
            status.duration_ms = elapsed
            self.log.error("  [6/7]   FAILED: %s (%dms)", exc, elapsed)

        return status

    def _phase_dashboard(self) -> SubsystemStatus:
        """Phase 7: Start the web dashboard."""
        status = self._statuses[StartupPhase.DASHBOARD.value]
        status.state = SubsystemState.STARTING
        t0 = time.monotonic()

        self.log.info("  [7/7]   DASHBOARD")

        try:
            emp = self._employee
            emp.dashboard.start()

            elapsed = int((time.monotonic() - t0) * 1000)
            status.duration_ms = elapsed
            status.started_at = datetime.now(timezone.utc).isoformat()
            status.state = SubsystemState.RUNNING
            status.message = emp.dashboard.url
            status.metadata = {
                "host": self.settings.dashboard_host,
                "port": self.settings.dashboard_port,
                "url": emp.dashboard.url,
            }

            self.log.info("  [7/7]   %s (%dms)", status.message, elapsed)

        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            status.state = SubsystemState.FAILED
            status.message = str(exc)
            status.duration_ms = elapsed
            self.log.error("  [7/7]   FAILED: %s (%dms)", exc, elapsed)

        return status

    # ── Convenience ──────────────────────────────────────────────────

    @property
    def employee(self):
        """Access the underlying AIEmployee instance."""
        return self._employee

    @property
    def cloud_watchers(self):
        """Access the CloudWatcherManager."""
        return self._cloud_mgr

    @property
    def git_sync(self):
        """Access the GitSyncWorker."""
        return self._git_sync

    def print_status(self) -> None:
        """Print a human-readable production status table."""
        health = self.get_health()
        overall = "HEALTHY" if health.healthy else "DEGRADED"

        print(f"\nProduction System: {overall}")
        print("=" * 70)
        print(f"{'Subsystem':<22} {'State':<12} {'Message'}")
        print("-" * 70)

        for name, info in health.subsystems.items():
            state = info.get("state", "unknown")
            message = info.get("message", "")
            icon = {
                "running": "[OK]",
                "degraded": "[~~]",
                "failed": "[!!]",
                "pending": "[..]",
                "stopped": "[--]",
            }.get(state, "[??]")
            print(f"  {icon} {name:<18} {state:<12} {message}")

        print("-" * 70)
        print(
            f"  Uptime: {health.uptime_seconds:.0f}s | "
            f"Cycles: {health.cycle_count} | "
            f"Git syncs: {health.git_sync_count}",
        )
        print("=" * 70)
