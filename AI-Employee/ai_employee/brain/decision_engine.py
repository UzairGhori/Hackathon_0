"""
AI Employee — Decision Engine (Gold Tier + Claude API)

The central brain that orchestrates the Task Intelligence Engine:

  1. TaskParser          → extracts metadata (sender, deadline, description)
  2. TaskClassifier      → classifies into 6 categories via Claude
  3. TaskPriorityEngine  → scores urgency (LOW–CRITICAL) and risk via Claude

Combines all three into a unified TaskIntelligenceResult (JSON output)
and produces a TaskDecision for the pipeline.
"""

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum

from ai_employee.brain.task_classifier import TaskClassifier, ClassificationResult
from ai_employee.brain.task_parser import TaskParser, ParsedTask
from ai_employee.brain.task_priority_engine import TaskPriorityEngine, PriorityResult

log = logging.getLogger("ai_employee.decision")


# ── Enums ────────────────────────────────────────────────────────────────

class Action(str, Enum):
    AUTO_EXECUTE = "auto_execute"
    NEEDS_APPROVAL = "needs_approval"
    NEEDS_REVIEW = "needs_review"
    DELEGATE = "delegate"


# ── Agent routing table ──────────────────────────────────────────────────

AGENT_ROUTING: dict[str, str] = {
    "Communication": "gmail_agent",
    "Sales":         "task_agent",
    "Marketing":     "task_agent",
    "Admin":         "task_agent",
    "Finance":       "odoo_agent",
    "Social Media":  "linkedin_agent",
}

# Sub-routing for Social Media: pick the right agent based on keywords
_SOCIAL_KEYWORDS: dict[str, str] = {
    "twitter":   "twitter_agent",
    "tweet":     "twitter_agent",
    "x.com":     "twitter_agent",
    "facebook":  "meta_agent",
    "instagram":  "meta_agent",
    "meta":      "meta_agent",
    "linkedin":  "linkedin_agent",
}


def _route_social_media(content: str) -> str:
    """Return the best social-media agent based on keywords in *content*."""
    lower = content.lower()
    for keyword, agent in _SOCIAL_KEYWORDS.items():
        if keyword in lower:
            return agent
    return "linkedin_agent"  # default for generic social media


# ── Output data structures ───────────────────────────────────────────────

@dataclass
class TaskDecision:
    """The routing decision for pipeline execution."""
    task_id: str
    title: str
    category: str
    priority: str             # urgency level: LOW | MEDIUM | HIGH | CRITICAL
    action: Action
    confidence: float
    reasoning: str
    assigned_agent: str
    steps: list[str]
    risk_score: float

    @property
    def priority_value(self) -> str:
        return self.priority


@dataclass
class TaskIntelligenceResult:
    """
    The unified output of the Task Intelligence Engine.
    Combines parser + classifier + priority into one structured JSON object.
    """
    task_id: str
    title: str
    category: str
    urgency: str
    confidence: float
    metadata: dict              # sender, deadline, description, required_action
    risk_score: float
    requires_approval: bool
    assigned_agent: str
    reasoning: str
    steps: list[str]
    urgency_signals: list[str]
    suggested_deadline: str
    keywords_detected: list[str]
    sub_category: str
    parse_method: str           # "claude" or "local"
    raw_content: str
    analyzed_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


# ── Step generation ──────────────────────────────────────────────────────

_CATEGORY_STEPS: dict[str, list[str]] = {
    "Communication": [
        "Draft the response addressing all points raised.",
        "Ensure tone matches the requested style.",
    ],
    "Sales": [
        "Review the sales context and client history.",
        "Draft the proposal or outreach material.",
        "Include relevant metrics and value propositions.",
    ],
    "Marketing": [
        "Analyze the target audience and campaign goals.",
        "Draft the marketing content with brand voice.",
        "Include call-to-action and engagement hooks.",
    ],
    "Admin": [
        "Gather all required information and documents.",
        "Organize and format the output as requested.",
        "Verify accuracy and completeness.",
    ],
    "Finance": [
        "Collect all relevant financial data.",
        "Run calculations and cross-check figures.",
        "Prepare the financial report or document.",
    ],
    "Social Media": [
        "Draft the social media post with appropriate tone.",
        "Prepare hashtags and media attachments if needed.",
        "Schedule or queue for approval.",
    ],
}


