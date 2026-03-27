"""
AI Employee — Loop Controller (Platinum Tier)

Higher-level lifecycle manager for the Ralph Wiggum Autonomous Loop.
Replaces the Gold-tier AgentRuntime with Platinum-tier features:

  - Start / Pause / Resume / Stop lifecycle
  - Approval-aware pause and auto-resume when approved
  - Permission gate enforcement (cloud draft mode)
  - Run queue for sequential task execution
  - Thread-safe concurrent access
  - Structured JSON logging per run
  - Dashboard API (stats, history, active runs)
  - Integration with all Platinum subsystems

Usage:
    controller = LoopController(
        decision_engine=engine,
        memory=memory,
        agent_map=agents,
        api_key=settings.claude_api_key,
        log_dir=Path("ai_employee/logs"),
        error_handler=error_handler,
        approval_manager=approval_manager,
        permission_manager=permissions,
        fallback_system=fallback,
        retry_manager=retry_mgr,
        audit_logger=audit,
    )

    # Run a task (blocking)
    result = controller.run("Draft a CEO briefing email")

    # Or queue tasks
    controller.enqueue("Process inbox emails")
    controller.enqueue("Generate weekly LinkedIn post")
    controller.start_queue()  # runs sequentially in background

    # Pause/resume
    controller.pause()
    controller.resume()

    # Dashboard data
    stats = controller.stats
    history = controller.run_history
"""

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from ai_employee.brain.task_planner import TaskPlanner
from ai_employee.brain.ralph_loop import (
    RalphLoop, LoopResult, TerminationReason,
)
from ai_employee.brain.iteration_logger import IterationLogger

log = logging.getLogger("ai_employee.loop_controller")


# ── Enums ────────────────────────────────────────────────────────────────

class ControllerState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class ControllerRunResult:
    """Result of a single controller-managed run."""
    task: str
    status: str
    loop_result: Optional[dict]
    total_duration_ms: int
    iterations: int
    fixes_attempted: int = 0
    fixes_succeeded: int = 0
    error: Optional[str] = None
    started_at: str = ""
    finished_at: str = ""
    approval_request_id: Optional[str] = None
    run_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


@dataclass
class QueuedTask:
    """A task waiting in the run queue."""
    task_id: str
    task: str
    priority: str = "MEDIUM"
    queued_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: RunStatus = RunStatus.QUEUED

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


# ── Loop Controller ──────────────────────────────────────────────────────

