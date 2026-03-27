"""
AI Employee — Task Classifier (Claude API-Powered)

Sends the raw markdown task to Claude and receives a structured
classification with category and confidence score.

Categories:
    Communication | Sales | Marketing | Admin | Finance | Social Media

Input:  Raw markdown string
Output: ClassificationResult (category, confidence, reasoning)

Falls back to keyword-based classification if the API key is missing
or the call fails.
"""

import json
import logging
from dataclasses import dataclass, asdict
from enum import Enum

import anthropic

log = logging.getLogger("ai_employee.classifier")

MODEL = "claude-sonnet-4-5-20250929"


class TaskCategory(str, Enum):
    COMMUNICATION = "Communication"
    SALES = "Sales"
    MARKETING = "Marketing"
    ADMIN = "Admin"
    FINANCE = "Finance"
    SOCIAL_MEDIA = "Social Media"


@dataclass
class ClassificationResult:
    category: str
    confidence: float       # 0.0 – 1.0
    reasoning: str
    sub_category: str       # finer-grained label from Claude
    keywords_detected: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


CLASSIFY_PROMPT = """You are a Task Intelligence Engine inside an AI Employee system.

Analyze the following task and classify it into EXACTLY ONE of these categories:

1. Communication  — emails, replies, messages, calls, meetings
2. Sales          — proposals, pitches, client outreach, deals, CRM
3. Marketing      — social media posts, campaigns, branding, content marketing, SEO
4. Admin          — scheduling, filing, reports, HR tasks, office management
5. Finance        — invoices, budgets, payments, expenses, financial reports
6. Social Media   — LinkedIn posts, tweets, Instagram, platform-specific content

Respond ONLY with valid JSON in this exact format (no markdown, no backticks):
{
    "category": "<one of: Communication, Sales, Marketing, Admin, Finance, Social Media>",
    "confidence": <float 0.0 to 1.0>,
    "reasoning": "<one sentence explaining why>",
    "sub_category": "<more specific label, e.g. Email Reply, LinkedIn Post, Budget Report>",
    "keywords_detected": ["<keyword1>", "<keyword2>"]
}

TASK CONTENT:
"""


# ── Keyword fallback tables ──────────────────────────────────────────────

_KEYWORD_MAP: dict[str, TaskCategory] = {
    "email":      TaskCategory.COMMUNICATION,
    "reply":      TaskCategory.COMMUNICATION,
    "respond":    TaskCategory.COMMUNICATION,
    "message":    TaskCategory.COMMUNICATION,
    "call":       TaskCategory.COMMUNICATION,
    "meeting":    TaskCategory.COMMUNICATION,
    "proposal":   TaskCategory.SALES,
    "pitch":      TaskCategory.SALES,
    "client":     TaskCategory.SALES,
    "deal":       TaskCategory.SALES,
    "lead":       TaskCategory.SALES,
    "revenue":    TaskCategory.SALES,
    "campaign":   TaskCategory.MARKETING,
    "brand":      TaskCategory.MARKETING,
    "seo":        TaskCategory.MARKETING,
    "content":    TaskCategory.MARKETING,
    "audience":   TaskCategory.MARKETING,
    "schedule":   TaskCategory.ADMIN,
    "report":     TaskCategory.ADMIN,
    "organize":   TaskCategory.ADMIN,
    "summary":    TaskCategory.ADMIN,
    "onboarding": TaskCategory.ADMIN,
    "invoice":    TaskCategory.FINANCE,
    "budget":     TaskCategory.FINANCE,
    "payment":    TaskCategory.FINANCE,
    "expense":    TaskCategory.FINANCE,
    "tax":        TaskCategory.FINANCE,
    "linkedin":   TaskCategory.SOCIAL_MEDIA,
    "tweet":      TaskCategory.SOCIAL_MEDIA,
    "post":       TaskCategory.SOCIAL_MEDIA,
    "instagram":  TaskCategory.SOCIAL_MEDIA,
    "hashtag":    TaskCategory.SOCIAL_MEDIA,
}


class TaskClassifier:
    """Classifies tasks using Claude API with keyword fallback."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else None

    @property
    def ai_enabled(self) -> bool:
        return self._client is not None

    def classify(self, content: str) -> ClassificationResult:
        """
        Classify a markdown task into one of 6 categories.
        Uses Claude API if available, else falls back to keywords.
        """
        if self._client:
            try:
                return self._classify_with_claude(content)
            except Exception as exc:
                log.warning("Claude API classification failed, using fallback: %s", exc)

        return self._classify_with_keywords(content)

    # ── Claude API path ──────────────────────────────────────────────────

    def _classify_with_claude(self, content: str) -> ClassificationResult:
        log.debug("Classifying with Claude API...")

        response = self._client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": CLASSIFY_PROMPT + content,
            }],
        )

        raw = response.content[0].text.strip()
        data = json.loads(raw)

        # Validate category
        valid_cats = {c.value for c in TaskCategory}
        cat = data.get("category", "Admin")
        if cat not in valid_cats:
            cat = "Admin"

        result = ClassificationResult(
            category=cat,
            confidence=min(max(float(data.get("confidence", 0.5)), 0.0), 1.0),
            reasoning=data.get("reasoning", "Classified by Claude API"),
            sub_category=data.get("sub_category", cat),
            keywords_detected=data.get("keywords_detected", []),
        )

        log.info(
            "Claude classified: %s (%.0f%%) — %s",
            result.category, result.confidence * 100, result.reasoning,
        )
        return result

    # ── Keyword fallback path ────────────────────────────────────────────

    def _classify_with_keywords(self, content: str) -> ClassificationResult:
        lower = content.lower()
        scores: dict[TaskCategory, int] = {}
        found_keywords: list[str] = []

        for keyword, category in _KEYWORD_MAP.items():
            if keyword in lower:
                scores[category] = scores.get(category, 0) + 1
                found_keywords.append(keyword)

        if not scores:
            return ClassificationResult(
                category=TaskCategory.ADMIN.value,
                confidence=0.3,
                reasoning="No strong category signals detected; defaulting to Admin.",
                sub_category="General Task",
                keywords_detected=[],
            )

        best_cat = max(scores, key=scores.get)
        max_hits = scores[best_cat]
        confidence = min(0.4 + max_hits * 0.15, 0.85)

        return ClassificationResult(
            category=best_cat.value,
            confidence=confidence,
            reasoning=f"Keyword match: {', '.join(found_keywords[:5])}",
            sub_category=best_cat.value,
            keywords_detected=found_keywords[:10],
        )
