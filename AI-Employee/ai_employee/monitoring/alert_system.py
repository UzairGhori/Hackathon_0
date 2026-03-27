"""
AI Employee — Alert System (Platinum Tier)

Multi-channel alert system that fires notifications when system health
degrades, services crash, thresholds are breached, or errors cascade.

Alert channels:
  1. In-memory ring buffer (last 500 alerts) for dashboard queries
  2. NDJSON file log (logs/alerts.json) for persistence
  3. Vault file (AI_Employee_Vault/Needs_Approval/) for human visibility
  4. Gmail notification to manager email (if configured)
  5. SystemLogger integration for structured log database

Features:
  - AlertLevel hierarchy: INFO → WARNING → ERROR → CRITICAL
  - AlertRule engine: define threshold-based rules (e.g. "3 failures in 5min")
  - Deduplication: suppress identical alerts within a cooldown window
  - Escalation: repeated warnings auto-escalate to ERROR/CRITICAL
  - Rate limiting: max N alerts per channel per hour
  - Dashboard API: recent(), stats(), acknowledge()

Usage:
    alerts = AlertSystem(
        log_dir=Path("ai_employee/logs"),
        vault_dir=Path("AI_Employee_Vault/Needs_Approval"),
        system_logger=syslog,
        gmail_sender=sender,
        manager_email="ceo@company.com",
    )
    alerts.fire(
        level=AlertLevel.ERROR,
        source="gmail_watcher",
        title="Gmail watcher crashed",
        detail="ConnectionError: timeout after 30s",
    )
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("ai_employee.alerts")


# ── Enums ────────────────────────────────────────────────────────────────

class AlertLevel(IntEnum):
    """Alert severity — higher is more urgent."""
    INFO = 0
    WARNING = 1
    ERROR = 2
    CRITICAL = 3

    @classmethod
    def from_string(cls, name: str) -> "AlertLevel":
        return {"info": cls.INFO, "warning": cls.WARNING,
                "error": cls.ERROR, "critical": cls.CRITICAL
                }.get(name.lower(), cls.INFO)


class AlertChannel(str):
    """Alert delivery channel identifiers."""
    MEMORY = "memory"
    FILE = "file"
    VAULT = "vault"
    EMAIL = "email"
    SYSLOG = "syslog"


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class AlertRecord:
    """A single alert event."""
    alert_id: str
    level: str
    source: str
    title: str
    detail: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    channels_delivered: list[str] = field(default_factory=list)
    acknowledged: bool = False
    acknowledged_by: str = ""
    acknowledged_at: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AlertRule:
    """
    Threshold-based alert rule.

    Fires an alert when `threshold` events from `source` occur within
    `window_seconds`.  Optional cooldown prevents repeat firing.
    """
    name: str
    source: str                     # source name to watch (or "*" for all)
    level: AlertLevel               # level to fire at
    threshold: int                  # number of events to trigger
    window_seconds: int = 300       # time window
    cooldown_seconds: int = 600     # suppress re-fire within this period
    title_template: str = ""        # "{source} health degraded"
    enabled: bool = True

    # Internal tracking (not serialized)
    _events: list = field(default_factory=list, repr=False)
    _last_fired: float = field(default=0.0, repr=False)


# ── Alert System ─────────────────────────────────────────────────────────

class AlertSystem:
    """
    Central alert dispatcher with multi-channel delivery.

    Thread-safe.  All channels are best-effort — a channel failure
    does not block other channels or the caller.
    """

    BUFFER_SIZE = 500
    MAX_ALERTS_PER_HOUR = 60  # Global rate limit

    def __init__(
        self,
        log_dir: Optional[Path] = None,
        vault_dir: Optional[Path] = None,
        system_logger=None,
        gmail_sender=None,
        manager_email: str = "",
        audit_logger=None,
    ):
        self._log_dir = log_dir or Path("ai_employee/logs")
        self._vault_dir = vault_dir
        self._syslog = system_logger
        self._gmail_sender = gmail_sender
        self._manager_email = manager_email
        self._audit = audit_logger

        self._lock = threading.Lock()
        self._counter = 0
        self._alerts: deque[AlertRecord] = deque(maxlen=self.BUFFER_SIZE)
        self._rules: list[AlertRule] = []
        self._dedup_cache: dict[str, float] = {}  # key → last_fire_time
        self._hourly_count = 0
        self._hourly_reset = time.monotonic() + 3600

        self._log_dir.mkdir(parents=True, exist_ok=True)
        if self._vault_dir:
            self._vault_dir.mkdir(parents=True, exist_ok=True)

    # ── Core API ─────────────────────────────────────────────────────

    def fire(
        self,
        level: AlertLevel | str,
        source: str,
        title: str,
        detail: str = "",
        metadata: Optional[dict] = None,
        channels: Optional[list[str]] = None,
    ) -> Optional[AlertRecord]:
        """
        Fire an alert to all (or specified) channels.

        Returns the AlertRecord if delivered, None if suppressed.
        """
        if isinstance(level, str):
            level = AlertLevel.from_string(level)

        # Rate limit
        if not self._rate_limit_ok():
            log.warning("Alert rate limit reached — suppressing: %s", title)
            return None

        # Deduplication: same source+title within 5 minutes
        dedup_key = f"{source}:{title}"
        now = time.monotonic()
        with self._lock:
            last = self._dedup_cache.get(dedup_key, 0)
            if now - last < 300:
                log.debug("Alert deduplicated: %s", dedup_key)
                return None
            self._dedup_cache[dedup_key] = now

        # Create record
        with self._lock:
            self._counter += 1
            alert_id = f"alert_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{self._counter}"

        record = AlertRecord(
            alert_id=alert_id,
            level=level.name,
            source=source,
            title=title,
            detail=detail,
            metadata=metadata or {},
        )

        # Deliver to channels
        target_channels = channels or [
            AlertChannel.MEMORY, AlertChannel.FILE,
            AlertChannel.SYSLOG,
        ]
        # Auto-add vault + email for ERROR/CRITICAL
        if level >= AlertLevel.ERROR:
            if AlertChannel.VAULT not in target_channels and self._vault_dir:
                target_channels.append(AlertChannel.VAULT)
            if AlertChannel.EMAIL not in target_channels and self._gmail_sender:
                target_channels.append(AlertChannel.EMAIL)

        for ch in target_channels:
            try:
                if ch == AlertChannel.MEMORY:
                    self._deliver_memory(record)
                elif ch == AlertChannel.FILE:
                    self._deliver_file(record)
                elif ch == AlertChannel.VAULT:
                    self._deliver_vault(record)
                elif ch == AlertChannel.EMAIL:
                    self._deliver_email(record)
                elif ch == AlertChannel.SYSLOG:
                    self._deliver_syslog(record)
                record.channels_delivered.append(ch)
            except Exception as exc:
                log.warning("Alert channel %s failed: %s", ch, exc)

        log.log(
            self._level_to_logging(level),
            "ALERT [%s] %s: %s — %s",
            record.alert_id, level.name, source, title,
        )

        # Feed rules engine
        self._feed_rules(source, level)

        return record

    def fire_for_service(
        self,
        source: str,
        title: str,
        detail: str = "",
        healthy: bool = True,
    ) -> Optional[AlertRecord]:
        """Convenience: fire an alert based on service health status."""
        if healthy:
            return None
        return self.fire(
            level=AlertLevel.ERROR,
            source=source,
            title=title,
            detail=detail,
        )

    # ── Rules engine ─────────────────────────────────────────────────

    def add_rule(self, rule: AlertRule) -> None:
        """Register a threshold-based alert rule."""
        self._rules.append(rule)
        log.info("Alert rule registered: %s (source=%s, threshold=%d/%ds)",
                 rule.name, rule.source, rule.threshold, rule.window_seconds)

    def _feed_rules(self, source: str, level: AlertLevel) -> None:
        """Feed an event into all matching rules."""
        now = time.monotonic()
        for rule in self._rules:
            if not rule.enabled:
                continue
            if rule.source != "*" and rule.source != source:
                continue

            rule._events.append(now)
            # Trim events outside window
            cutoff = now - rule.window_seconds
            rule._events = [t for t in rule._events if t > cutoff]

            if len(rule._events) >= rule.threshold:
                if now - rule._last_fired > rule.cooldown_seconds:
                    rule._last_fired = now
                    title = rule.title_template.format(
                        source=source, count=len(rule._events),
                    ) if rule.title_template else f"Rule '{rule.name}' triggered for {source}"
                    self.fire(
                        level=rule.level,
                        source="alert_rules",
                        title=title,
                        detail=f"Rule: {rule.name} — {len(rule._events)} events in {rule.window_seconds}s",
                        metadata={"rule": rule.name, "source": source},
                    )

    # ── Queries ──────────────────────────────────────────────────────

    def recent(self, limit: int = 50, level: Optional[str] = None) -> list[dict]:
        """Return recent alerts (most recent first)."""
        with self._lock:
            alerts = list(self._alerts)
        if level:
            alerts = [a for a in alerts if a.level == level.upper()]
        return [a.to_dict() for a in reversed(alerts)][:limit]

    def unacknowledged(self, limit: int = 50) -> list[dict]:
        """Return unacknowledged alerts."""
        with self._lock:
            alerts = [a for a in self._alerts if not a.acknowledged]
        return [a.to_dict() for a in reversed(alerts)][:limit]

    def acknowledge(self, alert_id: str, by: str = "manager") -> bool:
        """Mark an alert as acknowledged."""
        with self._lock:
            for alert in self._alerts:
                if alert.alert_id == alert_id:
                    alert.acknowledged = True
                    alert.acknowledged_by = by
                    alert.acknowledged_at = datetime.now(timezone.utc).isoformat()
                    return True
        return False

    @property
    def stats(self) -> dict:
        """Aggregated alert statistics."""
        with self._lock:
            alerts = list(self._alerts)
        total = len(alerts)
        by_level = {}
        by_source = {}
        unacked = 0
        for a in alerts:
            by_level[a.level] = by_level.get(a.level, 0) + 1
            by_source[a.source] = by_source.get(a.source, 0) + 1
            if not a.acknowledged:
                unacked += 1
        return {
            "total_alerts": total,
            "unacknowledged": unacked,
            "by_level": by_level,
            "by_source": by_source,
            "rules_active": sum(1 for r in self._rules if r.enabled),
        }

    # ── Channel delivery ─────────────────────────────────────────────

    def _deliver_memory(self, record: AlertRecord) -> None:
        with self._lock:
            self._alerts.append(record)

    def _deliver_file(self, record: AlertRecord) -> None:
        path = self._log_dir / "alerts.json"
        line = json.dumps(record.to_dict(), default=str, separators=(",", ":"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _deliver_vault(self, record: AlertRecord) -> None:
        if not self._vault_dir:
            return
        filename = f"Alert_{record.alert_id}.md"
        path = self._vault_dir / filename

        md = (
            f"# System Alert — {record.title}\n\n"
            f"| Field     | Value |\n"
            f"|-----------|-------|\n"
            f"| Alert ID  | `{record.alert_id}` |\n"
            f"| Level     | **{record.level}** |\n"
            f"| Source    | `{record.source}` |\n"
            f"| Time      | {record.timestamp[:19]} |\n\n"
            f"## Detail\n\n{record.detail or 'No additional detail.'}\n"
        )
        if record.metadata:
            md += f"\n## Metadata\n\n```json\n{json.dumps(record.metadata, indent=2, default=str)}\n```\n"

        path.write_text(md, encoding="utf-8")

    def _deliver_email(self, record: AlertRecord) -> None:
        if not self._gmail_sender or not self._manager_email:
            return
        subject = f"[AI Employee Alert] [{record.level}] {record.title}"
        body = (
            f"System Health Alert\n"
            f"{'=' * 40}\n\n"
            f"Level:   {record.level}\n"
            f"Source:  {record.source}\n"
            f"Time:    {record.timestamp}\n\n"
            f"Title:   {record.title}\n\n"
            f"Detail:\n{record.detail}\n\n"
            f"---\nAI Employee — Platinum Tier Health Monitor\n"
        )
        try:
            self._gmail_sender.send(self._manager_email, subject, body)
        except Exception as exc:
            log.warning("Failed to send alert email: %s", exc)

    def _deliver_syslog(self, record: AlertRecord) -> None:
        if not self._syslog:
            return
        level_map = {
            "INFO": "info", "WARNING": "warning",
            "ERROR": "error", "CRITICAL": "critical",
        }
        method = getattr(self._syslog, level_map.get(record.level, "warning"))
        method(record.source, f"[ALERT] {record.title}: {record.detail}",
               {"alert_id": record.alert_id})

    # ── Helpers ──────────────────────────────────────────────────────

    def _rate_limit_ok(self) -> bool:
        now = time.monotonic()
        if now >= self._hourly_reset:
            self._hourly_count = 0
            self._hourly_reset = now + 3600
        self._hourly_count += 1
        return self._hourly_count <= self.MAX_ALERTS_PER_HOUR

    @staticmethod
    def _level_to_logging(level: AlertLevel) -> int:
        return {
            AlertLevel.INFO: logging.INFO,
            AlertLevel.WARNING: logging.WARNING,
            AlertLevel.ERROR: logging.ERROR,
            AlertLevel.CRITICAL: logging.CRITICAL,
        }.get(level, logging.WARNING)
