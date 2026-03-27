"""
AI Employee — Analytics Engine

Computes real-time metrics and statistics for the dashboard:
  - Task throughput
  - Category distribution
  - Priority breakdown
  - Agent performance
  - Approval rates
"""

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from ai_employee.brain.memory import Memory

log = logging.getLogger("ai_employee.analytics")


class AnalyticsEngine:
    """Computes analytics from Memory data and live queue state."""

    def __init__(self, memory: Memory, settings):
        self._memory = memory
        self._settings = settings

    def compute(self) -> dict:
        """Return a full analytics snapshot."""
        tasks = self._memory.recent_tasks
        stats = self._memory.stats

        return {
            "timestamp": datetime.now().isoformat(),
            "overview": {
                "total_tasks": stats.get("total_tasks", 0),
                "approved": stats.get("approved", 0),
                "rejected": stats.get("rejected", 0),
                "auto_completed": stats.get("auto_completed", 0),
            },
            "queues": self._queue_depths(),
            "category_distribution": self._category_counts(tasks),
            "priority_breakdown": self._priority_counts(tasks),
            "recent_tasks": tasks[-10:],
        }

    def _queue_depths(self) -> dict[str, int]:
        def count(path: Path) -> int:
            return sum(1 for f in path.iterdir() if f.suffix == ".md") if path.exists() else 0

        return {
            "inbox": count(self._settings.inbox_dir),
            "needs_action": count(self._settings.needs_action_dir),
            "done": count(self._settings.done_dir),
            "pending_approval": count(self._settings.approval_dir),
        }

    @staticmethod
    def _category_counts(tasks: list[dict]) -> dict[str, int]:
        return dict(Counter(t.get("category", "Unknown") for t in tasks))

    @staticmethod
    def _priority_counts(tasks: list[dict]) -> dict[str, int]:
        return dict(Counter(t.get("priority", "Unknown") for t in tasks))

    def render_text_report(self) -> str:
        """Render analytics as a terminal-friendly report."""
        data = self.compute()
        o = data["overview"]
        q = data["queues"]

        lines = [
            "=" * 55,
            "  AI Employee — Analytics Report",
            "=" * 55,
            "",
            f"  Total tasks processed : {o['total_tasks']}",
            f"  Auto-completed        : {o['auto_completed']}",
            f"  Approved              : {o['approved']}",
            f"  Rejected              : {o['rejected']}",
            "",
            "  Queue Depths:",
            f"    Inbox              : {q['inbox']}",
            f"    Needs Action       : {q['needs_action']}",
            f"    Done               : {q['done']}",
            f"    Pending Approval   : {q['pending_approval']}",
            "",
        ]

        cats = data["category_distribution"]
        if cats:
            lines.append("  Category Distribution:")
            for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
                bar = "#" * count
                lines.append(f"    {cat:<20} {bar} ({count})")
            lines.append("")

        pris = data["priority_breakdown"]
        if pris:
            lines.append("  Priority Breakdown:")
            for pri, count in sorted(pris.items()):
                bar = "#" * count
                lines.append(f"    {pri:<10} {bar} ({count})")
            lines.append("")

        lines.append("=" * 55)
        return "\n".join(lines)
