"""
AI Employee — Task Scheduler

Pulls tasks from the TaskQueue and dispatches them to the correct
agent for execution.

Features:
  - Priority-based execution (CRITICAL first)
  - Automatic retry with exponential backoff
  - Per-task execution logs with timestamps
  - Scheduled task support (deferred execution)
  - Approval gating (pauses task until manager responds)
  - Structured execution results (JSON)

The Scheduler is driven by the Planner — it does not run its own loop.
The Planner calls scheduler.process_queue() during each pipeline cycle.
"""

import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from ai_employee.brain.task_queue import TaskQueue, QueuedTask, TaskStatus
from ai_employee.brain.memory import Memory

log = logging.getLogger("ai_employee.scheduler")


# ── Execution result ─────────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    """Structured result of a single task execution attempt."""
    task_id: str
    title: str
    agent: str
    status: str             # completed | failed | draft_created | sent | awaiting_approval
    retries: int
    duration_ms: float
    agent_result: dict      # raw dict returned by the agent
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


# ── Scheduler ────────────────────────────────────────────────────────────

class Scheduler:
    """
    Processes the task queue by dispatching tasks to agents.

    The agent_map is a dict of { agent_name: agent_instance }.
    Each agent must have an .execute(decision, content) method
    that returns a result dict.
    """

    def __init__(self, queue: TaskQueue, memory: Memory,
                 agent_map: dict, output_dir: Path,
                 max_retries: int = 3, base_delay: float = 1.0):
        self._queue = queue
        self._memory = memory
        self._agents = agent_map
        self._output_dir = output_dir
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._execution_log: list[ExecutionResult] = []

    # ── Main processing loop ─────────────────────────────────────────

    def process_queue(self) -> list[ExecutionResult]:
        """
        Process all pending tasks in priority order.
        Returns a list of execution results.
        """
        results: list[ExecutionResult] = []
        processed = 0

        for task in self._queue.drain():
            # Skip scheduled tasks that aren't due yet
            if task.status == TaskStatus.SCHEDULED and task.scheduled_for:
                if datetime.now().isoformat() < task.scheduled_for:
                    self._queue.update_status(task.task_id, TaskStatus.SCHEDULED)
                    continue

            # Route by planner action
            if task.action == "ignore":
                self._handle_ignore(task)
                continue

            if task.action == "ask_manager":
                self._handle_approval(task)
                results.append(self._make_result(
                    task, "awaiting_approval", 0, {},
                ))
                continue

            # Execute (execute_now or schedule that's due)
            result = self._execute_task(task)
            results.append(result)
            processed += 1

        if processed:
            log.info("Scheduler processed %d tasks", processed)

        return results

    def process_approvals(self, approval_dir: Path) -> list[ExecutionResult]:
        """
        Check approval directory for manager decisions on tasks
        that are awaiting_approval. Execute approved tasks.
        """
        results: list[ExecutionResult] = []

        for task in self._queue.awaiting_approval():
            approval_file = approval_dir / f"Approval_{task.task_id}.md"
            if not approval_file.exists():
                continue

            try:
                content = approval_file.read_text(encoding="utf-8")
            except OSError:
                continue

            upper = content.upper()
            if "<!-- DECISION BELOW THIS LINE -->" in content:
                decision_text = content.split(
                    "<!-- DECISION BELOW THIS LINE -->", 1
                )[1].strip().upper()
            else:
                decision_text = upper

            if "APPROVED" in decision_text or "YES" in decision_text:
                log.info("Manager approved: [%s] %s", task.task_id, task.title)
                self._memory.record_decision(task.task_id, "approved", "Manager approved")
                task.action = "execute_now"
                task.status = TaskStatus.PENDING
                result = self._execute_task(task)
                results.append(result)

            elif "REJECTED" in decision_text or "NO" in decision_text:
                log.info("Manager rejected: [%s] %s", task.task_id, task.title)
                self._memory.record_decision(task.task_id, "rejected", "Manager rejected")
                self._queue.update_status(task.task_id, TaskStatus.IGNORED)
                results.append(self._make_result(
                    task, "rejected", 0, {"decision": "rejected"},
                ))

        return results

    # ── Single task execution with retry ─────────────────────────────

    def _execute_task(self, task: QueuedTask) -> ExecutionResult:
        """
        Execute a single task through its assigned agent.
        Retries on failure with exponential backoff.
        """
        agent = self._agents.get(task.assigned_agent)
        if not agent:
            error = f"No agent registered for '{task.assigned_agent}'"
            log.error(error)
            task.log_attempt(success=False, message=error)
            self._queue.update_status(task.task_id, TaskStatus.FAILED)
            return self._make_result(task, "failed", 0, {}, error)

        # Build a lightweight decision object for the agent
        decision = self._build_decision(task)

        while True:
            start = time.perf_counter()
            try:
                agent_result = agent.execute(decision, task.raw_content)
                duration = (time.perf_counter() - start) * 1000

                # Determine success
                status = agent_result.get("status", "completed")
                is_success = status not in ("failed", "error")

                task.log_attempt(
                    success=is_success,
                    message=status,
                    result=agent_result,
                )

                if is_success:
                    self._queue.update_status(
                        task.task_id, TaskStatus.COMPLETED, agent_result,
                    )
                    self._memory.record_task(
                        task_id=task.task_id,
                        title=task.title,
                        category=task.category,
                        priority=task.urgency,
                        status="auto_completed",
                        approval_required=task.requires_approval,
                    )

                    result = self._make_result(
                        task, status, duration, agent_result,
                    )
                    self._execution_log.append(result)
                    self._write_execution_log(task, result)

                    log.info(
                        "Executed: [%s] '%s' -> %s (%.0fms, attempt %d)",
                        task.task_id, task.title, status,
                        duration, task.retries + 1,
                    )
                    return result
                else:
                    raise RuntimeError(agent_result.get("reason", status))

            except Exception as exc:
                duration = (time.perf_counter() - start) * 1000
                error_msg = str(exc)

                can_retry = self._queue.mark_for_retry(task.task_id, error_msg)
                if can_retry:
                    delay = self._base_delay * (2 ** (task.retries - 1))
                    log.warning(
                        "Retry %d/%d for [%s] in %.1fs: %s",
                        task.retries, task.max_retries,
                        task.task_id, delay, error_msg,
                    )
                    time.sleep(delay)
                    continue
                else:
                    result = self._make_result(
                        task, "failed", duration, {}, error_msg,
                    )
                    self._execution_log.append(result)
                    self._write_execution_log(task, result)

                    log.error(
                        "Failed permanently: [%s] '%s' after %d attempts: %s",
                        task.task_id, task.title, task.retries, error_msg,
                    )
                    return result

    # ── Approval handling ────────────────────────────────────────────

    def _handle_approval(self, task: QueuedTask) -> None:
        """Generate an approval request file for the manager."""
        self._queue.update_status(task.task_id, TaskStatus.AWAITING_APPROVAL)

        approval_dir = self._output_dir.parent / "AI_Employee_Vault" / "Needs_Approval"
        approval_dir.mkdir(parents=True, exist_ok=True)

        steps_md = "\n".join(f"{i}. {s}" for i, s in enumerate(task.steps, 1))
        meta = task.metadata

        approval_md = f"""# Approval Request — {task.title}

---

| Field             | Value                                |
|-------------------|--------------------------------------|
| Task ID           | `{task.task_id}`                     |
| Category          | {task.category}                      |
| Urgency           | **{task.urgency}**                   |
| Risk Score        | {task.risk_score:.0%}                |
| Assigned Agent    | `{task.assigned_agent}`              |
| Sender            | {meta.get('sender', 'N/A')}         |
| Deadline          | {meta.get('deadline', 'N/A')}       |
| Requested         | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |

---

## Description

{meta.get('description', 'No description available.')}

## Required Action

{meta.get('required_action', 'No specific action extracted.')}

## Execution Plan

{steps_md}

---

## Decision Reasoning

> {task.reasoning}

---

<!-- DECISION BELOW THIS LINE -->

**Manager Decision:** PENDING

*(Replace PENDING with APPROVED or REJECTED)*
"""
        filepath = approval_dir / f"Approval_{task.task_id}.md"
        filepath.write_text(approval_md, encoding="utf-8")
        log.info("Approval request written: %s", filepath.name)

    def _handle_ignore(self, task: QueuedTask) -> None:
        """Mark a task as ignored."""
        self._queue.update_status(task.task_id, TaskStatus.IGNORED)
        task.log_attempt(success=True, message="Ignored by planner")
        log.info("Ignored: [%s] '%s' — %s", task.task_id, task.title, task.reasoning)

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_decision(task: QueuedTask):
        """Build a lightweight TaskDecision-like object for agents."""
        from ai_employee.brain.decision_engine import TaskDecision, Action

        action_map = {
            "execute_now": Action.AUTO_EXECUTE,
            "schedule":    Action.AUTO_EXECUTE,
            "ask_manager": Action.NEEDS_APPROVAL,
            "ignore":      Action.NEEDS_REVIEW,
        }

        return TaskDecision(
            task_id=task.task_id,
            title=task.title,
            category=task.category,
            priority=task.urgency,
            action=action_map.get(task.action, Action.AUTO_EXECUTE),
            confidence=task.confidence,
            reasoning=task.reasoning,
            assigned_agent=task.assigned_agent,
            steps=task.steps,
            risk_score=task.risk_score,
        )

    @staticmethod
    def _make_result(task: QueuedTask, status: str, duration: float,
                     agent_result: dict, error: str = "") -> ExecutionResult:
        return ExecutionResult(
            task_id=task.task_id,
            title=task.title,
            agent=task.assigned_agent,
            status=status,
            retries=task.retries,
            duration_ms=duration,
            agent_result=agent_result,
            error=error,
        )

    def _write_execution_log(self, task: QueuedTask,
                             result: ExecutionResult) -> None:
        """Write a per-task execution log file."""
        log_dir = self._output_dir / ".." / "ai_employee" / "logs"
        log_dir = log_dir.resolve()
        log_dir.mkdir(parents=True, exist_ok=True)

        log_entry = {
            "task": task.summary_dict(),
            "execution": result.to_dict(),
            "attempts": task.execution_log,
        }

        log_file = log_dir / f"exec_{task.task_id}.json"
        try:
            log_file.write_text(
                json.dumps(log_entry, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass  # non-critical

    # ── Reporting ────────────────────────────────────────────────────

    @property
    def execution_history(self) -> list[dict]:
        return [r.to_dict() for r in self._execution_log]

    @property
    def stats(self) -> dict:
        total = len(self._execution_log)
        succeeded = sum(1 for r in self._execution_log if r.status not in ("failed", "error"))
        failed = total - succeeded
        avg_duration = (
            sum(r.duration_ms for r in self._execution_log) / total
            if total else 0
        )
        total_retries = sum(r.retries for r in self._execution_log)

        return {
            "total_executed": total,
            "succeeded": succeeded,
            "failed": failed,
            "total_retries": total_retries,
            "avg_duration_ms": round(avg_duration, 1),
        }

    def render_report(self) -> str:
        """Human-readable scheduler report."""
        s = self.stats
        q = self._queue.summary()

        lines = [
            "=" * 55,
            "  Scheduler Report",
            "=" * 55,
            "",
            f"  Queue total       : {q['total']}",
        ]
        for status, count in q.get("by_status", {}).items():
            lines.append(f"    {status:<20}: {count}")

        lines += [
            "",
            f"  Executed total    : {s['total_executed']}",
            f"  Succeeded         : {s['succeeded']}",
            f"  Failed            : {s['failed']}",
            f"  Total retries     : {s['total_retries']}",
            f"  Avg duration      : {s['avg_duration_ms']:.0f}ms",
            "",
        ]

        if self._execution_log:
            lines.append("  Recent Executions:")
            for r in self._execution_log[-10:]:
                icon = "[OK]" if r.status != "failed" else "[!!]"
                lines.append(
                    f"    {icon} [{r.task_id}] {r.title} -> "
                    f"{r.status} ({r.duration_ms:.0f}ms, {r.retries} retries)"
                )
            lines.append("")

        lines.append("=" * 55)
        return "\n".join(lines)


# ── Convenience import for json module ───────────────────────────────────
import json  # noqa: E402 (already imported at module level in practice)
