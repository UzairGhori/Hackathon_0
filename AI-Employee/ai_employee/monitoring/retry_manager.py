"""
AI Employee — Retry Manager

Automatic retry with configurable backoff strategies.

Features:
  - Per-agent retry policies (max attempts, backoff, jitter)
  - Three backoff strategies: exponential, linear, constant
  - Integrates with circuit breaker — skips retries when circuit is OPEN
  - Retry budget: system-wide cap to prevent retry storms
  - Full execution history for dashboard/audit

Usage:
    manager = RetryManager(status_aggregator, system_logger)
    manager.set_policy("gmail_agent", RetryPolicy(max_attempts=4, backoff="exponential"))

    result = manager.execute_with_retry(
        agent_name="gmail_agent",
        fn=lambda: agent.execute(decision, content),
    )
"""

import logging
import random
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Callable, Any, Optional

log = logging.getLogger("ai_employee.retry_manager")


# ── Enums / config ──────────────────────────────────────────────────────

class BackoffStrategy(str, Enum):
    EXPONENTIAL = "exponential"   # base * 2^attempt  (with optional jitter)
    LINEAR = "linear"             # base * attempt
    CONSTANT = "constant"         # base (fixed delay)


@dataclass
class RetryPolicy:
    """Retry configuration for a single agent or service."""
    max_attempts: int = 3
    backoff: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    base_delay: float = 1.0       # seconds
    max_delay: float = 60.0       # cap
    jitter: bool = True           # randomise ±25 %
    retry_on: tuple = ()          # empty = retry all exceptions

    def compute_delay(self, attempt: int) -> float:
        """Return the delay in seconds before the next attempt."""
        if self.backoff == BackoffStrategy.EXPONENTIAL:
            delay = self.base_delay * (2 ** attempt)
        elif self.backoff == BackoffStrategy.LINEAR:
            delay = self.base_delay * (attempt + 1)
        else:
            delay = self.base_delay

        delay = min(delay, self.max_delay)

        if self.jitter:
            delay *= random.uniform(0.75, 1.25)

        return round(delay, 2)


# Default policies per agent type
_DEFAULT_POLICIES: dict[str, RetryPolicy] = {
    "gmail_agent": RetryPolicy(max_attempts=3, backoff=BackoffStrategy.EXPONENTIAL,
                               base_delay=2.0),
    "linkedin_agent": RetryPolicy(max_attempts=2, backoff=BackoffStrategy.LINEAR,
                                  base_delay=5.0, max_delay=30.0),
    "odoo_agent": RetryPolicy(max_attempts=3, backoff=BackoffStrategy.EXPONENTIAL,
                              base_delay=3.0),
    "meta_agent": RetryPolicy(max_attempts=3, backoff=BackoffStrategy.EXPONENTIAL,
                              base_delay=2.0),
    "audit_agent": RetryPolicy(max_attempts=2, backoff=BackoffStrategy.CONSTANT,
                               base_delay=5.0),
    "task_agent": RetryPolicy(max_attempts=3, backoff=BackoffStrategy.EXPONENTIAL,
                              base_delay=1.0),
    "email_agent": RetryPolicy(max_attempts=3, backoff=BackoffStrategy.EXPONENTIAL,
                               base_delay=2.0),
}

_GLOBAL_DEFAULT = RetryPolicy(max_attempts=3, backoff=BackoffStrategy.EXPONENTIAL,
                              base_delay=1.0)


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class RetryAttempt:
    attempt: int
    success: bool
    duration_ms: int
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class RetryResult:
    """Result of an execute_with_retry call."""
    agent_name: str
    success: bool
    attempts: int
    total_duration_ms: int
    result: Any = None
    last_error: str = ""
    attempts_log: list[dict] = field(default_factory=list)
    circuit_blocked: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ── Retry Manager ────────────────────────────────────────────────────────

