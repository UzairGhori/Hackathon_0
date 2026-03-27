"""
AI Employee — Iteration Logger (Platinum Tier)

Structured, per-iteration logging for the Ralph Wiggum Autonomous Loop.

Each run produces an NDJSON log file (one JSON object per line) containing:
  - A header record with run metadata
  - One record per iteration with all 7 phase results and timings
  - A footer record with final summary and aggregated metrics

Features:
  - Per-phase wall-clock timing (observe, think, plan, act, check, fix)
  - Agent call tracking (which agent, duration, success/failure)
  - Error correlation (links errors to iterations and phases)
  - In-memory ring buffer for fast dashboard queries
  - Summary statistics: success rate, avg iteration time, phase heat map
  - Optional integration with AuditLogger for enterprise audit trail

File layout:
  logs/ralph/run_<timestamp>_<task_hash>.ndjson

Usage:
    logger = IterationLogger(log_dir=Path("ai_employee/logs/ralph"))
    run_id = logger.start_run("Draft a CEO briefing", max_iterations=10)

    logger.start_iteration(run_id, iteration=1)
    logger.log_phase(run_id, 1, "observe", result={...}, duration_ms=12)
    logger.log_phase(run_id, 1, "think",   result={...}, duration_ms=5)
    ...
    logger.end_iteration(run_id, iteration=1)

    logger.end_run(run_id, status="completed", reason="task_completed")
    summary = logger.get_run_summary(run_id)
"""

import hashlib
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("ai_employee.iteration_logger")


# ── Constants ────────────────────────────────────────────────────────────

PHASES = ("observe", "think", "plan", "act", "check", "fix")
BUFFER_SIZE = 200  # max runs kept in memory


# ── Enums ────────────────────────────────────────────────────────────────

class RecordType(str, Enum):
    RUN_START = "run_start"
    ITERATION = "iteration"
    PHASE = "phase"
    RUN_END = "run_end"


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class PhaseRecord:
    """Record for a single phase execution within an iteration."""
    phase: str
    started_at: str
    duration_ms: int
    success: bool
    result: dict = field(default_factory=dict)
    agent_name: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IterationRecord:
    """Full record of one loop iteration (all phases)."""
    iteration: int
    started_at: str
    finished_at: str = ""
    duration_ms: int = 0
    phases: dict[str, PhaseRecord] = field(default_factory=dict)
    progress: float = 0.0
    cumulative_errors: int = 0
    action_taken: str = ""
    outcome: str = ""  # "progressed", "stalled", "approval_paused", "fixed", "failed"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["phases"] = {k: v.to_dict() if isinstance(v, PhaseRecord) else v
                       for k, v in self.phases.items()}
        return d


@dataclass
class RunRecord:
    """Full record of a Ralph loop run."""
    run_id: str
    task: str
    started_at: str
    max_iterations: int
    status: str = "running"           # running, completed, failed, paused, timeout
    termination_reason: str = ""
    finished_at: str = ""
    total_duration_ms: int = 0
    iterations: list[IterationRecord] = field(default_factory=list)
    total_errors: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    total_steps: int = 0
    approval_pauses: int = 0

    # Aggregated phase timing
    phase_timing: dict[str, list[int]] = field(default_factory=lambda: {
        p: [] for p in PHASES
    })

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "max_iterations": self.max_iterations,
            "status": self.status,
            "termination_reason": self.termination_reason,
            "total_duration_ms": self.total_duration_ms,
            "iteration_count": len(self.iterations),
            "total_errors": self.total_errors,
            "completed_steps": self.completed_steps,
            "failed_steps": self.failed_steps,
            "total_steps": self.total_steps,
            "approval_pauses": self.approval_pauses,
            "iterations": [it.to_dict() for it in self.iterations],
            "phase_timing_summary": self._phase_timing_summary(),
        }

    def _phase_timing_summary(self) -> dict:
        """Average and max duration per phase."""
        summary = {}
        for phase, times in self.phase_timing.items():
            if times:
                summary[phase] = {
                    "count": len(times),
                    "avg_ms": int(sum(times) / len(times)),
                    "max_ms": max(times),
                    "total_ms": sum(times),
                }
            else:
                summary[phase] = {"count": 0, "avg_ms": 0, "max_ms": 0, "total_ms": 0}
        return summary


