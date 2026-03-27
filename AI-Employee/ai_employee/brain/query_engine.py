"""
AI Employee — Query Engine

High-level query interface for the memory database.
Provides parameterized queries, pagination, full-text search,
and analytics aggregations on top of the Database layer.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ai_employee.brain.database import Database

log = logging.getLogger("ai_employee.query_engine")


@dataclass
class Page:
    """A page of query results."""
    items: list[dict]
    total: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def has_prev(self) -> bool:
        return self.page > 1


class QueryEngine:
    """Advanced query interface over the AI Employee database."""

    def __init__(self, db: Database):
        self._db = db

    # -- Paginated queries ----------------------------------------------------

    def query_tasks(self, *, status: str | None = None,
                    category: str | None = None, priority: str | None = None,
                    search: str | None = None,
                    page: int = 1, page_size: int = 20) -> Page:
        """Query tasks with optional filters and pagination."""
        conditions: list[str] = []
        params: list[Any] = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if priority:
            conditions.append("priority = ?")
            params.append(priority)
        if search:
            conditions.append("(title LIKE ? OR task_id LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        # Count total
        count_sql = f"SELECT COUNT(*) FROM tasks{where}"
        total = self._db._conn.execute(count_sql, params).fetchone()[0]

        # Fetch page
        offset = (page - 1) * page_size
        data_sql = f"SELECT * FROM tasks{where} ORDER BY id DESC LIMIT ? OFFSET ?"
        rows = self._db._conn.execute(data_sql, params + [page_size, offset]).fetchall()

        return Page(
            items=[dict(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def query_decisions(self, *, task_id: str | None = None,
                        decision: str | None = None,
                        page: int = 1, page_size: int = 20) -> Page:
        """Query decisions with optional filters and pagination."""
        conditions: list[str] = []
        params: list[Any] = []

        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)
        if decision:
            conditions.append("decision = ?")
            params.append(decision)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        total = self._db._conn.execute(
            f"SELECT COUNT(*) FROM decisions{where}", params
        ).fetchone()[0]

        offset = (page - 1) * page_size
        rows = self._db._conn.execute(
            f"SELECT * FROM decisions{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()

        return Page(
            items=[dict(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def query_logs(self, *, level: str | None = None,
                   source: str | None = None, search: str | None = None,
                   page: int = 1, page_size: int = 50) -> Page:
        """Query logs with optional filters and pagination."""
        conditions: list[str] = []
        params: list[Any] = []

        if level:
            conditions.append("level = ?")
            params.append(level)
        if source:
            conditions.append("source = ?")
            params.append(source)
        if search:
            conditions.append("message LIKE ?")
            params.append(f"%{search}%")

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        total = self._db._conn.execute(
            f"SELECT COUNT(*) FROM logs{where}", params
        ).fetchone()[0]

        offset = (page - 1) * page_size
        rows = self._db._conn.execute(
            f"SELECT * FROM logs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()

        return Page(
            items=[dict(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    # -- Analytics aggregations -----------------------------------------------

    def tasks_by_category(self) -> dict[str, int]:
        """Count tasks grouped by category."""
        cur = self._db._conn.execute(
            "SELECT category, COUNT(*) as cnt FROM tasks GROUP BY category ORDER BY cnt DESC"
        )
        return {row["category"]: row["cnt"] for row in cur.fetchall()}

    def tasks_by_priority(self) -> dict[str, int]:
        """Count tasks grouped by priority."""
        cur = self._db._conn.execute(
            "SELECT priority, COUNT(*) as cnt FROM tasks GROUP BY priority ORDER BY cnt DESC"
        )
        return {row["priority"]: row["cnt"] for row in cur.fetchall()}

    def tasks_by_status(self) -> dict[str, int]:
        """Count tasks grouped by status."""
        cur = self._db._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status ORDER BY cnt DESC"
        )
        return {row["status"]: row["cnt"] for row in cur.fetchall()}

    def decision_summary(self) -> dict[str, int]:
        """Count decisions grouped by decision type."""
        return self._db.count_decisions_by_type()

    def tasks_over_time(self, days: int = 7) -> list[dict]:
        """Count tasks created per day for the last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cur = self._db._conn.execute(
            """SELECT DATE(created_at) as day, COUNT(*) as cnt
               FROM tasks WHERE created_at >= ?
               GROUP BY DATE(created_at) ORDER BY day""",
            (cutoff,),
        )
        return [{"date": row["day"], "count": row["cnt"]} for row in cur.fetchall()]

    def average_tasks_per_day(self, days: int = 30) -> float:
        """Average tasks per day over the last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cur = self._db._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE created_at >= ?", (cutoff,)
        )
        total = cur.fetchone()[0]
        return round(total / max(days, 1), 2)

    def recent_activity(self, hours: int = 24) -> dict:
        """Summary of activity in the last N hours."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        tasks = self._db._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE created_at >= ?", (cutoff,)
        ).fetchone()[0]

        decisions = self._db._conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE created_at >= ?", (cutoff,)
        ).fetchone()[0]

        logs = self._db._conn.execute(
            "SELECT COUNT(*) FROM logs WHERE created_at >= ?", (cutoff,)
        ).fetchone()[0]

        return {
            "period_hours": hours,
            "tasks_created": tasks,
            "decisions_made": decisions,
            "log_entries": logs,
        }

    # -- Context search -------------------------------------------------------

    def find_related_tasks(self, keyword: str, limit: int = 10) -> list[dict]:
        """Find tasks whose title contains the keyword."""
        cur = self._db._conn.execute(
            "SELECT * FROM tasks WHERE title LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{keyword}%", limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_task_history(self, task_id: str) -> dict:
        """Get full history for a task: task record + all decisions + all logs."""
        task_cur = self._db._conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        )
        task_row = task_cur.fetchone()

        decisions = self._db._conn.execute(
            "SELECT * FROM decisions WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()

        logs = self._db._conn.execute(
            "SELECT * FROM logs WHERE source = ? OR message LIKE ? ORDER BY id",
            (task_id, f"%{task_id}%"),
        ).fetchall()

        return {
            "task": dict(task_row) if task_row else None,
            "decisions": [dict(d) for d in decisions],
            "logs": [dict(l) for l in logs],
        }

    def full_report(self) -> dict:
        """Generate a comprehensive analytics report."""
        stats = self._db.get_stats()
        return {
            "generated_at": datetime.now().isoformat(),
            "overview": stats,
            "by_category": self.tasks_by_category(),
            "by_priority": self.tasks_by_priority(),
            "by_status": self.tasks_by_status(),
            "decisions": self.decision_summary(),
            "last_7_days": self.tasks_over_time(7),
            "avg_per_day_30d": self.average_tasks_per_day(30),
            "last_24h": self.recent_activity(24),
        }
