"""
AI Employee — Cloud Watchers (Platinum Tier)

Six always-on watchers that run 24/7 on the Cloud VM, each in its own thread
with independent polling intervals, circuit breakers, and health reporting.

Watchers:
    1. Gmail       — polls inbox for unread emails          (every 2 min)
    2. WhatsApp    — polls Meta WhatsApp Business Cloud API  (every 1 min)
    3. LinkedIn    — polls messages + connection requests    (every 5 min)
    4. Twitter     — polls mentions + timeline               (every 3 min)
    5. Instagram   — polls media metrics + comments          (every 5 min)
    6. Odoo        — polls overdue invoices + financials     (every 10 min)

Each watcher:
    - Runs in a daemon thread with its own sleep interval
    - Has its own CircuitBreaker (trips after 3 failures, recovers after 60s)
    - Writes results to vault/ for git sync to local machine
    - Reports health via get_status() for the /health endpoint
    - Can be individually enabled/disabled via environment variables

Usage:
    # Start all watchers
    python -m ai_employee.cloud_watchers

    # Start specific watchers
    python -m ai_employee.cloud_watchers --gmail --whatsapp --odoo

    # Health check only
    python -m ai_employee.cloud_watchers --health

Environment:
    WATCHER_GMAIL_INTERVAL=120          seconds between Gmail polls
    WATCHER_WHATSAPP_INTERVAL=60        seconds between WhatsApp polls
    WATCHER_LINKEDIN_INTERVAL=300       seconds between LinkedIn polls
    WATCHER_TWITTER_INTERVAL=180        seconds between Twitter polls
    WATCHER_INSTAGRAM_INTERVAL=300      seconds between Instagram polls
    WATCHER_ODOO_INTERVAL=600           seconds between Odoo polls
    WHATSAPP_TOKEN=...                  WhatsApp Business API token
    WHATSAPP_PHONE_NUMBER_ID=...        WhatsApp sender phone number ID
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# ── Project imports ──────────────────────────────────────────────────────────

from ai_employee.config.settings import Settings
from ai_employee.monitoring.service_status import CircuitBreaker, CircuitState

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] %(levelname)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("cloud_watchers")


# ── Watcher result ───────────────────────────────────────────────────────────

@dataclass
class WatcherResult:
    """Outcome of a single watcher poll cycle."""
    watcher: str
    success: bool
    items_found: int = 0
    items_processed: int = 0
    error: str = ""
    duration_ms: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    details: list[dict] = field(default_factory=list)


@dataclass
class WatcherHealth:
    """Health snapshot for a single watcher."""
    name: str
    enabled: bool
    running: bool
    circuit_state: str
    last_poll: str
    last_success: str
    last_error: str
    consecutive_failures: int
    total_polls: int
    total_successes: int
    total_failures: int
    interval_seconds: int
    uptime_seconds: float


# ── Base Watcher ─────────────────────────────────────────────────────────────

class BaseWatcher:
    """
    Abstract base for all cloud watchers.

    Subclasses implement poll() — the actual work of fetching data from an
    external service and writing results to the vault.
    """

    name: str = "base"
    default_interval: int = 300  # seconds

    def __init__(
        self,
        settings: Settings,
        interval: int | None = None,
        enabled: bool = True,
    ):
        self.settings = settings
        self.interval = interval or self.default_interval
        self.enabled = enabled

        # State
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._started_at: float = 0.0
        self._last_poll: str = ""
        self._last_success: str = ""
        self._last_error: str = ""
        self._consecutive_failures: int = 0
        self._total_polls: int = 0
        self._total_successes: int = 0
        self._total_failures: int = 0

        # Circuit breaker: trip after 3 failures, recover after 60s
        self._breaker = CircuitBreaker(
            failure_threshold=settings.circuit_breaker_threshold,
            recovery_timeout=settings.circuit_breaker_timeout,
        )

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the watcher daemon thread."""
        if not self.enabled:
            log.info("[%s] Disabled — skipping", self.name)
            return
        if self._thread and self._thread.is_alive():
            log.warning("[%s] Already running", self.name)
            return

        self._stop.clear()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, name=f"watcher-{self.name}", daemon=True,
        )
        self._thread.start()
        log.info("[%s] Started (interval=%ds)", self.name, self.interval)

    def stop(self) -> None:
        """Signal the watcher to stop."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
            log.info("[%s] Stopped", self.name)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Main loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        """Background loop: poll → sleep → repeat."""
        # Small initial stagger to avoid all watchers hitting APIs at once
        import random
        self._stop.wait(random.uniform(0.5, 3.0))

        while not self._stop.is_set():
            self._run_once()
            self._stop.wait(self.interval)

    def _run_once(self) -> WatcherResult:
        """Execute a single poll cycle with circuit breaker protection."""
        self._total_polls += 1
        self._last_poll = datetime.now(timezone.utc).isoformat()

        # Check circuit breaker
        if not self._breaker.can_proceed():
            msg = f"Circuit OPEN — skipping poll (will retry after {self._breaker.recovery_timeout}s)"
            log.warning("[%s] %s", self.name, msg)
            return WatcherResult(
                watcher=self.name, success=False, error=msg,
            )

        start = time.monotonic()
        try:
            result = self.poll()
            elapsed = (time.monotonic() - start) * 1000
            result.duration_ms = elapsed

            if result.success:
                self._breaker.record_success()
                self._consecutive_failures = 0
                self._total_successes += 1
                self._last_success = result.timestamp
                log.info(
                    "[%s] Poll OK — %d found, %d processed (%.0fms)",
                    self.name, result.items_found,
                    result.items_processed, elapsed,
                )
            else:
                self._breaker.record_failure()
                self._consecutive_failures += 1
                self._total_failures += 1
                self._last_error = result.error
                log.warning(
                    "[%s] Poll FAIL — %s (%.0fms)",
                    self.name, result.error, elapsed,
                )

            # Write result to vault for audit trail
            self._write_result(result)
            return result

        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            self._breaker.record_failure()
            self._consecutive_failures += 1
            self._total_failures += 1
            self._last_error = str(exc)
            log.error("[%s] Poll EXCEPTION — %s (%.0fms)", self.name, exc, elapsed)
            result = WatcherResult(
                watcher=self.name, success=False,
                error=str(exc), duration_ms=elapsed,
            )
            self._write_result(result)
            return result

    # ── Abstract ─────────────────────────────────────────────────────────

    def poll(self) -> WatcherResult:
        """
        Override in subclass.  Fetch data from external service,
        process it, write outputs to vault.  Return a WatcherResult.
        """
        raise NotImplementedError

    # ── Health ───────────────────────────────────────────────────────────

    def get_health(self) -> WatcherHealth:
        return WatcherHealth(
            name=self.name,
            enabled=self.enabled,
            running=self.running,
            circuit_state=self._breaker.state.value,
            last_poll=self._last_poll,
            last_success=self._last_success,
            last_error=self._last_error,
            consecutive_failures=self._consecutive_failures,
            total_polls=self._total_polls,
            total_successes=self._total_successes,
            total_failures=self._total_failures,
            interval_seconds=self.interval,
            uptime_seconds=(
                time.monotonic() - self._started_at if self._started_at else 0
            ),
        )

    # ── Vault output ─────────────────────────────────────────────────────

    def _write_result(self, result: WatcherResult) -> None:
        """Append watcher result to NDJSON log."""
        log_dir = self.settings.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "watcher_results.json"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(result), default=str) + "\n")
        except OSError:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  WATCHER IMPLEMENTATIONS
# ═════════════════════════════════════════════════════════════════════════════


class GmailWatcher(BaseWatcher):
    """Polls Gmail for unread emails, triages them into the vault."""

    name = "gmail"
    default_interval = 120  # 2 minutes

    def __init__(self, settings: Settings, **kw: Any):
        has_creds = settings.gmail_credentials_path.exists()
        super().__init__(settings, enabled=has_creds, **kw)
        self._agent = None

    def _ensure_agent(self):
        if self._agent is None:
            from ai_employee.agents.gmail_agent import GmailAgent
            self._agent = GmailAgent(self.settings)

    def poll(self) -> WatcherResult:
        self._ensure_agent()
        if not self._agent.enabled:
            return WatcherResult(
                watcher=self.name, success=True,
                error="Gmail agent not configured",
            )
        results = self._agent.process_inbox(max_emails=10)
        actions = [r.get("action", "unknown") for r in results]
        return WatcherResult(
            watcher=self.name,
            success=True,
            items_found=len(results),
            items_processed=sum(1 for a in actions if a in ("sent", "drafted", "flagged")),
            details=results,
        )


class WhatsAppWatcher(BaseWatcher):
    """
    Polls Meta WhatsApp Business Cloud API for incoming messages.

    Uses the WhatsApp Business Platform (Cloud API) to:
      1. Fetch new messages via GET /v21.0/{phone_number_id}/messages
      2. Write incoming messages to vault/Inbox/ as .md files
      3. Track processed message IDs to prevent duplicates

    Required environment variables:
        WHATSAPP_TOKEN             — System User access token
        WHATSAPP_PHONE_NUMBER_ID   — Business phone number ID
    """

    name = "whatsapp"
    default_interval = 60  # 1 minute

    GRAPH_BASE = "https://graph.facebook.com/v21.0"

    def __init__(self, settings: Settings, **kw: Any):
        self._token = os.getenv("WHATSAPP_TOKEN", "")
        self._phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        super().__init__(settings, enabled=bool(self._token and self._phone_id), **kw)
        self._processed_ids: set[str] = set()
        self._processed_path = settings.vault_dir / "whatsapp_processed_ids.json"
        self._load_processed()

    def _load_processed(self) -> None:
        if self._processed_path.exists():
            try:
                data = json.loads(self._processed_path.read_text("utf-8"))
                self._processed_ids = set(data.get("processed_ids", []))
            except (json.JSONDecodeError, OSError):
                self._processed_ids = set()

    def _save_processed(self) -> None:
        try:
            self._processed_path.parent.mkdir(parents=True, exist_ok=True)
            self._processed_path.write_text(json.dumps({
                "processed_ids": sorted(self._processed_ids),
                "count": len(self._processed_ids),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        import requests
        params = params or {}
        url = f"{self.GRAPH_BASE}/{endpoint}"
        headers = {"Authorization": f"Bearer {self._token}"}
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        return resp.json()

    def _send_message(self, to: str, body: str) -> dict:
        """Send a text reply via WhatsApp Cloud API."""
        import requests
        url = f"{self.GRAPH_BASE}/{self._phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        return resp.json()

    def poll(self) -> WatcherResult:
        """
        Poll for new WhatsApp messages.

        NOTE: The WhatsApp Cloud API uses webhooks as its primary delivery
        mechanism for incoming messages.  This poller checks the business
        profile and any webhook-buffered messages that were stored locally
        by the webhook receiver.  For a full production deployment, pair
        this watcher with a Flask/FastAPI webhook endpoint that writes
        incoming messages to vault/Inbox/.
        """
        # Check for locally-buffered webhook messages (from a companion endpoint)
        wa_inbox = self.settings.vault_dir / "whatsapp_inbox"
        wa_inbox.mkdir(parents=True, exist_ok=True)

        new_messages: list[dict] = []

        # Process any JSON files dropped by the webhook receiver
        for msg_file in sorted(wa_inbox.glob("*.json")):
            try:
                msg = json.loads(msg_file.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            msg_id = msg.get("id", msg_file.stem)
            if msg_id in self._processed_ids:
                continue

            # Write to vault/Inbox as a task
            self._write_inbox_task(msg)
            self._processed_ids.add(msg_id)
            new_messages.append(msg)

            # Archive the raw message
            done_dir = wa_inbox / "processed"
            done_dir.mkdir(exist_ok=True)
            msg_file.rename(done_dir / msg_file.name)

        # Also check the WhatsApp Business Profile health (validates creds)
        profile_ok = True
        try:
            profile = self._get(self._phone_id, {
                "fields": "verified_name,quality_rating,messaging_limit_tier",
            })
            if "error" in profile:
                profile_ok = False
        except Exception:
            profile_ok = False

        if new_messages:
            self._save_processed()

        return WatcherResult(
            watcher=self.name,
            success=profile_ok,
            items_found=len(new_messages),
            items_processed=len(new_messages),
            error="" if profile_ok else "WhatsApp API health check failed",
            details=new_messages,
        )

    def _write_inbox_task(self, msg: dict) -> None:
        """Convert a WhatsApp message into a vault/Inbox/ markdown task."""
        sender = msg.get("from", "unknown")
        text = msg.get("text", {}).get("body", msg.get("body", ""))
        ts = msg.get("timestamp", datetime.now(timezone.utc).isoformat())
        msg_id = msg.get("id", "unknown")

        filename = f"whatsapp_{sender}_{msg_id[:12]}.md"
        filepath = self.settings.inbox_dir / filename

        content = (
            f"# WhatsApp Message\n\n"
            f"| Field   | Value |\n"
            f"|---------|-------|\n"
            f"| From    | {sender} |\n"
            f"| Time    | {ts} |\n"
            f"| Channel | WhatsApp |\n"
            f"| Msg ID  | `{msg_id}` |\n\n"
            f"---\n\n"
            f"## Message\n\n"
            f"{text}\n"
        )

        filepath.write_text(content, encoding="utf-8")
        log.info("[whatsapp] Inbox task created: %s", filename)


class LinkedInWatcher(BaseWatcher):
    """Polls LinkedIn for new messages and connection requests."""

    name = "linkedin"
    default_interval = 300  # 5 minutes

    def __init__(self, settings: Settings, **kw: Any):
        has_creds = bool(settings.linkedin_email and settings.linkedin_password)
        super().__init__(settings, enabled=has_creds, **kw)
        self._agent = None

    def _ensure_agent(self):
        if self._agent is None:
            from ai_employee.agents.linkedin_agent import LinkedInAgent
            self._agent = LinkedInAgent(self.settings)

    def poll(self) -> WatcherResult:
        self._ensure_agent()
        if not self._agent.enabled:
            return WatcherResult(
                watcher=self.name, success=True,
                error="LinkedIn agent not configured",
            )
        results = self._agent.process_messages(max_messages=10)
        actions = [r.get("action", "unknown") for r in results]
        return WatcherResult(
            watcher=self.name,
            success=True,
            items_found=len(results),
            items_processed=sum(
                1 for a in actions
                if a in ("replied", "drafted", "flagged", "accepted_connection")
            ),
            details=results,
        )


class TwitterWatcher(BaseWatcher):
    """Polls Twitter for mentions and engagement metrics."""

    name = "twitter"
    default_interval = 180  # 3 minutes

    def __init__(self, settings: Settings, **kw: Any):
        has_creds = bool(settings.twitter_bearer_token)
        super().__init__(settings, enabled=has_creds, **kw)
        self._agent = None

    def _ensure_agent(self):
        if self._agent is None:
            from ai_employee.agents.twitter_agent import TwitterAgent
            self._agent = TwitterAgent(self.settings)

    def poll(self) -> WatcherResult:
        self._ensure_agent()
        if not self._agent.enabled:
            return WatcherResult(
                watcher=self.name, success=True,
                error="Twitter agent not configured",
            )
        results = self._agent.process_social(max_items=10)
        return WatcherResult(
            watcher=self.name,
            success=True,
            items_found=len(results),
            items_processed=len(results),
            details=results,
        )


class InstagramWatcher(BaseWatcher):
    """
    Polls Instagram Business account for new media, comments, and metrics.

    Uses MetaClient under the hood but focuses solely on Instagram —
    the Facebook portion is not polled by this watcher.
    """

    name = "instagram"
    default_interval = 300  # 5 minutes

    def __init__(self, settings: Settings, **kw: Any):
        has_creds = bool(settings.meta_access_token and settings.meta_ig_user_id)
        super().__init__(settings, enabled=has_creds, **kw)
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            from ai_employee.integrations.meta_client import MetaClient
            self._client = MetaClient(
                access_token=self.settings.meta_access_token,
                page_id=self.settings.meta_page_id,
                ig_user_id=self.settings.meta_ig_user_id,
            )

    def poll(self) -> WatcherResult:
        self._ensure_client()
        if not self.settings.meta_ig_user_id:
            return WatcherResult(
                watcher=self.name, success=True,
                error="META_IG_USER_ID not configured",
            )

        # Fetch recent media
        media = self._client._ig_recent_media(limit=20)

        # Fetch account insights
        insights = self._client._ig_user_insights(
            metrics=["impressions", "reach", "profile_views"],
            period="day", days=1,
        )

        # Check for new comments on recent posts (engagement trigger)
        new_comments = 0
        for item in media[:5]:  # Top 5 recent posts
            new_comments += item.get("comments", 0)

        # Write metrics snapshot to vault for dashboard
        self._write_metrics_snapshot(media, insights)

        return WatcherResult(
            watcher=self.name,
            success=True,
            items_found=len(media),
            items_processed=len(media),
            details=[{
                "action": "ig_metrics_poll",
                "media_count": len(media),
                "recent_comments": new_comments,
                "insights_available": "error" not in insights,
            }],
        )

    def _write_metrics_snapshot(self, media: list, insights: dict) -> None:
        """Write latest IG metrics to vault/Reports/ for dashboard."""
        reports_dir = self.settings.briefing_dir
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / "ig_metrics_latest.json"
        try:
            path.write_text(json.dumps({
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "media_count": len(media),
                "recent_media": media[:10],
                "insights": insights.get("data", []),
            }, indent=2, default=str), encoding="utf-8")
        except OSError:
            pass


class OdooWatcher(BaseWatcher):
    """Polls Odoo ERP for overdue invoices, financial summaries."""

    name = "odoo"
    default_interval = 600  # 10 minutes

    def __init__(self, settings: Settings, **kw: Any):
        has_creds = bool(settings.odoo_url and settings.odoo_password)
        super().__init__(settings, enabled=has_creds, **kw)
        self._agent = None

    def _ensure_agent(self):
        if self._agent is None:
            from ai_employee.agents.odoo_agent import OdooAgent
            self._agent = OdooAgent(self.settings)

    def poll(self) -> WatcherResult:
        self._ensure_agent()
        if not self._agent.enabled:
            return WatcherResult(
                watcher=self.name, success=True,
                error="Odoo agent not configured",
            )
        results = self._agent.process_accounting(max_items=20)

        overdue = [r for r in results if r.get("action") == "overdue_invoice"]
        summaries = [r for r in results if r.get("action") == "financial_summary"]

        # Write overdue alerts to vault for approval pipeline
        for inv in overdue:
            self._write_overdue_alert(inv)

        return WatcherResult(
            watcher=self.name,
            success=True,
            items_found=len(results),
            items_processed=len(overdue) + len(summaries),
            details=results,
        )

    def _write_overdue_alert(self, invoice: dict) -> None:
        """Write overdue invoice alert to vault/Needs_Action/."""
        name = invoice.get("name", "unknown")
        partner = invoice.get("partner", "unknown")
        amount = invoice.get("amount_due", 0)
        currency = invoice.get("currency", "USD")
        days = invoice.get("days_overdue", 0)

        safe_name = name.replace("/", "_").replace("\\", "_")
        filename = f"Overdue_{safe_name}.md"
        filepath = self.settings.needs_action_dir / filename

        if filepath.exists():
            return  # Already alerted

        content = (
            f"# Overdue Invoice Alert\n\n"
            f"| Field       | Value |\n"
            f"|-------------|-------|\n"
            f"| Invoice     | {name} |\n"
            f"| Partner     | {partner} |\n"
            f"| Amount Due  | {amount} {currency} |\n"
            f"| Days Overdue| {days} |\n"
            f"| Source      | Odoo ERP |\n"
            f"| Detected    | {datetime.now(timezone.utc).isoformat()} |\n\n"
            f"---\n\n"
            f"## Action Required\n\n"
            f"Follow up with {partner} on payment for invoice {name}.\n"
        )
        filepath.write_text(content, encoding="utf-8")
        log.info("[odoo] Overdue alert written: %s", filename)


# ═════════════════════════════════════════════════════════════════════════════
#  WATCHER MANAGER
# ═════════════════════════════════════════════════════════════════════════════

class CloudWatcherManager:
    """
    Manages all six watchers: start, stop, health reporting.

    The manager itself is NOT a thread — it coordinates the watcher threads
    and provides a unified interface for the process supervisor.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._stop = threading.Event()

        # Read per-watcher intervals from environment
        def _interval(name: str, default: int) -> int:
            return int(os.getenv(f"WATCHER_{name.upper()}_INTERVAL", str(default)))

        # Instantiate all watchers
        self.watchers: dict[str, BaseWatcher] = {
            "gmail": GmailWatcher(
                settings, interval=_interval("gmail", 120),
            ),
            "whatsapp": WhatsAppWatcher(
                settings, interval=_interval("whatsapp", 60),
            ),
            "linkedin": LinkedInWatcher(
                settings, interval=_interval("linkedin", 300),
            ),
            "twitter": TwitterWatcher(
                settings, interval=_interval("twitter", 180),
            ),
            "instagram": InstagramWatcher(
                settings, interval=_interval("instagram", 300),
            ),
            "odoo": OdooWatcher(
                settings, interval=_interval("odoo", 600),
            ),
        }

    def start(self, only: list[str] | None = None) -> None:
        """
        Start watchers.  If `only` is provided, start only those watchers.
        Otherwise start all enabled watchers.
        """
        targets = only or list(self.watchers.keys())
        started = []

        for name in targets:
            watcher = self.watchers.get(name)
            if not watcher:
                log.warning("Unknown watcher: %s", name)
                continue
            watcher.start()
            if watcher.running:
                started.append(name)

        log.info(
            "CloudWatcherManager started %d/%d watchers: %s",
            len(started), len(targets), ", ".join(started) or "(none)",
        )

    def stop(self) -> None:
        """Stop all running watchers."""
        self._stop.set()
        for watcher in self.watchers.values():
            watcher.stop()
        log.info("All watchers stopped")

    def wait(self) -> None:
        """Block until stop signal received (for main thread)."""
        try:
            self._stop.wait()
        except KeyboardInterrupt:
            log.info("Interrupted — shutting down")
            self.stop()

    def get_health(self) -> dict:
        """Return health snapshot for all watchers."""
        watchers_health = {}
        overall_healthy = True

        for name, watcher in self.watchers.items():
            health = watcher.get_health()
            watchers_health[name] = asdict(health)
            if health.enabled and not health.running:
                overall_healthy = False
            if health.circuit_state == "open":
                overall_healthy = False

        return {
            "healthy": overall_healthy,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "watchers": watchers_health,
            "summary": {
                "total": len(self.watchers),
                "enabled": sum(
                    1 for w in self.watchers.values() if w.enabled
                ),
                "running": sum(
                    1 for w in self.watchers.values() if w.running
                ),
                "circuit_open": sum(
                    1 for w in self.watchers.values()
                    if w._breaker.state == CircuitState.OPEN
                ),
            },
        }

    def print_status(self) -> None:
        """Print a human-readable status table."""
        health = self.get_health()
        print(f"\nCloud Watchers — {'HEALTHY' if health['healthy'] else 'DEGRADED'}")
        print("=" * 72)
        print(f"{'Watcher':<12} {'Enabled':<9} {'Running':<9} {'Circuit':<11} "
              f"{'Polls':<7} {'Fails':<7} {'Interval'}")
        print("-" * 72)

        for name, wh in health["watchers"].items():
            print(
                f"{wh['name']:<12} "
                f"{'yes' if wh['enabled'] else 'no':<9} "
                f"{'yes' if wh['running'] else 'no':<9} "
                f"{wh['circuit_state']:<11} "
                f"{wh['total_polls']:<7} "
                f"{wh['total_failures']:<7} "
                f"{wh['interval_seconds']}s"
            )

        s = health["summary"]
        print("-" * 72)
        print(f"Total: {s['total']} | Enabled: {s['enabled']} | "
              f"Running: {s['running']} | Circuit Open: {s['circuit_open']}")


