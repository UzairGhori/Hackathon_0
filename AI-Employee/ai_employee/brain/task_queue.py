"""
AI Employee — Priority Task Queue

Thread-safe priority queue that orders tasks by urgency level.

Features:
  - Heap-based ordering: CRITICAL > HIGH > MEDIUM > LOW
  - Persistent to JSON (survives restarts)
  - De-duplication by task_id
  - Status tracking per item (pending → running → completed/failed)
  - Filter / peek / drain operations

Each item in the queue wraps a TaskIntelligenceResult with execution
metadata: retry count, timestamps, assigned action, and execution log.
"""

import heapq
import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Iterator

log = logging.getLogger("ai_employee.queue")


# ── Priority ordering (lower number = higher priority) ───────────────────

class _UrgencyRank(IntEnum):
    """Numeric rank for heap ordering (min-heap, so lower = first)."""
    CRITICAL = 0
    HIGH     = 1
    MEDIUM   = 2
    LOW      = 3

    @classmethod
    def from_label(cls, label: str) -> "_UrgencyRank":
        return cls[label.upper()] if label.upper() in cls.__members__ else cls.LOW


# ── Task status lifecycle ────────────────────────────────────────────────

class TaskStatus:
    PENDING           = "pending"
    SCHEDULED         = "scheduled"
    RUNNING           = "running"
    COMPLETED         = "completed"
    FAILED            = "failed"
    AWAITING_APPROVAL = "awaiting_approval"
    IGNORED           = "ignored"


# ── Queue item ───────────────────────────────────────────────────────────

@dataclass
class QueuedTask:
    """A task wrapped with queue metadata."""

    # Identity
    task_id: str
    title: str

    # Intelligence output
    category: str
    urgency: str            # LOW | MEDIUM | HIGH | CRITICAL
    confidence: float
    risk_score: float
    requires_approval: bool
    assigned_agent: str
    reasoning: str
    steps: list[str]
    metadata: dict          # sender, deadline, description, required_action

    # Planner decision
    action: str             # execute_now | schedule | ask_manager | ignore
    status: str = TaskStatus.PENDING

    # Execution tracking
    retries: int = 0
    max_retries: int = 3
    last_error: str = ""
    execution_log: list[dict] = field(default_factory=list)
    result: dict = field(default_factory=dict)

    # Scheduling
    scheduled_for: str = ""     # ISO timestamp or empty
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str = ""
    completed_at: str = ""

    # Raw content (for agent execution)
    raw_content: str = ""

    # ── Convenience ──────────────────────────────────────────────────

    @property
    def priority_rank(self) -> int:
        return _UrgencyRank.from_label(self.urgency).value

    def log_attempt(self, success: bool, message: str,
                    result: dict | None = None) -> None:
        """Append an entry to the execution log."""
        self.execution_log.append({
            "attempt": self.retries + 1,
            "success": success,
            "message": message,
            "result": result or {},
            "timestamp": datetime.now().isoformat(),
        })
        if not success:
            self.retries += 1
            self.last_error = message

    @property
    def can_retry(self) -> bool:
        return self.retries < self.max_retries

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    def summary_dict(self) -> dict:
        """Compact output matching the user-requested format."""
        return {
            "task": self.title,
            "priority": self.urgency.lower(),
            "action": self.action,
            "requires_approval": self.requires_approval,
            "status": self.status,
            "assigned_agent": self.assigned_agent,
            "retries": self.retries,
        }

    # ── Heap comparison (min-heap: lower rank = higher priority) ─────

    def __lt__(self, other: "QueuedTask") -> bool:
        if self.priority_rank != other.priority_rank:
            return self.priority_rank < other.priority_rank
        return self.created_at < other.created_at  # FIFO within same level


# ── Priority queue ───────────────────────────────────────────────────────

