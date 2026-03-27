"""
AI Employee — Agent Runtime (Lifecycle Manager)

Entry point for running the Ralph Wiggum Autonomous Loop.
Provides thread-safe execution, timeout handling, JSON logging,
and run history tracking.

Usage:
    runtime = AgentRuntime(decision_engine, memory, agent_map, api_key, log_dir)
    result = runtime.run("Draft a professional email to the client")
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from ai_employee.brain.task_planner import TaskPlanner
from ai_employee.brain.ralph_loop import RalphLoop, LoopResult, TerminationReason

log = logging.getLogger("ai_employee.agent_runtime")


@dataclass
class RuntimeResult:
    task: str
    status: str
    loop_result: Optional[dict]
    total_duration_ms: int
    iterations: int
    error: Optional[str] = None
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


class AgentRuntime:
    """
    Lifecycle manager for the Ralph Wiggum Autonomous Loop.

    Parameters
    ----------
    decision_engine : DecisionEngine
        For task analysis.
    memory : Memory
        Persistent memory store.
    agent_map : dict
        Name → agent instance mapping.
    api_key : str
        Anthropic API key (optional, enables Claude-backed planning).
    log_dir : Path
        Directory for JSON execution logs.
    max_iterations : int
        Max loop iterations per run.
    timeout_seconds : int
        Hard timeout for the entire run.
    stall_threshold : int
        Consecutive failures before declaring stall.
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
    ):
        self._decision_engine = decision_engine
        self._memory = memory
        self._agent_map = agent_map
        self._api_key = api_key
        self._log_dir = log_dir or Path("ai_employee/logs")
        self._max_iterations = max_iterations
        self._timeout_seconds = timeout_seconds
        self._stall_threshold = stall_threshold

        self._task_planner = TaskPlanner(api_key)
        self._lock = threading.Lock()
        self._run_history: list[RuntimeResult] = []

    def run(self, task: str) -> RuntimeResult:
        """
        Execute the Ralph loop for the given task.

        Thread-safe — only one run at a time.  Creates a fresh RalphLoop
        instance for each invocation.
        """
        with self._lock:
            return self._run_locked(task)

    def _run_locked(self, task: str) -> RuntimeResult:
        started_at = datetime.now().isoformat()
        start_time = time.time()

        log.info("AgentRuntime: starting task — %s", task[:80])

        loop = RalphLoop(
            decision_engine=self._decision_engine,
            task_planner=self._task_planner,
            memory=self._memory,
            agent_map=self._agent_map,
            max_iterations=self._max_iterations,
            stall_threshold=self._stall_threshold,
        )

        # Run with timeout using a daemon thread
        loop_result: Optional[LoopResult] = None
        error: Optional[str] = None

        def _target():
            nonlocal loop_result
            loop_result = loop.run(task)

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join(timeout=self._timeout_seconds)

        duration_ms = int((time.time() - start_time) * 1000)
        finished_at = datetime.now().isoformat()

        if thread.is_alive():
            error = f"Timeout after {self._timeout_seconds}s"
            log.warning("AgentRuntime: %s", error)
            result = RuntimeResult(
                task=task,
                status="timeout",
                loop_result=None,
                total_duration_ms=duration_ms,
                iterations=0,
                error=error,
                started_at=started_at,
                finished_at=finished_at,
            )
        elif loop_result:
            result = RuntimeResult(
                task=task,
                status=loop_result.status,
                loop_result=loop_result.to_dict(),
                total_duration_ms=duration_ms,
                iterations=loop_result.iterations,
                error=loop_result.error,
                started_at=started_at,
                finished_at=finished_at,
            )
        else:
            result = RuntimeResult(
                task=task,
                status="failed",
                loop_result=None,
                total_duration_ms=duration_ms,
                iterations=0,
                error="Loop returned no result",
                started_at=started_at,
                finished_at=finished_at,
            )

        self._run_history.append(result)
        self._write_log(result)

        log.info(
            "AgentRuntime: finished — status=%s, iterations=%d, duration=%dms",
            result.status, result.iterations, result.total_duration_ms,
        )

        return result

    def _write_log(self, result: RuntimeResult) -> None:
        """Write a JSON log file for the run."""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = self._log_dir / f"ralph_run_{timestamp}.json"
            log_path.write_text(result.to_json(indent=2), encoding="utf-8")
            log.info("AgentRuntime: log written to %s", log_path)
        except Exception as exc:
            log.error("AgentRuntime: failed to write log: %s", exc)

    @property
    def run_history(self) -> list[RuntimeResult]:
        """Return list of past run results."""
        return list(self._run_history)

    @property
    def stats(self) -> dict:
        """Aggregate statistics across all runs."""
        total = len(self._run_history)
        if total == 0:
            return {
                "total_runs": 0,
                "completed": 0,
                "failed": 0,
                "avg_duration_ms": 0,
                "avg_iterations": 0,
            }

        completed = sum(1 for r in self._run_history if r.status == "completed")
        failed = sum(1 for r in self._run_history if r.status != "completed")
        avg_duration = sum(r.total_duration_ms for r in self._run_history) / total
        avg_iterations = sum(r.iterations for r in self._run_history) / total

        return {
            "total_runs": total,
            "completed": completed,
            "failed": failed,
            "avg_duration_ms": int(avg_duration),
            "avg_iterations": round(avg_iterations, 1),
        }
