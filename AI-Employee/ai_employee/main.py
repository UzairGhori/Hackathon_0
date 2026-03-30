"""
AI Employee — Autonomous Digital Worker Runtime

A continuously-running AI Employee that operates like a real digital worker:

  1. BOOT       — Load config, create directories, validate credentials
  2. AGENTS     — Initialize Gmail, LinkedIn, Email, and Task agents
  3. PLANNER    — Start the decision engine + task queue + scheduler
  4. DASHBOARD  — Launch the FastAPI web dashboard
  5. WATCHER    — Monitor vault/Inbox/ for new tasks in real-time
  6. MONITOR    — Background health checks with auto-restart
  7. CYCLE      — Periodic sweep: triage → plan → execute → gmail →
                   linkedin → approvals → stats

The employee runs until stopped with Ctrl+C, then shuts down gracefully.

Usage:
    python -m ai_employee.main                # Start continuous operation
    python -m ai_employee.main -i 2           # Cycle every 2 minutes
    python -m ai_employee.main -v             # Verbose (debug) logging
    python -m ai_employee.main --once         # Single cycle, then exit
    python -m ai_employee.main --health       # Print health report and exit
    python -m ai_employee.main --stats        # Print analytics and exit
    python -m ai_employee.main --queue        # Print task queue and exit
    python -m ai_employee.main --gmail        # Process Gmail once and exit
    python -m ai_employee.main --linkedin     # Process LinkedIn once and exit
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── Internal imports ─────────────────────────────────────────────────────
from ai_employee.config.settings import Settings
from ai_employee.brain.memory import Memory
from ai_employee.brain.planner import AutonomousPlanner
from ai_employee.brain.decision_engine import DecisionEngine, TaskIntelligenceResult
from ai_employee.brain.task_queue import TaskQueue
from ai_employee.brain.scheduler import Scheduler
from ai_employee.brain.approval_queue import ApprovalQueue
from ai_employee.brain.approval_manager import ApprovalManager
from ai_employee.brain.agent_runtime import AgentRuntime
from ai_employee.brain.loop_controller import LoopController
from ai_employee.brain.role_manager import RoleManager
from ai_employee.brain.secrets_manager import SecretsManager
from ai_employee.brain.security_layer import SecurityLayer
from ai_employee.agents.email_agent import EmailAgent
from ai_employee.agents.gmail_agent import GmailAgent
from ai_employee.agents.linkedin_agent import LinkedInAgent
from ai_employee.agents.task_agent import TaskAgent
from ai_employee.agents.odoo_agent import OdooAgent
from ai_employee.agents.meta_agent import MetaAgent
from ai_employee.agents.twitter_agent import TwitterAgent
from ai_employee.agents.audit_agent import AuditAgent
from ai_employee.agents.executive_brief_generator import ExecutiveBriefGenerator
from ai_employee.integrations.odoo_client import OdooClient
from ai_employee.integrations.meta_client import MetaClient
from ai_employee.integrations.twitter_client import TwitterClient
from ai_employee.integrations.gmail_client import GmailClient
from ai_employee.integrations.gmail_reader import GmailReader
from ai_employee.integrations.gmail_sender import GmailSender
from ai_employee.integrations.linkedin_client import LinkedInClient
from ai_employee.integrations.linkedin_scraper import LinkedInScraper
from ai_employee.integrations.linkedin_reply_generator import LinkedInReplyGenerator
from ai_employee.integrations.tool_registry import ToolRegistry
from ai_employee.integrations.server_manager import MCPServerManager
from ai_employee.integrations.mcp_router import MCPRouter
from ai_employee.monitoring.watcher import InboxWatcher, process_file
from ai_employee.monitoring.health_check import HealthCheck
from ai_employee.monitoring.service_status import StatusAggregator
from ai_employee.monitoring.system_logs import SystemLogger, LogCapture
from ai_employee.monitoring.health_monitor import HealthMonitor
from ai_employee.monitoring.alert_system import AlertSystem, AlertLevel, AlertRule
from ai_employee.monitoring.auto_restart import AutoRestartManager
from ai_employee.monitoring.error_handler import ErrorHandler
from ai_employee.monitoring.retry_manager import RetryManager
from ai_employee.monitoring.fallback_system import FallbackSystem
from ai_employee.monitoring.audit_logger import AuditLogger, AuditEvent
from ai_employee.dashboard.analytics import AnalyticsEngine
from ai_employee.dashboard.analytics_engine import CEOAnalyticsEngine
from ai_employee.dashboard.ceo_dashboard import register_ceo_routes
from ai_employee.dashboard.web_app import WebDashboardServer


BANNER = r"""
 _____ _____   _____                _
|  _  |_   _| |   __|_____ ___| |___ _ _ ___ ___
|     | | |   |   __|     | . | | . | | | -_| -_|
|__|__| |_|   |_____|_|_|_|  _|_|___|_  |___|___|
                          |_|       |___|

          GOLD TIER — Autonomous AI Employee
        Agent Factory Hackathon