class DecisionEngine:
    """
    Orchestrates the full Task Intelligence Engine pipeline.

    Claude API key is optional — if missing, all three modules
    fall back to local keyword/regex analysis.
    """

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._parser = TaskParser(api_key)
        self._classifier = TaskClassifier(api_key)
        self._priority = TaskPriorityEngine(api_key)

    @property
    def ai_enabled(self) -> bool:
        return bool(self._api_key)

    def analyze(self, task_id: str, title: str,
                content: str) -> TaskDecision:
        """
        Run the full intelligence pipeline and return a TaskDecision
        for the planner/agent pipeline.
        """
        result = self.full_analysis(task_id, content)

        action = self._determine_action(result)

        return TaskDecision(
            task_id=result.task_id,
            title=result.title,
            category=result.category,
            priority=result.urgency,
            action=action,
            confidence=result.confidence,
            reasoning=result.reasoning,
            assigned_agent=result.assigned_agent,
            steps=result.steps,
            risk_score=result.risk_score,
        )

    def full_analysis(self, task_id: str,
                      content: str) -> TaskIntelligenceResult:
        """
        Run all three intelligence modules and return the full
        structured JSON result. This is the primary output.
        """
        # ── 1. Parse metadata ────────────────────────────────────────
        parsed: ParsedTask = self._parser.parse(content, task_id=task_id)

        # ── 2. Classify category ─────────────────────────────────────
        classified: ClassificationResult = self._classifier.classify(content)

        # ── 3. Evaluate priority ─────────────────────────────────────
        priority: PriorityResult = self._priority.evaluate(content)

        # ── 4. Route to agent ────────────────────────────────────────
        agent = AGENT_ROUTING.get(classified.category, "task_agent")
        if classified.category == "Social Media":
            agent = _route_social_media(content)

        # ── 5. Generate steps ────────────────────────────────────────
        steps = self._generate_steps(content, classified.category)

        # ── 6. Build reasoning ───────────────────────────────────────
        reasoning = (
            f"Category: {classified.reasoning} | "
            f"Priority: {priority.reasoning}"
        )

        # ── 7. Compose the unified result ────────────────────────────
        result = TaskIntelligenceResult(
            task_id=parsed.task_id,
            title=parsed.title,
            category=classified.category,
            urgency=priority.urgency,
            confidence=classified.confidence,
            metadata={
                "sender": parsed.sender,
                "deadline": parsed.deadline,
                "description": parsed.description,
                "required_action": parsed.required_action,
                "recipients": parsed.recipients,
                "attachments": parsed.attachments,
            },
            risk_score=priority.risk_score,
            requires_approval=priority.requires_approval,
            assigned_agent=agent,
            reasoning=reasoning,
            steps=steps,
            urgency_signals=priority.urgency_signals,
            suggested_deadline=priority.suggested_deadline,
            keywords_detected=classified.keywords_detected,
            sub_category=classified.sub_category,
            parse_method=parsed.parse_method,
            raw_content=content,
        )

        log.info(
            "Intelligence: [%s] '%s' -> %s | %s | risk=%.1f | agent=%s | method=%s",
            result.task_id, result.title, result.category,
            result.urgency, result.risk_score, result.assigned_agent,
            result.parse_method,
        )

        return result

    # ── Private helpers ──────────────────────────────────────────────────

    @staticmethod
    def _determine_action(result: TaskIntelligenceResult) -> Action:
        if result.requires_approval:
            return Action.NEEDS_APPROVAL
        if result.risk_score > 0.6:
            return Action.NEEDS_REVIEW
        return Action.AUTO_EXECUTE

    @staticmethod
    def _generate_steps(content: str, category: str) -> list[str]:
        steps = ["Read and understand the full task description."]

        bullets = [
            line.strip().lstrip("- ").strip()
            for line in content.splitlines()
            if line.strip().startswith("- ") and not line.strip().startswith("- [")
        ]
        if bullets:
            steps.append("Identify key requirements:")
            for b in bullets:
                steps.append(f"  -> {b}")

        steps.extend(_CATEGORY_STEPS.get(category, ["Break into sub-tasks and execute in order."]))
        steps.append("Write the final output as a markdown file.")
        steps.append("Route the output for review or approval.")
        return steps
