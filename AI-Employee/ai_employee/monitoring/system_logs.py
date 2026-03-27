"""
AI Employee — Centralized Structured Logging

Routes structured log entries to SQLite via Memory:
  - SystemLogger: facade for writing structured logs
  - LogCapture: Python logging.Handler that intercepts ai_employee.* logs
  - LogQuery: query interface for retrieving stored logs
"""

import logging
import traceback
from datetime import datetime
from enum import IntEnum
from typing import Any

log = logging.getLogger("ai_employee.monitoring.logs")


# ── Log Level Enum ───────────────────────────────────────────────────────

class LogLevel(IntEnum):
    """Structured log levels (mirrors Python logging levels)."""
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

    @classmethod
    def from_string(cls, name: str) -> "LogLevel":
        """Convert a level name string to LogLevel."""
        mapping = {
            "DEBUG": cls.DEBUG,
            "INFO": cls.INFO,
            "WARNING": cls.WARNING,
            "ERROR": cls.ERROR,
            "CRITICAL": cls.CRITICAL,
        }
        return mapping.get(name.upper(), cls.INFO)

    @classmethod
    def from_logging_level(cls, level: int) -> "LogLevel":
        """Convert a Python logging level int to LogLevel."""
        if level >= logging.CRITICAL:
            return cls.CRITICAL
        if level >= logging.ERROR:
            return cls.ERROR
        if level >= logging.WARNING:
            return cls.WARNING
        if level >= logging.INFO:
            return cls.INFO
        return cls.DEBUG


# ── Log Query ────────────────────────────────────────────────────────────

class LogQuery:
    """Query interface for retrieving structured logs from Memory."""

    def __init__(self, memory):
        self._memory = memory

    def recent(self, limit: int = 50, level: str | None = None,
               source: str | None = None) -> list[dict]:
        """Get recent log entries with optional filters."""
        return self._memory.get_recent_logs(
            limit=limit, level=level, source=source,
        )

    def errors(self, limit: int = 20) -> list[dict]:
        """Get recent error and critical log entries."""
        results = self._memory.get_recent_logs(limit=limit, level="ERROR")
        results += self._memory.get_recent_logs(limit=limit, level="CRITICAL")
        # Sort by timestamp descending if available, take top N
        results.sort(
            key=lambda x: x.get("timestamp", x.get("created_at", "")),
            reverse=True,
        )
        return results[:limit]

    def by_source(self, source: str, limit: int = 50) -> list[dict]:
        """Get recent log entries from a specific source."""
        return self._memory.get_recent_logs(limit=limit, source=source)


# ── System Logger ────────────────────────────────────────────────────────

class SystemLogger:
    """
    Facade for structured logging that persists to SQLite via Memory.

    Usage:
        syslog = SystemLogger(memory)
        syslog.info("gmail_agent", "Processed 5 emails")
        syslog.error("dashboard", "Server thread crashed", {"port": 8080})
    """

    def __init__(self, memory):
        self._memory = memory
        self._query = LogQuery(memory)

    def log(self, level: LogLevel, source: str, message: str,
            context: dict | None = None) -> None:
        """Write a structured log entry to the database."""
        try:
            self._memory.record_log(
                level=level.name,
                source=source,
                message=message,
                context=context,
            )
        except Exception as exc:
            # Fall back to Python logger if DB write fails
            log.error("Failed to write structured log: %s", exc)

    def debug(self, source: str, message: str,
              context: dict | None = None) -> None:
        self.log(LogLevel.DEBUG, source, message, context)

    def info(self, source: str, message: str,
             context: dict | None = None) -> None:
        self.log(LogLevel.INFO, source, message, context)

    def warning(self, source: str, message: str,
                context: dict | None = None) -> None:
        self.log(LogLevel.WARNING, source, message, context)

    def error(self, source: str, message: str,
              context: dict | None = None) -> None:
        self.log(LogLevel.ERROR, source, message, context)

    def critical(self, source: str, message: str,
                 context: dict | None = None) -> None:
        self.log(LogLevel.CRITICAL, source, message, context)

    def query(self) -> LogQuery:
        """Return a LogQuery for reading stored logs."""
        return self._query


# ── Log Capture Handler ─────────────────────────────────────────────────

class LogCapture(logging.Handler):
    """
    Python logging.Handler that captures log records from ai_employee.*
    loggers and routes them to SystemLogger with source context.

    Usage:
        capture = LogCapture(system_logger)
        capture.install()  # Installs on the root ai_employee logger
    """

    def __init__(self, system_logger: SystemLogger,
                 min_level: int = logging.WARNING):
        super().__init__(level=min_level)
        self._system_logger = system_logger
        self._installed = False

    def emit(self, record: logging.LogRecord) -> None:
        """Handle a log record by routing to SystemLogger."""
        try:
            level = LogLevel.from_logging_level(record.levelno)
            source = record.name  # e.g. "ai_employee.dashboard"

            context: dict[str, Any] = {
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            }

            if record.exc_info and record.exc_info[1]:
                context["exception"] = "".join(
                    traceback.format_exception(*record.exc_info)
                )

            message = record.getMessage()

            self._system_logger.log(level, source, message, context)

        except Exception:
            # Avoid infinite recursion if logging itself fails
            self.handleError(record)

    def install(self) -> None:
        """Install this handler on the ai_employee root logger."""
        if self._installed:
            return
        root_logger = logging.getLogger("ai_employee")
        root_logger.addHandler(self)
        self._installed = True
        log.debug("LogCapture installed on ai_employee logger")

    def uninstall(self) -> None:
        """Remove this handler from the ai_employee root logger."""
        if not self._installed:
            return
        root_logger = logging.getLogger("ai_employee")
        root_logger.removeHandler(self)
        self._installed = False
