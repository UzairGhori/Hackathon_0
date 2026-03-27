"""
AI Employee — System-Wide Error Handler

Central error detection, classification, and routing for the entire pipeline.

Responsibilities:
  - Classify errors by severity (LOW → CRITICAL) and type (TRANSIENT, PERMANENT, etc.)
  - Detect patterns: repeated failures, cascading outages, threshold breaches
  - Record every error in the structured log and per-agent metrics
  - Decide whether an error is retryable, needs fallback, or needs human escalation
  - Provide a single ``handle()`` entry point that the pipeline calls on any failure

Integration points:
  - StatusAggregator — circuit breaker health per service
  - SystemLogger     — persistent structured logging
  - RetryManager     — automatic retry dispatch
  - FallbackSystem   — alternative agent routing
"""

import logging
import threading
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Any

log = logging.getLogger("ai_employee.error_handler")


# ── Enums ────────────────────────────────────────────────────────────────

class ErrorSeverity(str, Enum):
    LOW = "low"              # Non-critical, informational
    MEDIUM = "medium"        # Degraded, but system continues
    HIGH = "high"            # Significant — agent or service down
    CRITICAL = "critical"    # System-wide impact, needs immediate attention


class ErrorType(str, Enum):
    TRANSIENT = "transient"            # Network timeout, rate limit, temporary outage
    PERMANENT = "permanent"            # Bad credentials, missing config, logic error
    RESOURCE = "resource"              # Out of memory, disk full, quota exhausted
    DEPENDENCY = "dependency"          # External API down, third-party failure
    CONFIGURATION = "configuration"    # Wrong settings, missing env vars
    UNKNOWN = "unknown"


class RecoveryAction(str, Enum):
    RETRY = "retry"                    # Retryable via RetryManager
    FALLBACK = "fallback"              # Switch to alternative agent via FallbackSystem
    ESCALATE = "escalate"              # Needs human / manager attention
    SKIP = "skip"                      # Non-critical, safe to skip
    CIRCUIT_OPEN = "circuit_open"      # Circuit breaker tripped, wait for recovery


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class ErrorRecord:
    """Structured record of a single error event."""
    error_id: str
    timestamp: str
    source: str                         # agent or service name
    error_type: ErrorType
    severity: ErrorSeverity
    message: str
    exception_type: str = ""
    traceback: str = ""
    context: dict = field(default_factory=dict)
    recovery_action: RecoveryAction = RecoveryAction.SKIP
    resolved: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["error_type"] = self.error_type.value
        d["severity"] = self.severity.value
        d["recovery_action"] = self.recovery_action.value
        return d


@dataclass
class ErrorHandlerResult:
    """Returned by ErrorHandler.handle() to inform the caller what to do."""
    error_record: ErrorRecord
    should_retry: bool
    should_fallback: bool
    should_escalate: bool
    circuit_open: bool
    retry_eligible: bool                # True if retries remain
    fallback_agent: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "error": self.error_record.to_dict(),
            "should_retry": self.should_retry,
            "should_fallback": self.should_fallback,
            "should_escalate": self.should_escalate,
            "circuit_open": self.circuit_open,
            "retry_eligible": self.retry_eligible,
            "fallback_agent": self.fallback_agent,
        }


# ── Keyword-based error classification ───────────────────────────────────

_TRANSIENT_KEYWORDS = [
    "timeout", "timed out", "rate limit", "rate_limit", "429",
    "503", "502", "504", "connection reset", "connection refused",
    "temporary", "retry", "throttl", "overloaded", "capacity",
    "ssl", "eof", "broken pipe",
]

_RESOURCE_KEYWORDS = [
    "memory", "disk", "quota", "space", "oom", "out of memory",
    "storage", "no space", "resource",
]

_CONFIG_KEYWORDS = [
    "credential", "api_key", "api key", "token", "auth",
    "permission", "forbidden", "401", "403", "not found",
    "config", "missing", "invalid key", "not set",
]

_DEPENDENCY_KEYWORDS = [
    "external", "api", "service unavailable", "upstream",
    "dns", "resolve", "unreachable",
]


