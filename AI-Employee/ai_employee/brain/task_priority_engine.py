"""
AI Employee — Task Priority Engine (Claude API-Powered)

Analyzes task content and determines urgency level using Claude's
reasoning capabilities.

Urgency Levels:
    LOW      — No time pressure, can be done anytime
    MEDIUM   — Should be done soon, has moderate importance
    HIGH     — Time-sensitive, needs attention today
    CRITICAL — Immediate action required, business impact

Also computes:
    - risk_score     (0.0–1.0) — how risky is executing this task
    - requires_approval (bool) — should a human gate this
    - reasoning      (str)     — Claude's explanation

Input:  Raw markdown string
Output: PriorityResult dataclass / JSON dict

Falls back to keyword scoring if Claude API is unavailable.
"""

import json
import logging
from dataclasses import dataclass, asdict
from enum import Enum

import anthropic

log = logging.getLogger("ai_employee.priority")

MODEL = "claude-sonnet-4-5-20250929"


class Urgency(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class PriorityResult:
    urgency: str              # LOW | MEDIUM | HIGH | CRITICAL
    risk_score: float         # 0.0 – 1.0
    requires_approval: bool
    reasoning: str
    urgency_signals: list[str]    # keywords/phrases that drove the score
    suggested_deadline: str       # Claude's inferred deadline

    def to_dict(self) -> dict:
        return asdict(self)


PRIORITY_PROMPT = """You are a Priority Engine inside an AI Employee system.

Analyze the following task and determine its urgency and risk level.

Urgency levels:
- LOW: No time pressure, routine task, can be done anytime
- MEDIUM: Should be done soon, has moderate importance or complexity
- HIGH: Time-sensitive, needs attention today, has a stated deadline
- CRITICAL: Immediate action required, uses words like "urgent", "ASAP", "emergency", business impact

Risk assessment:
- Consider if the task involves external-facing actions (sending emails, publishing, payments)
- Consider if the task involves destructive actions (deleting, removing, canceling)
- Consider if the task involves financial transactions
- Higher risk = more likely to need human approval

Respond ONLY with valid JSON in this exact format (no markdown, no backticks):
{
    "urgency": "<one of: LOW, MEDIUM, HIGH, CRITICAL>",
    "risk_score": <float 0.0 to 1.0>,
    "requires_approval": <true or false>,
    "reasoning": "<1-2 sentences explaining why>",
    "urgency_signals": ["<signal1>", "<signal2>"],
    "suggested_deadline": "<inferred deadline or 'No deadline detected'>"
}

TASK CONTENT:
"""


# ── Keyword scoring tables ───────────────────────────────────────────────

_CRITICAL_KEYWORDS = ["urgent", "asap", "immediately", "emergency", "critical"]
_HIGH_KEYWORDS = ["deadline", "important", "right away", "top priority", "today", "end of day"]
_MEDIUM_KEYWORDS = ["soon", "this week", "next week", "by friday", "by monday"]
_RISK_KEYWORDS = {
    "send": 0.15, "email": 0.15, "publish": 0.20, "post": 0.15,
    "submit": 0.15, "announce": 0.20, "release": 0.20, "deploy": 0.25,
    "payment": 0.30, "invoice": 0.25, "contract": 0.25, "transfer": 0.35,
    "delete": 0.30, "remove": 0.25, "drop": 0.30, "cancel": 0.25,
}


class TaskPriorityEngine:
    """Determines task urgency and risk using Claude API."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else None

    @property
    def ai_enabled(self) -> bool:
        return self._client is not None

    def evaluate(self, content: str) -> PriorityResult:
        """
        Evaluate the urgency and risk of a task.
        Uses Claude API if available, else falls back to keywords.
        """
        if self._client:
            try:
                return self._evaluate_with_claude(content)
            except Exception as exc:
                log.warning("Claude API priority failed, using fallback: %s", exc)

        return self._evaluate_with_keywords(content)

    # ── Claude API path ──────────────────────────────────────────────────

    def _evaluate_with_claude(self, content: str) -> PriorityResult:
        log.debug("Evaluating priority with Claude API...")

        response = self._client.messages.create(
            model=MODEL,
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": PRIORITY_PROMPT + content,
            }],
        )

        raw = response.content[0].text.strip()
        data = json.loads(raw)

        # Validate urgency
        valid = {u.value for u in Urgency}
        urgency = data.get("urgency", "LOW").upper()
        if urgency not in valid:
            urgency = "MEDIUM"

        result = PriorityResult(
            urgency=urgency,
            risk_score=min(max(float(data.get("risk_score", 0.3)), 0.0), 1.0),
            requires_approval=bool(data.get("requires_approval", False)),
            reasoning=data.get("reasoning", "Evaluated by Claude API"),
            urgency_signals=data.get("urgency_signals", []),
            suggested_deadline=data.get("suggested_deadline", "No deadline detected"),
        )

        log.info(
            "Claude priority: %s (risk=%.1f, approval=%s) — %s",
            result.urgency, result.risk_score,
            result.requires_approval, result.reasoning,
        )
        return result

    # ── Keyword fallback path ────────────────────────────────────────────

    def _evaluate_with_keywords(self, content: str) -> PriorityResult:
        lower = content.lower()
        signals: list[str] = []

        # Detect urgency level
        critical_hits = [kw for kw in _CRITICAL_KEYWORDS if kw in lower]
        high_hits = [kw for kw in _HIGH_KEYWORDS if kw in lower]
        medium_hits = [kw for kw in _MEDIUM_KEYWORDS if kw in lower]

        if critical_hits:
            urgency = Urgency.CRITICAL.value
            signals.extend(critical_hits)
        elif high_hits:
            urgency = Urgency.HIGH.value
            signals.extend(high_hits)
        elif medium_hits:
            urgency = Urgency.MEDIUM.value
            signals.extend(medium_hits)
        else:
            # Check complexity (bullet count)
            bullet_count = sum(1 for l in content.splitlines() if l.strip().startswith("- "))
            if bullet_count >= 5:
                urgency = Urgency.MEDIUM.value
                signals.append(f"{bullet_count} requirements listed")
            else:
                urgency = Urgency.LOW.value

        # Compute risk score
        risk = 0.0
        for keyword, score in _RISK_KEYWORDS.items():
            if keyword in lower:
                risk += score
                signals.append(f"risk:{keyword}")
        risk = min(risk, 1.0)

        requires_approval = risk > 0.4 or any(
            kw in lower for kw in ["send", "publish", "post", "payment", "delete"]
        )

        # Build reasoning
        if signals:
            reasoning = f"Keyword analysis: detected {', '.join(signals[:5])}"
        else:
            reasoning = "No urgency or risk signals detected."

        # Infer deadline
        deadline = "No deadline detected"
        if "end of day" in lower or "today" in lower:
            deadline = "Today (end of day)"
        elif "tomorrow" in lower:
            deadline = "Tomorrow"
        elif "this week" in lower:
            deadline = "End of this week"

        return PriorityResult(
            urgency=urgency,
            risk_score=risk,
            requires_approval=requires_approval,
            reasoning=reasoning,
            urgency_signals=signals[:10],
            suggested_deadline=deadline,
        )
