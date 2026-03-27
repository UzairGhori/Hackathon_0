"""
AI Employee — Fallback System

Automatic routing to alternative agents when a primary agent fails.

Features:
  - Fallback chains: ordered list of alternative agents per primary agent
  - Health-aware selection: skips agents whose circuit breaker is OPEN
  - Category-based discovery: uses AGENT_ROUTING to find same-category alternatives
  - Execution wrapper: attempts the fallback agent with the same task
  - Full audit trail of all fallback decisions

Usage:
    fallback = FallbackSystem(agent_map, status_aggregator, system_logger)

    # Get the best available fallback for a failed agent
    alt = fallback.get_fallback("gmail_agent")

    # Or execute a task through the fallback chain automatically
    result = fallback.execute_with_fallback(
        primary_agent="gmail_agent",
        decision=decision,
        content=content,
    )
"""

import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Any

log = logging.getLogger("ai_employee.fallback_system")


# ── Fallback chain definitions ───────────────────────────────────────────
# Each primary agent has an ordered list of alternatives to try.
# "task_agent" is the universal fallback — it can draft output for any task.

_FALLBACK_CHAINS: dict[str, list[str]] = {
    "gmail_agent":    ["email_agent", "task_agent"],
    "email_agent":    ["gmail_agent", "task_agent"],
    "linkedin_agent": ["task_agent"],
    "odoo_agent":     ["task_agent"],
    "meta_agent":     ["twitter_agent", "task_agent"],
    "twitter_agent":  ["meta_agent", "task_agent"],
    "audit_agent":    ["task_agent"],
    "task_agent":     [],               # last resort — no fallback
}

# Category → agents mapping for dynamic discovery
_CATEGORY_AGENTS: dict[str, list[str]] = {
    "Communication": ["gmail_agent", "email_agent", "task_agent"],
    "Sales":         ["task_agent"],
    "Marketing":     ["task_agent", "meta_agent"],
    "Admin":         ["task_agent"],
    "Finance":       ["odoo_agent", "task_agent"],
    "Social Media":  ["linkedin_agent", "meta_agent", "twitter_agent", "task_agent"],
}


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class FallbackAttempt:
    """Record of a single fallback attempt."""
    agent_name: str
    success: bool
    duration_ms: int
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class FallbackResult:
    """Result of a full fallback chain execution."""
    primary_agent: str
    final_agent: str
    success: bool
    attempts: int
    result: Any = None
    total_duration_ms: int = 0
    last_error: str = ""
    chain: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FallbackEvent:
    """Audit record for a fallback decision."""
    timestamp: str
    primary_agent: str
    primary_error: str
    fallback_agent: str
    reason: str
    success: bool


# ── Fallback System ─────────────────────────────────────────────────────

