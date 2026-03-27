"""
AI Employee — Persistent Memory Module

SQLite-backed persistent memory for the AI Employee.
Stores task history, decisions, conversation logs, and learned patterns.

Backward-compatible: exposes the same interface as the original JSON version.
On first run, automatically migrates data from legacy memory.json.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_employee.brain.database import Database
from ai_employee.brain.query_engine import QueryEngine

log = logging.getLogger("ai_employee.memory")


class Memory:
    """SQLite-backed persistent memory for the AI Employee."""

    def __init__(self, memory_path: Path):
        """
        Initialize memory.

        Parameters
        ----------
        memory_path : Path
            Legacy JSON path (vault/memory.json).  The SQLite database is
            stored alongside it as ``memory.db``.  If the JSON file exists
            it is auto-migrated into SQLite on first run.
        """
        self._legacy_path = memory_path
        self._db_path = memory_path.with_suffix(".db")
        self._db = Database(self._db_path)
        self._query = QueryEngine(self._db)

        # Auto-migrate from legacy JSON if it exists
        if self._legacy_path.exists():
            count = self._db.migrate_from_json(self._legacy_path)
            if count > 0:
                log.info("Migrated %d tasks from JSON to SQLite", count)

        log.debug("Memory ready: %d tasks on record", self.total_tasks)

    # -- Backward-compatible interface ----------------------------------------

    def record_task(self, task_id: str, title: str, category: str,
                    priority: str, status: str, approval_required: bool) -> None:
        """Record a processed task."""
        if not self._db.task_exists(task_id):
            self._db.insert_task(task_id, title, category, priority,
                                 status, approval_required)
            log.info("Recorded task: %s [%s]", title, status)
        else:
            self._db.update_task_status(task_id, status)
            log.debug("Updated task status: %s -> %s", task_id, status)

    def was_processed(self, task_id: str) -> bool:
        """Check if a task was already processed (O(1) indexed lookup)."""
        return self._db.task_exists(task_id)

    def record_decision(self, task_id: str, decision: str, reason: str) -> None:
        """Log a decision made by the decision engine."""
        self._db.insert_decision(task_id, decision, reason)

    def learn_pattern(self, key: str, value: Any) -> None:
        """Store a learned pattern (e.g., common task types)."""
        self._db.set_pattern(key, value)

    def get_pattern(self, key: str) -> Any:
        """Retrieve a learned pattern by key."""
        return self._db.get_pattern(key)

    def save(self) -> None:
        """No-op — SQLite auto-commits. Kept for backward compatibility."""
        pass

    # -- Statistics (backward-compatible) -------------------------------------

    @property
    def stats(self) -> dict:
        """Aggregate stats matching the legacy format."""
        return self._db.get_stats()

    @property
    def recent_tasks(self) -> list[dict]:
        """Return the last 20 tasks as dicts (legacy format)."""
        rows = self._db.get_recent_tasks(limit=20)
        # Map SQLite columns to legacy dict keys for consumers (analytics.py)
        result = []
        for r in rows:
            result.append({
                "task_id": r["task_id"],
                "title": r["title"],
                "category": r["category"],
                "priority": r["priority"],
                "status": r["status"],
                "approval_required": bool(r["approval_required"]),
                "timestamp": r["created_at"],
            })
        return result

    @property
    def total_tasks(self) -> int:
        return self._db.get_task_count()

    # -- Extended API (new capabilities) --------------------------------------

    def record_log(self, level: str, source: str, message: str,
                   context: dict | None = None) -> None:
        """Write a structured log entry to the database."""
        self._db.insert_log(level, source, message, context)

    def get_recent_logs(self, limit: int = 50, level: str | None = None,
                        source: str | None = None) -> list[dict]:
        """Retrieve recent log entries with optional filters."""
        return self._db.get_recent_logs(limit=limit, level=level, source=source)

    def get_all_patterns(self) -> dict[str, Any]:
        """Return all learned patterns."""
        return self._db.get_all_patterns()

    def get_recent_decisions(self, limit: int = 20) -> list[dict]:
        """Return recent decisions."""
        return self._db.get_recent_decisions(limit=limit)

    # -- Query engine access --------------------------------------------------

    @property
    def query(self) -> QueryEngine:
        """Access the advanced query engine for complex queries."""
        return self._query

    @property
    def db(self) -> Database:
        """Direct database access for advanced use cases."""
        return self._db

    # -- Lifecycle ------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()
