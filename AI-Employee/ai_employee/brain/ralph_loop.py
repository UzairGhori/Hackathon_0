"""
AI Employee — Ralph Wiggum Autonomous Loop (Platinum Tier)

A 7-phase cognitive cycle that keeps iterating on a task until completion:

    Observe → Think → Plan → Act → Check → Fix → Repeat

The loop terminates when:
  - All plan steps are completed              (TASK_COMPLETED)
  - Human approval is required and not given  (APPROVAL_REQUIRED)
  - max_iterations is reached                 (MAX_ITERATIONS)
  - Stall detected — N consecutive failures   (NO_PROGRESS)
  - Failure ratio exceeds threshold           (UNRECOVERABLE)
  - Hard timeout reached                      (TIMEOUT)

Platinum Tier integrations:
  - PermissionManager — draft-mode awareness, action gating
  - ApprovalManager   — pause loop when human approval needed
  - ErrorHandler      — structured error classification and recovery
  - AuditLogger       — enterprise audit trail per phase
  - IterationLogger   — detailed NDJSON per-iteration logging
  - FallbackSystem    — agent substitution on failure
  - RetryManager      — smart retry with backoff

Phases:
  1. OBSERVE  — gather plan state, agent health, previous results
  2. THINK    — analyze observation, detect issues, decide direction
  3. PLAN     — create or revise the step-by-step plan
  4. ACT      — dispatch current step to assigned agent
  5. CHECK    — verify action output, validate against expectations
  6. FIX      — if check failed, attempt auto-repair (retry/fallback/replan)
  7. REPEAT   — back to observe (or terminate)
"""

import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional

from ai_employee.brain.task_planner import TaskPlanner, TaskPlan, PlanStep, StepStatus

log = logging.getLogger("ai_employee.ralph_loop")


# ── Enums ────────────────────────────────────────────────────────────────

class LoopPhase(str, Enum):
    OBSERVE = "observe"
    THINK = "think"
    PLAN = "plan"
    ACT = "act"
    CHECK = "check"
    FIX = "fix"
    DONE = "done"


class TerminationReason(str, Enum):
    TASK_COMPLETED = "task_completed"
    APPROVAL_REQUIRED = "approval_required"
    MAX_ITERATIONS = "max_iterations"
    UNRECOVERABLE = "unrecoverable"
    TIMEOUT = "timeout"
    NO_PROGRESS = "no_progress"


# ── Phase result data classes ────────────────────────────────────────────

@dataclass
class ObservationResult:
    iteration: int
    plan_progress: float
    current_step: Optional[str]
    completed_steps: list[str]
    failed_steps: list[str]
    previous_action_result: Optional[dict]
    environment: dict = field(default_factory=dict)
    agent_health: dict = field(default_factory=dict)


@dataclass
class ThinkResult:
    needs_action: bool
    assessment: str
    identified_issues: list[str]
    should_replan: bool
    confidence: float
    requires_approval: bool = False
    approval_reason: str = ""


@dataclass
class PlanResult:
    plan_changed: bool
    current_step_description: str
    assigned_agent: str
    progress: float


@dataclass
class ActResult:
    step_id: str
    agent_name: str
    success: bool
    agent_output: Optional[dict]
    duration_ms: int
    error: Optional[str] = None
    was_draft: bool = False  # True if action was converted to draft (cloud mode)


@dataclass
class CheckResult:
    """Phase 5: Verification of the action output."""
    step_id: str
    passed: bool
    checks_run: list[str]
    failures: list[str]
    expected_outcome: str
    actual_outcome: str
    confidence: float  # 0.0–1.0 how confident we are the step succeeded
    requires_approval: bool = False
    approval_reason: str = ""


@dataclass
class FixResult:
    """Phase 6: Repair attempt after a failed check."""
    attempted: bool
    strategy: str  # "retry", "fallback", "replan", "skip", "escalate"
    success: bool
    agent_used: str = ""
    fix_output: Optional[dict] = None
    error: str = ""
    duration_ms: int = 0


@dataclass
class IterationLog:
    iteration: int
    phase: str
    observation: Optional[dict] = None
    thinking: Optional[dict] = None
    planning: Optional[dict] = None
    action: Optional[dict] = None
    check: Optional[dict] = None
    fix: Optional[dict] = None
    duration_ms: int = 0


