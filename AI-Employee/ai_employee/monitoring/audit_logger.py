"""
AI Employee — Enterprise Audit Logger

Immutable, append-only audit trail for every meaningful action in the system.

Every entry records:
  - WHO    — which agent / service performed the action
  - WHAT   — event type (task_received, tool_used, result, error, …)
  - WHEN   — ISO-8601 timestamp with monotonic sequence number
  - WHERE  — pipeline phase or subsystem
  - INPUT  — what went in (task content, tool parameters)
  - OUTPUT — what came out (agent result, error message)
  - WHY    — decision reasoning, classification, risk score

Persistence:
  - Primary:  ``logs/audit_log.json``  — newline-delimited JSON (one object per line)
  - Rotation: when the file exceeds ``max_file_bytes`` a new segment is created
              (``audit_log_<timestamp>.json``) and ``audit_log.json`` is reset
  - In-memory ring buffer (last ``buffer_size`` entries) for fast dashboard queries

Thread safety:
  - All writes are serialised through a single ``threading.Lock``

Integration:
  - Constructed inside ``AIEmployee.__init__()``
  - Pipeline phases call ``audit.log_*()`` helper methods
  - ErrorHandler / RetryManager / FallbackSystem feed their events here
  - Dashboard can query ``audit.recent()`` and ``audit.query()``
"""

import json
import logging
import os
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("ai_employee.audit")


# ── Event taxonomy ───────────────────────────────────────────────────────

class AuditEvent(str, Enum):
    """Every auditable event type in the system."""

    # Task lifecycle
    TASK_RECEIVED = "task_received"
    TASK_CLASSIFIED = "task_classified"
    TASK_QUEUED = "task_queued"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_SKIPPED = "task_skipped"

    # Agent / tool execution
    TOOL_USED = "tool_used"
    AGENT_CALLED = "agent_called"
    AGENT_RESULT = "agent_result"

    # Approval flow
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_EXPIRED = "approval_expired"

    # Error recovery
    ERROR_OCCURRED = "error_occurred"
    RETRY_ATTEMPTED = "retry_attempted"
    RETRY_SUCCEEDED = "retry_succeeded"
    RETRY_EXHAUSTED = "retry_exhausted"
    FALLBACK_TRIGGERED = "fallback_triggered"
    FALLBACK_SUCCEEDED = "fallback_succeeded"
    FALLBACK_EXHAUSTED = "fallback_exhausted"
    CIRCUIT_OPENED = "circuit_opened"
    CIRCUIT_CLOSED = "circuit_closed"

    # System lifecycle
    SYSTEM_BOOT = "system_boot"
    SYSTEM_SHUTDOWN = "system_shutdown"
    CYCLE_STARTED = "cycle_started"
    CYCLE_COMPLETED = "cycle_completed"
    SERVICE_STARTED = "service_started"
    SERVICE_STOPPED = "service_stopped"
    CONFIG_LOADED = "config_loaded"


class AuditSeverity(str, Enum):
    """Severity / importance of an audit entry."""
    TRACE = "trace"       # Fine-grained diagnostic (tool params)
    INFO = "info"         # Normal operation
    WARNING = "warning"   # Degraded but functional
    ERROR = "error"       # Action failed
    CRITICAL = "critical" # System-wide impact


# ── Core data structure ──────────────────────────────────────────────────

@dataclass
class AuditEntry:
    """Single immutable audit record."""
    seq: int                        # monotonic sequence number
    timestamp: str                  # ISO-8601 UTC
    event: str                      # AuditEvent value
    severity: str                   # AuditSeverity value
    source: str                     # agent / service name
    phase: str                      # pipeline phase or subsystem
    summary: str                    # one-line human description
    task_id: str = ""
    input_data: Optional[dict] = None
    output_data: Optional[dict] = None
    error: Optional[str] = None
    error_type: str = ""
    duration_ms: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Drop None fields to keep the JSON compact
        return {k: v for k, v in d.items() if v is not None and v != "" and v != {}}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, separators=(",", ":"))


# ── Query helpers ────────────────────────────────────────────────────────