def _classify_error_type(exc: Optional[BaseException], message: str) -> ErrorType:
    """Infer error type from the exception and message text."""
    lower = message.lower()
    exc_name = type(exc).__name__.lower() if exc else ""

    if any(kw in lower or kw in exc_name for kw in _TRANSIENT_KEYWORDS):
        return ErrorType.TRANSIENT
    if any(kw in lower for kw in _RESOURCE_KEYWORDS):
        return ErrorType.RESOURCE
    if any(kw in lower for kw in _CONFIG_KEYWORDS):
        return ErrorType.CONFIGURATION
    if any(kw in lower for kw in _DEPENDENCY_KEYWORDS):
        return ErrorType.DEPENDENCY

    # Exception-type heuristics
    if exc:
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return ErrorType.TRANSIENT
        if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)):
            return ErrorType.PERMANENT

    return ErrorType.UNKNOWN


def _classify_severity(
    error_type: ErrorType,
    source: str,
    consecutive_failures: int,
) -> ErrorSeverity:
    """Determine severity based on error type and failure history."""
    # Configuration errors are always high — system cannot function
    if error_type == ErrorType.CONFIGURATION:
        return ErrorSeverity.HIGH

    # Cascading failures (many in a row) escalate
    if consecutive_failures >= 5:
        return ErrorSeverity.CRITICAL
    if consecutive_failures >= 3:
        return ErrorSeverity.HIGH

    # Resource exhaustion is high
    if error_type == ErrorType.RESOURCE:
        return ErrorSeverity.HIGH

    # Single transient failure is low/medium
    if error_type == ErrorType.TRANSIENT:
        return ErrorSeverity.LOW if consecutive_failures < 2 else ErrorSeverity.MEDIUM

    # Permanent errors are medium (agent broken, but others work)
    if error_type == ErrorType.PERMANENT:
        return ErrorSeverity.MEDIUM

    return ErrorSeverity.MEDIUM


def _decide_recovery(
    error_type: ErrorType,
    severity: ErrorSeverity,
    circuit_open: bool,
    has_fallback: bool,
    retry_eligible: bool,
) -> RecoveryAction:
    """Decide the recovery action given the error context."""
    if circuit_open:
        return RecoveryAction.CIRCUIT_OPEN

    if severity == ErrorSeverity.CRITICAL:
        return RecoveryAction.ESCALATE

    if error_type == ErrorType.CONFIGURATION:
        return RecoveryAction.ESCALATE

    if error_type == ErrorType.TRANSIENT and retry_eligible:
        return RecoveryAction.RETRY

    if error_type == ErrorType.DEPENDENCY and has_fallback:
        return RecoveryAction.FALLBACK

    if error_type in (ErrorType.PERMANENT, ErrorType.RESOURCE):
        if has_fallback:
            return RecoveryAction.FALLBACK
        return RecoveryAction.ESCALATE

    # Unknown — try retry first, then fallback
    if retry_eligible:
        return RecoveryAction.RETRY
    if has_fallback:
        return RecoveryAction.FALLBACK

    return RecoveryAction.SKIP


# ── Error Handler ────────────────────────────────────────────────────────

