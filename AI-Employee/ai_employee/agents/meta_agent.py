"""
AI Employee — Meta Social Agent (Facebook + Instagram)

Autonomous agent that orchestrates social media workflows via Meta Graph API:

  1. POST     — Publish content to Facebook Page and Instagram Business
  2. METRICS  — Fetch engagement metrics for both platforms
  3. SUMMARY  — Generate weekly engagement digests
  4. SAFETY   — Content posts require human approval before publishing

Follows the same interface as other agents:
    execute(decision, content) -> dict
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from ai_employee.integrations.meta_client import MetaClient

log = logging.getLogger("ai_employee.agent.meta")


# ── Safety data classes ─────────────────────────────────────────────────

@dataclass
class MetaSafetyCheck:
    """Result of safety analysis on a Meta social operation."""
    is_safe_to_auto_send: bool
    is_content: bool = True
    flags: list[str] = field(default_factory=list)
    risk_level: str = "medium"  # content posts default to medium

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MetaActionLog:
    """Audit log entry for every Meta Agent action."""
    timestamp: str
    action: str       # "facebook_post", "instagram_post", "metrics_fetch", etc.
    target: str       # description of what was acted upon
    safety: dict
    decision: str     # "auto_execute", "needs_approval", "flagged"
    result: str       # "success", "failed", "pending_approval"
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Meta Social Agent ───────────────────────────────────────────────────

class MetaAgent:
    """
    Social media agent for Facebook Pages and Instagram Business.

    Follows the same interface as GmailAgent, LinkedInAgent, OdooAgent:
    execute(decision, content) -> dict
    """

    def __init__(
        self,
        meta: MetaClient,
        output_dir: Path = Path("."),
        log_dir: Path = Path("."),
    ):
        self._meta = meta
        self._output_dir = output_dir
        self._log_dir = log_dir
        self._action_log: list[MetaActionLog] = []

        # Check if configured
        self.enabled = bool(meta.access_token and meta.access_token != "your-long-lived-page-access-token")

        if self.enabled:
            log.info("MetaAgent enabled (FB page: %s, IG: %s)",
                     meta.page_id or "not set", meta.ig_user_id or "not set")
        else:
            log.info("MetaAgent disabled — META_ACCESS_TOKEN not configured")

    # ── Standard agent interface ──────────────────────────────────────

    def execute(self, decision: dict, content: str = "") -> dict:
        """Execute a Meta social action based on a decision."""
        action = decision.get("action", "")
        if action == "post_facebook":
            return self.post_facebook(
                message=decision.get("message", content),
                link=decision.get("link", ""),
            )
        elif action == "post_instagram":
            return self.post_instagram(
                image_url=decision.get("image_url", ""),
                caption=decision.get("caption", content),
            )
        elif action == "get_metrics":
            return self.get_metrics()
        elif action == "weekly_summary":
            return self.get_weekly_summary()
        else:
            return {"success": False, "error": f"Unknown action: {action}"}

    # ── Facebook ──────────────────────────────────────────────────────

    def post_facebook(self, message: str, link: str = "") -> dict:
        """Post to Facebook Page."""
        safety = self._check_safety("facebook_post", message)
        if not safety.is_safe_to_auto_send:
            self._log_action("facebook_post", message[:80], safety, "needs_approval", "pending_approval")
            return {
                "action": "needs_approval",
                "platform": "facebook",
                "message": message[:80],
                "safety": safety.to_dict(),
            }

        result = self._meta.post_facebook(message=message, link=link)
        status = "success" if result.get("success") else "failed"
        self._log_action("facebook_post", message[:80], safety, "auto_execute", status,
                         result.get("post_id", result.get("error", "")))
        return {**result, "action": "facebook_post", "platform": "facebook"}

    # ── Instagram ─────────────────────────────────────────────────────

    def post_instagram(self, image_url: str, caption: str = "") -> dict:
        """Post an image to Instagram Business."""
        safety = self._check_safety("instagram_post", caption)
        if not safety.is_safe_to_auto_send:
            self._log_action("instagram_post", caption[:80], safety, "needs_approval", "pending_approval")
            return {
                "action": "needs_approval",
                "platform": "instagram",
                "caption": caption[:80],
                "safety": safety.to_dict(),
            }

        result = self._meta.post_instagram(image_url=image_url, caption=caption)
        status = "success" if result.get("success") else "failed"
        self._log_action("instagram_post", caption[:80], safety, "auto_execute", status,
                         result.get("media_id", result.get("error", "")))
        return {**result, "action": "instagram_post", "platform": "instagram"}

    # ── Metrics ───────────────────────────────────────────────────────

    def get_metrics(self) -> dict:
        """Fetch engagement metrics for both platforms."""
        try:
            metrics = self._meta.get_social_metrics()
            self._log_action("metrics_fetch", "all_platforms",
                             MetaSafetyCheck(is_safe_to_auto_send=True, risk_level="low"),
                             "auto_execute", "success")
            return {"action": "metrics_fetch", "success": True, **metrics}
        except Exception as exc:
            self._log_action("metrics_fetch", "all_platforms",
                             MetaSafetyCheck(is_safe_to_auto_send=True, risk_level="low"),
                             "auto_execute", "failed", str(exc))
            return {"action": "metrics_fetch", "success": False, "error": str(exc)}

    def get_weekly_summary(self) -> dict:
        """Generate weekly engagement summary."""
        try:
            summary = self._meta.generate_weekly_summary()
            self._log_action("weekly_summary", "7_day_digest",
                             MetaSafetyCheck(is_safe_to_auto_send=True, risk_level="low"),
                             "auto_execute", "success")
            return {"action": "weekly_summary", "success": True, **summary}
        except Exception as exc:
            self._log_action("weekly_summary", "7_day_digest",
                             MetaSafetyCheck(is_safe_to_auto_send=True, risk_level="low"),
                             "auto_execute", "failed", str(exc))
            return {"action": "weekly_summary", "success": False, "error": str(exc)}

    # ── Process all (called by pipeline phase) ────────────────────────

    def process_social(self, max_items: int = 10) -> list[dict]:
        """
        Main pipeline method — fetch metrics and generate summary.
        Called by AIEmployee.phase_meta().
        """
        results = []

        # Always fetch current metrics
        metrics_result = self.get_metrics()
        results.append(metrics_result)

        # Generate weekly summary
        summary_result = self.get_weekly_summary()
        results.append(summary_result)

        return results

    # ── Safety ────────────────────────────────────────────────────────

    def _check_safety(self, action: str, content: str) -> MetaSafetyCheck:
        """Assess whether a social post can be auto-published."""
        flags = []
        is_safe = True

        # Content posts always need approval (they're public)
        if action in ("facebook_post", "instagram_post"):
            flags.append("public_content_post")
            is_safe = False  # Require approval for all posts

        if len(content) > 500:
            flags.append("long_content")

        risk = "high" if not is_safe else "low"
        return MetaSafetyCheck(
            is_safe_to_auto_send=is_safe,
            flags=flags,
            risk_level=risk,
        )

    # ── Logging ───────────────────────────────────────────────────────

    def _log_action(
        self, action: str, target: str, safety: MetaSafetyCheck,
        decision: str, result: str, details: str = "",
    ) -> None:
        entry = MetaActionLog(
            timestamp=datetime.now().isoformat(),
            action=action,
            target=target,
            safety=safety.to_dict(),
            decision=decision,
            result=result,
            details=details,
        )
        self._action_log.append(entry)
        log.info("META [%s] %s → %s (%s)", action, target[:40], result, decision)

        # Persist to file
        log_file = self._log_dir / "meta_action_log.json"
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            existing = []
            if log_file.exists():
                existing = json.loads(log_file.read_text(encoding="utf-8"))
            existing.append(entry.to_dict())
            log_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        except Exception as exc:
            log.error("Failed to persist meta action log: %s", exc)