# ═════════════════════════════════════════════════════════════════════════════
#  HEALTH HTTP ENDPOINT
# ═════════════════════════════════════════════════════════════════════════════

def start_health_server(manager: CloudWatcherManager, port: int = 9090) -> None:
    """
    Start a minimal HTTP health endpoint for monitoring.

    GET /health  → JSON health snapshot
    GET /status  → Human-readable text
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                data = manager.get_health()
                status = 200 if data["healthy"] else 503
                body = json.dumps(data, indent=2, default=str).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/status":
                data = manager.get_health()
                lines = [f"Cloud Watchers: {'HEALTHY' if data['healthy'] else 'DEGRADED'}"]
                for name, wh in data["watchers"].items():
                    state = "UP" if wh["running"] else "DOWN"
                    lines.append(f"  {name}: {state} ({wh['circuit_state']})")
                body = "\n".join(lines).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def log_message(self, fmt, *args):
            pass  # Suppress access logs

    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(
        target=server.serve_forever, name="health-http", daemon=True,
    )
    thread.start()
    log.info("Health endpoint listening on http://0.0.0.0:%d/health", port)


# ═════════════════════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Employee — Cloud Watchers (Platinum Tier)",
    )
    parser.add_argument("--gmail",     action="store_true", help="Enable Gmail watcher")
    parser.add_argument("--whatsapp",  action="store_true", help="Enable WhatsApp watcher")
    parser.add_argument("--linkedin",  action="store_true", help="Enable LinkedIn watcher")
    parser.add_argument("--twitter",   action="store_true", help="Enable Twitter watcher")
    parser.add_argument("--instagram", action="store_true", help="Enable Instagram watcher")
    parser.add_argument("--odoo",      action="store_true", help="Enable Odoo watcher")
    parser.add_argument("--all",       action="store_true", help="Enable all watchers (default)")
    parser.add_argument("--health",    action="store_true", help="Print health status and exit")
    parser.add_argument("--health-port", type=int, default=9090, help="Health HTTP port (default: 9090)")

    args = parser.parse_args()

    settings = Settings.load()
    settings.ensure_dirs()

    manager = CloudWatcherManager(settings)

    if args.health:
        manager.print_status()
        return

    # Determine which watchers to start
    specific = []
    if args.gmail:     specific.append("gmail")
    if args.whatsapp:  specific.append("whatsapp")
    if args.linkedin:  specific.append("linkedin")
    if args.twitter:   specific.append("twitter")
    if args.instagram: specific.append("instagram")
    if args.odoo:      specific.append("odoo")

    only = specific if specific and not args.all else None

    # Graceful shutdown on SIGTERM/SIGINT
    def handle_signal(signum, frame):
        log.info("Signal %d received — shutting down", signum)
        manager.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Start health HTTP endpoint
    start_health_server(manager, port=args.health_port)

    # Start watchers
    log.info("=" * 60)
    log.info("  AI Employee — Cloud Watchers (Platinum Tier)")
    log.info("  PID: %d", os.getpid())
    log.info("  Health: http://0.0.0.0:%d/health", args.health_port)
    log.info("=" * 60)

    manager.start(only=only)
    manager.print_status()

    # Block main thread until shutdown
    manager.wait()


if __name__ == "__main__":
    main()