@dataclass
class AuditQuery:
    """Filter parameters for querying the in-memory ring buffer."""
    event: Optional[str] = None
    source: Optional[str] = None
    severity: Optional[str] = None
    task_id: Optional[str] = None
    phase: Optional[str] = None
    since: Optional[str] = None       # ISO-8601 lower bound
    limit: int = 100


# ── Audit Logger ─────────────────────────────────────────────────────────

class AuditLogger:
    """
    Enterprise-grade append-only audit logger.

    Parameters
    ----------
    log_dir : Path
        Directory that contains (or will contain) ``audit_log.json``.
    buffer_size : int
        In-memory ring buffer capacity for fast queries.
    max_file_bytes : int
        When the primary log file exceeds this size it is rotated.
        Default 10 MB.
    """

    _FILE_NAME = "audit_log.json"

    def __init__(
        self,
        log_dir: Path,
        buffer_size: int = 5000,
        max_file_bytes: int = 10 * 1024 * 1024,
    ):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._log_dir / self._FILE_NAME
        self._max_bytes = max_file_bytes
        self._buffer: deque[AuditEntry] = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._seq = 0
        self._session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._total_written = 0

        # Pre-load entry count from existing file
        if self._log_path.exists():
            try:
                self._seq = sum(1 for _ in open(self._log_path, "r", encoding="utf-8"))
            except Exception:
                self._seq = 0

    # ══════════════════════════════════════════════════════════════════
    #  HIGH-LEVEL CONVENIENCE METHODS
    # ══════════════════════════════════════════════════════════════════

    # ── Task lifecycle ───────────────────────────────────────────────

    def log_task_received(
        self,
        task_id: str,
        source: str,
        title: str,
        content_preview: str = "",
        metadata: Optional[dict] = None,
    ) -> AuditEntry:
        return self._write(
            event=AuditEvent.TASK_RECEIVED,
            severity=AuditSeverity.INFO,
            source=source,
            phase="triage",
            summary=f"Task received: {title}",
            task_id=task_id,
            input_data={"title": title, "preview": content_preview[:500]},
            metadata=metadata or {},
        )

    def log_task_classified(
        self,
        task_id: str,
        category: str,
        priority: str,
        assigned_agent: str,
        confidence: float,
        risk_score: float,
    ) -> AuditEntry:
        return self._write(
            event=AuditEvent.TASK_CLASSIFIED,
            severity=AuditSeverity.INFO,
            source="decision_engine",
            phase="plan",
            summary=f"Classified [{category}] priority={priority} -> {assigned_agent}",
            task_id=task_id,
            output_data={
                "category": category,
                "priority": priority,
                "assigned_agent": assigned_agent,
                "confidence": round(confidence, 3),
                "risk_score": round(risk_score, 3),
            },
        )

    def log_task_completed(
        self,
        task_id: str,
        agent: str,
        result: Optional[dict] = None,
        duration_ms: int = 0,
    ) -> AuditEntry:
        return self._write(
            event=AuditEvent.TASK_COMPLETED,
            severity=AuditSeverity.INFO,
            source=agent,
            phase="execute",
            summary=f"Task completed by {agent}",
            task_id=task_id,
            output_data=self._safe_result(result),
            duration_ms=duration_ms,
        )

    def log_task_failed(
        self,
        task_id: str,
        agent: str,
        error: str,
        error_type: str = "",
        duration_ms: int = 0,
    ) -> AuditEntry:
        return self._write(
            event=AuditEvent.TASK_FAILED,
            severity=AuditSeverity.ERROR,
            source=agent,
            phase="execute",
            summary=f"Task failed in {agent}: {error[:120]}",
            task_id=task_id,
            error=error,
            error_type=error_type,
            duration_ms=duration_ms,
        )

    # ── Tool / agent execution ───────────────────────────────────────

    def log_tool_used(
        self,
        tool_name: str,
        agent: str,
        phase: str = "execute",
        parameters: Optional[dict] = None,
        result: Optional[dict] = None,
        success: bool = True,
        duration_ms: int = 0,
        error: str = "",
        task_id: str = "",
    ) -> AuditEntry:
        sev = AuditSeverity.INFO if success else AuditSeverity.ERROR
        summary = f"Tool {tool_name} {'OK' if success else 'FAILED'} via {agent}"
        return self._write(
            event=AuditEvent.TOOL_USED,
            severity=sev,
            source=agent,
            phase=phase,
            summary=summary,
            task_id=task_id,
            input_data=self._sanitise_params(parameters),
            output_data=self._safe_result(result) if success else None,
            error=error if not success else None,
            duration_ms=duration_ms,
        )

    def log_agent_called(
        self,
        agent: str,
        action: str,
        task_id: str = "",
        phase: str = "execute",
        input_data: Optional[dict] = None,
    ) -> AuditEntry:
        return self._write(
            event=AuditEvent.AGENT_CALLED,
            severity=AuditSeverity.INFO,
            source=agent,
            phase=phase,
            summary=f"Agent {agent} invoked: {action}",
            task_id=task_id,
            input_data=input_data,
        )

    def log_agent_result(
        self,
        agent: str,
        status: str,
        task_id: str = "",
        result: Optional[dict] = None,
        duration_ms: int = 0,
    ) -> AuditEntry:
        sev = AuditSeverity.INFO if status != "failed" else AuditSeverity.ERROR
        return self._write(
            event=AuditEvent.AGENT_RESULT,
            severity=sev,
            source=agent,
            phase="execute",
            summary=f"Agent {agent} result: {status}",
            task_id=task_id,
            output_data=self._safe_result(result),
            duration_ms=duration_ms,
        )

    # ── Error recovery ───────────────────────────────────────────────

    def log_error(
        self,
        source: str,
        error: str,
        error_type: str = "",
        severity: AuditSeverity = AuditSeverity.ERROR,
        phase: str = "",
        task_id: str = "",
        exc: Optional[BaseException] = None,
        context: Optional[dict] = None,
    ) -> AuditEntry:
        tb = ""
        if exc:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return self._write(
            event=AuditEvent.ERROR_OCCURRED,
            severity=severity,
            source=source,
            phase=phase,
            summary=f"Error in {source}: {error[:120]}",
            task_id=task_id,
            error=error,
            error_type=error_type,
            metadata={"traceback": tb, **(context or {})} if tb else (context or {}),
        )

    def log_retry(
        self,
        agent: str,
        attempt: int,
        max_attempts: int,
        error: str,
        delay_s: float = 0,
        task_id: str = "",
    ) -> AuditEntry:
        return self._write(
            event=AuditEvent.RETRY_ATTEMPTED,
            severity=AuditSeverity.WARNING,
            source=agent,
            phase="retry",
            summary=f"Retry {attempt}/{max_attempts} for {agent} (delay {delay_s:.1f}s)",
            task_id=task_id,
            error=error,
            metadata={"attempt": attempt, "max_attempts": max_attempts, "delay_s": delay_s},
        )

    def log_retry_success(self, agent: str, attempt: int, task_id: str = "") -> AuditEntry:
        return self._write(
            event=AuditEvent.RETRY_SUCCEEDED,
            severity=AuditSeverity.INFO,
            source=agent,
            phase="retry",
            summary=f"Retry succeeded for {agent} on attempt {attempt}",
            task_id=task_id,
            metadata={"attempt": attempt},
        )

    def log_retry_exhausted(
        self,
        agent: str,
        attempts: int,
        last_error: str,
        task_id: str = "",
    ) -> AuditEntry:
        return self._write(
            event=AuditEvent.RETRY_EXHAUSTED,
            severity=AuditSeverity.ERROR,
            source=agent,
            phase="retry",
            summary=f"Retries exhausted for {agent} after {attempts} attempts",
            task_id=task_id,
            error=last_error,
            metadata={"total_attempts": attempts},
        )

    def log_fallback(
        self,
        primary: str,
        fallback: str,
        reason: str,
        success: bool,
        task_id: str = "",
        duration_ms: int = 0,
    ) -> AuditEntry:
        event = AuditEvent.FALLBACK_SUCCEEDED if success else AuditEvent.FALLBACK_TRIGGERED
        sev = AuditSeverity.INFO if success else AuditSeverity.WARNING
        return self._write(
            event=event,
            severity=sev,
            source=primary,
            phase="fallback",
            summary=f"Fallback {primary} -> {fallback}: {'OK' if success else reason[:80]}",
            task_id=task_id,
            metadata={"primary": primary, "fallback": fallback, "reason": reason},
            duration_ms=duration_ms,
        )

    # ── Approval flow ────────────────────────────────────────────────

    def log_approval_requested(
        self, task_id: str, agent: str, title: str, category: str,
    ) -> AuditEntry:
        return self._write(
            event=AuditEvent.APPROVAL_REQUESTED,
            severity=AuditSeverity.INFO,
            source=agent,
            phase="approval",
            summary=f"Approval requested: {title}",
            task_id=task_id,
            metadata={"category": category},
        )

    def log_approval_decided(
        self, task_id: str, decision: str, title: str = "",
    ) -> AuditEntry:
        event = (AuditEvent.APPROVAL_GRANTED if decision == "approved"
                 else AuditEvent.APPROVAL_DENIED if decision == "rejected"
                 else AuditEvent.APPROVAL_EXPIRED)
        return self._write(
            event=event,
            severity=AuditSeverity.INFO,
            source="approval_manager",
            phase="approval",
            summary=f"Approval {decision}: {title}",
            task_id=task_id,
        )

    # ── System lifecycle ─────────────────────────────────────────────

    def log_system_event(
        self,
        event: AuditEvent,
        summary: str,
        metadata: Optional[dict] = None,
    ) -> AuditEntry:
        return self._write(
            event=event,
            severity=AuditSeverity.INFO,
            source="system",
            phase="lifecycle",
            summary=summary,
            metadata=metadata or {},
        )

    def log_cycle(
        self,
        cycle_num: int,
        event: AuditEvent,
        results: Optional[dict] = None,
    ) -> AuditEntry:
        tag = "started" if event == AuditEvent.CYCLE_STARTED else "completed"
        return self._write(
            event=event,
            severity=AuditSeverity.INFO,
            source="pipeline",
            phase="cycle",
            summary=f"Cycle {cycle_num} {tag}",
            output_data=results,
            metadata={"cycle": cycle_num},
        )

    # ══════════════════════════════════════════════════════════════════
    #  QUERY INTERFACE
    # ══════════════════════════════════════════════════════════════════

    def recent(self, limit: int = 100) -> list[dict]:
        """Return the most recent *limit* entries from the ring buffer."""
        with self._lock:
            entries = list(self._buffer)
        # Most-recent-first
        entries.reverse()
        return [e.to_dict() for e in entries[:limit]]

    def query(self, q: AuditQuery) -> list[dict]:
        """Filter the ring buffer by the given query parameters."""
        with self._lock:
            entries = list(self._buffer)

        results = []
        for e in reversed(entries):
            if q.event and e.event != q.event:
                continue
            if q.source and e.source != q.source:
                continue
            if q.severity and e.severity != q.severity:
                continue
            if q.task_id and e.task_id != q.task_id:
                continue
            if q.phase and e.phase != q.phase:
                continue
            if q.since and e.timestamp < q.since:
                continue
            results.append(e.to_dict())
            if len(results) >= q.limit:
                break
        return results

    def query_errors(self, limit: int = 50) -> list[dict]:
        """Shortcut: return recent ERROR and CRITICAL entries."""
        return self.query(AuditQuery(severity="error", limit=limit)) + \
               self.query(AuditQuery(severity="critical", limit=limit))

    def query_by_task(self, task_id: str, limit: int = 200) -> list[dict]:
        """Return the full audit trail for a single task."""
        return self.query(AuditQuery(task_id=task_id, limit=limit))

    # ══════════════════════════════════════════════════════════════════
    #  STATISTICS
    # ══════════════════════════════════════════════════════════════════

    @property
    def stats(self) -> dict:
        """Aggregate counters across the current session."""
        with self._lock:
            entries = list(self._buffer)

        by_event: dict[str, int] = {}
        by_source: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        error_count = 0

        for e in entries:
            by_event[e.event] = by_event.get(e.event, 0) + 1
            by_source[e.source] = by_source.get(e.source, 0) + 1
            by_severity[e.severity] = by_severity.get(e.severity, 0) + 1
            if e.severity in (AuditSeverity.ERROR.value, AuditSeverity.CRITICAL.value):
                error_count += 1

        return {
            "session_id": self._session_id,
            "total_entries": self._total_written,
            "buffer_entries": len(entries),
            "error_count": error_count,
            "by_event": dict(sorted(by_event.items(), key=lambda x: -x[1])),
            "by_source": dict(sorted(by_source.items(), key=lambda x: -x[1])),
            "by_severity": by_severity,
            "log_file": str(self._log_path),
        }

    @property
    def total_entries(self) -> int:
        return self._total_written

    # ══════════════════════════════════════════════════════════════════
    #  INTERNAL
    # ══════════════════════════════════════════════════════════════════

    def _write(
        self,
        event: AuditEvent,
        severity: AuditSeverity,
        source: str,
        phase: str,
        summary: str,
        task_id: str = "",
        input_data: Optional[dict] = None,
        output_data: Optional[dict] = None,
        error: Optional[str] = None,
        error_type: str = "",
        duration_ms: int = 0,
        metadata: Optional[dict] = None,
    ) -> AuditEntry:
        """Create an AuditEntry, append to buffer, persist to file."""
        with self._lock:
            self._seq += 1
            entry = AuditEntry(
                seq=self._seq,
                timestamp=datetime.now(timezone.utc).isoformat(),
                event=event.value,
                severity=severity.value,
                source=source,
                phase=phase,
                summary=summary,
                task_id=task_id,
                input_data=input_data,
                output_data=output_data,
                error=error,
                error_type=error_type,
                duration_ms=duration_ms,
                metadata=metadata or {},
            )

            self._buffer.append(entry)
            self._total_written += 1
            self._persist(entry)

        return entry

    def _persist(self, entry: AuditEntry) -> None:
        """Append one JSON line to the log file. Rotate if oversized."""
        try:
            self._maybe_rotate()
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(entry.to_json() + "\n")
        except Exception as exc:
            # Never let audit logging crash the system
            log.error("Audit write failed: %s", exc)

    def _maybe_rotate(self) -> None:
        """Rotate the log file when it exceeds ``max_file_bytes``."""
        if not self._log_path.exists():
            return
        try:
            size = self._log_path.stat().st_size
        except OSError:
            return
        if size < self._max_bytes:
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        rotated = self._log_dir / f"audit_log_{ts}.json"
        try:
            self._log_path.rename(rotated)
            log.info("Audit log rotated -> %s", rotated.name)
        except OSError as exc:
            log.error("Audit log rotation failed: %s", exc)

    # ── Sanitisation helpers ─────────────────────────────────────────

    @staticmethod
    def _sanitise_params(params: Optional[dict]) -> Optional[dict]:
        """Remove secrets from parameters before logging."""
        if not params:
            return None
        redacted = dict(params)
        for key in list(redacted.keys()):
            lower = key.lower()
            if any(s in lower for s in ("password", "secret", "token", "key", "credential")):
                redacted[key] = "***REDACTED***"
        return redacted

    @staticmethod
    def _safe_result(result: Optional[dict]) -> Optional[dict]:
        """
        Ensure a result dict is JSON-serialisable and not excessively large.
        Truncate string values over 2 000 chars.
        """
        if result is None:
            return None
        safe: dict[str, Any] = {}
        for k, v in result.items():
            if isinstance(v, str) and len(v) > 2000:
                safe[k] = v[:2000] + "…[truncated]"
            elif isinstance(v, (dict, list)):
                try:
                    json.dumps(v, default=str)
                    safe[k] = v
                except (TypeError, ValueError):
                    safe[k] = str(v)[:500]
            else:
                safe[k] = v
        return safe
