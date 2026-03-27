"""
AI Employee — Advanced Gmail Agent

Autonomous agent that orchestrates the full Gmail workflow:

  1. WATCH   — Poll Gmail inbox for new unread emails
  2. ANALYZE — Send email content to the Task Intelligence Engine
  3. DRAFT   — Generate reply drafts using Claude API
  4. SAFETY  — Enforce safety rules before any send action
  5. SEND    — Auto-send if safe + approved, otherwise create draft

Safety Rules:
  - NEVER auto-send financial approvals or payment confirmations
  - FLAG sensitive emails (passwords, SSN, credit cards, legal)
  - LOG every action to audit trail
  - Require human approval for external-facing financial communication
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from ai_employee.brain.decision_engine import DecisionEngine, TaskDecision
from ai_employee.integrations.gmail_reader import GmailReader, EmailMessage
from ai_employee.integrations.gmail_sender import GmailSender, SendResult

log = logging.getLogger("ai_employee.agent.gmail")


# ── Safety constants ─────────────────────────────────────────────────────

FINANCIAL_KEYWORDS = [
    "invoice", "payment", "transfer", "wire", "bank account",
    "routing number", "iban", "swift", "purchase order", "refund",
    "billing", "credit card", "debit", "payroll", "tax",
    "budget approval", "financial approval", "expense",
]

SENSITIVE_KEYWORDS = [
    "password", "ssn", "social security", "credit card number",
    "cvv", "pin", "secret", "confidential", "nda",
    "legal notice", "subpoena", "lawsuit", "termination",
    "fired", "layoff", "salary", "compensation",
    "medical", "hipaa", "phi", "pii",
]

AUTO_IGNORE_PATTERNS = [
    r"no-?reply@",
    r"noreply@",
    r"mailer-daemon@",
    r"postmaster@",
    r"unsubscribe",
]


@dataclass
class SafetyCheck:
    """Result of the safety analysis on an email."""
    is_safe_to_auto_send: bool
    is_financial: bool
    is_sensitive: bool
    flags: list[str] = field(default_factory=list)
    risk_level: str = "low"   # low, medium, high, critical

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GmailActionLog:
    """Audit log entry for every Gmail Agent action."""
    timestamp: str
    action: str           # "analyzed", "drafted", "sent", "flagged", "ignored"
    message_id: str
    subject: str
    sender: str
    safety: dict
    decision: str
    result: str
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class GmailAgent:
    """
    Advanced Gmail Agent — watches inbox, analyzes with intelligence engine,
    generates Claude-powered reply drafts, enforces safety, and sends.
    """

    def __init__(
        self,
        reader: GmailReader,
        sender: GmailSender,
        decision_engine: DecisionEngine,
        api_key: str = "",
        output_dir: Path = Path("."),
        log_dir: Path = Path("."),
    ):
        self._reader = reader
        self._sender = sender
        self._engine = decision_engine
        self._api_key = api_key
        self._output_dir = output_dir
        self._log_dir = log_dir
        self._action_log: list[GmailActionLog] = []

    @property
    def name(self) -> str:
        return "gmail_agent"

    @property
    def enabled(self) -> bool:
        return self._reader.enabled and self._sender.enabled

    # ── Main entry point ─────────────────────────────────────────────────

    def execute(self, decision: TaskDecision, content: str) -> dict:
        """
        Execute a task routed by the scheduler (for pipeline compatibility).
        Generates a reply draft based on the decision and content.
        """
        log.info("GmailAgent executing task: %s", decision.title)

        draft_body = self._generate_reply_draft(content, decision.title)
        safety = self._check_safety(content + " " + draft_body)

        if not safety.is_safe_to_auto_send or decision.action.value == "needs_approval":
            # Save as draft file for review
            return self._save_draft_file(decision, draft_body, safety)

        # Auto-send is safe
        result = self._sender.send(
            to="",  # No recipient from task — create draft instead
            subject=f"Re: {decision.title}",
            body=draft_body,
        )
        return {
            "status": "draft_created" if not result.success else "sent",
            "agent": self.name,
            "safety": safety.to_dict(),
            "timestamp": datetime.now().isoformat(),
        }

    # ── Inbox processing pipeline ────────────────────────────────────────

    def process_inbox(self, max_emails: int = 10) -> list[dict]:
        """
        Full pipeline: fetch unread → analyze → draft → safety check → act.

        Returns a list of result dicts for each processed email.
        """
        if not self.enabled:
            log.warning("Gmail Agent not enabled — missing credentials")
            return []

        emails = self._reader.fetch_unread(max_results=max_emails)
        if not emails:
            log.info("No new emails to process")
            return []

        results = []
        for email in emails:
            result = self._process_single_email(email)
            results.append(result)

        # Save action log
        self._save_action_log()

        log.info("Processed %d emails: %d sent, %d drafted, %d flagged",
                 len(results),
                 sum(1 for r in results if r["action"] == "sent"),
                 sum(1 for r in results if r["action"] == "drafted"),
                 sum(1 for r in results if r["action"] == "flagged"))

        return results

    def _process_single_email(self, email: EmailMessage) -> dict:
        """Process one email through the full pipeline."""
        log.info("Processing email: '%s' from %s",
                 email.subject, email.sender_email)

        # Step 0: Check if this should be auto-ignored
        if self._should_ignore(email):
            self._log_action("ignored", email, SafetyCheck(
                is_safe_to_auto_send=False, is_financial=False,
                is_sensitive=False, flags=["auto-ignore pattern matched"],
            ), "ignored", "Skipped — matches auto-ignore pattern")
            self._reader.mark_processed(email.message_id)
            return {
                "action": "ignored",
                "message_id": email.message_id,
                "subject": email.subject,
                "reason": "auto-ignore pattern",
            }

        # Step 1: Run through Task Intelligence Engine
        task_content = email.to_markdown()
        intelligence = self._engine.full_analysis(
            task_id=f"email-{email.message_id[:8]}",
            content=task_content,
        )

        # Step 2: Safety check on the email
        safety = self._check_safety(task_content)

        # Step 3: Generate reply draft with Claude
        reply_body = self._generate_reply_draft(
            task_content, email.subject,
        )

        # Step 4: Decide action based on safety + intelligence
        action = self._decide_action(email, intelligence, safety)

        # Step 5: Execute the decision
        result = self._execute_decision(
            action, email, reply_body, safety, intelligence,
        )

        # Step 6: Mark as processed
        self._reader.mark_processed(email.message_id)
        self._reader.mark_as_read(email.message_id)

        return result

    # ── Safety engine ────────────────────────────────────────────────────

    def _check_safety(self, content: str) -> SafetyCheck:
        """Run safety analysis on email content."""
        content_lower = content.lower()
        flags = []
        is_financial = False
        is_sensitive = False

        # Check financial keywords
        for kw in FINANCIAL_KEYWORDS:
            if kw in content_lower:
                is_financial = True
                flags.append(f"FINANCIAL: '{kw}' detected")

        # Check sensitive keywords
        for kw in SENSITIVE_KEYWORDS:
            if kw in content_lower:
                is_sensitive = True
                flags.append(f"SENSITIVE: '{kw}' detected")

        # Determine risk level
        if is_financial and is_sensitive:
            risk_level = "critical"
        elif is_financial:
            risk_level = "high"
        elif is_sensitive:
            risk_level = "medium"
        else:
            risk_level = "low"

        is_safe = not is_financial and risk_level in ("low", "medium")

        return SafetyCheck(
            is_safe_to_auto_send=is_safe,
            is_financial=is_financial,
            is_sensitive=is_sensitive,
            flags=flags,
            risk_level=risk_level,
        )

    def _should_ignore(self, email: EmailMessage) -> bool:
        """Check if an email should be auto-ignored (no-reply, etc.)."""
        sender = email.sender_email.lower()
        for pattern in AUTO_IGNORE_PATTERNS:
            if re.search(pattern, sender):
                return True
        return False

    # ── Decision logic ───────────────────────────────────────────────────

    def _decide_action(self, email: EmailMessage,
                       intelligence, safety: SafetyCheck) -> str:
        """
        Decide what to do with this email.

        Returns: "send", "draft", "flag", "ignore"
        """
        # Rule 1: NEVER auto-send financial content
        if safety.is_financial:
            log.warning("SAFETY: Financial content detected — requires approval")
            return "flag"

        # Rule 2: Flag sensitive content for review
        if safety.is_sensitive:
            log.warning("SAFETY: Sensitive content detected — creating draft")
            return "draft"

        # Rule 3: High risk score → draft for review
        if intelligence.risk_score > 0.4:
            return "draft"

        # Rule 4: Intelligence says approval needed
        if intelligence.requires_approval:
            return "draft"

        # Rule 5: Low confidence → draft for review
        if intelligence.confidence < 0.5:
            return "draft"

        # Rule 6: Safe + confident → auto-send
        return "send"

    # ── Action execution ─────────────────────────────────────────────────

    def _execute_decision(self, action: str, email: EmailMessage,
                          reply_body: str, safety: SafetyCheck,
                          intelligence) -> dict:
        """Execute the decided action."""

        if action == "send":
            return self._do_send(email, reply_body, safety)
        elif action == "draft":
            return self._do_draft(email, reply_body, safety, intelligence)
        elif action == "flag":
            return self._do_flag(email, reply_body, safety, intelligence)
        else:
            return self._do_ignore(email, safety)

    def _do_send(self, email: EmailMessage, reply_body: str,
                 safety: SafetyCheck) -> dict:
        """Auto-send a reply."""
        result = self._sender.send(
            to=email.sender_email,
            subject=f"Re: {email.subject}",
            body=reply_body,
            thread_id=email.thread_id,
        )

        self._log_action("sent", email, safety,
                         "auto_send", f"Sent reply (ID: {result.message_id})")

        return {
            "action": "sent",
            "message_id": email.message_id,
            "subject": email.subject,
            "recipient": email.sender_email,
            "send_result": result.to_dict(),
            "safety": safety.to_dict(),
        }

    def _do_draft(self, email: EmailMessage, reply_body: str,
                  safety: SafetyCheck, intelligence) -> dict:
        """Create a draft for human review."""
        # Create draft in Gmail
        draft_result = self._sender.create_draft(
            to=email.sender_email,
            subject=f"Re: {email.subject}",
            body=reply_body,
            thread_id=email.thread_id,
        )

        # Also save a local review file
        self._save_review_file(email, reply_body, safety, intelligence)

        self._log_action("drafted", email, safety,
                         "create_draft",
                         f"Draft created (ID: {draft_result.message_id})")

        return {
            "action": "drafted",
            "message_id": email.message_id,
            "subject": email.subject,
            "draft_result": draft_result.to_dict(),
            "safety": safety.to_dict(),
        }

    def _do_flag(self, email: EmailMessage, reply_body: str,
                 safety: SafetyCheck, intelligence) -> dict:
        """Flag for manager approval — financial/high-risk content."""
        # Create approval request file
        self._save_approval_file(email, reply_body, safety, intelligence)

        # Create draft in Gmail (not sent)
        draft_result = self._sender.create_draft(
            to=email.sender_email,
            subject=f"Re: {email.subject}",
            body=reply_body,
            thread_id=email.thread_id,
        )

        self._log_action("flagged", email, safety,
                         "requires_approval",
                         f"Flagged for approval: {', '.join(safety.flags)}")

        return {
            "action": "flagged",
            "message_id": email.message_id,
            "subject": email.subject,
            "flags": safety.flags,
            "risk_level": safety.risk_level,
            "draft_result": draft_result.to_dict(),
            "safety": safety.to_dict(),
        }

    def _do_ignore(self, email: EmailMessage, safety: SafetyCheck) -> dict:
        """Ignore the email — no action needed."""
        self._log_action("ignored", email, safety, "no_action", "")
        return {
            "action": "ignored",
            "message_id": email.message_id,
            "subject": email.subject,
        }

    # ── Claude-powered reply generation ──────────────────────────────────

    def _generate_reply_draft(self, content: str,
                              subject: str) -> str:
        """
        Generate a professional reply draft using Claude API.
        Falls back to a template if Claude is unavailable.
        """
        if not self._api_key:
            return self._template_reply(content, subject)

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": (
                        f"You are a professional AI assistant drafting an "
                        f"email reply. Write a concise, professional reply "
                        f"to the following email. Keep it under 200 words. "
                        f"Do NOT include Subject line — just the body.\n\n"
                        f"---\n{content}\n---\n\n"
                        f"Reply:"
                    ),
                }],
            )

            draft = response.content[0].text.strip()
            log.info("Claude generated reply draft for '%s' (%d chars)",
                     subject, len(draft))
            return draft

        except Exception as exc:
            log.warning("Claude reply generation failed: %s — using template",
                        exc)
            return self._template_reply(content, subject)

    @staticmethod
    def _template_reply(content: str, subject: str) -> str:
        """Fallback template when Claude API is unavailable."""
        return (
            f"Thank you for your email regarding \"{subject}\".\n\n"
            f"I have received your message and will review the details. "
            f"I will get back to you with a comprehensive response shortly.\n\n"
            f"Best regards,\n"
            f"AI Employee — Gold Tier\n"
        )

    # ── File output helpers ──────────────────────────────────────────────

    def _save_draft_file(self, decision: TaskDecision,
                         body: str, safety: SafetyCheck) -> dict:
        """Save a draft file for task pipeline compatibility."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"EmailDraft_{timestamp}.md"
        filepath = self._output_dir / filename

        md = (
            f"# Email Draft — {decision.title}\n\n"
            f"---\n\n"
            f"| Field      | Value                         |\n"
            f"|------------|-------------------------------|\n"
            f"| Priority   | {decision.priority}           |\n"
            f"| Safety     | {safety.risk_level}           |\n"
            f"| Status     | DRAFT — Needs Approval        |\n"
            f"| Created    | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n\n"
            f"---\n\n"
            f"## Body\n\n{body}\n\n"
            f"---\n\n"
            f"> **Action Required:** Review this draft and mark as APPROVED.\n"
        )
        filepath.write_text(md, encoding="utf-8")
        log.info("Email draft saved: %s", filename)

        return {
            "status": "draft_created",
            "agent": self.name,
            "draft_path": str(filepath),
            "safety": safety.to_dict(),
            "timestamp": datetime.now().isoformat(),
        }

    def _save_review_file(self, email: EmailMessage, reply_body: str,
                          safety: SafetyCheck, intelligence) -> None:
        """Save a review file in Needs_Action for human review."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"EmailReview_{email.message_id[:8]}_{timestamp}.md"
        filepath = self._output_dir / filename

        flags_md = "\n".join(f"  - {f}" for f in safety.flags) or "  - None"

        md = (
            f"# Email Review Required\n\n"
            f"---\n\n"
            f"| Field      | Value                         |\n"
            f"|------------|-------------------------------|\n"
            f"| From       | {email.sender} <{email.sender_email}> |\n"
            f"| Subject    | {email.subject}               |\n"
            f"| Date       | {email.date}                  |\n"
            f"| Category   | {intelligence.category}       |\n"
            f"| Urgency    | {intelligence.urgency}        |\n"
            f"| Risk       | {safety.risk_level}           |\n"
            f"| Confidence | {intelligence.confidence:.0%} |\n\n"
            f"---\n\n"
            f"## Original Email\n\n{email.body_plain or email.snippet}\n\n"
            f"---\n\n"
            f"## Proposed Reply\n\n{reply_body}\n\n"
            f"---\n\n"
            f"## Safety Flags\n\n{flags_md}\n\n"
            f"---\n\n"
            f"> **Action:** Review the proposed reply, edit if needed, then "
            f"mark as APPROVED to send or REJECTED to discard.\n"
        )
        filepath.write_text(md, encoding="utf-8")
        log.info("Review file saved: %s", filename)

    def _save_approval_file(self, email: EmailMessage, reply_body: str,
                            safety: SafetyCheck, intelligence) -> None:
        """Save an approval request file for flagged emails."""
        approval_dir = self._output_dir.parent / "Needs_Approval"
        approval_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Approval_Email_{email.message_id[:8]}_{timestamp}.md"
        filepath = approval_dir / filename

        flags_md = "\n".join(f"  - {f}" for f in safety.flags)

        md = (
            f"# APPROVAL REQUIRED — Email Reply\n\n"
            f"---\n\n"
            f"| Field      | Value                         |\n"
            f"|------------|-------------------------------|\n"
            f"| From       | {email.sender} <{email.sender_email}> |\n"
            f"| Subject    | {email.subject}               |\n"
            f"| Risk Level | **{safety.risk_level.upper()}**|\n"
            f"| Category   | {intelligence.category}       |\n"
            f"| Urgency    | {intelligence.urgency}        |\n\n"
            f"---\n\n"
            f"## Safety Flags\n\n{flags_md}\n\n"
            f"## Original Email\n\n{email.body_plain or email.snippet}\n\n"
            f"---\n\n"
            f"## Proposed Reply\n\n{reply_body}\n\n"
            f"---\n\n"
            f"## Decision\n\n"
            f"- [ ] APPROVED — Send the reply\n"
            f"- [ ] REJECTED — Discard the draft\n\n"
            f"> **WARNING:** This email was flagged due to financial/sensitive "
            f"content. A human must approve before sending.\n"
        )
        filepath.write_text(md, encoding="utf-8")
        log.info("Approval file saved: %s", filename)

    # ── Audit logging ────────────────────────────────────────────────────

    def _log_action(self, action: str, email: EmailMessage,
                    safety: SafetyCheck, decision: str,
                    details: str) -> None:
        """Record an action to the in-memory audit log."""
        entry = GmailActionLog(
            timestamp=datetime.now().isoformat(),
            action=action,
            message_id=email.message_id,
            subject=email.subject,
            sender=email.sender_email,
            safety=safety.to_dict(),
            decision=decision,
            result=action,
            details=details,
        )
        self._action_log.append(entry)

    def _save_action_log(self) -> None:
        """Persist the action log to disk."""
        if not self._action_log:
            return

        self._log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self._log_dir / f"gmail_agent_{timestamp}.json"

        try:
            data = [entry.to_dict() for entry in self._action_log]
            filepath.write_text(
                json.dumps(data, indent=2), encoding="utf-8",
            )
            log.info("Gmail Agent action log saved: %s", filepath.name)
        except Exception as exc:
            log.error("Failed to save action log: %s", exc)

    def get_action_log(self) -> list[dict]:
        """Return the current action log as a list of dicts."""
        return [e.to_dict() for e in self._action_log]
