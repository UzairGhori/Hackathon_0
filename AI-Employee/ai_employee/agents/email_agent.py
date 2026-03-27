"""
AI Employee — Email Agent

Autonomous agent that handles email-related tasks:
  - Drafting replies
  - Sending approved emails
  - Processing approval request emails
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from ai_employee.brain.decision_engine import TaskDecision
from ai_employee.integrations.gmail_client import GmailClient

log = logging.getLogger("ai_employee.agent.email")


class EmailAgent:
    """Handles all email-related task execution."""

    def __init__(self, gmail: GmailClient, output_dir: Path):
        self._gmail = gmail
        self._output_dir = output_dir

    @property
    def name(self) -> str:
        return "email_agent"

    @property
    def enabled(self) -> bool:
        return self._gmail.enabled

    def execute(self, decision: TaskDecision, content: str) -> dict:
        """
        Execute an email task based on the decision engine's output.

        Returns a result dict with status and details.
        """
        log.info("EmailAgent executing: %s", decision.title)

        # Extract email details from the task content
        recipient = self._extract_email(content)
        subject = self._extract_subject(content, decision.title)
        body = self._extract_body(content)

        if decision.action.value == "auto_execute":
            return self._auto_execute(recipient, subject, body, decision)
        else:
            return self._create_draft(recipient, subject, body, decision)

    def _auto_execute(self, to: str, subject: str, body: str,
                      decision: TaskDecision) -> dict:
        """Send email immediately (only for approved auto-execute tasks)."""
        if not to:
            return self._create_draft(to, subject, body, decision)

        success = self._gmail.send(to, subject, body)
        return {
            "status": "sent" if success else "failed",
            "agent": self.name,
            "recipient": to,
            "subject": subject,
            "timestamp": datetime.now().isoformat(),
        }

    def _create_draft(self, to: str, subject: str, body: str,
                      decision: TaskDecision) -> dict:
        """Save an email draft for human review."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        draft_name = f"EmailDraft_{timestamp}.md"
        draft_path = self._output_dir / draft_name

        draft_md = f"""# Email Draft — {subject}

---

| Field      | Value                         |
|------------|-------------------------------|
| To         | {to or 'TBD'}                 |
| Subject    | {subject}                     |
| Priority   | {decision.priority}           |
| Status     | DRAFT — Needs Approval        |
| Created    | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |

---

## Body

{body}

---

> **Action Required:** Review this draft and mark as APPROVED to send.
"""
        draft_path.write_text(draft_md, encoding="utf-8")
        log.info("Email draft saved: %s", draft_name)

        return {
            "status": "draft_created",
            "agent": self.name,
            "draft_path": str(draft_path),
            "timestamp": datetime.now().isoformat(),
        }

    def send_approval_request(self, manager_email: str,
                              decision: TaskDecision) -> bool:
        """Send an approval request to the manager."""
        summary = "\n".join(f"  {i}. {s}" for i, s in enumerate(decision.steps, 1))
        return self._gmail.send_approval_request(
            manager_email, decision.title, summary,
        )

    # ── Content extraction helpers ───────────────────────────────────────

    @staticmethod
    def _extract_email(text: str) -> str:
        match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
        return match.group(0) if match else ""

    @staticmethod
    def _extract_subject(text: str, fallback: str) -> str:
        for line in text.splitlines():
            lower = line.lower().strip()
            if lower.startswith("subject:"):
                return line.split(":", 1)[1].strip()
        return f"Re: {fallback}"

    @staticmethod
    def _extract_body(text: str) -> str:
        lines = text.strip().splitlines()
        body_lines = [
            line for line in lines
            if not line.strip().startswith("#")
            and not re.match(r"^(to|from|subject|date|cc|bcc)\s*:", line, re.IGNORECASE)
        ]
        return "\n".join(body_lines).strip() or "No body content extracted."
