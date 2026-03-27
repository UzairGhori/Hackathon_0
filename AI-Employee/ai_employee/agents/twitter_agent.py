"""
AI Employee — Twitter (X) Social Agent

Autonomous agent that orchestrates Twitter/X workflows via Twitter API v2:

  1. POST      — Publish tweets (requires human approval)
  2. MENTIONS  — Fetch recent mentions
  3. SUMMARY   — Generate weekly engagement digests
  4. SAFETY    — Tweet posts require human approval before publishing

Follows the same interface as other agents:
    execute(decision, content) -> dict
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from ai_employee.integrations.twitter_client import TwitterClient

log = logging.getLogger("ai_employee.agent.twitter")


# ── Safety data classes ─────────────────────────────────────────────────

@dataclass
class TwitterSafetyCheck:
    """Result of safety analysis on a Twitter operation."""
    is_safe_to_auto_send: bool
    is_content: bool = True
    flags: list[str] = field(default_factory=list)
    risk_level: str = "medium"  # content posts default to medium

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TwitterActionLog:
    """Audit log entry for every Twitter Agent action."""
    timestamp: str
    action: str       # "post_tweet", "get_mentions", "weekly_summary", etc.
    target: str       # description of what was acted upon
    safety: dict
    decision: str     # "auto_execute", "needs_approval", "flagged"
    result: str       # "success", "failed", "pending_approval"
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Twitter Social Agent ───────────────────────────────────────────────────

class TwitterAgent:
    """
    Social media agent for Twitter/X.

    Follows the same interface as GmailAgent, LinkedInAgent, OdooAgent, MetaAgent:
    execute(decision, content) -> dict
    """

    def __init__(
        self,
        twitter: TwitterClient,
        output_dir: Path = Path("."),
        log_dir: Path = Path("."),
    ):
        self._twitter = twitter
        self._output_dir = output_dir
        self._log_dir = log_dir
        self._action_log: list[TwitterActionLog] = []

        # Check if configured
        self.enabled = bool(
            twitter.bearer_token
            and twitter.bearer_token != "your-twitter-bearer-token"
        )

        if self.enabled:
            log.info("TwitterAgent enabled")
        else:
            log.info("TwitterAgent disabled — TWITTER_BEARER_TOKEN not configured")

    # ── Standard agent interface ──────────────────────────────────────

    def execute(self, decision, content: str = "") -> dict:
        """Execute a Twitter action based on a decision (dict or TaskDecision)."""
        # Support both dict and TaskDecision dataclass
        auto_approve = False
        if isinstance(decision, dict):
            action = decision.get("action", "")
            text = decision.get("text", content)
            task_id = decision.get("task_id", "")
        else:
            action = getattr(decision, "action", "")
            # Convert Action enum to string if needed
            action = str(action.value) if hasattr(action, "value") else str(action)
            text = content
            task_id = getattr(decision, "task_id", "")

        # Auto-approve when called from Ralph autonomous loop
        if str(task_id).startswith("ralph_"):
            auto_approve = True

        # Infer action from content keywords when action is not tweet-specific
        if action not in ("post_tweet", "get_mentions", "weekly_summary"):
            lower = (content or "").lower()
            if "post" in lower or "tweet" in lower:
                action = "post_tweet"
            elif "mention" in lower:
                action = "get_mentions"
            elif "summary" in lower:
                action = "weekly_summary"
            else:
                action = "post_tweet"  # default for twitter agent

        if action == "post_tweet":
            # Clean tweet text: strip "Post on Twitter:" style prefixes
            clean = re.sub(r'^(?:post\s+(?:on\s+)?(?:twitter|x)\s*[:\-]\s*)', '', text, flags=re.IGNORECASE).strip()
            return self.post_tweet(text=clean or text, auto_approve=auto_approve)
        elif action == "get_mentions":
            return self.get_mentions()
        elif action == "weekly_summary":
            return self.get_weekly_summary()
        else:
            return {"success": False, "error": f"Unknown action: {action}"}

    # ── Tweet posting ─────────────────────────────────────────────────

    def post_tweet(self, text: str, auto_approve: bool = False) -> dict:
        """Post a tweet to Twitter/X."""
        safety = self._check_safety("post_tweet", text)
        if not safety.is_safe_to_auto_send and not auto_approve:
            self._log_action("post_tweet", text[:80], safety, "needs_approval", "pending_approval")
            return {
                "action": "needs_approval",
                "platform": "twitter",
                "text": text[:80],
                "safety": safety.to_dict(),
            }

        result = self._twitter.post_tweet(text=text)
        status = "success" if result.get("success") else "failed"
        self._log_action("post_tweet", text[:80], safety, "auto_execute", status,
                         result.get("tweet_id", result.get("error", "")))
        return {**result, "action": "post_tweet", "platform": "twitter"}

    # ── Mentions ──────────────────────────────────────────────────────

    def get_mentions(self, max_results: int = 25) -> dict:
        """Fetch recent mentions."""
        try:
            mentions = self._twitter.get_mentions(max_results=max_results)
            self._log_action("get_mentions", f"{len(mentions)} mentions",
                             TwitterSafetyCheck(is_safe_to_auto_send=True, risk_level="low"),
                             "auto_execute", "success")
            return {"action": "get_mentions", "success": True, "mentions": mentions,
                    "count": len(mentions)}
        except Exception as exc:
            self._log_action("get_mentions", "all",
                             TwitterSafetyCheck(is_safe_to_auto_send=True, risk_level="low"),
                             "auto_execute", "failed", str(exc))
            return {"action": "get_mentions", "success": False, "error": str(exc)}

    # ── Weekly summary ────────────────────────────────────────────────

    def get_weekly_summary(self) -> dict:
        """Generate weekly engagement summary."""
        try:
            summary = self._twitter.generate_weekly_summary()
            self._log_action("weekly_summary", "7_day_digest",
                             TwitterSafetyCheck(is_safe_to_auto_send=True, risk_level="low"),
                             "auto_execute", "success")
            return {"action": "weekly_summary", "success": True, **summary}
        except Exception as exc:
            self._log_action("weekly_summary", "7_day_digest",
                             TwitterSafetyCheck(is_safe_to_auto_send=True, risk_level="low"),
                             "auto_execute", "failed", str(exc))
            return {"action": "weekly_summary", "success": False, "error": str(exc)}

    # ── Process all (called by pipeline phase) ────────────────────────

    def process_social(self, max_items: int = 10) -> list[dict]:
        """
        Main pipeline method — fetch mentions and generate summary.
        Called by AIEmployee.phase_twitter().
        """
        results = []

        # Fetch recent mentions
        mentions_result = self.get_mentions(max_results=max_items)
        results.append(mentions_result)

        # Generate weekly summary
        summary_result = self.get_weekly_summary()
        results.append(summary_result)

        return results

    # ── Safety ────────────────────────────────────────────────────────

    def _check_safety(self, action: str, content: str) -> TwitterSafetyCheck:
        """Assess whether a tweet can be auto-published."""
        flags = []
        is_safe = True

        # Content posts always need approval (they're public)
        if action == "post_tweet":
            flags.append("public_content_post")
            is_safe = False  # Require approval for all tweets

        if len(content) > 280:
            flags.append("exceeds_character_limit")

        risk = "high" if not is_safe else "low"
        return TwitterSafetyCheck(
            is_safe_to_auto_send=is_safe,
            flags=flags,
            risk_level=risk,
        )

    # ── Logging ───────────────────────────────────────────────────────

    def _log_action(
        self, action: str, target: str, safety: TwitterSafetyCheck,
        decision: str, result: str, details: str = "",
    ) -> None:
        entry = TwitterActionLog(
            timestamp=datetime.now().isoformat(),
            action=action,
            target=target,
            safety=safety.to_dict(),
            decision=decision,
            result=result,
            details=details,
        )
        self._action_log.append(entry)
        log.info("TWITTER [%s] %s → %s (%s)", action, target[:40], result, decision)

        # Persist to file
        log_file = self._log_dir / "twitter_action_log.json"
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            existing = []
            if log_file.exists():
                existing = json.loads(log_file.read_text(encoding="utf-8"))
            existing.append(entry.to_dict())
            log_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        except Exception as exc:
            log.error("Failed to persist twitter action log: %s", exc)