"""


# ── Logging setup ────────────────────────────────────────────────────────

def setup_logging(log_dir: Path, verbose: bool = False) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"employee_{datetime.now().strftime('%Y%m%d')}.log"

    logger = logging.getLogger("ai_employee")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    )

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


def _format_duration(td: timedelta) -> str:
    """Format a timedelta as a human-readable string."""
    total = int(td.total_seconds())
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


# ── AI Employee ──────────────────────────────────────────────────────────

class AIEmployee:
    """
    The Gold Tier AI Employee — a fully autonomous digital worker.

    Manages the complete lifecycle: construction of all components,
    booting, starting background services, running periodic work cycles,
    and graceful shutdown.
    """

    def __init__(self, settings: Settings, log: logging.Logger):
        self.settings = settings
        self.log = log

        # Runtime state
        self._running = False
        self._cycle_count = 0
        self._start_time: datetime | None = None
        self._stop_event = threading.Event()

        # ── Brain ────────────────────────────────────────────────────
        self.memory = Memory(settings.memory_file)
        self.decision_engine = DecisionEngine(settings.anthropic_api_key)

        # ── Integrations ─────────────────────────────────────────────
        self.gmail = GmailClient(settings.email_address, settings.email_password)
        self.gmail_reader = GmailReader(
            credentials_path=settings.gmail_credentials_path,
            token_path=settings.gmail_token_path,
            processed_ids_path=settings.gmail_processed_ids_path,
        )
        self.gmail_sender = GmailSender(
            credentials_path=settings.gmail_credentials_path,
            token_path=settings.gmail_token_path,
            send_log_path=settings.gmail_send_log_path,
        )
        self.linkedin = LinkedInClient(
            settings.linkedin_email, settings.linkedin_password,
            drafts_dir=settings.needs_action_dir,
            log_path=settings.linkedin_action_log_path,
        )
        self.linkedin_scraper = LinkedInScraper(
            email=settings.linkedin_email,
            password=settings.linkedin_password,
            processed_ids_path=settings.linkedin_processed_ids_path,
            max_messages_per_hour=settings.linkedin_max_messages_per_hour,
            max_messages_per_day=settings.linkedin_max_messages_per_day,
        )
        self.linkedin_reply_gen = LinkedInReplyGenerator(
            api_key=settings.anthropic_api_key,
            log_dir=settings.log_dir,
        )
        self.odoo = OdooClient(
            url=settings.odoo_url,
            db=settings.odoo_db,
            username=settings.odoo_username,
            password=settings.odoo_password,
        )
        self.meta = MetaClient(
            access_token=settings.meta_access_token,
            page_id=settings.meta_page_id,
            ig_user_id=settings.meta_ig_user_id,
        )
        self.twitter = TwitterClient(
            bearer_token=settings.twitter_bearer_token,
            api_key=settings.twitter_api_key,
            api_secret=settings.twitter_api_secret,
            access_token=settings.twitter_access_token,
            access_token_secret=settings.twitter_access_token_secret,
        )

        # ── Agents ───────────────────────────────────────────────────
        self.email_agent = EmailAgent(self.gmail, settings.needs_action_dir)
        self.gmail_agent = GmailAgent(
            reader=self.gmail_reader,
            sender=self.gmail_sender,
            decision_engine=self.decision_engine,
            api_key=settings.anthropic_api_key,
            output_dir=settings.needs_action_dir,
            log_dir=settings.log_dir,
        )
        self.linkedin_agent = LinkedInAgent(
            linkedin=self.linkedin,
            output_dir=settings.needs_action_dir,
            scraper=self.linkedin_scraper,
            reply_generator=self.linkedin_reply_gen,
            decision_engine=self.decision_engine,
            api_key=settings.anthropic_api_key,
            log_dir=settings.log_dir,
            max_messages_per_hour=settings.linkedin_max_messages_per_hour,
            max_connections_per_day=settings.linkedin_max_connections_per_day,
        )
        self.task_agent = TaskAgent(settings.needs_action_dir)
        self.odoo_agent = OdooAgent(
            odoo=self.odoo,
            output_dir=settings.needs_action_dir,
            decision_engine=self.decision_engine,
            api_key=settings.anthropic_api_key,
            log_dir=settings.log_dir,
        )

        self.meta_agent = MetaAgent(
            meta=self.meta,
            output_dir=settings.needs_action_dir,
            log_dir=settings.log_dir,
        )

        self.twitter_agent = TwitterAgent(
            twitter=self.twitter,
            output_dir=settings.needs_action_dir,
            log_dir=settings.log_dir,
        )

        self.audit_agent = AuditAgent(
            odoo=self.odoo,
            meta=self.meta,
            gmail_reader=self.gmail_reader,
            gmail_send_log_path=settings.gmail_send_log_path,
            output_dir=settings.briefing_dir,
            log_dir=settings.log_dir,
        )

        self.brief_generator = ExecutiveBriefGenerator(
            odoo=self.odoo,
            meta=self.meta,
            twitter=self.twitter,
            gmail_reader=self.gmail_reader,
            gmail_send_log_path=settings.gmail_send_log_path,
            output_dir=settings.briefing_dir,
            log_dir=settings.log_dir,
        )

        self._agent_map = {
            "email_agent": self.email_agent,
            "gmail_agent": self.gmail_agent,
            "linkedin_agent": self.linkedin_agent,
            "odoo_agent": self.odoo_agent,
            "meta_agent": self.meta_agent,
            "twitter_agent": self.twitter_agent,
            "audit_agent": self.audit_agent,
            "executive_brief_generator": self.brief_generator,
            "task_agent": self.task_agent,
        }

        # ── Ralph Wiggum Autonomous Loop ─────────────────────────────
        # Gold tier (backward compat)
        self.agent_runtime = AgentRuntime(
            decision_engine=self.decision_engine,
            memory=self.memory,
            agent_map=self._agent_map,
            api_key=settings.anthropic_api_key,
            log_dir=settings.log_dir,
            max_iterations=10,
            timeout_seconds=300,
            stall_threshold=3,
        )

        # ── Planner (queue + scheduler) ──────────────────────────────
        queue_path = settings.vault_dir / "task_queue.json"
        self.planner = AutonomousPlanner(
            needs_action_dir=settings.needs_action_dir,
            memory=self.memory,
            api_key=settings.anthropic_api_key,
            agent_map=self._agent_map,
            queue_path=queue_path,
        )

        # ── Approval system ──────────────────────────────────────────
        self.approval_queue = ApprovalQueue(
            persist_path=settings.approval_queue_path,
            default_expiry_hours=24,
        )
        self.approval_manager = ApprovalManager(
            queue=self.approval_queue,
            approval_dir=settings.approval_dir,
            log_dir=settings.log_dir,
            agent_map=self._agent_map,
            gmail_sender=self.gmail_sender if self.gmail_sender.enabled else None,
            manager_email=settings.email_address,
        )

        # ── Monitoring ───────────────────────────────────────────────
        self.health_check = HealthCheck(settings)
        self.watcher = InboxWatcher(settings.inbox_dir, self._on_new_file)

        self.system_logger = SystemLogger(self.memory)
        self.status_aggregator = StatusAggregator()

        cb_threshold = settings.circuit_breaker_threshold
        cb_timeout = settings.circuit_breaker_timeout
        for svc_name in ("dashboard", "inbox_watcher"):
            self.status_aggregator.register(
                svc_name, "service",
                failure_threshold=cb_threshold,
                recovery_timeout=cb_timeout,
            )
        for agent_name in self._agent_map:
            self.status_aggregator.register(
                agent_name, "agent",
                failure_threshold=cb_threshold,
                recovery_timeout=cb_timeout,
            )

        # ── Error Recovery System ─────────────────────────────────────
        self.fallback_system = FallbackSystem(
            agent_map=self._agent_map,
            status_aggregator=self.status_aggregator,
            system_logger=self.system_logger,
        )
        self.error_handler = ErrorHandler(
            status_aggregator=self.status_aggregator,
            system_logger=self.system_logger,
            fallback_system=self.fallback_system,
        )
        self.retry_manager = RetryManager(
            status_aggregator=self.status_aggregator,
            system_logger=self.system_logger,
        )

        # ── Audit Logger ─────────────────────────────────────────────
        self.audit_logger = AuditLogger(log_dir=settings.log_dir)

        # ── Platinum Loop Controller ────────────────────────────────
        self.loop_controller = LoopController(
            decision_engine=self.decision_engine,
            memory=self.memory,
            agent_map=self._agent_map,
            api_key=settings.anthropic_api_key,
            log_dir=settings.log_dir,
            max_iterations=10,
            timeout_seconds=300,
            stall_threshold=3,
            error_handler=self.error_handler,
            approval_manager=self.approval_manager,
            permission_manager=None,  # injected later if cloud mode
            fallback_system=self.fallback_system,
            retry_manager=self.retry_manager,
            audit_logger=self.audit_logger,
        )

        self.log_capture = LogCapture(self.system_logger)
        self.log_capture.install()

        # ── Dashboard ────────────────────────────────────────────────
        self.analytics = AnalyticsEngine(self.memory, settings)
        self.dashboard = WebDashboardServer(
            host=settings.dashboard_host,
            port=settings.dashboard_port,
            memory=self.memory,
            analytics=self.analytics,
            approval_manager=self.approval_manager,
            status_aggregator=self.status_aggregator,
            system_logger=self.system_logger,
            health_check=self.health_check,
            health_monitor=None,
            planner=self.planner,
            decision_engine=self.decision_engine,
            settings=settings,
            audit_agent=self.audit_agent,
        )

        # ── CEO Analytics Engine ───────────────────────────────────────
        self.ceo_analytics = CEOAnalyticsEngine(
            memory=self.memory,
            settings=settings,
            approval_manager=self.approval_manager,
            audit_agent=self.audit_agent,
            meta_agent=self.meta_agent,
            odoo_agent=self.odoo_agent,
            gmail_agent=self.gmail_agent,
            planner=self.planner,
            status_aggregator=self.status_aggregator,
            audit_logger=self.audit_logger,
            error_handler=self.error_handler,
            retry_manager=self.retry_manager,
            fallback_system=self.fallback_system,
        )

        # Register CEO dashboard routes on the FastAPI app
        from ai_employee.dashboard.web_app import app as _web_app
        register_ceo_routes(_web_app, self.ceo_analytics)

        # ── MCP Server Architecture ──────────────────────────────────
        self.tool_registry = ToolRegistry()
        self.mcp_manager = MCPServerManager()
        self.mcp_router = MCPRouter(self.tool_registry, self.mcp_manager)

        # ── Alert System (Platinum) ──────────────────────────────────
        self.alert_system = AlertSystem(
            log_dir=settings.log_dir,
            vault_dir=settings.approval_dir,
            system_logger=self.system_logger,
            gmail_sender=getattr(self, "gmail_sender", None),
            manager_email=getattr(settings, "email_address", ""),
            audit_logger=self.audit_logger,
        )
        # Default rules: cascade detection
        self.alert_system.add_rule(AlertRule(
            name="cascade_errors",
            source="*",
            level=AlertLevel.CRITICAL,
            threshold=5,
            window_seconds=300,
            cooldown_seconds=600,
            title_template="{source}: cascade detected ({count} errors in 5min)",
        ))

        # ── Auto-Restart Manager (Platinum) ──────────────────────────
        self.auto_restart = AutoRestartManager(
            alert_system=self.alert_system,
            audit_logger=self.audit_logger,
            system_logger=self.system_logger,
        )
        # Register services
        if self.dashboard:
            self.auto_restart.register_dashboard("dashboard_server", self.dashboard)
        if self.watcher:
            self.auto_restart.register_inbox_watcher("inbox_watcher", self.watcher)

        # ── Health monitor (Platinum) ────────────────────────────────
        self.health_monitor = HealthMonitor(
            settings=settings,
            memory=self.memory,
            health_check=self.health_check,
            dashboard_server=self.dashboard,
            inbox_watcher=self.watcher,
            agent_map=self._agent_map,
            status_aggregator=self.status_aggregator,
            system_logger=self.system_logger,
            alert_system=self.alert_system,
            auto_restart=self.auto_restart,
            audit_logger=self.audit_logger,
            mcp_server_manager=self.mcp_manager,
        )

        # ── Security Isolation Layer (Platinum) ──────────────────────
        self.role_manager = RoleManager(
            role="cloud_ai" if os.getenv("SYNC_ROLE") == "cloud" else "ceo",
        )
        self.secrets_manager = SecretsManager()
        self.secrets_manager.load_from_settings(settings)
        self.security_layer = SecurityLayer(
            role_manager=self.role_manager,
            secrets_manager=self.secrets_manager,
            audit_logger=self.audit_logger,
        )

        # Wire health_monitor into the dashboard's module state
        from ai_employee.dashboard import web_app as _web_mod
        _web_mod._health_monitor = self.health_monitor

    # ══════════════════════════════════════════════════════════════════
    #  LIFECYCLE
    # ══════════════════════════════════════════════════════════════════

    def boot(self) -> None:
        """Phase 0: Create directories, validate config, run health check."""
        self.settings.ensure_dirs()
        self.audit_logger.log_system_event(
            AuditEvent.SYSTEM_BOOT, "AI Employee booting",
            {"project_root": str(self.settings.project_root)},
        )

        q = self.planner.queue_summary()
        aq = self.approval_queue.summary()
        self.log.info(
            "  BOOT    | Memory: %d tasks | Queue: %d (%d pending) | Approvals: %d pending",
            self.memory.total_tasks, q["total"],
            q.get("by_status", {}).get("pending", 0),
            aq.get("pending", 0),
        )

        health = self.health_check.run()
        self.log.info(
            "  BOOT    | Health: %s (uptime: %.0fs)",
            "HEALTHY" if health.overall else "DEGRADED",
            health.uptime_seconds,
        )
        for c in health.components:
            icon = "[OK]" if c.healthy else "[!!]"
            self.log.info("  BOOT    |   %s %s — %s", icon, c.name, c.message)

        for warning in self.settings.validate():
            self.log.warning("  CONFIG  | %s", warning)

    def start_services(self) -> None:
        """Start all background services: dashboard, watcher, health monitor."""
        self.log.info("")

        # 1. Dashboard
        self.dashboard.start()
        self.log.info("  SERVICE | Dashboard started    %s", self.dashboard.url)

        # 2. Inbox watcher
        self.watcher.start()
        self.log.info("  SERVICE | Watcher started      %s", self.settings.inbox_dir)

        # 3. Health monitor
        self.health_monitor.start()
        self.log.info(
            "  SERVICE | Monitor started      interval=%ds",
            self.settings.health_check_interval,
        )

        # 4. MCP servers
        tool_count = self.tool_registry.discover_all()
        self.log.info("  SERVICE | MCP registry         %d tools discovered", tool_count)
        mcp_results = self.mcp_manager.start_all()
        running = sum(1 for ok in mcp_results.values() if ok)
        self.log.info(
            "  SERVICE | MCP servers           %d/%d started",
            running, len(mcp_results),
        )
        self.mcp_manager.start_health_monitor(interval=60)

        self.system_logger.info(
            "lifecycle", "All services started",
            {"dashboard": self.dashboard.url,
             "watcher": str(self.settings.inbox_dir),
             "mcp_tools": tool_count,
             "mcp_servers": running},
        )

    def _log_startup(self, interval: int) -> None:
        """Print a startup status report showing agents and config."""
        self.log.info("")
        self.log.info("  " + "-" * 50)
        self.log.info("  AGENTS")
        for name, agent in self._agent_map.items():
            enabled = getattr(agent, "enabled", True)
            icon = "[OK]" if enabled else "[--]"
            self.log.info("    %s %-20s %s", icon, name,
                          "ready" if enabled else "disabled")
        self.log.info("")
        self.log.info("  RUNTIME")
        self.log.info("    Cycle interval  : %d min", interval)
        self.log.info("    AI Engine       : %s",
                      "Claude API" if self.decision_engine.ai_enabled else "local fallback")
        self.log.info("    Dashboard       : %s", self.dashboard.url)
        self.log.info("    Inbox watch     : %s", self.settings.inbox_dir)
        self.log.info("  " + "-" * 50)
        self.log.info("")

    def run(self, interval_minutes: int = 5) -> None:
        """
        Main entry point — runs the AI Employee continuously.

        1. Boot system
        2. Start background services (dashboard, watcher, monitor)
        3. Run an initial sweep (cycle 0)
        4. Loop: sleep → full pipeline cycle → repeat
        5. On Ctrl+C: graceful shutdown
        """
        self._running = True
        self._start_time = datetime.now()
        self._stop_event.clear()

        # Phase 1: Boot
        self.boot()

        # Phase 2: Start services
        self.start_services()

        # Phase 3: Report
        self._log_startup(interval_minutes)
        self.log.info("  ONLINE  | AI Employee is running. Press Ctrl+C to stop.")
        self.log.info("")
        self.system_logger.info("lifecycle", "AI Employee online")

        # Phase 4: Initial sweep
        self.run_cycle(0)

        # Phase 5: Main loop
        try:
            while self._running:
                self.log.info(
                    "  IDLE    | Next cycle in %d min ...", interval_minutes,
                )
                stopped = self._stop_event.wait(timeout=interval_minutes * 60)
                if stopped or not self._running:
                    break
                self._cycle_count += 1
                self.run_cycle(self._cycle_count)

        except KeyboardInterrupt:
            pass

        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Gracefully stop all services and persist state."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

        self.log.info("")
        self.log.info("=" * 55)
        self.log.info("  SHUTDOWN")
        self.log.info("=" * 55)

        self.system_logger.info("lifecycle", "Shutdown initiated")

        # Stop services in reverse order
        self.log.info("  STOP    | MCP servers")
        self.mcp_manager.stop_all()

        self.log.info("  STOP    | Health monitor")
        self.health_monitor.stop()

        self.log.info("  STOP    | Inbox watcher")
        self.watcher.stop()

        self.log.info("  STOP    | Dashboard")
        self.dashboard.stop()

        # Persist state
        self.log.info("  SAVE    | Approval audit log")
        try:
            self.approval_manager.save_audit_log()
        except Exception as exc:
            self.log.error("  SAVE    | Audit log failed: %s", exc)

        # Summary
        uptime = ""
        if self._start_time:
            uptime = _format_duration(datetime.now() - self._start_time)

        self.log.info("")
        self.log.info(
            "  EXIT    | AI Employee stopped. Cycles: %d | Uptime: %s",
            self._cycle_count, uptime or "N/A",
        )
        self.system_logger.info(
            "lifecycle", "AI Employee stopped",
            {"cycles": self._cycle_count, "uptime": uptime},
        )
        self.audit_logger.log_system_event(
            AuditEvent.SYSTEM_SHUTDOWN, "AI Employee stopped",
            {"cycles": self._cycle_count, "uptime": uptime,
             "audit_entries": self.audit_logger.total_entries},
        )

    # ══════════════════════════════════════════════════════════════════
    #  PIPELINE PHASES
    # ══════════════════════════════════════════════════════════════════

    def run_cycle(self, cycle_num: int) -> None:
        """Run one full processing cycle across all pipeline stages."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log.info("=" * 55)
        self.log.info("  CYCLE %d | %s", cycle_num, timestamp)
        self.log.info("=" * 55)

        self.audit_logger.log_cycle(cycle_num, AuditEvent.CYCLE_STARTED)

        triaged = self._run_phase_tracked("task_agent", self.phase_triage)
        planned = self._run_phase_tracked("task_agent", self.phase_plan)
        executed = self._run_phase_tracked("task_agent", self.phase_execute)
        gmail_count = self._run_phase_tracked("gmail_agent", self.phase_gmail)
        linkedin_count = self._run_phase_tracked(
            "linkedin_agent", self.phase_linkedin,
        )
        odoo_count = self._run_phase_tracked(
            "odoo_agent", self.phase_odoo,
        )
        meta_count = self._run_phase_tracked(
            "meta_agent", self.phase_meta,
        )
        twitter_count = self._run_phase_tracked(
            "twitter_agent", self.phase_twitter,
        )
        audit_count = self._run_phase_tracked(
            "audit_agent", self.phase_audit,
        )
        approvals = self.phase_check_approvals()
        self.phase_stats()

        total_in = triaged + planned
        total_out = executed + gmail_count + linkedin_count + odoo_count + meta_count + twitter_count + audit_count
        if total_in == 0 and total_out == 0:
            self.log.info("  RESULT  | No new work. System is idle.")
        else:
            self.log.info(
                "  RESULT  | Triaged: %d | Planned: %d | Executed: %d "
                "| Gmail: %d | LinkedIn: %d | Odoo: %d | Meta: %d | Twitter: %d | Audit: %d",
                triaged, planned, executed, gmail_count, linkedin_count,
                odoo_count, meta_count, twitter_count, audit_count,
            )

        if approvals["pending"] > 0:
            self.log.info(
                "  RESULT  | Pending approvals: %d", approvals["pending"],
            )

        cycle_results = {
            "triaged": triaged, "planned": planned, "executed": executed,
            "gmail": gmail_count, "linkedin": linkedin_count,
            "odoo": odoo_count, "meta": meta_count, "twitter": twitter_count,
            "audit": audit_count,
            "approvals_pending": approvals["pending"],
        }
        self.system_logger.info(
            "cycle", f"Cycle {cycle_num} complete", cycle_results,
        )
        self.audit_logger.log_cycle(
            cycle_num, AuditEvent.CYCLE_COMPLETED, results=cycle_results,
        )
        self.log.info("")

    def _run_phase_tracked(self, service_name: str, phase_fn) -> int:
        """Run a pipeline phase with error recovery (retry + fallback)."""
        phase_name = phase_fn.__name__
        svc = self.status_aggregator.get(service_name)

        # Audit: agent called
        self.audit_logger.log_agent_called(
            agent=service_name, action=phase_name, phase=phase_name,
        )

        start_ms = time.perf_counter()
        try:
            result = phase_fn()
            duration_ms = int((time.perf_counter() - start_ms) * 1000)
            if svc:
                svc.record_success()
            self.error_handler.clear_consecutive(service_name)

            # Audit: agent result (success)
            self.audit_logger.log_agent_result(
                agent=service_name, status="completed",
                result={"count": result}, duration_ms=duration_ms,
            )
            return result

        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_ms) * 1000)

            # Audit: error occurred
            self.audit_logger.log_error(
                source=service_name, error=str(exc),
                error_type=type(exc).__name__, phase=phase_name, exc=exc,
            )

            # 1. Classify the error and decide recovery action
            handling = self.error_handler.handle(
                source=service_name,
                exc=exc,
                message=str(exc),
                context={"phase": phase_name},
            )

            # 2. Retry if eligible
            if handling.should_retry:
                self.log.info("  RETRY   | %s — attempting automatic retry", service_name)
                self.audit_logger.log_retry(
                    agent=service_name, attempt=1,
                    max_attempts=self.retry_manager.get_policy(service_name).max_attempts,
                    error=str(exc), task_id="",
                )
                retry_result = self.retry_manager.execute_with_retry(
                    agent_name=service_name,
                    fn=phase_fn,
                    context={"phase": phase_name},
                )
                if retry_result.success:
                    self.error_handler.mark_resolved(handling.error_record.error_id)
                    self.error_handler.clear_consecutive(service_name)
                    self.audit_logger.log_retry_success(
                        agent=service_name, attempt=retry_result.attempts,
                    )
                    return retry_result.result if isinstance(retry_result.result, int) else 0
                else:
                    self.audit_logger.log_retry_exhausted(
                        agent=service_name, attempts=retry_result.attempts,
                        last_error=retry_result.last_error,
                    )

            # 3. Fallback if available
            if handling.should_fallback and handling.fallback_agent:
                self.log.info(
                    "  FALLBACK| %s -> %s", service_name, handling.fallback_agent,
                )
                self.audit_logger.log_fallback(
                    primary=service_name, fallback=handling.fallback_agent,
                    reason=str(exc), success=False,
                )
                try:
                    fb_agent = self._agent_map.get(handling.fallback_agent)
                    if fb_agent and hasattr(fb_agent, "enabled") and fb_agent.enabled:
                        fb_result = phase_fn()
                        self.audit_logger.log_fallback(
                            primary=service_name, fallback=handling.fallback_agent,
                            reason="fallback_executed", success=True,
                        )
                except Exception:
                    pass  # fallback also failed, fall through

            # 4. Log final failure
            self.audit_logger.log_task_failed(
                task_id="", agent=service_name,
                error=str(exc), error_type=type(exc).__name__,
                duration_ms=duration_ms,
            )
            self.log.error(
                "  PHASE   | %s FAILED: %s (recovery=%s)",
                service_name, exc, handling.error_record.recovery_action.value,
            )
            return 0

    # ── Individual phases ─────────────────────────────────────────────

    def phase_triage(self) -> int:
        """Phase 1: Triage new inbox files into Needs_Action."""
        inbox_files = sorted(self.settings.inbox_dir.glob("*.md"))
        existing = (
            set(f.name for f in self.settings.needs_action_dir.iterdir())
            if self.settings.needs_action_dir.exists() else set()
        )
        count = 0
        for filepath in inbox_files:
            if f"Response_{filepath.name}" in existing:
                continue
            self.log.info("  TRIAGE  | %s", filepath.name)
            self.audit_logger.log_task_received(
                task_id=filepath.stem, source="inbox_watcher",
                title=filepath.name,
                content_preview=filepath.name,
            )
            try:
                result = process_file(str(filepath), self.settings.needs_action_dir)
                if result:
                    count += 1
            except Exception as exc:
                self.log.error("  TRIAGE  | FAILED %s: %s", filepath.name, exc)
                self.audit_logger.log_error(
                    source="triage", error=str(exc), phase="triage",
                    task_id=filepath.stem, exc=exc,
                )
        return count

    def phase_plan(self) -> int:
        """Phase 2: Analyze + decide + enqueue for all new tasks."""
        inbox_files = sorted(self.settings.inbox_dir.glob("*.md"))
        existing = (
            set(f.name for f in self.settings.needs_action_dir.iterdir())
            if self.settings.needs_action_dir.exists() else set()
        )
        count = 0
        for filepath in inbox_files:
            already = any(
                p.endswith(f"_{filepath.name}") and p.startswith("Plan_")
                for p in existing
            )
            if already:
                continue
            self.log.info("  PLAN    | %s", filepath.name)
            try:
                result = self.planner.create_plan(str(filepath))
                if result:
                    self.log.info("  PLAN    | -> %s", Path(result).name)
                    count += 1
            except Exception as exc:
                self.log.error("  PLAN    | FAILED %s: %s", filepath.name, exc)
        return count

    def phase_execute(self) -> int:
        """Phase 3: Run the scheduler — execute queued tasks."""
        results = self.planner.execute_pending()
        for r in results:
            icon = "[OK]" if r.status != "failed" else "[!!]"
            self.log.info(
                "  EXEC    | %s [%s] '%s' -> %s (%dms, %d retries)",
                icon, r.task_id, r.title, r.status,
                r.duration_ms, r.retries,
            )
            # Audit: tool used + result for each executed task
            self.audit_logger.log_tool_used(
                tool_name=r.agent, agent=r.agent, phase="execute",
                result=r.agent_result,
                success=(r.status != "failed"),
                duration_ms=int(r.duration_ms),
                error=r.error,
                task_id=r.task_id,
            )
            if r.status == "failed":
                self.audit_logger.log_task_failed(
                    task_id=r.task_id, agent=r.agent,
                    error=r.error, duration_ms=int(r.duration_ms),
                )
            else:
                self.audit_logger.log_task_completed(
                    task_id=r.task_id, agent=r.agent,
                    result=r.agent_result, duration_ms=int(r.duration_ms),
                )
        return len(results)

    def phase_gmail(self) -> int:
        """Phase 4: Process Gmail inbox — fetch, analyze, draft/send."""
        if not self.gmail_agent.enabled:
            return 0
        self.log.info("  GMAIL   | Processing inbox...")
        try:
            results = self.gmail_agent.process_inbox(max_emails=10)
            for r in results:
                action = r.get("action", "unknown")
                subject = r.get("subject", "")[:40]
                icon = {
                    "sent": "[>>]", "drafted": "[DR]",
                    "flagged": "[!!]", "ignored": "[--]",
                }.get(action, "[??]")
                self.log.info("  GMAIL   | %s %s — '%s'", icon, action, subject)
            return len(results)
        except Exception as exc:
            self.log.error("  GMAIL   | FAILED: %s", exc)
            return 0

    def phase_linkedin(self) -> int:
        """Phase 5: Process LinkedIn messages — fetch, analyze, reply/draft."""
        if not self.linkedin_agent.enabled:
            return 0
        self.log.info("  LINKEDIN| Processing messages...")
        try:
            results = self.linkedin_agent.process_messages(max_messages=10)
            for r in results:
                action = r.get("action", "unknown")
                sender = r.get("sender", "unknown")[:25]
                icon = {
                    "replied": "[>>]", "drafted": "[DR]",
                    "flagged": "[!!]", "ignored": "[--]",
                    "accepted_connection": "[+C]",
                    "flagged_connection": "[?C]",
                }.get(action, "[??]")
                self.log.info("  LINKEDIN| %s %s — %s", icon, action, sender)

            rates = self.linkedin_agent.get_rate_status()
            self.log.info(
                "  LINKEDIN| Rate limits — Msgs: %d/%d/hr | Conns: %d/%d/day",
                rates["messages"]["max_per_hour"] - rates["messages"]["hourly_remaining"],
                rates["messages"]["max_per_hour"],
                rates["connections"]["max_per_day"] - rates["connections"]["daily_remaining"],
                rates["connections"]["max_per_day"],
            )
            return len(results)
        except Exception as exc:
            self.log.error("  LINKEDIN| FAILED: %s", exc)
            return 0

    def phase_odoo(self) -> int:
        """Phase 6: Process Odoo accounting — check overdue, generate reports."""
        if not self.odoo_agent.enabled:
            return 0
        self.log.info("  ODOO    | Processing accounting...")
        try:
            results = self.odoo_agent.process_accounting(max_items=20)
            for r in results:
                action = r.get("action", "unknown")
                if action == "overdue_invoice":
                    self.log.info(
                        "  ODOO    | [!!] Overdue: %s — %s (%s %s, %d days)",
                        r.get("name", ""), r.get("partner", ""),
                        r.get("amount_due", 0), r.get("currency", ""),
                        r.get("days_overdue", 0),
                    )
                elif action == "financial_summary":
                    pl = r.get("data", {}).get("profit_loss", {})
                    self.log.info(
                        "  ODOO    | [$$] P&L: income=%s expense=%s net=%s",
                        pl.get("income_total", "N/A"),
                        pl.get("expense_total", "N/A"),
                        pl.get("net_profit", "N/A"),
                    )
            return len(results)
        except Exception as exc:
            self.log.error("  ODOO    | FAILED: %s", exc)
            return 0

    def phase_meta(self) -> int:
        """Phase 7: Process Meta social — fetch metrics, post content."""
        if not self.meta_agent.enabled:
            return 0
        self.log.info("  META    | Processing social media...")
        try:
            results = self.meta_agent.process_social(max_items=10)
            for r in results:
                action = r.get("action", "unknown")
                if action == "metrics_fetch":
                    fb = r.get("facebook", {})
                    ig = r.get("instagram", {})
                    fb_posts = len(fb.get("recent_posts", []))
                    ig_posts = len(ig.get("recent_media", []))
                    self.log.info(
                        "  META    | [OK] Metrics: FB %d posts, IG %d posts",
                        fb_posts, ig_posts,
                    )
                elif action == "weekly_summary":
                    combined = r.get("combined", {})
                    self.log.info(
                        "  META    | [OK] Weekly: %d posts, %d engagements",
                        combined.get("total_posts", 0),
                        combined.get("total_engagements", 0),
                    )
                elif action == "needs_approval":
                    self.log.info(
                        "  META    | [??] %s post needs approval",
                        r.get("platform", "unknown"),
                    )
            return len(results)
        except Exception as exc:
            self.log.error("  META    | FAILED: %s", exc)
            return 0

    def phase_twitter(self) -> int:
        """Phase 7b: Process Twitter/X — fetch mentions, generate summary."""
        if not self.twitter_agent.enabled:
            return 0
        self.log.info("  TWITTER | Processing social media...")
        try:
            results = self.twitter_agent.process_social(max_items=10)
            for r in results:
                action = r.get("action", "unknown")
                if action == "get_mentions":
                    self.log.info(
                        "  TWITTER | [OK] Mentions: %d fetched",
                        r.get("count", 0),
                    )
                elif action == "weekly_summary":
                    tw = r.get("twitter", {})
                    self.log.info(
                        "  TWITTER | [OK] Weekly: %d tweets, %d likes, %d retweets",
                        tw.get("total_tweets", 0),
                        tw.get("total_likes", 0),
                        tw.get("total_retweets", 0),
                    )
                elif action == "needs_approval":
                    self.log.info(
                        "  TWITTER | [??] Tweet needs approval",
                    )
            return len(results)
        except Exception as exc:
            self.log.error("  TWITTER | FAILED: %s", exc)
            return 0

    def phase_audit(self) -> int:
        """Phase: Generate weekly CEO briefing report."""
        if not self.audit_agent.enabled:
            return 0
        self.log.info("  AUDIT   | Generating weekly briefing...")
        try:
            path = self.audit_agent.generate_weekly_briefing()
            self.log.info("  AUDIT   | [OK] Briefing saved: %s", path.name)
            return 1
        except Exception as exc:
            self.log.error("  AUDIT   | FAILED: %s", exc)
            return 0

    def phase_check_approvals(self) -> dict:
        """Phase 8: Check for manager decisions + execute approved."""
        counts = {"approved": 0, "rejected": 0, "pending": 0, "expired": 0}

        file_results = self.approval_manager.check_file_approvals()
        for r in file_results:
            status = r.get("status", "")
            if status == "approved":
                counts["approved"] += 1
                self.log.info("  APPROVE | [%s] '%s' -> APPROVED",
                              r.get("request_id", ""), r.get("title", ""))
            elif status == "rejected":
                counts["rejected"] += 1
                self.log.info("  APPROVE | [%s] '%s' -> REJECTED",
                              r.get("request_id", ""), r.get("title", ""))

        scheduler_results = self.planner.check_approvals(self.settings.approval_dir)
        for r in scheduler_results:
            if r.status == "rejected":
                counts["rejected"] += 1
                self.log.info("  APPROVE | [%s] -> REJECTED", r.task_id)
            elif r.status == "awaiting_approval":
                counts["pending"] += 1
            else:
                counts["approved"] += 1
                self.log.info("  APPROVE | [%s] -> APPROVED + EXECUTED", r.task_id)

        expired = self.approval_manager.process_expiry()
        counts["expired"] = len(expired)
        for e in expired:
            self.log.info("  APPROVE | [%s] '%s' -> EXPIRED",
                          e.get("request_id", ""), e.get("title", ""))

        counts["pending"] += self.approval_queue.pending_count
        counts["pending"] += len(self.planner.queue.awaiting_approval())

        self.approval_manager.save_audit_log()
        return counts

    def phase_stats(self) -> None:
        """Phase 7: Log pipeline stats."""
        stages = [
            ("Inbox", self.settings.inbox_dir),
            ("Needs_Action", self.settings.needs_action_dir),
            ("Done", self.settings.done_dir),
            ("Approvals", self.settings.approval_dir),
        ]
        parts = []
        for name, path in stages:
            n = sum(1 for f in path.glob("*.md")) if path.exists() else 0
            parts.append(f"{name}: {n}")

        q = self.planner.queue_summary()
        parts.append(
            f"Queue: {q['total']} "
            f"({q.get('by_status', {}).get('pending', 0)} pending)"
        )
        self.log.info("  STATS   | %s", " | ".join(parts))

    # ── Watch mode callback ──────────────────────────────────────────

    def _on_new_file(self, filepath: str) -> None:
        """Called by InboxWatcher when a new .md file arrives."""
        filename = Path(filepath).name
        self.log.info("  DETECT  | New file: %s", filename)

        try:
            process_file(filepath, self.settings.needs_action_dir)
        except Exception as exc:
            self.log.error("  TRIAGE  | FAILED: %s", exc)

        try:
            result = self.planner.create_plan(filepath)
            if result:
                self.log.info("  PLAN    | -> %s", Path(result).name)
        except Exception as exc:
            self.log.error("  PLAN    | FAILED: %s", exc)

        executed = self.planner.execute_pending()
        for r in executed:
            self.log.info(
                "  EXEC    | [%s] '%s' -> %s", r.task_id, r.title, r.status,
            )

        self.phase_stats()