class FallbackSystem:
    """
    Routes tasks to alternative agents when the primary agent fails.

    Parameters
    ----------
    agent_map : dict[str, agent]
        Name → agent instance mapping. Each agent must have ``execute(decision, content)``.
    status_aggregator : StatusAggregator | None
        For checking circuit breaker health of candidate agents.
    system_logger : SystemLogger | None
        Persistent structured log.
    """

    def __init__(
        self,
        agent_map: dict,
        status_aggregator=None,
        system_logger=None,
    ):
        self._agents = agent_map
        self._aggregator = status_aggregator
        self._sys_log = system_logger

        self._chains: dict[str, list[str]] = dict(_FALLBACK_CHAINS)
        self._events: list[FallbackEvent] = []

    # ── Chain management ─────────────────────────────────────────────

    def set_chain(self, primary: str, fallbacks: list[str]) -> None:
        """Override the fallback chain for a primary agent."""
        self._chains[primary] = fallbacks
        log.info("Fallback chain set for %s: %s", primary, fallbacks)

    def get_chain(self, primary: str) -> list[str]:
        """Return the fallback chain for a primary agent."""
        return list(self._chains.get(primary, []))

    # ── Fallback resolution ──────────────────────────────────────────

    def get_fallback(self, primary: str, category: Optional[str] = None) -> Optional[str]:
        """
        Return the best available fallback agent for *primary*.

        Checks the static chain first, then category-based discovery.
        Skips agents that are absent from agent_map, disabled, or
        circuit-breaker OPEN.
        """
        candidates = self._build_candidate_list(primary, category)

        for name in candidates:
            if self._is_available(name):
                return name

        return None

    def get_all_fallbacks(self, primary: str, category: Optional[str] = None) -> list[str]:
        """Return all available fallback agents in priority order."""
        candidates = self._build_candidate_list(primary, category)
        return [name for name in candidates if self._is_available(name)]

    # ── Execution ────────────────────────────────────────────────────

    def execute_with_fallback(
        self,
        primary_agent: str,
        decision,
        content: str,
        category: Optional[str] = None,
        primary_error: str = "",
    ) -> FallbackResult:
        """
        Attempt to execute a task through the fallback chain.

        Tries each fallback agent in order until one succeeds or the
        chain is exhausted.

        Parameters
        ----------
        primary_agent : str
            The agent that originally failed.
        decision : TaskDecision
            The task decision to pass to the fallback agent.
        content : str
            The task content.
        category : str, optional
            Task category for discovering additional fallbacks.
        primary_error : str
            Error message from the primary agent failure.

        Returns
        -------
        FallbackResult
        """
        overall_start = time.time()
        chain = self.get_all_fallbacks(primary_agent, category)
        attempts_log: list[FallbackAttempt] = []

        if not chain:
            log.warning("No fallback agents available for %s", primary_agent)
            return FallbackResult(
                primary_agent=primary_agent,
                final_agent=primary_agent,
                success=False,
                attempts=0,
                last_error=f"No fallback agents available (primary error: {primary_error})",
                total_duration_ms=0,
            )

        log.info("Fallback chain for %s: %s", primary_agent, chain)

        for fb_name in chain:
            agent = self._agents.get(fb_name)
            if not agent:
                continue

            start = time.time()
            try:
                # Patch the decision's assigned_agent for the fallback
                patched_decision = self._patch_decision(decision, fb_name)
                result = agent.execute(patched_decision, content)
                duration_ms = int((time.time() - start) * 1000)

                attempts_log.append(FallbackAttempt(
                    agent_name=fb_name,
                    success=True,
                    duration_ms=duration_ms,
                ))

                # Record success on circuit breaker
                if self._aggregator:
                    svc = self._aggregator.get(fb_name)
                    if svc:
                        svc.record_success()

                # Audit event
                self._record_event(
                    primary_agent, primary_error, fb_name,
                    "Fallback succeeded", True,
                )

                log.info(
                    "Fallback %s -> %s succeeded (%dms)",
                    primary_agent, fb_name, duration_ms,
                )

                if self._sys_log:
                    self._sys_log.info(
                        "fallback_system",
                        f"Fallback {primary_agent} -> {fb_name} succeeded",
                        {"primary": primary_agent, "fallback": fb_name,
                         "duration_ms": duration_ms},
                    )

                total_ms = int((time.time() - overall_start) * 1000)
                return FallbackResult(
                    primary_agent=primary_agent,
                    final_agent=fb_name,
                    success=True,
                    attempts=len(attempts_log),
                    result=result,
                    total_duration_ms=total_ms,
                    chain=[asdict(a) for a in attempts_log],
                )

            except Exception as exc:
                duration_ms = int((time.time() - start) * 1000)
                error_msg = str(exc)

                attempts_log.append(FallbackAttempt(
                    agent_name=fb_name,
                    success=False,
                    duration_ms=duration_ms,
                    error=error_msg,
                ))

                # Record failure on circuit breaker
                if self._aggregator:
                    svc = self._aggregator.get(fb_name)
                    if svc:
                        svc.record_failure(error_msg)

                self._record_event(
                    primary_agent, primary_error, fb_name,
                    f"Fallback failed: {error_msg}", False,
                )

                log.warning(
                    "Fallback %s -> %s failed: %s",
                    primary_agent, fb_name, error_msg,
                )

        # Entire chain exhausted
        total_ms = int((time.time() - overall_start) * 1000)
        last_err = attempts_log[-1].error if attempts_log else primary_error

        if self._sys_log:
            self._sys_log.error(
                "fallback_system",
                f"All fallbacks exhausted for {primary_agent}",
                {"primary": primary_agent, "attempts": len(attempts_log),
                 "last_error": last_err},
            )

        return FallbackResult(
            primary_agent=primary_agent,
            final_agent=primary_agent,
            success=False,
            attempts=len(attempts_log),
            last_error=last_err,
            total_duration_ms=total_ms,
            chain=[asdict(a) for a in attempts_log],
        )

    # ── Stats / audit ────────────────────────────────────────────────

    @property
    def events(self) -> list[dict]:
        return [asdict(e) for e in self._events]

    @property
    def recent_events(self) -> list[dict]:
        return [asdict(e) for e in self._events[-50:]]

    @property
    def stats(self) -> dict:
        total = len(self._events)
        if total == 0:
            return {
                "total_fallbacks": 0,
                "succeeded": 0,
                "failed": 0,
                "by_primary": {},
                "by_fallback": {},
            }

        succeeded = sum(1 for e in self._events if e.success)
        by_primary: dict[str, int] = {}
        by_fallback: dict[str, int] = {}

        for e in self._events:
            by_primary[e.primary_agent] = by_primary.get(e.primary_agent, 0) + 1
            by_fallback[e.fallback_agent] = by_fallback.get(e.fallback_agent, 0) + 1

        return {
            "total_fallbacks": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "by_primary": by_primary,
            "by_fallback": by_fallback,
        }

    # ── Internal ─────────────────────────────────────────────────────

    def _build_candidate_list(self, primary: str, category: Optional[str]) -> list[str]:
        """Build an ordered list of fallback candidates."""
        seen = {primary}
        candidates = []

        # 1) Static chain
        for name in self._chains.get(primary, []):
            if name not in seen:
                candidates.append(name)
                seen.add(name)

        # 2) Category-based discovery
        if category:
            for name in _CATEGORY_AGENTS.get(category, []):
                if name not in seen:
                    candidates.append(name)
                    seen.add(name)

        # 3) Universal fallback
        if "task_agent" not in seen:
            candidates.append("task_agent")

        return candidates

    def _is_available(self, name: str) -> bool:
        """Check if an agent is present, enabled, and circuit-healthy."""
        agent = self._agents.get(name)
        if not agent:
            return False
        if not getattr(agent, "enabled", True):
            return False
        if self._aggregator:
            svc = self._aggregator.get(name)
            if svc and not svc.can_execute():
                return False
        return True

    @staticmethod
    def _patch_decision(decision, new_agent: str):
        """
        Return a copy of the decision with assigned_agent replaced.
        Works with both TaskDecision dataclasses and plain dicts.
        """
        from ai_employee.brain.decision_engine import TaskDecision
        if isinstance(decision, TaskDecision):
            return TaskDecision(
                task_id=decision.task_id,
                title=decision.title,
                category=decision.category,
                priority=decision.priority,
                action=decision.action,
                confidence=decision.confidence,
                reasoning=f"{decision.reasoning} [fallback from {decision.assigned_agent}]",
                assigned_agent=new_agent,
                steps=decision.steps,
                risk_score=decision.risk_score,
            )
        # dict fallback
        patched = dict(decision)
        patched["assigned_agent"] = new_agent
        return patched

    def _record_event(
        self,
        primary: str,
        primary_error: str,
        fallback: str,
        reason: str,
        success: bool,
    ) -> None:
        self._events.append(FallbackEvent(
            timestamp=datetime.now().isoformat(),
            primary_agent=primary,
            primary_error=primary_error,
            fallback_agent=fallback,
            reason=reason,
            success=success,
        ))
