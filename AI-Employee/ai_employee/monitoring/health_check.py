"""
AI Employee — System Health Check

Monitors the health of all system components:
  - Directory structure integrity
  - Agent availability
  - Integration connectivity
  - Memory file integrity
  - Queue depths
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger("ai_employee.health")


@dataclass
class ComponentStatus:
    name: str
    healthy: bool
    message: str
    last_checked: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class SystemHealth:
    overall: bool
    uptime_seconds: float
    components: list[ComponentStatus]
    queue_depths: dict[str, int]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class HealthCheck:
    """Runs health checks across the entire AI Employee system."""

    def __init__(self, settings):
        self._settings = settings
        self._start_time = time.time()

    @property
    def uptime(self) -> float:
        return time.time() - self._start_time

    def run(self) -> SystemHealth:
        """Execute all health checks and return a SystemHealth report."""
        components = [
            self._check_directories(),
            self._check_memory(),
            self._check_claude_api(),
            self._check_gmail(),
            self._check_linkedin(),
        ]

        queue_depths = self._measure_queues()
        overall = all(c.healthy for c in components)

        health = SystemHealth(
            overall=overall,
            uptime_seconds=self.uptime,
            components=components,
            queue_depths=queue_depths,
        )

        status = "HEALTHY" if overall else "DEGRADED"
        log.info("Health check: %s (uptime: %.0fs)", status, self.uptime)
        return health

    def _check_directories(self) -> ComponentStatus:
        """Verify all required directories exist."""
        dirs = {
            "inbox": self._settings.inbox_dir,
            "needs_action": self._settings.needs_action_dir,
            "done": self._settings.done_dir,
            "logs": self._settings.log_dir,
            "approval": self._settings.approval_dir,
        }
        missing = [name for name, path in dirs.items() if not path.exists()]
        if missing:
            return ComponentStatus(
                name="directories",
                healthy=False,
                message=f"Missing: {', '.join(missing)}",
            )
        return ComponentStatus(name="directories", healthy=True, message="All directories OK")

    def _check_memory(self) -> ComponentStatus:
        """Verify memory file is accessible."""
        mem_path = self._settings.memory_file
        if mem_path.exists():
            try:
                mem_path.read_text(encoding="utf-8")
                return ComponentStatus(name="memory", healthy=True, message="Memory file OK")
            except OSError as exc:
                return ComponentStatus(name="memory", healthy=False, message=str(exc))
        return ComponentStatus(name="memory", healthy=True, message="Memory file will be created on first use")

    def _check_gmail(self) -> ComponentStatus:
        """Check Gmail configuration."""
        if self._settings.email_address and self._settings.email_password:
            return ComponentStatus(name="gmail", healthy=True, message="Configured")
        return ComponentStatus(name="gmail", healthy=False, message="Not configured (EMAIL_ADDRESS / EMAIL_PASSWORD)")

    def _check_linkedin(self) -> ComponentStatus:
        """Check LinkedIn configuration."""
        if self._settings.linkedin_email and self._settings.linkedin_password:
            return ComponentStatus(name="linkedin", healthy=True, message="Configured")
        return ComponentStatus(name="linkedin", healthy=False, message="Not configured")

    def _check_claude_api(self) -> ComponentStatus:
        """Check Claude API configuration."""
        if self._settings.anthropic_api_key:
            return ComponentStatus(
                name="claude_api", healthy=True,
                message="Configured — Task Intelligence Engine uses Claude API",
            )
        return ComponentStatus(
            name="claude_api", healthy=False,
            message="ANTHROPIC_API_KEY not set — using local keyword fallback",
        )

    def _measure_queues(self) -> dict[str, int]:
        """Count files in each pipeline stage."""
        def count_md(path: Path) -> int:
            if not path.exists():
                return 0
            return sum(1 for f in path.iterdir() if f.suffix == ".md")

        return {
            "inbox": count_md(self._settings.inbox_dir),
            "needs_action": count_md(self._settings.needs_action_dir),
            "done": count_md(self._settings.done_dir),
            "pending_approval": count_md(self._settings.approval_dir),
        }

    def render_report(self, health: SystemHealth) -> str:
        """Render health check as a readable string."""
        lines = [
            f"System Health: {'HEALTHY' if health.overall else 'DEGRADED'}",
            f"Uptime: {health.uptime_seconds:.0f}s",
            "",
            "Components:",
        ]
        for c in health.components:
            icon = "[OK]" if c.healthy else "[!!]"
            lines.append(f"  {icon} {c.name}: {c.message}")

        lines.append("")
        lines.append("Queue Depths:")
        for name, count in health.queue_depths.items():
            lines.append(f"  {name}: {count}")

        return "\n".join(lines)
