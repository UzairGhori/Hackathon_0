"""
AI Employee — Service Status & Circuit Breaker

Provides service-level health tracking with circuit breaker pattern:
  - CircuitBreaker prevents cascading failures by halting calls to failing services
  - ServiceMetrics tracks success/failure rates per service
  - ServiceStatus wraps both into a single per-service health object
  - StatusAggregator maintains a registry of all services for dashboard reporting
"""

import logging
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

log = logging.getLogger("ai_employee.monitoring.status")


# ── Enums ────────────────────────────────────────────────────────────────

class HealthState(Enum):
    """Overall health state for a service or the system."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    RESTARTING = "restarting"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"        # Normal operation — calls pass through
    OPEN = "open"            # Tripped — calls are blocked
    HALF_OPEN = "half_open"  # Probing — one call allowed to test recovery


# ── Circuit Breaker ──────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Prevents cascading failures by tracking consecutive errors.

    State machine:
        CLOSED  → OPEN       after `failure_threshold` consecutive failures
        OPEN    → HALF_OPEN  after `recovery_timeout` seconds
        HALF_OPEN → CLOSED   on next success
        HALF_OPEN → OPEN     on next failure
    """

    def __init__(self, failure_threshold: int = 3,
                 recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if (self._state == CircuitState.OPEN
                    and self._time_since_last_failure() >= self.recovery_timeout):
                self._state = CircuitState.HALF_OPEN
                log.debug("Circuit breaker → HALF_OPEN (recovery timeout elapsed)")
            return self._state

    def can_proceed(self) -> bool:
        """Return True if a call should be allowed through."""
        current = self.state  # triggers OPEN→HALF_OPEN transition
        return current in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        """Record a successful call — resets the breaker to CLOSED."""
        with self._lock:
            self._failure_count = 0
            if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                log.info("Circuit breaker → CLOSED (success)")
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed call — may trip the breaker to OPEN."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                log.warning("Circuit breaker → OPEN (failed during HALF_OPEN)")
            elif (self._state == CircuitState.CLOSED
                  and self._failure_count >= self.failure_threshold):
                self._state = CircuitState.OPEN
                log.warning(
                    "Circuit breaker → OPEN (threshold %d reached)",
                    self.failure_threshold,
                )

    def reset(self) -> None:
        """Manually reset the breaker to CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = 0.0

    def _time_since_last_failure(self) -> float:
        if self._last_failure_time == 0.0:
            return float("inf")
        return time.time() - self._last_failure_time

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
        }


# ── Service Metrics ──────────────────────────────────────────────────────

@dataclass
class ServiceMetrics:
    """Tracks call counts and timing for a single service."""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    last_success: str | None = None
    last_failure: str | None = None
    last_error: str | None = None

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 1.0
        return self.successful_calls / self.total_calls

    def record_success(self) -> None:
        self.total_calls += 1
        self.successful_calls += 1
        self.last_success = datetime.now().isoformat()

    def record_failure(self, error: str = "") -> None:
        self.total_calls += 1
        self.failed_calls += 1
        self.last_failure = datetime.now().isoformat()
        self.last_error = error

    def to_dict(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "success_rate": round(self.success_rate, 3),
            "last_success": self.last_success,
            "last_failure": self.last_failure,
            "last_error": self.last_error,
        }


# ── Service Status ───────────────────────────────────────────────────────

class ServiceStatus:
    """
    Per-service health wrapper combining CircuitBreaker + ServiceMetrics.

    Usage:
        status = ServiceStatus("gmail_agent", "agent")
        if status.can_execute():
            try:
                do_work()
                status.record_success()
            except Exception as e:
                status.record_failure(str(e))
    """

    def __init__(self, name: str, service_type: str = "service",
                 failure_threshold: int = 3, recovery_timeout: float = 60.0):
        self.name = name
        self.service_type = service_type
        self.breaker = CircuitBreaker(failure_threshold, recovery_timeout)
        self.metrics = ServiceMetrics()
        self._health_state = HealthState.UNKNOWN
        self._enabled = True

    @property
    def health(self) -> HealthState:
        if not self._enabled:
            return HealthState.DISABLED
        if self.breaker.state == CircuitState.OPEN:
            return HealthState.UNHEALTHY
        if self.breaker.state == CircuitState.HALF_OPEN:
            return HealthState.DEGRADED
        if self.metrics.total_calls == 0:
            return HealthState.UNKNOWN
        if self.metrics.success_rate < 0.5:
            return HealthState.DEGRADED
        return HealthState.HEALTHY

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def can_execute(self) -> bool:
        """Check if the service should accept new calls."""
        if not self._enabled:
            return False
        return self.breaker.can_proceed()

    def record_success(self) -> None:
        self.metrics.record_success()
        self.breaker.record_success()

    def record_failure(self, error: str = "") -> None:
        self.metrics.record_failure(error)
        self.breaker.record_failure()
        log.warning("Service '%s' failure: %s", self.name, error or "unknown")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.service_type,
            "health": self.health.value,
            "enabled": self._enabled,
            "circuit_breaker": self.breaker.to_dict(),
            "metrics": self.metrics.to_dict(),
        }


# ── Status Aggregator ────────────────────────────────────────────────────

class StatusAggregator:
    """
    Central registry for all service statuses.

    Provides an overall system health view and dashboard-ready summaries.
    """

    def __init__(self):
        self._services: dict[str, ServiceStatus] = {}
        self._lock = threading.Lock()

    def register(self, name: str, service_type: str = "service",
                 failure_threshold: int = 3,
                 recovery_timeout: float = 60.0) -> ServiceStatus:
        """Register a service and return its ServiceStatus object."""
        with self._lock:
            status = ServiceStatus(
                name, service_type,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
            )
            self._services[name] = status
            log.debug("Registered service: %s (%s)", name, service_type)
            return status

    def get(self, name: str) -> ServiceStatus | None:
        """Get a service status by name."""
        return self._services.get(name)

    def all_services(self) -> dict[str, ServiceStatus]:
        """Return all registered services."""
        return dict(self._services)

    def overall_health(self) -> HealthState:
        """Compute overall system health from all services."""
        if not self._services:
            return HealthState.UNKNOWN

        states = [s.health for s in self._services.values()
                  if s.enabled]

        if not states:
            return HealthState.DISABLED

        if any(s == HealthState.UNHEALTHY for s in states):
            return HealthState.UNHEALTHY
        if any(s == HealthState.DEGRADED for s in states):
            return HealthState.DEGRADED
        if any(s == HealthState.RESTARTING for s in states):
            return HealthState.DEGRADED
        if all(s in (HealthState.HEALTHY, HealthState.UNKNOWN) for s in states):
            return HealthState.HEALTHY

        return HealthState.UNKNOWN

    def get_unhealthy(self) -> list[ServiceStatus]:
        """Return all services that are not healthy."""
        return [
            s for s in self._services.values()
            if s.health in (HealthState.UNHEALTHY, HealthState.DEGRADED)
        ]

    def summary(self) -> dict:
        """Return a dashboard-ready summary of all services."""
        services = []
        for s in self._services.values():
            services.append(s.to_dict())

        return {
            "overall_health": self.overall_health().value,
            "total_services": len(self._services),
            "unhealthy_count": len(self.get_unhealthy()),
            "services": services,
            "timestamp": datetime.now().isoformat(),
        }