class ErrorHandler:
    """
    Central error detection, classification, and routing.

    Parameters
    ----------
    status_aggregator : StatusAggregator
        Circuit breaker / health tracking for all services.
    system_logger : SystemLogger
        Structured persistent log.
    fallback_system : FallbackSystem | None
        For resolving alternative agents (injected after construction).
    """

    def __init__(
        self,
        status_aggregator,
        system_logger,
        fallback_system=None,
    ):
        self._aggregator = status_aggregator
        self._sys_log = system_logger
        self._fallback = fallback_system

        self._lock = threading.Lock()
        self._error_count = 0
        self._errors: list[ErrorRecord] = []
        self._consecutive_by_source: dict[str, int] = {}

    # ── Public API ───────────────────────────────────────────────────

    def handle(
        self,
        source: str,
        exc: Optional[BaseException],
        message: str = "",
        context: Optional[dict] = None,
        retry_eligible: bool = True,
    ) -> ErrorHandlerResult:
        """
        Handle an error from any part of the system.

        Parameters
        ----------
        source : str
            The agent or service name that errored (e.g. "gmail_agent").
        exc : BaseException | None
            The caught exception (None for logical errors).
        message : str
            Human-readable description. Defaults to str(exc).
        context : dict | None
            Additional context (task_id, step, etc.).
        retry_eligible : bool
            Whether the caller allows retries.

        Returns
        -------
        ErrorHandlerResult
            Instructions for the caller: retry, fallback, escalate, or skip.
        """
        with self._lock:
            return self._handle_locked(source, exc, message, context, retry_eligible)

    @property
    def error_history(self) -> list[dict]:
        """Return all recorded errors as dicts (most recent last)."""
        return [e.to_dict() for e in self._errors]

    @property
    def recent_errors(self) -> list[dict]:
        """Return the last 50 errors."""
        return [e.to_dict() for e in self._errors[-50:]]

    @property
    def stats(self) -> dict:
        total = len(self._errors)
        by_type = {}
        by_severity = {}
        by_source = {}
        resolved = 0

        for e in self._errors:
            by_type[e.error_type.value] = by_type.get(e.error_type.value, 0) + 1
            by_severity[e.severity.value] = by_severity.get(e.severity.value, 0) + 1
            by_source[e.source] = by_source.get(e.source, 0) + 1
            if e.resolved:
                resolved += 1

        return {
            "total_errors": total,
            "resolved": resolved,
            "unresolved": total - resolved,
            "by_type": by_type,
            "by_severity": by_severity,
            "by_source": by_source,
        }

    def mark_resolved(self, error_id: str) -> bool:
        """Mark an error as resolved (e.g. after successful retry)."""
        for e in self._errors:
            if e.error_id == error_id:
                e.resolved = True
                return True
        return False

    def clear_consecutive(self, source: str) -> None:
        """Reset the consecutive failure counter for a source (call on success)."""
        self._consecutive_by_source[source] = 0

    # ── Internal ─────────────────────────────────────────────────────

    def _handle_locked(
        self,
        source: str,
        exc: Optional[BaseException],
        message: str,
        context: Optional[dict],
        retry_eligible: bool,
    ) -> ErrorHandlerResult:
        self._error_count += 1
        now = datetime.now()

        msg = message or (str(exc) if exc else "Unknown error")

        # Track consecutive failures per source
        prev = self._consecutive_by_source.get(source, 0)
        self._consecutive_by_source[source] = prev + 1
        consecutive = self._consecutive_by_source[source]

        # Classify
        error_type = _classify_error_type(exc, msg)
        severity = _classify_severity(error_type, source, consecutive)

        # Check circuit breaker
        svc = self._aggregator.get(source) if self._aggregator else None
        circuit_open = False
        if svc:
            svc.record_failure(msg)
            circuit_open = not svc.can_execute()

        # Check fallback availability
        has_fallback = False
        fallback_agent = None
        if self._fallback:
            fb = self._fallback.get_fallback(source)
            if fb:
                has_fallback = True
                fallback_agent = fb

        # Decide recovery
        recovery = _decide_recovery(
            error_type, severity, circuit_open,
            has_fallback, retry_eligible,
        )

        # Build the traceback string
        tb = ""
        if exc:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

        # Create record
        record = ErrorRecord(
            error_id=f"err_{now.strftime('%Y%m%d_%H%M%S')}_{self._error_count}",
            timestamp=now.isoformat(),
            source=source,
            error_type=error_type,
            severity=severity,
            message=msg,
            exception_type=type(exc).__name__ if exc else "",
            traceback=tb,
            context=context or {},
            recovery_action=recovery,
        )
        self._errors.append(record)

        # Log to persistent store
        log_level = {
            ErrorSeverity.LOW: "warning",
            ErrorSeverity.MEDIUM: "error",
            ErrorSeverity.HIGH: "error",
            ErrorSeverity.CRITICAL: "critical",
        }.get(severity, "error")

        getattr(self._sys_log, log_level)(
            source,
            f"[{error_type.value}] {msg}",
            {
                "error_id": record.error_id,
                "severity": severity.value,
                "recovery": recovery.value,
                "consecutive_failures": consecutive,
                **(context or {}),
            },
        )

        # Also log to Python logger for console visibility
        log.warning(
            "Error [%s] %s | type=%s severity=%s recovery=%s consecutive=%d",
            record.error_id, source, error_type.value,
            severity.value, recovery.value, consecutive,
        )

        should_retry = recovery == RecoveryAction.RETRY
        should_fallback = recovery == RecoveryAction.FALLBACK
        should_escalate = recovery == RecoveryAction.ESCALATE

        return ErrorHandlerResult(
            error_record=record,
            should_retry=should_retry,
            should_fallback=should_fallback,
            should_escalate=should_escalate,
            circuit_open=circuit_open,
            retry_eligible=retry_eligible and not circuit_open,
            fallback_agent=fallback_agent if should_fallback else None,
        )