class LoopController:
    """
    Platinum-tier lifecycle manager for the Ralph Wiggum Autonomous Loop.

    Thread-safe. Manages run lifecycle, queueing, pause/resume, and
    integrates with all Platinum subsystems.
    """

    def __init__(
        self,
        decision_engine,
        memory,
        agent_map: dict,
        api_key: str = "",
        log_dir: Optional[Path] = None,
        max_iterations: int = 10,
        timeout_seconds: int = 300,
        stall_threshold: int = 3,
        # Platinum integrations
        error_handler=None,
        approval_manager=None,
        permission_manager=None,
        fallback_system=None,
        retry_manager=None,
        audit_logger=None,
    ):
        self._decision_engine = decision_engine
        self._memory = memory
        self._agent_map = agent_map
        self._api_key = api_key
        self._log_dir = log_dir or Path("ai_employee/logs")
        self._max_iterations = max_iterations
        self._timeout_seconds = timeout_seconds
        self._stall_threshold = stall_threshold

        # Platinum subsystems
        self._error_handler = error_handler
        self._approval_manager = approval_manager
        self._permissions = permission_manager
        self._fallback = fallback_system
        self._retry_manager = retry_manager
        self._audit = audit_logger

        # Internal state
        self._planner = TaskPlanner(api_key)
        self._iter_logger = IterationLogger(
            log_dir=self._log_dir / "ralph",
            audit_logger=audit_logger,
        )

        self._lock = threading.Lock()
        self._state = ControllerState.IDLE
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused initially
        self._stop_flag = False

        self._run_history: list[ControllerRunResult] = []
        self._task_queue: deque[QueuedTask] = deque()
        self._queue_thread: Optional[threading.Thread] = None
        self._active_task: Optional[str] = None
        self._paused_results: dict[str, LoopResult] = {}  # approval_id → result

    # ── Primary API: run a single task ───────────────────────────────

    def run(self, task: str) -> ControllerRunResult:
        """
        Execute the Ralph loop for the given task (blocking).

        Thread-safe — only one run at a time.
        """
        with self._lock:
            if self._state == ControllerState.RUNNING:
                return ControllerRunResult(
                    task=task, status="rejected",
                    loop_result=None, total_duration_ms=0,
                    iterations=0, error="Another task is already running",
                )
            self._state = ControllerState.RUNNING
            self._active_task = task
            self._stop_flag = False

        try:
            return self._run_task(task)
        finally:
            with self._lock:
                self._active_task = None
                if self._state == ControllerState.RUNNING:
                    self._state = ControllerState.IDLE

    def _run_task(self, task: str) -> ControllerRunResult:
        """Execute a single task with timeout and logging."""
        started_at = datetime.now(timezone.utc).isoformat()
        start_time = time.time()

        log.info("LoopController: starting — %s", task[:80])

        # Audit: task started
        if self._audit:
            try:
                self._audit.log_event(
                    event="task_started",
                    source="loop_controller",
                    summary=f"Task started: {task[:60]}",
                )
            except Exception:
                pass

        loop = RalphLoop(
            decision_engine=self._decision_engine,
            task_planner=self._planner,
            memory=self._memory,
            agent_map=self._agent_map,
            max_iterations=self._max_iterations,
            stall_threshold=self._stall_threshold,
            error_handler=self._error_handler,
            approval_manager=self._approval_manager,
            permission_manager=self._permissions,
            fallback_system=self._fallback,
            retry_manager=self._retry_manager,
            iteration_logger=self._iter_logger,
            audit_logger=self._audit,
        )

        # Run with timeout in a daemon thread
        loop_result: Optional[LoopResult] = None
        error: Optional[str] = None

        def _target():
            nonlocal loop_result
            loop_result = loop.run(task)

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join(timeout=self._timeout_seconds)

        duration_ms = int((time.time() - start_time) * 1000)
        finished_at = datetime.now(timezone.utc).isoformat()

        if thread.is_alive():
            error = f"Timeout after {self._timeout_seconds}s"
            log.warning("LoopController: %s", error)
            result = ControllerRunResult(
                task=task, status="timeout",
                loop_result=None, total_duration_ms=duration_ms,
                iterations=0, error=error,
                started_at=started_at, finished_at=finished_at,
            )
        elif loop_result:
            result = ControllerRunResult(
                task=task,
                status=loop_result.status,
                loop_result=loop_result.to_dict(),
                total_duration_ms=duration_ms,
                iterations=loop_result.iterations,
                fixes_attempted=loop_result.fixes_attempted,
                fixes_succeeded=loop_result.fixes_succeeded,
                error=loop_result.error,
                started_at=started_at,
                finished_at=finished_at,
                approval_request_id=loop_result.approval_request_id,
            )

            # Track paused-for-approval runs
            if loop_result.status == "approval_required" and loop_result.approval_request_id:
                self._paused_results[loop_result.approval_request_id] = loop_result
                with self._lock:
                    self._state = ControllerState.PAUSED
        else:
            result = ControllerRunResult(
                task=task, status="failed",
                loop_result=None, total_duration_ms=duration_ms,
                iterations=0, error="Loop returned no result",
                started_at=started_at, finished_at=finished_at,
            )

        self._run_history.append(result)
        self._write_log(result)

        log.info(
            "LoopController: finished — status=%s, iterations=%d, "
            "duration=%dms, fixes=%d/%d",
            result.status, result.iterations, result.total_duration_ms,
            result.fixes_attempted, result.fixes_succeeded,
        )

        return result

    # ── Queue management ─────────────────────────────────────────────

    def enqueue(self, task: str, priority: str = "MEDIUM") -> str:
        """Add a task to the run queue. Returns the task_id."""
        task_id = f"q_{int(time.time())}_{len(self._task_queue)}"
        queued = QueuedTask(task_id=task_id, task=task, priority=priority)

        with self._lock:
            self._task_queue.append(queued)

        log.info("Enqueued: %s — %s", task_id, task[:60])
        return task_id

    def start_queue(self) -> bool:
        """Start processing the task queue in a background thread."""
        with self._lock:
            if self._queue_thread and self._queue_thread.is_alive():
                log.warning("Queue processor already running")
                return False
            self._stop_flag = False

        self._queue_thread = threading.Thread(
            target=self._process_queue, daemon=True,
            name="ralph-queue",
        )
        self._queue_thread.start()
        log.info("Queue processor started (%d tasks)", len(self._task_queue))
        return True

    def _process_queue(self) -> None:
        """Process tasks from the queue sequentially."""
        while not self._stop_flag:
            # Respect pause
            self._pause_event.wait()

            with self._lock:
                if not self._task_queue:
                    self._state = ControllerState.IDLE
                    break
                queued = self._task_queue.popleft()
                queued.status = RunStatus.RUNNING

            try:
                result = self.run(queued.task)
                queued.status = RunStatus(result.status) if result.status in RunStatus.__members__ else RunStatus.COMPLETED
            except Exception as exc:
                log.error("Queue task failed: %s — %s", queued.task_id, exc)
                queued.status = RunStatus.FAILED

    def cancel_queued(self, task_id: str) -> bool:
        """Remove a queued task by ID."""
        with self._lock:
            for i, t in enumerate(self._task_queue):
                if t.task_id == task_id:
                    del self._task_queue[i]
                    log.info("Cancelled queued task: %s", task_id)
                    return True
        return False

    def get_queue(self) -> list[dict]:
        """Return the current task queue."""
        with self._lock:
            return [t.to_dict() for t in self._task_queue]

    # ── Lifecycle controls ───────────────────────────────────────────

    def pause(self) -> bool:
        """Pause the queue processor (current task runs to completion)."""
        with self._lock:
            if self._state not in (ControllerState.RUNNING, ControllerState.IDLE):
                return False
            self._state = ControllerState.PAUSED
            self._pause_event.clear()

        log.info("LoopController paused")
        return True

    def resume(self) -> bool:
        """Resume a paused controller."""
        with self._lock:
            if self._state != ControllerState.PAUSED:
                return False
            self._state = ControllerState.RUNNING
            self._pause_event.set()

        log.info("LoopController resumed")
        return True

    def stop(self) -> bool:
        """Stop the queue processor."""
        with self._lock:
            self._stop_flag = True
            self._pause_event.set()  # Unblock if paused
            self._state = ControllerState.STOPPING

        if self._queue_thread and self._queue_thread.is_alive():
            self._queue_thread.join(timeout=10)

        with self._lock:
            self._state = ControllerState.IDLE

        log.info("LoopController stopped")
        return True

    # ── Approval resume ──────────────────────────────────────────────

    def resume_after_approval(self, approval_id: str) -> Optional[ControllerRunResult]:
        """
        Resume a task that was paused for approval.

        After the human approves via dashboard/file, call this to
        re-run the task from where it left off.
        """
        paused = self._paused_results.pop(approval_id, None)
        if not paused:
            log.warning("No paused run found for approval %s", approval_id)
            return None

        log.info("Resuming after approval %s — %s", approval_id, paused.task[:60])

        with self._lock:
            self._state = ControllerState.RUNNING

        # Re-run the task (it will create a new plan from scratch,
        # but this is safe since the approval was for the entire task)
        return self.run(paused.task)

    def get_paused_runs(self) -> list[dict]:
        """Return runs paused for approval."""
        return [
            {
                "approval_id": aid,
                "task": result.task,
                "iterations_completed": result.iterations,
                "completed_steps": result.completed_steps,
                "total_steps": result.total_steps,
            }
            for aid, result in self._paused_results.items()
        ]

    # ── Queries / Dashboard API ──────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state.value

    @property
    def is_running(self) -> bool:
        return self._state == ControllerState.RUNNING

    @property
    def is_paused(self) -> bool:
        return self._state == ControllerState.PAUSED

    @property
    def active_task(self) -> Optional[str]:
        return self._active_task

    @property
    def run_history(self) -> list[ControllerRunResult]:
        return list(self._run_history)

    @property
    def stats(self) -> dict:
        """Aggregate statistics across all runs."""
        total = len(self._run_history)
        if total == 0:
            return {
                "state": self._state.value,
                "total_runs": 0,
                "completed": 0,
                "failed": 0,
                "approval_paused": 0,
                "avg_duration_ms": 0,
                "avg_iterations": 0,
                "total_fixes_attempted": 0,
                "total_fixes_succeeded": 0,
                "fix_success_rate": 0.0,
                "queued_tasks": len(self._task_queue),
                "paused_runs": len(self._paused_results),
            }

        completed = sum(1 for r in self._run_history if r.status == "completed")
        failed = sum(1 for r in self._run_history
                     if r.status in ("failed", "timeout", "no_progress"))
        approval_paused = sum(1 for r in self._run_history
                              if r.status == "approval_required")
        avg_duration = sum(r.total_duration_ms for r in self._run_history) / total
        avg_iterations = sum(r.iterations for r in self._run_history) / total
        total_fixes = sum(r.fixes_attempted for r in self._run_history)
        total_fix_ok = sum(r.fixes_succeeded for r in self._run_history)

        return {
            "state": self._state.value,
            "total_runs": total,
            "completed": completed,
            "failed": failed,
            "approval_paused": approval_paused,
            "avg_duration_ms": int(avg_duration),
            "avg_iterations": round(avg_iterations, 1),
            "total_fixes_attempted": total_fixes,
            "total_fixes_succeeded": total_fix_ok,
            "fix_success_rate": round(total_fix_ok / total_fixes, 3) if total_fixes else 0.0,
            "queued_tasks": len(self._task_queue),
            "paused_runs": len(self._paused_results),
        }

    def get_run_details(self, index: int = -1) -> Optional[dict]:
        """Get details of a specific run by index (-1 = latest)."""
        try:
            return self._run_history[index].to_dict()
        except IndexError:
            return None

    def get_iteration_details(self, run_id: str) -> Optional[dict]:
        """Get detailed iteration data for a run (from IterationLogger)."""
        return self._iter_logger.get_run(run_id)

    def get_recent_runs(self, limit: int = 20) -> list[dict]:
        """Get recent run summaries from the IterationLogger."""
        return self._iter_logger.get_recent_runs(limit)

    def get_global_stats(self) -> dict:
        """Get aggregated stats from the IterationLogger."""
        return self._iter_logger.get_global_stats()

    # ── Logging ──────────────────────────────────────────────────────

    def _write_log(self, result: ControllerRunResult) -> None:
        """Write a JSON log file for the run."""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = self._log_dir / f"ralph_run_{timestamp}.json"
            log_path.write_text(result.to_json(indent=2), encoding="utf-8")
            log.info("LoopController: log written to %s", log_path)
        except Exception as exc:
            log.error("LoopController: failed to write log: %s", exc)
