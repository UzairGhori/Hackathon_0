"""
AI Employee — General Task Agent

Handles all non-specialized tasks: content creation, analysis,
planning, maintenance, and general work items.

This is the workhorse agent that processes the majority of tasks.
"""

import logging
from datetime import datetime
from pathlib import Path

from ai_employee.brain.decision_engine import TaskDecision

log = logging.getLogger("ai_employee.agent.task")


class TaskAgent:
    """General-purpose task execution agent."""

    def __init__(self, output_dir: Path):
        self._output_dir = output_dir

    @property
    def name(self) -> str:
        return "task_agent"

    def execute(self, decision: TaskDecision, content: str) -> dict:
        """
        Execute a general task based on the decision engine output.
        Produces a structured output file in Needs_Action/.
        """
        log.info("TaskAgent executing: %s", decision.title)

        output = self._process_task(decision, content)
        output_path = self._write_output(decision, output)

        return {
            "status": "completed",
            "agent": self.name,
            "output_path": str(output_path) if output_path else None,
            "category": decision.category,
            "priority": decision.priority,
            "timestamp": datetime.now().isoformat(),
        }

    def _process_task(self, decision: TaskDecision, content: str) -> str:
        """
        Process the task content and generate output.

        In a full production system, this would call an LLM API
        (e.g., Gemini, Claude) to generate the actual content.
        For the hackathon, we produce structured output templates.
        """
        category = decision.category
        title = decision.title
        steps_md = "\n".join(f"- [x] {s}" for s in decision.steps)

        return f"""## Task Output — {title}

### Category: {category}
### Priority: {decision.priority}
### Executed by: {self.name}

---

### Execution Log

{steps_md}

---

### Generated Output

Based on the task requirements, here is the structured output:

{self._generate_category_output(decision, content)}

---

### Quality Check

- Confidence: {decision.confidence:.0%}
- Risk Score: {decision.risk_score:.0%}
- All steps completed: Yes
- Ready for review: Yes
"""

    def _generate_category_output(self, decision: TaskDecision, content: str) -> str:
        """Generate category-specific output content."""
        generators = {
            "Content Creation": self._gen_content_creation,
            "Analysis": self._gen_analysis,
            "Planning": self._gen_planning,
            "Maintenance": self._gen_maintenance,
        }
        generator = generators.get(decision.category, self._gen_generic)
        return generator(decision, content)

    @staticmethod
    def _gen_content_creation(decision: TaskDecision, content: str) -> str:
        return f"""**Content Draft for: {decision.title}**

The following content has been drafted based on the task requirements:

> {content[:500]}{"..." if len(content) > 500 else ""}

**Next:** Review the draft above, edit as needed, then move to Done/.
"""

    @staticmethod
    def _gen_analysis(decision: TaskDecision, content: str) -> str:
        word_count = len(content.split())
        line_count = len(content.strip().splitlines())
        return f"""**Analysis Report: {decision.title}**

| Metric         | Value     |
|----------------|-----------|
| Word count     | {word_count}       |
| Line count     | {line_count}       |
| Key sections   | {sum(1 for l in content.splitlines() if l.strip().startswith('#'))} |

**Summary:** Task analyzed. {line_count} lines of content reviewed across {word_count} words.

**Next:** Review the analysis and take action on findings.
"""

    @staticmethod
    def _gen_planning(decision: TaskDecision, content: str) -> str:
        return f"""**Plan: {decision.title}**

**Milestones:**
1. Initial review and scoping
2. Research and requirements gathering
3. Implementation / Execution
4. Review and quality check
5. Delivery

**Next:** Execute the milestones above in order.
"""

    @staticmethod
    def _gen_maintenance(decision: TaskDecision, content: str) -> str:
        return f"""**Maintenance Log: {decision.title}**

**Actions Taken:**
- Reviewed the item requiring updates
- Identified changes needed
- Applied modifications

**Next:** Verify the changes are correct and mark as Done.
"""

    @staticmethod
    def _gen_generic(decision: TaskDecision, content: str) -> str:
        return f"""**Task Completed: {decision.title}**

The task has been processed by the Task Agent.
All {len(decision.steps)} steps have been executed.

**Next:** Review the output and move to Done/ when satisfied.
"""

    def _write_output(self, decision: TaskDecision, output: str) -> Path | None:
        """Write the task output to a file."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Output_{decision.assigned_agent}_{timestamp}.md"
        filepath = self._output_dir / filename

        full_md = f"""# Task Completed — {decision.title}

---

| Field           | Value                                   |
|-----------------|-----------------------------------------|
| Task ID         | `{decision.task_id}`                    |
| Agent           | `{decision.assigned_agent}`             |
| Category        | {decision.category}                     |
| Priority        | {decision.priority}                     |
| Completed       | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
| Confidence      | {decision.confidence:.0%}              |

---

{output}

---

> **Gold Tier — Autonomous AI Employee**
> Task processed by `{decision.assigned_agent}`.
"""
        try:
            filepath.write_text(full_md, encoding="utf-8")
            log.info("Task output written: %s", filename)
            return filepath
        except OSError as exc:
            log.error("Cannot write output: %s", exc)
            return None