class TaskQueue:
    """
    Thread-safe, persistent priority queue for AI Employee tasks.

    Items are ordered by urgency: CRITICAL first, LOW last.
    Persisted to a JSON file so the queue survives restarts.
    """

    def __init__(self, persist_path: Path | None = None):
        self._heap: list[QueuedTask] = []
        self._index: dict[str, QueuedTask] = {}   # task_id → item
        self._lock = threading.Lock()
        self._persist_path = persist_path
        self._load()

    # ── Core operations ──────────────────────────────────────────────

    def enqueue(self, task: QueuedTask) -> bool:
        """
        Add a task to the queue.
        Returns False if a task with the same ID already exists.
        """
        with self._lock:
            if task.task_id in self._index:
                log.debug("Task %s already in queue — skipping", task.task_id)
                return False

            heapq.heappush(self._heap, task)
            self._index[task.task_id] = task
            self._save()

            log.info(
                "Enqueued: [%s] '%s' urgency=%s action=%s agent=%s",
                task.task_id, task.title, task.urgency,
                task.action, task.assigned_agent,
            )
            return True

    def dequeue(self) -> QueuedTask | None:
        """
        Pop the highest-priority PENDING task from the queue.
        Returns None if no pending tasks exist.
        """
        with self._lock:
            # Rebuild heap with only pending items at the top
            pending = [t for t in self._heap if t.status == TaskStatus.PENDING]
            if not pending:
                return None

            # Sort to find highest priority
            pending.sort()
            task = pending[0]
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now().isoformat()
            self._save()

            log.info("Dequeued: [%s] '%s' (%s)", task.task_id, task.title, task.urgency)
            return task

    def peek(self) -> QueuedTask | None:
        """Look at the highest-priority pending task without removing it."""
        with self._lock:
            pending = [t for t in self._heap if t.status == TaskStatus.PENDING]
            if not pending:
                return None
            pending.sort()
            return pending[0]

    def get(self, task_id: str) -> QueuedTask | None:
        """Retrieve a specific task by ID."""
        return self._index.get(task_id)

    def update_status(self, task_id: str, status: str,
                      result: dict | None = None) -> None:
        """Update the status of a task in the queue."""
        with self._lock:
            task = self._index.get(task_id)
            if not task:
                return
            task.status = status
            if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.IGNORED):
                task.completed_at = datetime.now().isoformat()
            if result:
                task.result = result
            self._save()

    def mark_for_retry(self, task_id: str, error: str) -> bool:
        """
        Mark a failed task for retry. Returns True if retries remain.
        """
        with self._lock:
            task = self._index.get(task_id)
            if not task:
                return False

            task.log_attempt(success=False, message=error)

            if task.can_retry:
                task.status = TaskStatus.PENDING
                log.warning(
                    "Retry %d/%d for [%s]: %s",
                    task.retries, task.max_retries, task_id, error,
                )
                self._save()
                return True
            else:
                task.status = TaskStatus.FAILED
                log.error(
                    "Max retries reached for [%s]: %s",
                    task_id, error,
                )
                self._save()
                return False

    # ── Bulk queries ─────────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._heap)

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self._heap if t.status == TaskStatus.PENDING)

    def all_tasks(self) -> list[QueuedTask]:
        """Return all tasks, sorted by priority."""
        with self._lock:
            return sorted(self._heap)

    def by_status(self, status: str) -> list[QueuedTask]:
        """Return tasks filtered by status."""
        with self._lock:
            return sorted(t for t in self._heap if t.status == status)

    def pending(self) -> list[QueuedTask]:
        return self.by_status(TaskStatus.PENDING)

    def completed(self) -> list[QueuedTask]:
        return self.by_status(TaskStatus.COMPLETED)

    def failed(self) -> list[QueuedTask]:
        return self.by_status(TaskStatus.FAILED)

    def awaiting_approval(self) -> list[QueuedTask]:
        return self.by_status(TaskStatus.AWAITING_APPROVAL)

    def drain(self) -> Iterator[QueuedTask]:
        """Yield all pending tasks in priority order (destructive)."""
        while True:
            task = self.dequeue()
            if task is None:
                break
            yield task

    def summary(self) -> dict:
        """Return a summary of queue state."""
        with self._lock:
            counts: dict[str, int] = {}
            for t in self._heap:
                counts[t.status] = counts.get(t.status, 0) + 1
            return {
                "total": len(self._heap),
                "by_status": counts,
                "next": self._heap[0].title if self._heap and any(
                    t.status == TaskStatus.PENDING for t in self._heap
                ) else None,
            }

    # ── Persistence ──────────────────────────────────────────────────

    def _save(self) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = [t.to_dict() for t in self._heap]
            self._persist_path.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not persist queue: %s", exc)

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for item in raw:
                task = QueuedTask(**{
                    k: v for k, v in item.items()
                    if k in QueuedTask.__dataclass_fields__
                })
                heapq.heappush(self._heap, task)
                self._index[task.task_id] = task
            log.info("Queue loaded: %d tasks (%d pending)",
                     len(self._heap), self.pending_count)
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            log.warning("Could not load queue, starting fresh: %s", exc)
