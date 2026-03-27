"""
AI Employee — Task Planner (Step Decomposition Engine)

Decomposes a task description into an ordered list of executable steps,
each assigned to a specific agent.  Uses Claude for intelligent decomposition
when an API key is available, otherwise falls back to a local heuristic
based on the AGENT_ROUTING table.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

log = logging.getLogger("ai_employee.task_planner")


# ── Data classes ─────────────────────────────────────────────────────────

class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    step_id: str
    description: str
    assigned_agent: str
    expected_outcome: str
    dependencies: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: Optional[dict] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class TaskPlan:
    task_description: str
    steps: list[PlanStep]
    revision: int = 0
    overall_goal: str = ""

    @property
    def current_step(self) -> Optional[PlanStep]:
        """Return the first step that is PENDING or IN_PROGRESS."""
        for step in self.steps:
            if step.status in (StepStatus.PENDING, StepStatus.IN_PROGRESS):
                return step
        return None

    @property
    def is_complete(self) -> bool:
        """True when every step is COMPLETED or SKIPPED."""
        return all(
            s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
            for s in self.steps
        )

    @property
    def progress(self) -> float:
        """Fraction of steps that are COMPLETED or SKIPPED (0.0–1.0)."""
        if not self.steps:
            return 1.0
        done = sum(
            1 for s in self.steps
            if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
        )
        return done / len(self.steps)

    @property
    def failed_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == StepStatus.FAILED]

    def to_dict(self) -> dict:
        return {
            "task_description": self.task_description,
            "overall_goal": self.overall_goal,
            "revision": self.revision,
            "steps": [s.to_dict() for s in self.steps],
        }


# ── Agent routing (imported lazily to avoid circular deps) ───────────────

def _get_agent_routing() -> dict[str, str]:
    from ai_employee.brain.decision_engine import AGENT_ROUTING
    return AGENT_ROUTING


# ── Task Planner ─────────────────────────────────────────────────────────

class TaskPlanner:
    """Decomposes a task into executable steps with agent assignments."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._client = None
        if api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=api_key)
            except Exception as exc:
                log.warning("Anthropic client init failed, using local fallback: %s", exc)

    def create_plan(self, task: str, context: Optional[dict] = None) -> TaskPlan:
        """
        Decompose *task* into an ordered list of PlanSteps.

        Parameters
        ----------
        task : str
            The task description to decompose.
        context : dict, optional
            Additional context (e.g. from DecisionEngine.full_analysis).

        Returns
        -------
        TaskPlan
        """
        if self._client:
            try:
                return self._create_plan_claude(task, context)
            except Exception as exc:
                log.warning("Claude plan failed, falling back to local: %s", exc)

        return self._create_plan_local(task, context)

    def revise_plan(self, plan: TaskPlan, evaluation: dict) -> TaskPlan:
        """
        Revise a plan based on evaluation feedback (e.g. after failures).

        Returns a new TaskPlan with incremented revision number.
        """
        if self._client:
            try:
                return self._revise_plan_claude(plan, evaluation)
            except Exception as exc:
                log.warning("Claude revise failed, falling back to local: %s", exc)

        return self._revise_plan_local(plan, evaluation)

    # ── Claude-backed planning ───────────────────────────────────────

    def _create_plan_claude(self, task: str, context: Optional[dict]) -> TaskPlan:
        routing = _get_agent_routing()
        available_agents = list(routing.values()) + ["task_agent"]

        context_block = ""
        if context:
            context_block = f"\n\nAdditional context:\n{json.dumps(context, indent=2, default=str)}"

        prompt = (
            f"You are a task-planning AI. Decompose the following task into "
            f"concrete, ordered steps. Each step must be assigned to one of "
            f"these agents: {', '.join(sorted(set(available_agents)))}.\n\n"
            f"Task: {task}{context_block}\n\n"
            f"Return ONLY valid JSON (no markdown fences) with this structure:\n"
            f'{{"overall_goal": "...", "steps": ['
            f'{{"step_id": "step_1", "description": "...", '
            f'"assigned_agent": "...", "expected_outcome": "...", '
            f'"dependencies": []}}, ...]}}'
        )

        response = self._client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3].strip()

        data = json.loads(text)

        steps = []
        for s in data.get("steps", []):
            steps.append(PlanStep(
                step_id=s.get("step_id", f"step_{len(steps)+1}"),
                description=s.get("description", ""),
                assigned_agent=s.get("assigned_agent", "task_agent"),
                expected_outcome=s.get("expected_outcome", ""),
                dependencies=s.get("dependencies", []),
            ))

        plan = TaskPlan(
            task_description=task,
            steps=steps,
            overall_goal=data.get("overall_goal", task),
        )
        log.info("Claude plan created: %d steps", len(steps))
        return plan

    def _revise_plan_claude(self, plan: TaskPlan, evaluation: dict) -> TaskPlan:
        prompt = (
            f"You are a task-planning AI. A previous plan had failures. "
            f"Revise the plan to address the issues.\n\n"
            f"Original plan:\n{json.dumps(plan.to_dict(), indent=2, default=str)}\n\n"
            f"Evaluation:\n{json.dumps(evaluation, indent=2, default=str)}\n\n"
            f"Return ONLY valid JSON (no markdown fences) with the same structure "
            f"as the original plan, with revised steps."
        )

        response = self._client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3].strip()

        data = json.loads(text)

        steps = []
        for s in data.get("steps", []):
            steps.append(PlanStep(
                step_id=s.get("step_id", f"step_{len(steps)+1}"),
                description=s.get("description", ""),
                assigned_agent=s.get("assigned_agent", "task_agent"),
                expected_outcome=s.get("expected_outcome", ""),
                dependencies=s.get("dependencies", []),
            ))

        revised = TaskPlan(
            task_description=plan.task_description,
            steps=steps,
            revision=plan.revision + 1,
            overall_goal=data.get("overall_goal", plan.overall_goal),
        )
        log.info("Claude plan revised (rev %d): %d steps", revised.revision, len(steps))
        return revised

    # ── Local fallback planning ──────────────────────────────────────

    def _create_plan_local(self, task: str, context: Optional[dict]) -> TaskPlan:
        """Build a generic 3-step plan using AGENT_ROUTING."""
        routing = _get_agent_routing()

        # Determine the best agent from context or keyword matching
        agent = "task_agent"
        if context and "assigned_agent" in context:
            agent = context["assigned_agent"]
        else:
            task_lower = task.lower()
            # Direct keyword → agent mapping (checked first)
            _keyword_agents = {
                "twitter": "twitter_agent",
                "tweet":   "twitter_agent",
                "x.com":   "twitter_agent",
                "facebook": "meta_agent",
                "instagram": "meta_agent",
                "linkedin": "linkedin_agent",
                "gmail":   "gmail_agent",
                "email":   "gmail_agent",
                "odoo":    "odoo_agent",
                "invoice": "odoo_agent",
            }
            for keyword, agent_name in _keyword_agents.items():
                if keyword in task_lower:
                    agent = agent_name
                    break
            else:
                # Fallback: match by category name
                for category, agent_name in routing.items():
                    if category.lower() in task_lower:
                        agent = agent_name
                        break

        steps = [
            PlanStep(
                step_id="step_1",
                description=f"Analyze task: {task[:80]}",
                assigned_agent=agent,
                expected_outcome="Task understood and requirements extracted",
            ),
            PlanStep(
                step_id="step_2",
                description=f"Execute: {task[:80]}",
                assigned_agent=agent,
                expected_outcome="Primary task action completed",
                dependencies=["step_1"],
            ),
            PlanStep(
                step_id="step_3",
                description="Verify output and finalize",
                assigned_agent=agent,
                expected_outcome="Output verified and ready for delivery",
                dependencies=["step_2"],
            ),
        ]

        plan = TaskPlan(
            task_description=task,
            steps=steps,
            overall_goal=task,
        )
        log.info("Local plan created: %d steps, agent=%s", len(steps), agent)
        return plan

    def _revise_plan_local(self, plan: TaskPlan, evaluation: dict) -> TaskPlan:
        """Reset failed steps to PENDING with a retry note."""
        new_steps = []
        for step in plan.steps:
            if step.status == StepStatus.FAILED:
                new_steps.append(PlanStep(
                    step_id=step.step_id,
                    description=f"[RETRY] {step.description}",
                    assigned_agent=step.assigned_agent,
                    expected_outcome=step.expected_outcome,
                    dependencies=step.dependencies,
                    status=StepStatus.PENDING,
                ))
            else:
                new_steps.append(step)

        revised = TaskPlan(
            task_description=plan.task_description,
            steps=new_steps,
            revision=plan.revision + 1,
            overall_goal=plan.overall_goal,
        )
        log.info("Local plan revised (rev %d): %d failed steps reset",
                 revised.revision, len(plan.failed_steps))
        return revised