# ══════════════════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="AI Employee — Autonomous Digital Worker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "By default the AI Employee runs continuously with all services\n"
            "active (dashboard, watcher, health monitor). Use --once for a\n"
            "single cycle, or diagnostic flags for one-shot reports."
        ),
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single pipeline cycle and exit",
    )
    parser.add_argument(
        "-i", "--interval", type=int, default=None,
        help="Cycle interval in minutes (default: from .env or 5)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug-level logging",
    )
    # Diagnostic one-shot commands
    parser.add_argument("--health", action="store_true",
                        help="Print health report and exit")
    parser.add_argument("--stats", action="store_true",
                        help="Print analytics + scheduler report and exit")
    parser.add_argument("--queue", action="store_true",
                        help="Print task queue state and exit")
    parser.add_argument("--gmail", action="store_true",
                        help="Process Gmail inbox once and exit")
    parser.add_argument("--linkedin", action="store_true",
                        help="Process LinkedIn messages once and exit")
    parser.add_argument("--odoo", action="store_true",
                        help="Process Odoo accounting once and exit")
    parser.add_argument("--meta", action="store_true",
                        help="Process Meta (Facebook+Instagram) once and exit")
    parser.add_argument("--twitter", action="store_true",
                        help="Process Twitter/X once and exit")
    parser.add_argument("--audit", action="store_true",
                        help="Generate weekly CEO briefing and exit")
    parser.add_argument("--brief", action="store_true",
                        help="Generate CEO Executive Brief and exit")
    parser.add_argument("--mcp-status", action="store_true",
                        help="Show MCP server registry and status, then exit")
    parser.add_argument("--ralph", type=str, default=None, metavar="TASK",
                        help="Run Ralph Wiggum autonomous loop on a task and exit")
    # Service / run-mode flags
    parser.add_argument("--dashboard", action="store_true",
                        help="Launch the web dashboard only (no cycles)")
    parser.add_argument("--loop", action="store_true",
                        help="Run continuous cycles (combine with --gmail/--linkedin/--odoo)")
    parser.add_argument("--watch", action="store_true",
                        help="Watch vault/Inbox for new files and process them")
    parser.add_argument("--monitor", action="store_true",
                        help="Start health monitor and display status")
    args = parser.parse_args()

    # ── Load config + logging ────────────────────────────────────────
    settings = Settings.load()
    log = setup_logging(settings.log_dir, args.verbose)

    interval = args.interval if args.interval is not None else settings.cycle_interval_minutes

    print(BANNER)
    log.info("  Project : %s", settings.project_root)
    log.info("  Vault   : %s", settings.vault_dir)
    log.info("")

    # ── Build the system ─────────────────────────────────────────────
    employee = AIEmployee(settings, log)

    # ── Diagnostic commands (one-shot, no services started) ──────────

    if args.health:
        employee.boot()
        health = employee.health_check.run()
        print(employee.health_check.render_report(health))
        return

    if args.stats:
        employee.boot()
        print(employee.analytics.render_text_report())
        print()
        print(employee.planner.scheduler_report())
        return

    if args.queue:
        employee.boot()
        q = employee.planner.queue
        print("=" * 55)
        print("  Task Queue")
        print("=" * 55)
        summary = q.summary()
        print(f"\n  Total: {summary['total']}")
        for status, count in summary.get("by_status", {}).items():
            print(f"    {status:<22}: {count}")
        print()
        for task in q.all_tasks():
            icon = {
                "pending": "[..]", "running": "[>>]",
                "completed": "[OK]", "failed": "[!!]",
                "awaiting_approval": "[??]", "ignored": "[--]",
                "scheduled": "[~~]",
            }.get(task.status, "[  ]")
            sched = (
                f" (scheduled: {task.scheduled_for[:16]})"
                if task.scheduled_for else ""
            )
            print(
                f"  {icon} [{task.urgency:>8}] {task.title:<30} "
                f"-> {task.assigned_agent} | {task.action}{sched}"
            )
            if task.retries:
                print(f"         retries: {task.retries}/{task.max_retries}"
                      f" | last_error: {task.last_error}")
        print()
        print("=" * 55)
        return

    if args.gmail:
        employee.boot()
        log.info("  MODE    | Gmail inbox processing")
        log.info("")
        if not employee.gmail_agent.enabled:
            log.error("  GMAIL   | Agent disabled — credentials.json not found")
            return
        count = employee.phase_gmail()
        log.info("  GMAIL   | Processed %d emails", count)
        return

    if args.linkedin:
        employee.boot()
        log.info("  MODE    | LinkedIn message processing")
        log.info("")
        if not employee.linkedin_agent.enabled:
            log.error("  LINKEDIN| Agent disabled — credentials not set")
            return
        count = employee.phase_linkedin()
        log.info("  LINKEDIN| Processed %d items", count)
        return

    if args.odoo:
        employee.boot()
        log.info("  MODE    | Odoo accounting processing")
        log.info("")
        if not employee.odoo_agent.enabled:
            log.error("  ODOO    | Agent disabled — ODOO_URL/ODOO_PASSWORD not set")
            return
        count = employee.phase_odoo()
        log.info("  ODOO    | Processed %d items", count)
        return

    if args.meta:
        employee.boot()
        log.info("  MODE    | Meta social media processing")
        log.info("")
        if not employee.meta_agent.enabled:
            log.error("  META    | Agent disabled — META_ACCESS_TOKEN not set")
            return
        count = employee.phase_meta()
        log.info("  META    | Processed %d items", count)
        return

    if args.twitter:
        employee.boot()
        log.info("  MODE    | Twitter/X social media processing")
        log.info("")
        if not employee.twitter_agent.enabled:
            log.error("  TWITTER | Agent disabled — TWITTER_BEARER_TOKEN not set")
            return
        count = employee.phase_twitter()
        log.info("  TWITTER | Processed %d items", count)
        return

    if args.audit:
        employee.boot()
        log.info("  MODE    | Weekly audit briefing generation")
        log.info("")
        count = employee.phase_audit()
        log.info("  AUDIT   | Generated %d briefing(s)", count)
        return

    if args.brief:
        employee.boot()
        log.info("  MODE    | CEO Executive Brief generation")
        log.info("")
        try:
            path = employee.brief_generator.generate_brief()
            log.info("  BRIEF   | [OK] Executive Brief saved: %s", path.name)
        except Exception as exc:
            log.error("  BRIEF   | FAILED: %s", exc)
        return

    if args.mcp_status:
        employee.boot()
        log.info("  MODE    | MCP Server Status")
        log.info("")

        # Discover tools
        tool_count = employee.tool_registry.discover_all()
        summary = employee.tool_registry.summary()

        print("=" * 55)
        print("  MCP Server Architecture — Status")
        print("=" * 55)
        print()
        print(f"  Total tools: {summary['total_tools']}")
        print(f"  Total servers: {summary['total_servers']}")
        print()

        # Per-server breakdown
        for server_info in summary.get("servers", []):
            icon = "[OK]" if server_info["status"] != "error" else "[!!]"
            print(f"  {icon} {server_info['name']:<25} "
                  f"category={server_info['category']:<15} "
                  f"tools={server_info['tool_count']}")

        print()

        # Per-category breakdown
        print("  By category:")
        for cat, count in summary.get("by_category", {}).items():
            print(f"    {cat:<20}: {count} tools")

        print()

        # List all tools
        print("  All registered tools:")
        for tool in employee.tool_registry.list_tools():
            print(f"    {tool.server_name:<20} {tool.name}")

        print()
        print("=" * 55)
        return

    if args.ralph:
        employee.boot()
        log.info("  MODE    | Ralph Wiggum Autonomous Loop (Platinum)")
        log.info("  TASK    | %s", args.ralph)
        log.info("")
        result = employee.loop_controller.run(args.ralph)
        print("=" * 55)
        print("  Ralph Wiggum Autonomous Loop — Result")
        print("=" * 55)
        print()
        print(f"  Status      : {result.status}")
        print(f"  Iterations  : {result.iterations}")
        print(f"  Duration    : {result.total_duration_ms}ms")
        print(f"  Fixes       : {result.fixes_succeeded}/{result.fixes_attempted}")
        if result.error:
            print(f"  Error       : {result.error}")
        if result.approval_request_id:
            print(f"  Approval    : {result.approval_request_id}")
        if result.loop_result:
            lr = result.loop_result
            print(f"  Steps       : {lr.get('completed_steps', 0)}/{lr.get('total_steps', 0)} completed")
            print(f"  Failed      : {lr.get('failed_steps', 0)}")
            print(f"  Termination : {lr.get('termination_reason', 'N/A')}")
        print()
        print("  Full JSON log:")
        print(result.to_json(indent=2))
        print()
        print("=" * 55)
        return

    # ── Dashboard-only mode ──────────────────────────────────────────

    if args.dashboard and not args.loop:
        employee.boot()
        log.info("  MODE    | Dashboard only")
        employee.dashboard.start()
        log.info("  SERVICE | Dashboard running at %s", employee.dashboard.url)
        log.info("  INFO    | Press Ctrl+C to stop.")
        if args.watch:
            employee.watcher.start()
            log.info("  SERVICE | Watcher started      %s", settings.inbox_dir)
        if args.monitor:
            employee.health_monitor.start()
            log.info("  SERVICE | Health monitor started")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if args.monitor:
                employee.health_monitor.stop()
            if args.watch:
                employee.watcher.stop()
            employee.dashboard.stop()
            log.info("  EXIT    | Dashboard stopped.")
        return

    # ── Loop mode with specific agents ───────────────────────────────

    if args.loop:
        employee.boot()
        agent_flags = []
        if args.gmail:
            agent_flags.append("gmail")
        if args.linkedin:
            agent_flags.append("linkedin")
        if args.odoo:
            agent_flags.append("odoo")
        if args.meta:
            agent_flags.append("meta")
        if args.twitter:
            agent_flags.append("twitter")
        if args.audit:
            agent_flags.append("audit")

        if args.dashboard:
            employee.dashboard.start()
            log.info("  SERVICE | Dashboard running at %s", employee.dashboard.url)
        if args.watch:
            employee.watcher.start()
            log.info("  SERVICE | Watcher started      %s", settings.inbox_dir)
        if args.monitor:
            employee.health_monitor.start()
            log.info("  SERVICE | Health monitor started")

        if not agent_flags:
            # No specific agents — run full continuous mode
            employee.run(interval_minutes=interval)
            return

        log.info("  MODE    | Looping agents: %s (every %d min)", ", ".join(agent_flags), interval)
        log.info("  INFO    | Press Ctrl+C to stop.")
        log.info("")

        cycle = 0
        try:
            while True:
                cycle += 1
                log.info("  CYCLE %d | %s", cycle, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                if "gmail" in agent_flags:
                    employee._run_phase_tracked("gmail_agent", employee.phase_gmail)
                if "linkedin" in agent_flags:
                    employee._run_phase_tracked("linkedin_agent", employee.phase_linkedin)
                if "odoo" in agent_flags:
                    employee._run_phase_tracked("odoo_agent", employee.phase_odoo)
                if "meta" in agent_flags:
                    employee._run_phase_tracked("meta_agent", employee.phase_meta)
                if "twitter" in agent_flags:
                    employee._run_phase_tracked("twitter_agent", employee.phase_twitter)
                if "audit" in agent_flags:
                    employee._run_phase_tracked("audit_agent", employee.phase_audit)
                employee.phase_stats()
                log.info("  IDLE    | Next cycle in %d min ...", interval)
                log.info("")
                time.sleep(interval * 60)
        except KeyboardInterrupt:
            pass
        finally:
            if args.monitor:
                employee.health_monitor.stop()
            if args.watch:
                employee.watcher.stop()
            if args.dashboard:
                employee.dashboard.stop()
            log.info("  EXIT    | Stopped after %d cycles.", cycle)
        return

    # ── Watch-only mode ──────────────────────────────────────────────

    if args.watch and not args.loop:
        employee.boot()
        log.info("  MODE    | Watch mode — monitoring %s", settings.inbox_dir)
        employee.watcher.start()
        if args.monitor:
            employee.health_monitor.start()
        log.info("  INFO    | Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if args.monitor:
                employee.health_monitor.stop()
            employee.watcher.stop()
            log.info("  EXIT    | Watcher stopped.")
        return

    # ── Monitor-only mode ────────────────────────────────────────────

    if args.monitor and not args.loop and not args.watch and not args.dashboard:
        employee.boot()
        log.info("  MODE    | Health monitor")
        employee.health_monitor.start()
        log.info("  INFO    | Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(5)
                report = employee.health_check.run()
                print(employee.health_check.render_report(report))
        except KeyboardInterrupt:
            pass
        finally:
            employee.health_monitor.stop()
            log.info("  EXIT    | Monitor stopped.")
        return

    # ── Run modes ────────────────────────────────────────────────────

    if args.once:
        # Single cycle, no background services
        log.info("  MODE    | Single cycle")
        log.info("")
        employee.boot()
        employee.run_cycle(1)
        log.info("  EXIT    | Done.")
    else:
        # Default: continuous autonomous operation
        employee.run(interval_minutes=interval)


if __name__ == "__main__":
    main()