class RetryManager:
    """
    Automatic retry with backoff, circuit-breaker awareness, and budgeting.

    Parameters
    ----------
    status_aggregator : StatusAggregator
        For circuit breaker checks.
    system_logger : SystemLogger
        For persistent error logging.
    global_retry_budget : int
        Maximum retries allowed per minute across all agents to prevent
        retry storms.  0 = unlimited.
    """

    def __init__(
        self,
        status_aggregator=None,
        system_logger=None,
        global_retry_budget: int = 30,
    ):
        self._aggregator = status_aggregator
        self._sys_log = system_logger
        self._budget = global_retry_budget

        self._policies: dict[str, RetryPolicy] = dict(_DEFAULT_POLICIES)
        self._lock = threading.Lock()
        self._history: list[RetryResult] = []

        # Budget tracking: count of retries in the current minute
        self._budget_count = 0
        self._budget_window_start = time.time()

    # ── Policy management ────────────────────────────────────────────

    def set_policy(self, agent_name: str, policy: RetryPolicy) -> None:
        """Override the retry policy for a specific agent."""
        self._policies[agent_name] = policy
        log.info("Retry policy set for %s: max=%d backoff=%s",
                 agent_name, policy.max_attempts, policy.backoff.value)

    def get_policy(self, agent_name: str) -> RetryPolicy:
        """Return the retry policy for an agent (falls back to global default)."""
        return self._policies.get(agent_name, _GLOBAL_DEFAULT)

    # ── Main entry point ─────────────────────────────────────────────

    def execute_with_retry(
        self,
        agent_name: str,
        fn: Callable[[], Any],
        context: Optional[dict] = None,
    ) -> RetryResult:
        """
        Execute ``fn()`` with automatic retry according to the agent's policy.

        Parameters
        ----------
        agent_name : str
            Name of the agent (for policy lookup and circuit checks).
        fn : callable
            The operation to execute. Must raise on failure.
        context : dict, optional
            Extra context for logging.

        Returns
        -------
        RetryResult
        """
        policy = self.get_policy(agent_name)
        attempts_log: list[RetryAttempt] = []
        overall_start = time.time()

        # Check circuit breaker before even trying
        if self._aggregator:
            svc = self._aggregator.get(agent_name)
            if svc and not svc.can_execute():
                log.warning("Retry skipped for %s: circuit breaker OPEN", agent_name)
                result = RetryResult(
                    agent_name=agent_name,
                    success=False,
                    attempts=0,
                    total_duration_ms=0,
                    last_error="Circuit breaker OPEN — retries blocked",
                    circuit_blocked=True,
                )
                self._record(result)
                return result

        last_error = ""
        result_value = None

        for attempt in range(policy.max_attempts):
            # Budget check
            if not self._check_budget():
                log.warning("Global retry budget exhausted — aborting retries for %s",
                            agent_name)
                last_error = "Global retry budget exhausted"
                break

            start = time.time()
            try:
                result_value = fn()
                duration_ms = int((time.time() - start) * 1000)

                attempts_log.append(RetryAttempt(
                    attempt=attempt + 1,
                    success=True,
                    duration_ms=duration_ms,
                ))

                # Record success on circuit breaker
                if self._aggregator:
                    svc = self._aggregator.get(agent_name)
                    if svc:
                        svc.record_success()

                if attempt > 0:
                    log.info("Retry succeeded for %s on attempt %d/%d (%dms)",
                             agent_name, attempt + 1, policy.max_attempts, duration_ms)

                total_ms = int((time.time() - overall_start) * 1000)
                result = RetryResult(
                    agent_name=agent_name,
                    success=True,
                    attempts=attempt + 1,
                    total_duration_ms=total_ms,
                    result=result_value,
                    attempts_log=[asdict(a) for a in attempts_log],
                )
                self._record(result)
                return result

            except Exception as exc:
                duration_ms = int((time.time() - start) * 1000)
                last_error = str(exc)

                attempts_log.append(RetryAttempt(
                    attempt=attempt + 1,
                    success=False,
                    duration_ms=duration_ms,
                    error=last_error,
                ))

                # Check if we should filter by exception type
                if policy.retry_on and not isinstance(exc, policy.retry_on):
                    log.warning("Non-retryable exception for %s: %s", agent_name, exc)
                    break

                # Record failure on circuit breaker
                if self._aggregator:
                    svc = self._aggregator.get(agent_name)
                    if svc:
                        svc.record_failure(last_error)

                # Check if circuit just tripped
                if self._aggregator:
                    svc = self._aggregator.get(agent_name)
                    if svc and not svc.can_execute():
                        log.warning("Circuit breaker tripped for %s during retry", agent_name)
                        break

                is_last = attempt >= policy.max_attempts - 1
                if is_last:
                    log.error("All %d retries exhausted for %s: %s",
                              policy.max_attempts, agent_name, last_error)
                    break

                delay = policy.compute_delay(attempt)
                log.warning(
                    "Retry %d/%d for %s in %.1fs: %s",
                    attempt + 1, policy.max_attempts,
                    agent_name, delay, last_error,
                )

                if self._sys_log:
                    self._sys_log.warning(
                        agent_name,
                        f"Retry {attempt+1}/{policy.max_attempts}: {last_error}",
                        {"attempt": attempt + 1, "delay": delay, **(context or {})},
                    )

                time.sleep(delay)

        # All attempts exhausted
        total_ms = int((time.time() - overall_start) * 1000)
        result = RetryResult(
            agent_name=agent_name,
            success=False,
            attempts=len(attempts_log),
            total_duration_ms=total_ms,
            last_error=last_error,
            attempts_log=[asdict(a) for a in attempts_log],
        )
        self._record(result)

        if self._sys_log:
            self._sys_log.error(
                agent_name,
                f"Retries exhausted after {len(attempts_log)} attempts: {last_error}",
                {"total_attempts": len(attempts_log),
                 "total_duration_ms": total_ms,
                 **(context or {})},
            )

        return result

    # ── Budget ───────────────────────────────────────────────────────

    def _check_budget(self) -> bool:
        """Return True if the global retry budget allows another attempt."""
        if self._budget <= 0:
            return True  # unlimited

        with self._lock:
            now = time.time()
            # Reset window every 60 seconds
            if now - self._budget_window_start >= 60:
                self._budget_count = 0
                self._budget_window_start = now

            if self._budget_count >= self._budget:
                return False

            self._budget_count += 1
            return True

    # ── History / stats ──────────────────────────────────────────────

    def _record(self, result: RetryResult) -> None:
        self._history.append(result)

    @property
    def history(self) -> list[dict]:
        return [r.to_dict() for r in self._history]

    @property
    def recent_retries(self) -> list[dict]:
        return [r.to_dict() for r in self._history[-50:]]

    @property
    def stats(self) -> dict:
        total = len(self._history)
        if total == 0:
            return {
                "total_operations": 0,
                "succeeded": 0,
                "failed": 0,
                "total_attempts": 0,
                "avg_attempts": 0,
                "circuit_blocked": 0,
            }

        succeeded = sum(1 for r in self._history if r.success)
        failed = total - succeeded
        total_attempts = sum(r.attempts for r in self._history)
        circuit_blocked = sum(1 for r in self._history if r.circuit_blocked)

        return {
            "total_operations": total,
            "succeeded": succeeded,
            "failed": failed,
            "total_attempts": total_attempts,
            "avg_attempts": round(total_attempts / total, 1),
            "circuit_blocked": circuit_blocked,
        }