@dataclass
class LoopResult:
    task: str
    status: str  # "completed", "failed", "max_iterations", "no_progress", "timeout", "approval_required"
    termination_reason: TerminationReason
    iterations: int
    completed_steps: int
    failed_steps: int
    total_steps: int
    outputs: list[dict] = field(default_factory=list)
    iteration_logs: list[dict] = field(default_factory=list)
    final_plan: Optional[dict] = None
    error: Optional[str] = None
    approval_request_id: Optional[str] = None
    fixes_attempted: int = 0
    fixes_succeeded: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Ralph Loop (Platinum) ───────────────────────────────────────────────

class RalphLoop:
    """
    The 7-phase autonomous loop:
        Observe → Think → Plan → Act → Check → Fix → Repeat

    Parameters
    ----------
    decision_engine : DecisionEngine
        Used for initial task analysis (full_analysis).
    task_planner : TaskPlanner
        Decomposes tasks into steps and revises plans.
    memory : Memory
        Persistent memory for task recording.
    agent_map : dict[str, agent]
        Name→agent mapping.  Each agent must have ``execute(decision, content)``.
    max_iterations : int
        Hard cap on loop iterations.
    stall_threshold : int
        Consecutive failures before declaring NO_PROGRESS.
    error_handler : ErrorHandler | None
        Structured error classification (Platinum).
    approval_manager : ApprovalManager | None
        Submit actions for human approval (Platinum).
    permission_manager : PermissionManager | None
        Draft-mode action gating (Platinum).
    fallback_system : FallbackSystem | None
        Alternative agent routing (Platinum).
    retry_manager : RetryManager | None
        Smart retry with backoff (Platinum).
    iteration_logger : IterationLogger | None
        Structured per-iteration logging (Platinum).
    audit_logger : AuditLogger | None
        Enterprise audit trail (Platinum).
    """

    def __init__(
        self,
        decision_engine,
        task_planner: TaskPlanner,
        memory,
        agent_map: dict,
        max_iterations: int = 10,
        stall_threshold: int = 3,
        # Platinum integrations (all optional for backward compat)
        error_handler=None,
        approval_manager=None,
        permission_manager=None,
        fallback_system=None,
        retry_manager=None,
        iteration_logger=None,
        audit_logger=None,
    ):
        self._decision_engine = decision_engine
        self._planner = task_planner
        self._memory = memory
        self._agent_map = agent_map
        self._max_iterations = max_iterations
        self._stall_threshold = stall_threshold

        # Platinum subsystems
        self._error_handler = error_handler
        self._approval_manager = approval_manager
        self._permissions = permission_manager
        self._fallback = fallback_system
        self._retry_manager = retry_manager
        self._iter_logger = iteration_logger
        self._audit = audit_logger

        # State
        self._plan: Optional[TaskPlan] = None
        self._iteration = 0
        self._consecutive_failures = 0
        self._last_action_result: Optional[ActResult] = None
        self._all_outputs: list[dict] = []
        self._iteration_logs: list[IterationLog] = []
        self._fixes_attempted = 0
        self._fixes_succeeded = 0
        self._run_id: Optional[str] = None

    def run(self, task: str) -> LoopResult:
        """
        Execute the autonomous loop until the task is completed or an
        exit condition is met.
        """
        log.info("Ralph loop started (Platinum): %s", task[:80])
        loop_start = time.time()

        # Start structured logging
        if self._iter_logger:
            self._run_id = self._iter_logger.start_run(task, self._max_iterations)

        try:
            while self._iteration < self._max_iterations:
                self._iteration += 1
                iter_start = time.time()
                iter_log = IterationLog(iteration=self._iteration, phase="started")

                if self._iter_logger and self._run_id:
                    self._iter_logger.start_iteration(self._run_id, self._iteration)

                log.info("--- Iteration %d/%d ---", self._iteration, self._max_iterations)

                # Phase 1: OBSERVE
                t0 = time.time()
                observation = self._observe(task)
                obs_ms = int((time.time() - t0) * 1000)
                iter_log.observation = asdict(observation)
                self._log_phase("observe", asdict(observation), obs_ms, True)

                # Phase 2: THINK
                t0 = time.time()
                thinking = self._think(observation)
                think_ms = int((time.time() - t0) * 1000)
                iter_log.thinking = asdict(thinking)
                self._log_phase("think", asdict(thinking), think_ms, True)

                # Check if approval is required (Think can detect this)
                if thinking.requires_approval:
                    approval_id = self._submit_approval(task, thinking.approval_reason)
                    iter_log.phase = LoopPhase.DONE.value
                    iter_log.duration_ms = int((time.time() - iter_start) * 1000)
                    self._iteration_logs.append(iter_log)
                    self._end_iteration("approval_paused")
                    return self._build_result(
                        task, TerminationReason.APPROVAL_REQUIRED,
                        approval_request_id=approval_id,
                    )

                if not thinking.needs_action and self._plan and self._plan.is_complete:
                    iter_log.phase = LoopPhase.DONE.value
                    iter_log.duration_ms = int((time.time() - iter_start) * 1000)
                    self._iteration_logs.append(iter_log)
                    self._end_iteration("completed")
                    return self._build_result(task, TerminationReason.TASK_COMPLETED)

                # Phase 3: PLAN
                t0 = time.time()
                plan_result = self._plan_phase(task, thinking)
                plan_ms = int((time.time() - t0) * 1000)
                iter_log.planning = asdict(plan_result)
                self._log_phase("plan", asdict(plan_result), plan_ms, True)

                if not self._plan or not self._plan.current_step:
                    iter_log.phase = LoopPhase.DONE.value
                    iter_log.duration_ms = int((time.time() - iter_start) * 1000)
                    self._iteration_logs.append(iter_log)
                    self._end_iteration("failed")
                    if self._plan and self._plan.is_complete:
                        return self._build_result(task, TerminationReason.TASK_COMPLETED)
                    return self._build_result(task, TerminationReason.UNRECOVERABLE,
                                              error="No actionable steps in plan")

                # Phase 4: ACT
                t0 = time.time()
                action_result = self._act()
                act_ms = int((time.time() - t0) * 1000)
                iter_log.action = asdict(action_result)
                self._last_action_result = action_result
                self._log_phase(
                    "act", asdict(action_result), act_ms,
                    action_result.success, action_result.agent_name,
                    action_result.error or "",
                )

                if action_result.success and action_result.agent_output:
                    self._all_outputs.append(action_result.agent_output)

                # Phase 5: CHECK
                t0 = time.time()
                check_result = self._check(action_result)
                check_ms = int((time.time() - t0) * 1000)
                iter_log.check = asdict(check_result)
                self._log_phase(
                    "check", asdict(check_result), check_ms,
                    check_result.passed,
                )

                # Approval gate from check phase
                if check_result.requires_approval:
                    approval_id = self._submit_approval(
                        task, check_result.approval_reason,
                    )
                    iter_log.phase = LoopPhase.CHECK.value
                    iter_log.duration_ms = int((time.time() - iter_start) * 1000)
                    self._iteration_logs.append(iter_log)
                    self._end_iteration("approval_paused")
                    return self._build_result(
                        task, TerminationReason.APPROVAL_REQUIRED,
                        approval_request_id=approval_id,
                    )

                # Phase 6: FIX (only if check failed)
                fix_result = None
                if not check_result.passed:
                    t0 = time.time()
                    fix_result = self._fix(action_result, check_result)
                    fix_ms = int((time.time() - t0) * 1000)
                    iter_log.fix = asdict(fix_result)
                    self._log_phase(
                        "fix", asdict(fix_result), fix_ms,
                        fix_result.success, fix_result.agent_used,
                        fix_result.error,
                    )

                    if fix_result.success:
                        self._consecutive_failures = 0
                        if fix_result.fix_output:
                            self._all_outputs.append(fix_result.fix_output)
                    else:
                        self._consecutive_failures += 1
                else:
                    # Check passed — reset failure counter
                    self._consecutive_failures = 0

                # Determine iteration outcome
                if check_result.passed:
                    outcome = "progressed"
                elif fix_result and fix_result.success:
                    outcome = "fixed"
                else:
                    outcome = "failed"

                iter_log.phase = LoopPhase.FIX.value if fix_result else LoopPhase.CHECK.value
                iter_log.duration_ms = int((time.time() - iter_start) * 1000)
                self._iteration_logs.append(iter_log)
                self._end_iteration(outcome)

                # Check termination conditions after fix
                if self._should_terminate():
                    reason = self._get_termination_reason()
                    return self._build_result(task, reason)

                # Plan complete after this step?
                if self._plan and self._plan.is_complete:
                    return self._build_result(task, TerminationReason.TASK_COMPLETED)

        except Exception as exc:
            log.error("Ralph loop crashed: %s", exc, exc_info=True)
            if self._error_handler:
                self._error_handler.handle(
                    "ralph_loop", exc, str(exc),
                    {"iteration": self._iteration},
                )
            return self._build_result(
                task, TerminationReason.UNRECOVERABLE,
                error=str(exc),
            )

        # Exhausted iterations
        return self._build_result(task, TerminationReason.MAX_ITERATIONS)

    # ── Phase 1: OBSERVE ─────────────────────────────────────────────

    def _observe(self, task: str) -> ObservationResult:
        """Gather current state of the plan, agent health, and environment."""
        completed = []
        failed = []
        current = None
        progress = 0.0

        if self._plan:
            completed = [s.step_id for s in self._plan.steps
                         if s.status == StepStatus.COMPLETED]
            failed = [s.step_id for s in self._plan.steps
                      if s.status == StepStatus.FAILED]
            cs = self._plan.current_step
            current = cs.step_id if cs else None
            progress = self._plan.progress

        prev_result = None
        if self._last_action_result:
            prev_result = asdict(self._last_action_result)

        available_agents = [
            name for name, agent in self._agent_map.items()
            if getattr(agent, "enabled", True)
        ]

        # Agent health from error handler
        agent_health = {}
        if self._error_handler:
            for name in self._agent_map:
                consecutive = self._error_handler._consecutive_by_source.get(name, 0)
                agent_health[name] = {
                    "consecutive_failures": consecutive,
                    "healthy": consecutive < self._stall_threshold,
                }

        return ObservationResult(
            iteration=self._iteration,
            plan_progress=progress,
            current_step=current,
            completed_steps=completed,
            failed_steps=failed,
            previous_action_result=prev_result,
            environment={"available_agents": available_agents},
            agent_health=agent_health,
        )

    # ── Phase 2: THINK ───────────────────────────────────────────────

    def _think(self, observation: ObservationResult) -> ThinkResult:
        """Analyze observation and decide direction."""
        issues = []
        should_replan = False
        needs_action = True
        confidence = 0.8
        requires_approval = False
        approval_reason = ""

        # Check for stalls
        if self._consecutive_failures >= self._stall_threshold:
            issues.append(
                f"Stalled: {self._consecutive_failures} consecutive failures"
            )
            should_replan = True
            confidence = 0.3

        # Check failure ratio
        if self._plan and self._plan.steps:
            total = len(self._plan.steps)
            failed_count = len(observation.failed_steps)
            if total > 0 and failed_count / total > 0.7:
                issues.append(
                    f"High failure ratio: {failed_count}/{total} steps failed"
                )
                needs_action = False
                confidence = 0.1

        # Check if any agent in the plan is unhealthy
        if self._plan and self._plan.current_step:
            agent_name = self._plan.current_step.assigned_agent
            health = observation.agent_health.get(agent_name, {})
            if not health.get("healthy", True):
                issues.append(f"Agent '{agent_name}' is unhealthy")
                should_replan = True
                confidence = 0.4

        # Check if plan is complete
        if self._plan and self._plan.is_complete:
            needs_action = False
            confidence = 1.0

        # First iteration always needs action
        if self._iteration == 1:
            needs_action = True
            confidence = 0.9

        # Permission check: if we're on cloud and current step is FINAL,
        # flag that approval may be required
        if (self._permissions and self._plan and self._plan.current_step):
            step = self._plan.current_step
            agent = step.assigned_agent
            # Check if the step description hints at a FINAL action
            step_lower = step.description.lower()
            final_keywords = [
                "send email", "post facebook", "post instagram",
                "post tweet", "post linkedin", "register payment",
                "confirm invoice", "send whatsapp",
            ]
            if self._permissions.is_cloud:
                for kw in final_keywords:
                    if kw in step_lower:
                        requires_approval = True
                        approval_reason = (
                            f"Step '{step.description}' involves a final action "
                            f"that requires human approval on cloud deployment"
                        )
                        break

        assessment = (
            f"Iteration {observation.iteration}: "
            f"progress={observation.plan_progress:.0%}, "
            f"completed={len(observation.completed_steps)}, "
            f"failed={len(observation.failed_steps)}, "
            f"consecutive_failures={self._consecutive_failures}"
        )

        return ThinkResult(
            needs_action=needs_action,
            assessment=assessment,
            identified_issues=issues,
            should_replan=should_replan,
            confidence=confidence,
            requires_approval=requires_approval,
            approval_reason=approval_reason,
        )

    # ── Phase 3: PLAN ────────────────────────────────────────────────

    def _plan_phase(self, task: str, thinking: ThinkResult) -> PlanResult:
        """Create or revise the plan."""
        plan_changed = False

        if self._plan is None:
            # First iteration — create initial plan
            context = None
            try:
                analysis = self._decision_engine.full_analysis(
                    task_id=f"ralph_{int(time.time())}",
                    content=task,
                )
                context = {
                    "category": analysis.category,
                    "urgency": analysis.urgency,
                    "assigned_agent": analysis.assigned_agent,
                    "risk_score": analysis.risk_score,
                    "steps": analysis.steps,
                }
            except Exception as exc:
                log.warning("Decision engine analysis failed: %s", exc)

            self._plan = self._planner.create_plan(task, context)
            plan_changed = True
            log.info("Plan created: %d steps", len(self._plan.steps))

        elif thinking.should_replan:
            # Revise the plan based on failures
            evaluation_context = {
                "issues": thinking.identified_issues,
                "assessment": thinking.assessment,
                "consecutive_failures": self._consecutive_failures,
            }
            self._plan = self._planner.revise_plan(self._plan, evaluation_context)
            plan_changed = True
            self._consecutive_failures = 0  # Reset after replan
            log.info("Plan revised (rev %d): %d steps",
                     self._plan.revision, len(self._plan.steps))

        current = self._plan.current_step
        return PlanResult(
            plan_changed=plan_changed,
            current_step_description=current.description if current else "",
            assigned_agent=current.assigned_agent if current else "",
            progress=self._plan.progress,
        )

    # ── Phase 4: ACT ─────────────────────────────────────────────────

    def _act(self) -> ActResult:
        """Execute the current step by dispatching to an agent."""
        step = self._plan.current_step
        if not step:
            return ActResult(
                step_id="none", agent_name="none", success=False,
                agent_output=None, duration_ms=0,
                error="No current step to execute",
            )

        step.status = StepStatus.IN_PROGRESS
        agent = self._agent_map.get(step.assigned_agent)

        if not agent:
            step.status = StepStatus.FAILED
            step.error = f"Agent '{step.assigned_agent}' not found"
            log.error("Agent not found: %s", step.assigned_agent)
            return ActResult(
                step_id=step.step_id, agent_name=step.assigned_agent,
                success=False, agent_output=None, duration_ms=0,
                error=step.error,
            )

        if not getattr(agent, "enabled", True):
            step.status = StepStatus.FAILED
            step.error = f"Agent '{step.assigned_agent}' is disabled"
            log.warning("Agent disabled: %s", step.assigned_agent)
            return ActResult(
                step_id=step.step_id, agent_name=step.assigned_agent,
                success=False, agent_output=None, duration_ms=0,
                error=step.error,
            )

        # Build a TaskDecision to pass to the agent
        from ai_employee.brain.decision_engine import TaskDecision, Action

        decision = TaskDecision(
            task_id=f"ralph_{step.step_id}",
            title=step.description,
            category="Admin",
            priority="MEDIUM",
            action=Action.AUTO_EXECUTE,
            confidence=0.8,
            reasoning="Ralph autonomous loop step execution",
            assigned_agent=step.assigned_agent,
            steps=[step.description],
            risk_score=0.2,
        )

        content = self._plan.task_description if self._plan else step.description

        start = time.time()
        try:
            result = agent.execute(decision, content)
            duration_ms = int((time.time() - start) * 1000)

            step.status = StepStatus.COMPLETED
            step.result = result

            # Clear error handler consecutive count on success
            if self._error_handler:
                self._error_handler.clear_consecutive(step.assigned_agent)

            log.info("Step %s completed by %s (%dms)",
                     step.step_id, step.assigned_agent, duration_ms)

            return ActResult(
                step_id=step.step_id, agent_name=step.assigned_agent,
                success=True, agent_output=result, duration_ms=duration_ms,
            )

        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            step.status = StepStatus.FAILED
            step.error = str(exc)
            log.error("Step %s failed: %s (%dms)",
                      step.step_id, exc, duration_ms)

            # Report to error handler
            if self._error_handler:
                self._error_handler.handle(
                    step.assigned_agent, exc, str(exc),
                    {"step_id": step.step_id, "iteration": self._iteration},
                )

            return ActResult(
                step_id=step.step_id, agent_name=step.assigned_agent,
                success=False, agent_output=None, duration_ms=duration_ms,
                error=str(exc),
            )

    # ── Phase 5: CHECK ───────────────────────────────────────────────

    def _check(self, action: ActResult) -> CheckResult:
        """Verify the action output against the expected outcome."""
        step = None
        if self._plan:
            for s in self._plan.steps:
                if s.step_id == action.step_id:
                    step = s
                    break

        expected = step.expected_outcome if step else ""
        checks_run = []
        failures = []
        requires_approval = False
        approval_reason = ""

        # Check 1: Did the action succeed at all?
        checks_run.append("action_success")
        if not action.success:
            failures.append(f"Action failed: {action.error}")

        # Check 2: Did the agent produce output?
        checks_run.append("has_output")
        if action.success and not action.agent_output:
            failures.append("Agent returned no output")

        # Check 3: Inspect output status field if present
        if action.agent_output and isinstance(action.agent_output, dict):
            status = action.agent_output.get("status", "")
            checks_run.append("output_status")
            if status in ("error", "failed", "failure"):
                failures.append(f"Agent output status: {status}")
            elif status in ("needs_approval", "approval_required", "pending_approval"):
                requires_approval = True
                approval_reason = (
                    f"Agent '{action.agent_name}' output requires approval: "
                    f"{action.agent_output.get('message', status)}"
                )

        # Check 4: Draft mode detection
        if action.was_draft:
            checks_run.append("draft_mode")
            requires_approval = True
            approval_reason = (
                f"Action was converted to draft (cloud mode). "
                f"Human approval required to execute."
            )

        # Check 5: High-risk output check
        if action.agent_output and isinstance(action.agent_output, dict):
            risk = action.agent_output.get("risk_level", "")
            if risk in ("high", "critical"):
                checks_run.append("risk_level")
                requires_approval = True
                approval_reason = f"High-risk output (risk={risk}) requires approval"

        passed = len(failures) == 0 and not requires_approval
        confidence = 1.0 if passed else (0.3 if failures else 0.6)

        actual = ""
        if action.agent_output and isinstance(action.agent_output, dict):
            actual = action.agent_output.get("status", str(action.agent_output)[:200])
        elif action.error:
            actual = f"Error: {action.error}"

        return CheckResult(
            step_id=action.step_id,
            passed=passed,
            checks_run=checks_run,
            failures=failures,
            expected_outcome=expected,
            actual_outcome=actual,
            confidence=confidence,
            requires_approval=requires_approval,
            approval_reason=approval_reason,
        )

    # ── Phase 6: FIX ─────────────────────────────────────────────────

    def _fix(self, action: ActResult, check: CheckResult) -> FixResult:
        """Attempt to repair a failed check via retry, fallback, or replan."""
        self._fixes_attempted += 1
        step = None
        if self._plan:
            for s in self._plan.steps:
                if s.step_id == action.step_id:
                    step = s
                    break

        # Strategy 1: Retry via RetryManager
        if self._retry_manager and step:
            agent = self._agent_map.get(action.agent_name)
            if agent:
                try:
                    from ai_employee.brain.decision_engine import TaskDecision, Action
                    decision = TaskDecision(
                        task_id=f"ralph_{step.step_id}_fix",
                        title=f"[RETRY] {step.description}",
                        category="Admin",
                        priority="MEDIUM",
                        action=Action.AUTO_EXECUTE,
                        confidence=0.6,
                        reasoning="Ralph loop fix phase retry",
                        assigned_agent=action.agent_name,
                        steps=[step.description],
                        risk_score=0.3,
                    )
                    content = self._plan.task_description if self._plan else step.description

                    start = time.time()
                    result = agent.execute(decision, content)
                    fix_ms = int((time.time() - start) * 1000)

                    # Retry succeeded
                    step.status = StepStatus.COMPLETED
                    step.result = result
                    step.error = None
                    self._fixes_succeeded += 1

                    if self._error_handler:
                        self._error_handler.clear_consecutive(action.agent_name)

                    log.info("Fix (retry) succeeded for step %s (%dms)",
                             step.step_id, fix_ms)
                    return FixResult(
                        attempted=True, strategy="retry", success=True,
                        agent_used=action.agent_name, fix_output=result,
                        duration_ms=fix_ms,
                    )
                except Exception as exc:
                    log.warning("Fix (retry) failed for step %s: %s",
                                step.step_id, exc)

        # Strategy 2: Fallback to alternative agent
        if self._fallback and step:
            fallback_agent_name = self._fallback.get_fallback(action.agent_name)
            if fallback_agent_name:
                fallback_agent = self._agent_map.get(fallback_agent_name)
                if fallback_agent and getattr(fallback_agent, "enabled", True):
                    try:
                        from ai_employee.brain.decision_engine import TaskDecision, Action
                        decision = TaskDecision(
                            task_id=f"ralph_{step.step_id}_fallback",
                            title=f"[FALLBACK] {step.description}",
                            category="Admin",
                            priority="MEDIUM",
                            action=Action.AUTO_EXECUTE,
                            confidence=0.5,
                            reasoning=f"Ralph loop fallback: {action.agent_name} → {fallback_agent_name}",
                            assigned_agent=fallback_agent_name,
                            steps=[step.description],
                            risk_score=0.3,
                        )
                        content = self._plan.task_description if self._plan else step.description

                        start = time.time()
                        result = fallback_agent.execute(decision, content)
                        fix_ms = int((time.time() - start) * 1000)

                        step.status = StepStatus.COMPLETED
                        step.result = result
                        step.error = None
                        self._fixes_succeeded += 1

                        log.info("Fix (fallback %s→%s) succeeded for step %s (%dms)",
                                 action.agent_name, fallback_agent_name,
                                 step.step_id, fix_ms)
                        return FixResult(
                            attempted=True, strategy="fallback", success=True,
                            agent_used=fallback_agent_name, fix_output=result,
                            duration_ms=fix_ms,
                        )
                    except Exception as exc:
                        log.warning("Fix (fallback) failed for step %s: %s",
                                    step.step_id, exc)

        # Strategy 3: Skip the step if it's non-critical
        if step and step.dependencies:
            # Has dependents — can't skip
            log.info("Fix: cannot skip step %s (has dependencies)", step.step_id)
        elif step:
            step.status = StepStatus.SKIPPED
            log.info("Fix: skipping non-critical step %s", step.step_id)
            return FixResult(
                attempted=True, strategy="skip", success=True,
                agent_used="", duration_ms=0,
            )

        # All strategies exhausted
        log.warning("Fix: all strategies exhausted for step %s", action.step_id)
        return FixResult(
            attempted=True, strategy="escalate", success=False,
            error="All fix strategies exhausted",
            duration_ms=0,
        )

    # ── Termination logic ────────────────────────────────────────────

    def _should_terminate(self) -> bool:
        """Check if any termination condition is met."""
        # Stall check
        if self._consecutive_failures >= self._stall_threshold:
            return True

        # Failure ratio
        if self._plan and self._plan.steps:
            total = len(self._plan.steps)
            failed_count = len(self._plan.failed_steps)
            if total > 0 and failed_count / total > 0.7:
                return True

        return False

    def _get_termination_reason(self) -> TerminationReason:
        """Determine why we're terminating."""
        if self._consecutive_failures >= self._stall_threshold:
            return TerminationReason.NO_PROGRESS

        if self._plan and self._plan.steps:
            total = len(self._plan.steps)
            failed_count = len(self._plan.failed_steps)
            if total > 0 and failed_count / total > 0.7:
                return TerminationReason.UNRECOVERABLE

        return TerminationReason.UNRECOVERABLE

    # ── Approval integration ─────────────────────────────────────────

    def _submit_approval(self, task: str, reason: str) -> Optional[str]:
        """Submit an approval request and return the request ID."""
        if not self._approval_manager:
            log.warning("Approval required but no ApprovalManager configured")
            return None

        request_id = f"ralph_iter{self._iteration}_{int(time.time())}"
        try:
            self._approval_manager.request_approval(
                request_id=request_id,
                title=f"Ralph Loop: {task[:60]}",
                description=reason,
                proposed_action=f"Continue autonomous execution of: {task[:200]}",
                source="ralph_loop",
                source_agent="ralph_loop",
                priority="HIGH",
                risk_level="medium",
            )
            log.info("Approval submitted: %s — %s", request_id, reason[:80])

            if self._iter_logger and self._run_id:
                self._iter_logger.log_approval_pause(
                    self._run_id, self._iteration, request_id,
                )
        except Exception as exc:
            log.error("Failed to submit approval: %s", exc)

        return request_id

    # ── Logging helpers ──────────────────────────────────────────────

    def _log_phase(
        self,
        phase: str,
        result: dict,
        duration_ms: int,
        success: bool,
        agent_name: str = "",
        error: str = "",
    ) -> None:
        """Log a phase to the iteration logger."""
        if self._iter_logger and self._run_id:
            self._iter_logger.log_phase(
                self._run_id, self._iteration, phase,
                result, duration_ms, success, agent_name, error,
            )

    def _end_iteration(self, outcome: str) -> None:
        """Finalize the current iteration in the logger."""
        progress = self._plan.progress if self._plan else 0.0
        if self._iter_logger and self._run_id:
            self._iter_logger.end_iteration(
                self._run_id, self._iteration, progress, outcome,
            )

    # ── Build result ─────────────────────────────────────────────────

    def _build_result(
        self,
        task: str,
        reason: TerminationReason,
        error: Optional[str] = None,
        approval_request_id: Optional[str] = None,
    ) -> LoopResult:
        """Build the final LoopResult."""
        completed_count = 0
        failed_count = 0
        total_count = 0

        if self._plan:
            total_count = len(self._plan.steps)
            completed_count = sum(
                1 for s in self._plan.steps
                if s.status == StepStatus.COMPLETED
            )
            failed_count = len(self._plan.failed_steps)

        status_map = {
            TerminationReason.TASK_COMPLETED: "completed",
            TerminationReason.APPROVAL_REQUIRED: "approval_required",
            TerminationReason.MAX_ITERATIONS: "max_iterations",
            TerminationReason.UNRECOVERABLE: "failed",
            TerminationReason.TIMEOUT: "timeout",
            TerminationReason.NO_PROGRESS: "no_progress",
        }

        result = LoopResult(
            task=task,
            status=status_map.get(reason, "failed"),
            termination_reason=reason,
            iterations=self._iteration,
            completed_steps=completed_count,
            failed_steps=failed_count,
            total_steps=total_count,
            outputs=self._all_outputs,
            iteration_logs=[asdict(il) for il in self._iteration_logs],
            final_plan=self._plan.to_dict() if self._plan else None,
            error=error,
            approval_request_id=approval_request_id,
            fixes_attempted=self._fixes_attempted,
            fixes_succeeded=self._fixes_succeeded,
        )

        # End the structured log run
        if self._iter_logger and self._run_id:
            self._iter_logger.end_run(
                self._run_id,
                status=result.status,
                reason=reason.value,
                completed_steps=completed_count,
                failed_steps=failed_count,
                total_steps=total_count,
            )

        log.info(
            "Ralph loop finished: status=%s, reason=%s, iterations=%d, "
            "completed=%d/%d, failed=%d, fixes=%d/%d",
            result.status, reason.value, self._iteration,
            completed_count, total_count, failed_count,
            self._fixes_succeeded, self._fixes_attempted,
        )

        # Record in memory
        try:
            self._memory.record_task(
                task_id=f"ralph_{int(time.time())}",
                title=task[:80],
                category="Admin",
                priority="MEDIUM",
                status=result.status,
                approval_required=(reason == TerminationReason.APPROVAL_REQUIRED),
            )
        except Exception as exc:
            log.warning("Failed to record task in memory: %s", exc)

        return result

    @staticmethod
    def _map_termination_reason(reason_text: str) -> TerminationReason:
        """Map evaluation reason text to a TerminationReason."""
        lower = reason_text.lower()
        if "no progress" in lower or "stall" in lower:
            return TerminationReason.NO_PROGRESS
        if "unrecoverable" in lower:
            return TerminationReason.UNRECOVERABLE
        if "timeout" in lower:
            return TerminationReason.TIMEOUT
        if "approval" in lower:
            return TerminationReason.APPROVAL_REQUIRED
        return TerminationReason.UNRECOVERABLE
