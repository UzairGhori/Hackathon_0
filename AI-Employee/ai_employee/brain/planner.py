"""
AI Employee — Autonomous Planner (Gold Tier)

The decision-making core of the AI Employee. Receives structured
TaskIntelligenceResults and decides:

  1. EXECUTE NOW   — low-risk, no approval needed → queue + run immediately
  2. SCHEDULE      — defer execution to a later time
  3. ASK MANAGER   — high-risk or external-facing → route to approval
  4. IGNORE        — spam, duplicates, or irrelevant → skip

Maintains an internal TaskQueue (priority-scheduled, persistent).
Drives the Scheduler for execution with retry + logging.
Writes plan files + intelligence JSON for every processed task.

    ┌──────────────┐
    │  Inbox .md   │
    └──────┬───────┘
           ▼
    ┌──────────────┐     ┌───────────────┐
    │  Decision    │────►│  TaskQueue    │
    │  Engine      │     │  (priority)   │
    └──────────────┘     └───────┬───────┘
                                 ▼
                         ┌───────────────┐
                         │  Scheduler    │
                         │  (retry/log)  │
                         └───────┬───────┘
                                 ▼
                     ┌───────┬───────┬───────┐
                     │ Email │ LI    │ Task  │
                     │ Agent │ Agent │ Agent │
                     └───────┴───────┴───────┘
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from ai_employee.brain.decision_engine import (
    DecisionEngine,
    TaskDecision,
    TaskIntelligenceResult,
    Action,
)
from ai_employee.brain.memory import Memory
from ai_employee.brain.task_queue import TaskQueue, QueuedTask, TaskStatus
from ai_employee.brain.scheduler import Scheduler, ExecutionResult

log = logging.getLogger("ai_employee.planner")


# ── Planner action labels ────────────────────────────────────────────────

EXECUTE_NOW = "execute_now"
SCHEDULE    = "schedule"
ASK_MANAGER = "ask_manager"
IGNORE      = "ignore"


class AutonomousPlanner:
    """
    The autonomous decision-maker.

    Receives raw inbox files, runs them through the Intelligence Engine,
    decides what to do, enqueues them, and drives the Scheduler.
    """

    def __init__(self, needs_action_dir: Path, memory: Memory,
                 api_key: str = "", agent_map: dict | None = None,
                 queue_path: Path | None = None):
        self._output_dir = needs_action_dir
        self._memory = memory
        self._engine = DecisionEngine(api_key)

        # Queue + Scheduler
        self._queue = TaskQueue(persist_path=queue_path)
        self._scheduler = Scheduler(
            queue=self._queue,
            memory=memory,
            agent_map=agent_map or {},
            output_dir=needs_action_dir,
        )

    # ── Public properties ────────────────────────────────────────────

    @property
    def queue(self) -> TaskQueue:
        return self._queue

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler

    def set_agents(self, agent_map: dict) -> None:
        """Set/update the agent map (called after agents are initialized)."""
        self._scheduler._agents = agent_map

    # ── Main entry point ─────────────────────────────────────────────

    def create_plan(self, filepath: str) -> str | None:
        """
        Full pipeline for one inbox file:
          1. Skip if already processed
          2. Run Task Intelligence Engine
          3. Decide action (execute / schedule / approve / ignore)
          4. Enqueue the task
          5. Write plan file + intelligence JSON
          6. Record in memory
          7. Return the plan file path
        """
        path = Path(filepath)
        filename = path.name
        task_id = path.stem

        # Skip duplicates
        if self._memory.was_processed(task_id):
            log.info("Skipping already-processed: %s", filename)
            return None

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.error("Cannot read %s: %s", filepath, exc)
            return None

        if not content.strip():
            log.info("Skipping empty file: %s", filename)
            return None

        # ── 1. Run intelligence engine ───────────────────────────────
        intel = self._engine.full_analysis(task_id, content)

        # ── 2. Decide what to do ─────────────────────────────────────
        action = self._decide_action(intel)
        log.info(
            "Decision: [%s] '%s' -> %s (urgency=%s, risk=%.0f%%, approval=%s)",
            task_id, intel.title, action, intel.urgency,
            intel.risk_score * 100, intel.requires_approval,
        )

        # ── 3. Create queued task ────────────────────────────────────
        queued = QueuedTask(
            task_id=task_id,
            title=intel.title,
            category=intel.category,
            urgency=intel.urgency,
            confidence=intel.confidence,
            risk_score=intel.risk_score,
            requires_approval=intel.requires_approval,
            assigned_agent=intel.assigned_agent,
            reasoning=intel.reasoning,
            steps=intel.steps,
            metadata=intel.metadata,
            action=action,
            status=TaskStatus.PENDING,
            raw_content=content,
            scheduled_for=self._compute_schedule(action, intel),
        )
        self._queue.enqueue(queued)

        # ── 4. Write intelligence JSON ───────────────────────────────
        self._write_intel_json(task_id, intel, action, queued)

        # ── 5. Build decision object for plan rendering ──────────────
        decision = TaskDecision(
            task_id=task_id,
            title=intel.title,
            category=intel.category,
            priority=intel.urgency,
            action=self._action_to_enum(action),
            confidence=intel.confidence,
            reasoning=intel.reasoning,
            assigned_agent=intel.assigned_agent,
            steps=intel.steps,
            risk_score=intel.risk_score,
        )

        # ── 6. Write plan markdown ───────────────────────────────────
        plan_md = _render_plan(decision, filename, content, intel, action, queued)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        plan_name = f"Plan_{timestamp}_{filename}"
        plan_path = self._output_dir / plan_name

        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(plan_md, encoding="utf-8")
        except OSError as exc:
            log.error("Cannot write plan: %s", exc)
            return None

        # ── 7. Record in memory ──────────────────────────────────────
        self._memory.record_task(
            task_id=task_id,
            title=intel.title,
            category=intel.category,
            priority=intel.urgency,
            status=action,
            approval_required=intel.requires_approval,
        )

        log.info("Plan created: %s", plan_name)
        return str(plan_path)

    # ── Execute queued tasks ─────────────────────────────────────────

    def execute_pending(self) -> list[ExecutionResult]:
        """Process all pending tasks in the queue through the scheduler."""
        return self._scheduler.process_queue()

    def check_approvals(self, approval_dir: Path) -> list[ExecutionResult]:
        """Check for manager decisions and execute approved tasks."""
        return self._scheduler.process_approvals(approval_dir)

    # ── Decision logic ───────────────────────────────────────────────

    def _decide_action(self, intel: TaskIntelligenceResult) -> str:
        """
        The core decision: what do we do with this task?

        Rules:
          1. If requires_approval or risk > 0.4   → ASK_MANAGER
          2. If urgency is CRITICAL or HIGH        → EXECUTE_NOW
          3. If urgency is MEDIUM                  → SCHEDULE
          4. If confidence < 0.3 (can't classify)  → IGNORE
          5. Otherwise                             → EXECUTE_NOW
        """
        # Rule 4: too ambiguous to act on
        if intel.confidence < 0.3:
            return IGNORE

        # Rule 1: risky or external-facing
        if intel.requires_approval or intel.risk_score > 0.4:
            return ASK_MANAGER

        # Rule 2: urgent tasks run immediately
        if intel.urgency in ("CRITICAL", "HIGH"):
            return EXECUTE_NOW

        # Rule 3: medium urgency → schedule for soon
        if intel.urgency == "MEDIUM":
            return SCHEDULE

        # Rule 5: low-priority but safe → execute
        return EXECUTE_NOW

    @staticmethod
    def _compute_schedule(action: str,
                          intel: TaskIntelligenceResult) -> str:
        """Compute a scheduled execution time for deferred tasks."""
        if action != SCHEDULE:
            return ""

        # Schedule 30 minutes from now for MEDIUM tasks
        scheduled = datetime.now() + timedelta(minutes=30)
        return scheduled.isoformat()

    @staticmethod
    def _action_to_enum(action: str) -> Action:
        return {
            EXECUTE_NOW: Action.AUTO_EXECUTE,
            SCHEDULE:    Action.AUTO_EXECUTE,
            ASK_MANAGER: Action.NEEDS_APPROVAL,
            IGNORE:      Action.NEEDS_REVIEW,
        }.get(action, Action.AUTO_EXECUTE)

    # ── Output writers ───────────────────────────────────────────────

    def _write_intel_json(self, task_id: str,
                          intel: TaskIntelligenceResult,
                          action: str, queued: QueuedTask) -> None:
        """Write the full intelligence + decision JSON."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        json_path = self._output_dir / f"Intel_{task_id}.json"

        output = {
            **intel.to_dict(),
            "planner_decision": {
                "action": action,
                "queue_status": queued.status,
                "scheduled_for": queued.scheduled_for or None,
                "max_retries": queued.max_retries,
            },
            "summary": queued.summary_dict(),
        }

        try:
            json_path.write_text(
                json.dumps(output, indent=2, default=str), encoding="utf-8",
            )
            log.info("Intelligence JSON: Intel_%s.json", task_id)
        except OSError as exc:
            log.warning("Could not write intelligence JSON: %s", exc)

    # ── Reporting ────────────────────────────────────────────────────

    def queue_summary(self) -> dict:
        return self._queue.summary()

    def scheduler_report(self) -> str:
        return self._scheduler.render_report()

    @staticmethod
    def _extract_title(text: str, filename: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return filename.replace(".md", "").replace("_", " ").replace("-", " ").title()


# ── Plan rendering ───────────────────────────────────────────────────────

_ACTION_LABELS = {
    EXECUTE_NOW: "EXECUTE IMMEDIATELY",
    SCHEDULE:    "SCHEDULED",
    ASK_MANAGER: "AWAITING MANAGER APPROVAL",
    IGNORE:      "IGNORED",
}

_ACTION_ICONS = {
    EXECUTE_NOW: ">>",
    SCHEDULE:    "[]",
    ASK_MANAGER: "??",
    IGNORE:      "--",
}


def _render_plan(d: TaskDecision, filename: str, content: str,
                 intel: TaskIntelligenceResult | None,
                 action: str, queued: QueuedTask) -> str:
    """Render a rich plan markdown file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    steps_md = "\n".join(f"{i}. {s}" for i, s in enumerate(d.steps, 1))

    risk_bar = "=" * int(d.risk_score * 10) + "-" * (10 - int(d.risk_score * 10))
    conf_bar = "=" * int(d.confidence * 10) + "-" * (10 - int(d.confidence * 10))

    action_label = _ACTION_LABELS.get(action, action)
    action_icon = _ACTION_ICONS.get(action, "  ")

    # Metadata from intelligence
    meta_section = ""
    if intel:
        meta = intel.metadata
        meta_section = f"""
## Extracted Metadata

| Field            | Value                                    |
|------------------|------------------------------------------|
| Sender           | {meta.get('sender', 'N/A')}              |
| Deadline         | {intel.suggested_deadline}               |
| Description      | {meta.get('description', 'N/A')}        |
| Required Action  | {meta.get('required_action', 'N/A')}    |
| Sub-Category     | {intel.sub_category}                     |
| Parse Method     | `{intel.parse_method}`                   |
| Urgency Signals  | {', '.join(intel.urgency_signals) if intel.urgency_signals else 'None'} |
| Keywords         | {', '.join(intel.keywords_detected) if intel.keywords_detected else 'None'} |

---
"""

    # Queue status section
    sched_line = ""
    if queued.scheduled_for:
        sched_line = f"\n| Scheduled For     | {queued.scheduled_for}                  |"

    return f"""# {action_icon} Task Plan — {d.title}

---

| Field                    | Value                                |
|--------------------------|--------------------------------------|
| Source file               | `Inbox/{filename}`                  |
| Plan created              | {timestamp}                         |
| Category                  | {d.category}                        |
| Urgency                   | **{d.priority}**                    |
| Planner Decision          | **{action_label}**                  |
| Assigned Agent            | `{d.assigned_agent}`                |
| Confidence                | {d.confidence:.0%} [{conf_bar}]     |
| Risk Score                | {d.risk_score:.0%} [{risk_bar}]     |
| Queue Status              | `{queued.status}`                   |
| Max Retries               | {queued.max_retries}                |{sched_line}

---

## Planner Reasoning

> {d.reasoning}

---
{meta_section}
## Original Task

```markdown
{content.strip()}
```

---

## Step-by-Step Plan

{steps_md}

---

## Urgency: {d.priority}

{_priority_text(d.priority)}

---

## Planner Action: {action_label}

{_action_text(action)}

---

## Output Format

```json
{json.dumps(queued.summary_dict(), indent=2)}
```

---

> **Gold Tier — Autonomous Planner**
> Decision: `{action}` | Agent: `{d.assigned_agent}` | Queue: `{queued.status}`
"""


def _priority_text(p: str) -> str:
    return {
        "CRITICAL": "CRITICAL — Multiple urgency indicators. Execute immediately.",
        "HIGH":     "Time-sensitive — needs attention today.",
        "MEDIUM":   "Moderate importance — scheduled for execution soon.",
        "LOW":      "Standard priority — no urgency indicators.",
    }.get(p, "")


def _action_text(a: str) -> str:
    return {
        EXECUTE_NOW: (
            "Task is internal, low-risk, and classified with sufficient confidence. "
            "The assigned agent will execute immediately. Retries are enabled."
        ),
        SCHEDULE: (
            "Task is medium priority and safe to execute. Scheduled for execution "
            "within 30 minutes. Will be picked up in the next scheduler cycle."
        ),
        ASK_MANAGER: (
            "Task involves external-facing or irreversible actions (risk score > 40%). "
            "An approval request has been placed in Needs_Approval/. "
            "Execution is paused until a manager responds with APPROVED or REJECTED."
        ),
        IGNORE: (
            "Task could not be classified with sufficient confidence, or was "
            "identified as a duplicate / irrelevant. No action taken."
        ),
    }.get(a, "")