@dataclass
class RunSummary:
    """Lightweight summary for dashboard display."""
    run_id: str
    task: str
    status: str
    started_at: str
    finished_at: str
    total_duration_ms: int
    iteration_count: int
    completed_steps: int
    total_steps: int
    total_errors: int
    termination_reason: str
    success_rate: float  # 0.0–1.0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Iteration Logger ─────────────────────────────────────────────────────

class IterationLogger:
    """
    Structured per-iteration logger for Ralph Wiggum Loop runs.

    Thread-safe. Writes NDJSON log files and maintains an in-memory
    buffer of recent runs for dashboard queries.
    """

    def __init__(
        self,
        log_dir: Optional[Path] = None,
        buffer_size: int = BUFFER_SIZE,
        audit_logger=None,
    ):
        self._log_dir = log_dir or Path("ai_employee/logs/ralph")
        self._buffer_size = buffer_size
        self._audit = audit_logger

        self._lock = threading.Lock()
        self._runs: dict[str, RunRecord] = {}
        self._recent: deque[RunSummary] = deque(maxlen=buffer_size)
        self._file_handles: dict[str, Path] = {}

        self._log_dir.mkdir(parents=True, exist_ok=True)

    # ── Run lifecycle ───────────────────────────────────────────────

    def start_run(self, task: str, max_iterations: int = 10) -> str:
        """Begin a new run. Returns the run_id."""
        now = datetime.now(timezone.utc)
        task_hash = hashlib.md5(task.encode()).hexdigest()[:8]
        run_id = f"run_{now.strftime('%Y%m%d_%H%M%S')}_{task_hash}"

        record = RunRecord(
            run_id=run_id,
            task=task,
            started_at=now.isoformat(),
            max_iterations=max_iterations,
        )

        with self._lock:
            self._runs[run_id] = record
            log_path = self._log_dir / f"{run_id}.ndjson"
            self._file_handles[run_id] = log_path

        # Write header
        self._append(run_id, {
            "type": RecordType.RUN_START.value,
            "run_id": run_id,
            "task": task,
            "max_iterations": max_iterations,
            "started_at": now.isoformat(),
        })

        # Audit integration
        if self._audit:
            try:
                self._audit.log_event(
                    event="task_started",
                    source="ralph_loop",
                    summary=f"Ralph run started: {task[:80]}",
                    metadata={"run_id": run_id},
                )
            except Exception:
                pass

        log.info("Run started: %s — %s", run_id, task[:60])
        return run_id

    def end_run(
        self,
        run_id: str,
        status: str,
        reason: str,
        completed_steps: int = 0,
        failed_steps: int = 0,
        total_steps: int = 0,
    ) -> Optional[RunSummary]:
        """Finalize a run. Returns a RunSummary."""
        now = datetime.now(timezone.utc)

        with self._lock:
            record = self._runs.get(run_id)
            if not record:
                log.warning("end_run: unknown run_id %s", run_id)
                return None

            record.status = status
            record.termination_reason = reason
            record.finished_at = now.isoformat()
            record.completed_steps = completed_steps
            record.failed_steps = failed_steps
            record.total_steps = total_steps

            start_dt = datetime.fromisoformat(record.started_at)
            record.total_duration_ms = int((now - start_dt).total_seconds() * 1000)

            success_rate = (
                completed_steps / total_steps if total_steps > 0 else 0.0
            )

            summary = RunSummary(
                run_id=run_id,
                task=record.task,
                status=status,
                started_at=record.started_at,
                finished_at=record.finished_at,
                total_duration_ms=record.total_duration_ms,
                iteration_count=len(record.iterations),
                completed_steps=completed_steps,
                total_steps=total_steps,
                total_errors=record.total_errors,
                termination_reason=reason,
                success_rate=round(success_rate, 3),
            )
            self._recent.append(summary)

        # Write footer
        self._append(run_id, {
            "type": RecordType.RUN_END.value,
            "run_id": run_id,
            "status": status,
            "reason": reason,
            "finished_at": now.isoformat(),
            "total_duration_ms": record.total_duration_ms,
            "iteration_count": len(record.iterations),
            "completed_steps": completed_steps,
            "failed_steps": failed_steps,
            "total_steps": total_steps,
            "total_errors": record.total_errors,
            "success_rate": round(success_rate, 3),
            "phase_timing": record._phase_timing_summary(),
        })

        # Audit integration
        if self._audit:
            try:
                event = "task_completed" if status == "completed" else "task_failed"
                self._audit.log_event(
                    event=event,
                    source="ralph_loop",
                    summary=f"Ralph run {status}: {record.task[:60]}",
                    metadata={
                        "run_id": run_id,
                        "reason": reason,
                        "iterations": len(record.iterations),
                        "duration_ms": record.total_duration_ms,
                    },
                )
            except Exception:
                pass

        log.info(
            "Run ended: %s — status=%s reason=%s iterations=%d duration=%dms",
            run_id, status, reason, len(record.iterations),
            record.total_duration_ms,
        )
        return summary

    # ── Iteration lifecycle ─────────────────────────────────────────

    def start_iteration(self, run_id: str, iteration: int) -> None:
        """Mark the beginning of an iteration."""
        now = datetime.now(timezone.utc)
        with self._lock:
            record = self._runs.get(run_id)
            if not record:
                return
            iter_rec = IterationRecord(
                iteration=iteration,
                started_at=now.isoformat(),
            )
            record.iterations.append(iter_rec)

    def end_iteration(
        self,
        run_id: str,
        iteration: int,
        progress: float = 0.0,
        outcome: str = "",
    ) -> None:
        """Finalize an iteration with outcome."""
        now = datetime.now(timezone.utc)

        with self._lock:
            record = self._runs.get(run_id)
            if not record or not record.iterations:
                return
            iter_rec = record.iterations[-1]
            if iter_rec.iteration != iteration:
                return

            iter_rec.finished_at = now.isoformat()
            start_dt = datetime.fromisoformat(iter_rec.started_at)
            iter_rec.duration_ms = int((now - start_dt).total_seconds() * 1000)
            iter_rec.progress = progress
            iter_rec.outcome = outcome

        # Write iteration record
        self._append(run_id, {
            "type": RecordType.ITERATION.value,
            "run_id": run_id,
            "iteration": iteration,
            "started_at": iter_rec.started_at,
            "finished_at": iter_rec.finished_at,
            "duration_ms": iter_rec.duration_ms,
            "progress": progress,
            "outcome": outcome,
            "phases": {k: v.to_dict() for k, v in iter_rec.phases.items()},
            "cumulative_errors": iter_rec.cumulative_errors,
        })

    # ── Phase logging ───────────────────────────────────────────────

    def log_phase(
        self,
        run_id: str,
        iteration: int,
        phase: str,
        result: dict,
        duration_ms: int,
        success: bool = True,
        agent_name: str = "",
        error: str = "",
    ) -> None:
        """Log a single phase execution."""
        now = datetime.now(timezone.utc)

        phase_rec = PhaseRecord(
            phase=phase,
            started_at=now.isoformat(),
            duration_ms=duration_ms,
            success=success,
            result=result,
            agent_name=agent_name,
            error=error,
        )

        with self._lock:
            record = self._runs.get(run_id)
            if not record or not record.iterations:
                return

            iter_rec = record.iterations[-1]
            if iter_rec.iteration != iteration:
                return

            iter_rec.phases[phase] = phase_rec

            if not success:
                iter_rec.cumulative_errors += 1
                record.total_errors += 1

            # Track phase timing for aggregation
            if phase in record.phase_timing:
                record.phase_timing[phase].append(duration_ms)

        # Write phase record
        self._append(run_id, {
            "type": RecordType.PHASE.value,
            "run_id": run_id,
            "iteration": iteration,
            "phase": phase,
            "duration_ms": duration_ms,
            "success": success,
            "agent_name": agent_name,
            "error": error,
            "result_keys": list(result.keys()) if result else [],
        })

    def log_approval_pause(self, run_id: str, iteration: int, request_id: str) -> None:
        """Record that the loop paused for human approval."""
        with self._lock:
            record = self._runs.get(run_id)
            if record:
                record.approval_pauses += 1

        self._append(run_id, {
            "type": "approval_pause",
            "run_id": run_id,
            "iteration": iteration,
            "approval_request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── Queries ──────────────────────────────────────────────────────

    def get_run(self, run_id: str) -> Optional[dict]:
        """Get full run record as dict."""
        with self._lock:
            record = self._runs.get(run_id)
            return record.to_dict() if record else None

    def get_run_summary(self, run_id: str) -> Optional[dict]:
        """Get lightweight run summary."""
        with self._lock:
            record = self._runs.get(run_id)
            if not record:
                return None
            total = record.total_steps or 1
            return RunSummary(
                run_id=run_id,
                task=record.task,
                status=record.status,
                started_at=record.started_at,
                finished_at=record.finished_at,
                total_duration_ms=record.total_duration_ms,
                iteration_count=len(record.iterations),
                completed_steps=record.completed_steps,
                total_steps=record.total_steps,
                total_errors=record.total_errors,
                termination_reason=record.termination_reason,
                success_rate=round(record.completed_steps / total, 3),
            ).to_dict()

    def get_recent_runs(self, limit: int = 20) -> list[dict]:
        """Return recent run summaries (most recent first)."""
        with self._lock:
            runs = list(self._recent)
        return [r.to_dict() for r in reversed(runs)][:limit]

    def get_iteration(self, run_id: str, iteration: int) -> Optional[dict]:
        """Get a specific iteration record."""
        with self._lock:
            record = self._runs.get(run_id)
            if not record:
                return None
            for it in record.iterations:
                if it.iteration == iteration:
                    return it.to_dict()
        return None

    def get_phase_stats(self, run_id: str) -> dict:
        """Get aggregated phase timing stats for a run."""
        with self._lock:
            record = self._runs.get(run_id)
            if not record:
                return {}
            return record._phase_timing_summary()

    def get_global_stats(self) -> dict:
        """Aggregate stats across all runs in memory."""
        with self._lock:
            runs = list(self._recent)

        if not runs:
            return {
                "total_runs": 0,
                "completed": 0,
                "failed": 0,
                "avg_duration_ms": 0,
                "avg_iterations": 0,
                "success_rate": 0.0,
            }

        total = len(runs)
        completed = sum(1 for r in runs if r.status == "completed")
        failed = sum(1 for r in runs if r.status != "completed")
        avg_duration = sum(r.total_duration_ms for r in runs) / total
        avg_iterations = sum(r.iteration_count for r in runs) / total
        avg_success = sum(r.success_rate for r in runs) / total

        return {
            "total_runs": total,
            "completed": completed,
            "failed": failed,
            "avg_duration_ms": int(avg_duration),
            "avg_iterations": round(avg_iterations, 1),
            "success_rate": round(avg_success, 3),
        }

    # ── Internal ─────────────────────────────────────────────────────

    def _append(self, run_id: str, record: dict) -> None:
        """Append an NDJSON record to the run's log file."""
        with self._lock:
            path = self._file_handles.get(run_id)
        if not path:
            return

        try:
            line = json.dumps(record, default=str, separators=(",", ":"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as exc:
            log.warning("Failed to write log record for %s: %s", run_id, exc)
