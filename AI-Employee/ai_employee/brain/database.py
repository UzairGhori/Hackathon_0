"""
AI Employee — SQLite Database Layer

Manages the SQLite database for persistent memory storage.
Tables: tasks, decisions, logs, patterns.
Thread-safe with WAL mode for concurrent reads.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("ai_employee.database")

# Current schema version — bump when altering tables
SCHEMA_VERSION = 1


class Database:
    """SQLite database manager for the AI Employee memory system."""

    def __init__(self, db_path: Path):
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    # -- Connection management ------------------------------------------------

    @property
    def _conn(self) -> sqlite3.Connection:
        """Thread-local connection with row factory."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def close(self) -> None:
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    # -- Schema ---------------------------------------------------------------

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id     TEXT    NOT NULL UNIQUE,
                title       TEXT    NOT NULL,
                category    TEXT    NOT NULL DEFAULT '',
                priority    TEXT    NOT NULL DEFAULT 'medium',
                status      TEXT    NOT NULL DEFAULT 'pending',
                approval_required INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_task_id  ON tasks(task_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_category ON tasks(category);

            CREATE TABLE IF NOT EXISTS decisions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id     TEXT    NOT NULL,
                decision    TEXT    NOT NULL,
                reason      TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_decisions_task_id  ON decisions(task_id);
            CREATE INDEX IF NOT EXISTS idx_decisions_decision ON decisions(decision);

            CREATE TABLE IF NOT EXISTS logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                level       TEXT    NOT NULL DEFAULT 'info',
                source      TEXT    NOT NULL DEFAULT '',
                message     TEXT    NOT NULL,
                context     TEXT    NOT NULL DEFAULT '{}',
                created_at  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_logs_level  ON logs(level);
            CREATE INDEX IF NOT EXISTS idx_logs_source ON logs(source);

            CREATE TABLE IF NOT EXISTS patterns (
                key         TEXT    PRIMARY KEY,
                value       TEXT    NOT NULL,
                learned_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        conn.commit()

        # Track schema version
        cur = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        row = cur.fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            conn.commit()
        log.debug("Database initialized at %s (schema v%d)", self._path, SCHEMA_VERSION)

    # -- Task CRUD ------------------------------------------------------------

    def insert_task(self, task_id: str, title: str, category: str,
                    priority: str, status: str, approval_required: bool) -> int:
        """Insert a task record. Returns the rowid."""
        now = datetime.now().isoformat()
        cur = self._conn.execute(
            """INSERT INTO tasks (task_id, title, category, priority, status,
                                  approval_required, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, title, category, priority, status,
             int(approval_required), now, now),
        )
        self._conn.commit()
        return cur.lastrowid

    def task_exists(self, task_id: str) -> bool:
        """Check if a task_id already exists."""
        cur = self._conn.execute(
            "SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)
        )
        return cur.fetchone() is not None

    def update_task_status(self, task_id: str, status: str) -> None:
        """Update the status of an existing task."""
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
            (status, now, task_id),
        )
        self._conn.commit()

    def get_recent_tasks(self, limit: int = 20) -> list[dict]:
        """Return the most recent tasks as dicts."""
        cur = self._conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = cur.fetchall()
        # Return in chronological order (oldest first)
        return [dict(r) for r in reversed(rows)]

    def get_task_count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM tasks")
        return cur.fetchone()[0]

    # -- Decision CRUD --------------------------------------------------------

    def insert_decision(self, task_id: str, decision: str, reason: str) -> int:
        """Insert a decision record. Returns the rowid."""
        now = datetime.now().isoformat()
        cur = self._conn.execute(
            "INSERT INTO decisions (task_id, decision, reason, created_at) VALUES (?, ?, ?, ?)",
            (task_id, decision, reason, now),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_recent_decisions(self, limit: int = 20) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in reversed(cur.fetchall())]

    def count_decisions_by_type(self) -> dict[str, int]:
        """Return counts grouped by decision type (approved, rejected, etc.)."""
        cur = self._conn.execute(
            "SELECT decision, COUNT(*) as cnt FROM decisions GROUP BY decision"
        )
        return {row["decision"]: row["cnt"] for row in cur.fetchall()}

    # -- Logs CRUD ------------------------------------------------------------

    def insert_log(self, level: str, source: str, message: str,
                   context: dict | None = None) -> int:
        """Insert a log entry. Returns the rowid."""
        now = datetime.now().isoformat()
        ctx = json.dumps(context or {}, default=str)
        cur = self._conn.execute(
            "INSERT INTO logs (level, source, message, context, created_at) VALUES (?, ?, ?, ?, ?)",
            (level, source, message, ctx, now),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_recent_logs(self, limit: int = 50, level: str | None = None,
                        source: str | None = None) -> list[dict]:
        """Return recent logs, optionally filtered by level or source."""
        query = "SELECT * FROM logs"
        params: list[Any] = []
        conditions = []
        if level:
            conditions.append("level = ?")
            params.append(level)
        if source:
            conditions.append("source = ?")
            params.append(source)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        result = []
        for r in reversed(rows):
            d = dict(r)
            d["context"] = json.loads(d["context"]) if d["context"] else {}
            result.append(d)
        return result

    # -- Patterns CRUD --------------------------------------------------------

    def set_pattern(self, key: str, value: Any) -> None:
        """Insert or update a pattern."""
        now = datetime.now().isoformat()
        val = json.dumps(value, default=str)
        self._conn.execute(
            """INSERT INTO patterns (key, value, learned_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?""",
            (key, val, now, now, val, now),
        )
        self._conn.commit()

    def get_pattern(self, key: str) -> Any:
        """Retrieve a pattern value by key, or None."""
        cur = self._conn.execute(
            "SELECT value FROM patterns WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        return json.loads(row["value"]) if row else None

    def get_all_patterns(self) -> dict[str, Any]:
        cur = self._conn.execute("SELECT key, value FROM patterns")
        return {row["key"]: json.loads(row["value"]) for row in cur.fetchall()}

    # -- Stats ----------------------------------------------------------------

    def get_stats(self) -> dict[str, int]:
        """Compute aggregate stats from the database."""
        total = self.get_task_count()
        decision_counts = self.count_decisions_by_type()

        cur = self._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'auto_completed'"
        )
        auto_completed = cur.fetchone()[0]

        return {
            "total_tasks": total,
            "approved": decision_counts.get("approved", 0),
            "rejected": decision_counts.get("rejected", 0),
            "auto_completed": auto_completed,
        }

    # -- Migration ------------------------------------------------------------

    def migrate_from_json(self, json_path: Path) -> int:
        """Import data from legacy memory.json. Returns count of imported tasks."""
        if not json_path.exists():
            return 0

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read legacy memory JSON: %s", exc)
            return 0

        imported = 0

        # Migrate tasks
        for t in data.get("tasks_processed", []):
            tid = t.get("task_id", "")
            if tid and not self.task_exists(tid):
                now = t.get("timestamp", datetime.now().isoformat())
                self._conn.execute(
                    """INSERT INTO tasks (task_id, title, category, priority,
                                          status, approval_required, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (tid, t.get("title", ""), t.get("category", ""),
                     t.get("priority", "medium"), t.get("status", "pending"),
                     int(t.get("approval_required", False)), now, now),
                )
                imported += 1

        # Migrate decisions
        for d in data.get("decisions", []):
            now = d.get("timestamp", datetime.now().isoformat())
            self._conn.execute(
                "INSERT INTO decisions (task_id, decision, reason, created_at) VALUES (?, ?, ?, ?)",
                (d.get("task_id", ""), d.get("decision", ""),
                 d.get("reason", ""), now),
            )

        # Migrate patterns
        for key, entry in data.get("patterns", {}).items():
            val = entry.get("value", entry) if isinstance(entry, dict) else entry
            learned = entry.get("learned_at", datetime.now().isoformat()) if isinstance(entry, dict) else datetime.now().isoformat()
            self._conn.execute(
                """INSERT OR IGNORE INTO patterns (key, value, learned_at, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (key, json.dumps(val, default=str), learned, learned),
            )

        self._conn.commit()

        if imported > 0:
            log.info("Migrated %d tasks from legacy JSON to SQLite", imported)

        # Rename old file so migration doesn't re-run
        backup = json_path.with_suffix(".json.bak")
        try:
            json_path.rename(backup)
            log.info("Legacy memory.json backed up to %s", backup.name)
        except OSError:
            pass

        return imported
