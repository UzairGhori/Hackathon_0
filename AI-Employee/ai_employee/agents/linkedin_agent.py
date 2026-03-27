"""
AI Employee — Advanced LinkedIn Automation Agent

Autonomous agent that orchestrates the full LinkedIn workflow:

  1. MONITOR    — Poll LinkedIn for new messages and connection requests
  2. ANALYZE    — Send content to the Task Intelligence Engine
  3. GENERATE   — Create smart replies using Claude API
  4. SAFETY     — Enforce anti-spam rules and rate limits
  5. ACT        — Send reply / create draft / send connection request

Capabilities:
  - Monitor LinkedIn messages
  - Generate smart replies with Claude
  - Send connection requests with personalized notes
  - Draft professional outreach messages
  - Rate-limited to prevent spam

Safety Rules:
  - NEVER spam — strict rate limits (configurable per hour/day)
  - Flag overly promotional or spammy content
  - Require approval for cold outreach to non-connections
  - Log all actions to audit trail
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from ai_employee.brain.decision_engine import DecisionEngine, TaskDecision
from ai_employee.integrations.linkedin_client import LinkedInClient, LinkedInActionResult
from ai_employee.integrations.linkedin_scraper import (
    LinkedInScraper, LinkedInMessage, ConnectionRequest, RateLimiter,
)
from ai_employee.integrations.linkedin_reply_generator import (
    LinkedInReplyGenerator, GeneratedReply, OutreachDraft,
)

log = logging.getLogger("ai_employee.agent.linkedin")


# ── Safety constants ─────────────────────────────────────────────────────

# Senders that should be auto-ignored
AUTO_IGNORE_PATTERNS = [
    r"linkedin\s*news",
    r"linkedin\s*notifications",
    r"no-?reply",
    r"linkedin\s*team",
]

# Keywords that require human approval before replying
SENSITIVE_KEYWORDS = [
    "salary", "compensation", "offer letter", "termination",
    "legal", "lawsuit", "nda", "confidential",
    "investment", "funding", "equity", "shares",
]

# Default rate limits
DEFAULT_MAX_MESSAGES_PER_HOUR = 15
DEFAULT_MAX_MESSAGES_PER_DAY = 80
DEFAULT_MAX_CONNECTIONS_PER_DAY = 25


# ── Data structures ─────────────────────────────────────────────────────

@dataclass
class SafetyCheck:
    """Result of safety analysis on LinkedIn content."""
    is_safe_to_auto_send: bool
    is_sensitive: bool
    is_spam_risk: bool
    flags: list[str] = field(default_factory=list)
    risk_level: str = "low"       # low, medium, high

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LinkedInActionLog:
    """Audit log entry for every LinkedIn Agent action."""
    timestamp: str
    action: str       # "replied", "drafted", "connected", "outreach", "flagged", "ignored"
    target: str       # recipient name or profile
    safety: dict
    decision: str
    result: str
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── LinkedIn Automation Agent ────────────────────────────────────────────

class LinkedInAgent:
    """
    Advanced LinkedIn Automation Agent — monitors messages, generates
    smart replies, sends connection requests, and drafts outreach.
    """

    def __init__(
        self,
        linkedin: LinkedInClient,
        output_dir: Path,
        scraper: LinkedInScraper | None = None,
        reply_generator: LinkedInReplyGenerator | None = None,
        decision_engine: DecisionEngine | None = None,
        api_key: str = "",
        log_dir: Path | None = None,
        max_messages_per_hour: int = DEFAULT_MAX_MESSAGES_PER_HOUR,
        max_connections_per_day: int = DEFAULT_MAX_CONNECTIONS_PER_DAY,
    ):
        self._linkedin = linkedin
        self._output_dir = output_dir
        self._scraper = scraper
        self._reply_gen = reply_generator or LinkedInReplyGenerator(api_key, log_dir)
        self._engine = decision_engine
        self._api_key = api_key
        self._log_dir = log_dir or output_dir
        self._action_log: list[LinkedInActionLog] = []

        # Rate limiting for outbound actions
        self._send_limiter = RateLimiter(
            max_per_hour=max_messages_per_hour,
            max_per_day=max_messages_per_hour * 6,
        )
        self._connection_limiter = RateLimiter(
            max_per_hour=max_connections_per_day // 4,
            max_per_day=max_connections_per_day,
        )

    @property
    def name(self) -> str:
        return "linkedin_agent"

    @property
    def enabled(self) -> bool:
        return self._linkedin.enabled

    # ── Main entry point (pipeline compatibility) ─────────────────────

    def execute(self, decision: TaskDecision, content: str) -> dict:
        """
        Execute a LinkedIn task routed by the scheduler.
        Handles posts, messages, and outreach based on content analysis.
        """
        log.info("LinkedInAgent executing: %s", decision.title)

        content_lower = content.lower()

        # Detect task type from content
        if any(kw in content_lower for kw in ["connect with", "connection request", "add connection"]):
            return self._handle_connection_task(decision, content)
        elif any(kw in content_lower for kw in ["outreach", "reach out", "cold message"]):
            return self._handle_outreach_task(decision, content)
        elif any(kw in content_lower for kw in ["reply to", "respond to", "message back"]):
            return self._handle_reply_task(decision, content)
        else:
            return self._handle_post_task(decision, content)

    # ── Message monitoring pipeline ───────────────────────────────────

    def process_messages(self, max_messages: int = 10) -> list[dict]:
        """
        Full pipeline: fetch messages → analyze → generate replies → safety → act.

        Returns a list of result dicts for each processed message.
        """
        if not self.enabled:
            log.warning("LinkedIn Agent not enabled — missing credentials")
            return []

        if not self._scraper:
            log.warning("LinkedIn scraper not configured — cannot monitor messages")
            return []

        messages = self._scraper.fetch_messages(max_results=max_messages)
        if not messages:
            log.info("No new LinkedIn messages to process")
            return []

        results = []
        for msg in messages:
            result = self._process_single_message(msg)
            results.append(result)

        # Process connection requests too
        conn_results = self._process_connection_requests()
        results.extend(conn_results)

        # Save logs
        self._save_action_log()
        self._reply_gen.save_generation_log()

        log.info("Processed %d LinkedIn items: %d replied, %d drafted, %d ignored",
                 len(results),
                 sum(1 for r in results if r["action"] == "replied"),
                 sum(1 for r in results if r["action"] == "drafted"),
                 sum(1 for r in results if r["action"] == "ignored"))

        return results

    def _process_single_message(self, msg: LinkedInMessage) -> dict:
        """Process one LinkedIn message through the full pipeline."""
        log.info("Processing LinkedIn message from %s: '%s'",
                 msg.sender_name, msg.content[:50])

        # Step 0: Auto-ignore check
        if self._should_ignore(msg):
            self._log_action("ignored", msg.sender_name,
                             SafetyCheck(is_safe_to_auto_send=False,
                                         is_sensitive=False, is_spam_risk=False,
                                         flags=["auto-ignore pattern"]),
                             "ignored", "Skipped — matches ignore pattern")
            self._scraper.mark_processed(msg.message_id)
            return {
                "action": "ignored",
                "message_id": msg.message_id,
                "sender": msg.sender_name,
                "reason": "auto-ignore pattern",
            }

        # Step 1: Safety check on incoming message
        safety = self._check_safety(msg.content)

        # Step 2: Determine reply style
        style = self._determine_reply_style(msg)

        # Step 3: Generate smart reply
        reply = self._reply_gen.generate_reply(
            message_content=msg.content,
            sender_name=msg.sender_name,
            sender_headline=msg.sender_headline,
            style=style,
        )

        # Step 4: Check safety of generated reply
        reply_safety = self._check_safety(reply.content)
        combined_safety = SafetyCheck(
            is_safe_to_auto_send=safety.is_safe_to_auto_send and reply_safety.is_safe_to_auto_send and reply.is_safe,
            is_sensitive=safety.is_sensitive or reply_safety.is_sensitive,
            is_spam_risk=reply_safety.is_spam_risk or not reply.is_safe,
            flags=safety.flags + reply_safety.flags + reply.flags,
            risk_level=max(safety.risk_level, reply_safety.risk_level,
                          key=lambda x: ["low", "medium", "high"].index(x)),
        )

        # Step 5: Decide action
        action = self._decide_message_action(msg, combined_safety, reply)

        # Step 6: Execute
        result = self._execute_message_decision(action, msg, reply, combined_safety)

        # Step 7: Mark processed
        self._scraper.mark_processed(msg.message_id)

        return result

    # ── Connection request processing ─────────────────────────────────

    def _process_connection_requests(self) -> list[dict]:
        """Process pending connection requests."""
        if not self._scraper:
            return []

        requests = self._scraper.fetch_connection_requests()
        results = []

        for req in requests:
            result = self._process_single_connection_request(req)
            results.append(result)

        return results

    def _process_single_connection_request(self, req: ConnectionRequest) -> dict:
        """Process one connection request."""
        log.info("Processing connection request from %s", req.sender_name)

        safety = self._check_safety(req.message or "")

        # Auto-accept if low risk, create draft for review if sensitive
        if safety.is_safe_to_auto_send and not safety.is_sensitive:
            self._log_action("accepted_connection", req.sender_name,
                             safety, "auto_accept", "Connection accepted")
            self._scraper.mark_processed(req.request_id)
            return {
                "action": "accepted_connection",
                "sender": req.sender_name,
                "safety": safety.to_dict(),
            }
        else:
            # Create review file
            self._save_connection_review(req, safety)
            self._log_action("flagged_connection", req.sender_name,
                             safety, "needs_review",
                             f"Flagged: {', '.join(safety.flags)}")
            self._scraper.mark_processed(req.request_id)
            return {
                "action": "flagged_connection",
                "sender": req.sender_name,
                "flags": safety.flags,
            }

    # ── Task handlers ─────────────────────────────────────────────────

    def _handle_post_task(self, decision: TaskDecision, content: str) -> dict:
        """Handle a LinkedIn post creation task."""
        post_content = self._extract_post_content(content)
        hashtags = self._extract_hashtags(content)

        draft_path = self._linkedin.create_draft(post_content, hashtags)

        if draft_path:
            return {
                "status": "draft_created",
                "agent": self.name,
                "type": "post",
                "draft_path": str(draft_path),
                "hashtags": hashtags,
                "char_count": len(post_content),
                "timestamp": datetime.now().isoformat(),
            }

        return {
            "status": "failed",
            "agent": self.name,
            "type": "post",
            "reason": "Could not create draft",
            "timestamp": datetime.now().isoformat(),
        }

    def _handle_connection_task(self, decision: TaskDecision,
                                content: str) -> dict:
        """Handle a connection request task."""
        # Extract target name/profile from content
        name = self._extract_name(content)
        note = self._reply_gen.generate_connection_note(
            recipient_name=name,
            reason=decision.title,
        )

        safety = self._check_safety(note)
        if not safety.is_safe_to_auto_send:
            # Save for review
            draft_path = self._linkedin.create_message_draft(
                name, f"Connection request note:\n{note}",
                context=f"Task: {decision.title}",
            )
            return {
                "status": "draft_created",
                "agent": self.name,
                "type": "connection_request",
                "draft_path": str(draft_path) if draft_path else "",
                "safety": safety.to_dict(),
                "timestamp": datetime.now().isoformat(),
            }

        if not self._connection_limiter.can_proceed():
            return {
                "status": "rate_limited",
                "agent": self.name,
                "type": "connection_request",
                "message": "Connection request limit reached",
                "limits": self._connection_limiter.status(),
                "timestamp": datetime.now().isoformat(),
            }

        result = self._linkedin.send_connection_request("", name, note)
        self._connection_limiter.record_action()

        return {
            "status": "sent" if result.success else "failed",
            "agent": self.name,
            "type": "connection_request",
            "recipient": name,
            "result": result.to_dict(),
            "timestamp": datetime.now().isoformat(),
        }

    def _handle_outreach_task(self, decision: TaskDecision,
                              content: str) -> dict:
        """Handle an outreach message task."""
        name = self._extract_name(content)
        headline = self._extract_headline(content)

        draft = self._reply_gen.draft_outreach(
            recipient_name=name,
            recipient_headline=headline,
            purpose=decision.title,
        )

        # Outreach always goes through review
        draft_path = self._linkedin.create_message_draft(
            name, draft.body,
            context=f"Outreach purpose: {decision.title}\n"
                    f"Connection note: {draft.connection_note}",
        )

        return {
            "status": "draft_created",
            "agent": self.name,
            "type": "outreach",
            "draft_path": str(draft_path) if draft_path else "",
            "is_safe": draft.is_safe,
            "flags": draft.flags,
            "timestamp": datetime.now().isoformat(),
        }

    def _handle_reply_task(self, decision: TaskDecision,
                           content: str) -> dict:
        """Handle a reply-to-message task."""
        name = self._extract_name(content)
        reply = self._reply_gen.generate_reply(
            message_content=content,
            sender_name=name,
            style="professional",
        )

        safety = self._check_safety(reply.content)

        if not safety.is_safe_to_auto_send or not reply.is_safe:
            draft_path = self._linkedin.create_message_draft(
                name, reply.content,
                context=f"Task: {decision.title}",
            )
            return {
                "status": "draft_created",
                "agent": self.name,
                "type": "reply",
                "draft_path": str(draft_path) if draft_path else "",
                "safety": safety.to_dict(),
                "timestamp": datetime.now().isoformat(),
            }

        if not self._send_limiter.can_proceed():
            return {
                "status": "rate_limited",
                "agent": self.name,
                "type": "reply",
                "limits": self._send_limiter.status(),
                "timestamp": datetime.now().isoformat(),
            }

        result = self._linkedin.send_message(name, "", reply.content)
        self._send_limiter.record_action()

        return {
            "status": "sent" if result.success else "failed",
            "agent": self.name,
            "type": "reply",
            "result": result.to_dict(),
            "timestamp": datetime.now().isoformat(),
        }

    def publish_approved(self, content: str) -> dict:
        """Publish a previously approved post."""
        success = self._linkedin.publish(content)
        return {
            "status": "published" if success else "failed",
            "agent": self.name,
            "timestamp": datetime.now().isoformat(),
        }

    # ── Safety engine ─────────────────────────────────────────────────

    def _check_safety(self, content: str) -> SafetyCheck:
        """Run safety analysis on content."""
        content_lower = content.lower()
        flags = []
        is_sensitive = False
        is_spam_risk = False

        # Check sensitive keywords
        for kw in SENSITIVE_KEYWORDS:
            if kw in content_lower:
                is_sensitive = True
                flags.append(f"SENSITIVE: '{kw}' detected")

        # Check for spam indicators
        if content.count("!") > 5:
            is_spam_risk = True
            flags.append("SPAM_RISK: Excessive exclamation marks")

        words = content.split()
        caps_words = [w for w in words if w.isupper() and len(w) > 3]
        if len(caps_words) > 3:
            is_spam_risk = True
            flags.append("SPAM_RISK: Multiple ALL-CAPS words")

        if len(content) > 2000:
            flags.append("LENGTH: Message exceeds 2000 characters")

        # Determine risk level
        if is_sensitive and is_spam_risk:
            risk_level = "high"
        elif is_sensitive or is_spam_risk:
            risk_level = "medium"
        else:
            risk_level = "low"

        is_safe = not is_sensitive and not is_spam_risk and risk_level == "low"

        return SafetyCheck(
            is_safe_to_auto_send=is_safe,
            is_sensitive=is_sensitive,
            is_spam_risk=is_spam_risk,
            flags=flags,
            risk_level=risk_level,
        )

    def _should_ignore(self, msg: LinkedInMessage) -> bool:
        """Check if a message should be auto-ignored."""
        sender = msg.sender_name.lower()
        for pattern in AUTO_IGNORE_PATTERNS:
            if re.search(pattern, sender):
                return True
        return False

    # ── Decision logic ────────────────────────────────────────────────

    def _decide_message_action(self, msg: LinkedInMessage,
                               safety: SafetyCheck,
                               reply: GeneratedReply) -> str:
        """
        Decide what to do with a message.
        Returns: "send", "draft", "flag", "ignore"
        """
        # Rule 1: Never auto-send if sensitive
        if safety.is_sensitive:
            log.warning("SAFETY: Sensitive content — creating draft for review")
            return "flag"

        # Rule 2: Never auto-send if spam risk
        if safety.is_spam_risk or not reply.is_safe:
            log.warning("SAFETY: Spam risk detected — creating draft")
            return "draft"

        # Rule 3: Rate limit check
        if not self._send_limiter.can_proceed():
            log.warning("RATE LIMIT: Message limit reached — creating draft")
            return "draft"

        # Rule 4: Low confidence reply → draft
        if reply.confidence < 0.5:
            return "draft"

        # Rule 5: InMail always needs approval
        if msg.is_inmail:
            return "draft"

        # Rule 6: Safe + confident → auto-send
        return "send"

    def _determine_reply_style(self, msg: LinkedInMessage) -> str:
        """Determine the appropriate reply style based on message context."""
        content_lower = msg.content.lower()

        if msg.is_connection_request:
            return "friendly"
        if any(kw in content_lower for kw in ["thank", "appreciate", "grateful"]):
            return "friendly"
        if any(kw in content_lower for kw in ["opportunity", "position", "role", "hiring"]):
            return "professional"
        if any(kw in content_lower for kw in ["follow up", "following up", "checking in"]):
            return "follow_up"

        return "professional"

    # ── Action execution ──────────────────────────────────────────────

    def _execute_message_decision(self, action: str, msg: LinkedInMessage,
                                  reply: GeneratedReply,
                                  safety: SafetyCheck) -> dict:
        """Execute the decided action for a message."""
        if action == "send":
            return self._do_send_reply(msg, reply, safety)
        elif action == "draft":
            return self._do_draft_reply(msg, reply, safety)
        elif action == "flag":
            return self._do_flag_message(msg, reply, safety)
        else:
            return self._do_ignore_message(msg, safety)

    def _do_send_reply(self, msg: LinkedInMessage,
                       reply: GeneratedReply,
                       safety: SafetyCheck) -> dict:
        """Auto-send a reply."""
        result = self._linkedin.send_message(
            msg.sender_name, msg.sender_profile_url, reply.content,
            thread_id=msg.thread_id,
        )
        self._send_limiter.record_action()

        self._log_action("replied", msg.sender_name, safety,
                         "auto_send", f"Reply sent ({reply.style})")

        return {
            "action": "replied",
            "message_id": msg.message_id,
            "sender": msg.sender_name,
            "style": reply.style,
            "method": reply.generation_method,
            "result": result.to_dict(),
            "safety": safety.to_dict(),
        }

    def _do_draft_reply(self, msg: LinkedInMessage,
                        reply: GeneratedReply,
                        safety: SafetyCheck) -> dict:
        """Create a draft for human review."""
        draft_path = self._linkedin.create_message_draft(
            msg.sender_name, reply.content,
            context=f"Original message: {msg.content[:200]}",
        )

        self._log_action("drafted", msg.sender_name, safety,
                         "create_draft", f"Draft created ({reply.style})")

        return {
            "action": "drafted",
            "message_id": msg.message_id,
            "sender": msg.sender_name,
            "draft_path": str(draft_path) if draft_path else "",
            "safety": safety.to_dict(),
        }

    def _do_flag_message(self, msg: LinkedInMessage,
                         reply: GeneratedReply,
                         safety: SafetyCheck) -> dict:
        """Flag for approval — sensitive content."""
        self._save_approval_file(msg, reply, safety)

        draft_path = self._linkedin.create_message_draft(
            msg.sender_name, reply.content,
            context=(
                f"Original message: {msg.content[:200]}\n\n"
                f"Safety flags: {', '.join(safety.flags)}"
            ),
        )

        self._log_action("flagged", msg.sender_name, safety,
                         "requires_approval",
                         f"Flagged: {', '.join(safety.flags)}")

        return {
            "action": "flagged",
            "message_id": msg.message_id,
            "sender": msg.sender_name,
            "flags": safety.flags,
            "risk_level": safety.risk_level,
            "draft_path": str(draft_path) if draft_path else "",
        }

    def _do_ignore_message(self, msg: LinkedInMessage,
                           safety: SafetyCheck) -> dict:
        """Ignore the message."""
        self._log_action("ignored", msg.sender_name, safety,
                         "no_action", "")
        return {
            "action": "ignored",
            "message_id": msg.message_id,
            "sender": msg.sender_name,
        }

    # ── Content extraction helpers ────────────────────────────────────

    @staticmethod
    def _extract_post_content(text: str) -> str:
        """Extract the main post content, stripping metadata."""
        lines = text.strip().splitlines()
        content_lines = []
        skip_metadata = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("|") or stripped.startswith("---"):
                skip_metadata = True
                continue
            if skip_metadata and not stripped:
                skip_metadata = False
                continue
            if not skip_metadata and stripped:
                content_lines.append(stripped)

        return "\n".join(content_lines).strip() or text.strip()

    @staticmethod
    def _extract_hashtags(text: str) -> list[str]:
        """Extract hashtags from content or generate from keywords."""
        existing = re.findall(r"#(\w+)", text)
        if existing:
            return existing[:10]

        keywords = [
            "AI", "automation", "productivity", "tech", "innovation",
            "leadership", "growth", "startup", "engineering", "data",
        ]
        lower = text.lower()
        return [kw for kw in keywords if kw.lower() in lower][:5]

    @staticmethod
    def _extract_name(content: str) -> str:
        """Extract a person's name from task content."""
        # Look for "connect with [Name]" or "reach out to [Name]"
        patterns = [
            r"connect with\s+([A-Z][a-z]+ [A-Z][a-z]+)",
            r"reach out to\s+([A-Z][a-z]+ [A-Z][a-z]+)",
            r"message\s+([A-Z][a-z]+ [A-Z][a-z]+)",
            r"reply to\s+([A-Z][a-z]+ [A-Z][a-z]+)",
            r"From:\s*([A-Z][a-z]+ [A-Z][a-z]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                return match.group(1)
        return "Connection"

    @staticmethod
    def _extract_headline(content: str) -> str:
        """Extract headline/title from content."""
        match = re.search(r"(?:headline|title|role):\s*(.+)", content, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    # ── File output helpers ───────────────────────────────────────────

    def _save_approval_file(self, msg: LinkedInMessage,
                            reply: GeneratedReply,
                            safety: SafetyCheck) -> None:
        """Save an approval request for flagged messages."""
        approval_dir = self._output_dir.parent / "Needs_Approval"
        approval_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Approval_LinkedIn_{msg.message_id[:8]}_{timestamp}.md"
        filepath = approval_dir / filename

        flags_md = "\n".join(f"  - {f}" for f in safety.flags)

        md = (
            f"# APPROVAL REQUIRED — LinkedIn Reply\n\n"
            f"---\n\n"
            f"| Field      | Value                         |\n"
            f"|------------|-------------------------------|\n"
            f"| From       | {msg.sender_name}             |\n"
            f"| Headline   | {msg.sender_headline}         |\n"
            f"| Risk Level | **{safety.risk_level.upper()}**|\n"
            f"| Reply Style| {reply.style}                 |\n"
            f"| Method     | {reply.generation_method}     |\n\n"
            f"---\n\n"
            f"## Safety Flags\n\n{flags_md}\n\n"
            f"## Original Message\n\n{msg.content}\n\n"
            f"---\n\n"
            f"## Proposed Reply\n\n{reply.content}\n\n"
            f"---\n\n"
            f"## Decision\n\n"
            f"- [ ] APPROVED — Send the reply\n"
            f"- [ ] REJECTED — Discard the draft\n\n"
            f"> **WARNING:** This message was flagged due to sensitive content. "
            f"A human must approve before sending.\n"
        )
        filepath.write_text(md, encoding="utf-8")
        log.info("LinkedIn approval file saved: %s", filename)

    def _save_connection_review(self, req: ConnectionRequest,
                                safety: SafetyCheck) -> None:
        """Save a connection request review file."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"LinkedIn_ConnReview_{timestamp}.md"
        filepath = self._output_dir / filename

        md = (
            f"# LinkedIn Connection Request — Review\n\n"
            f"---\n\n"
            f"| Field      | Value                         |\n"
            f"|------------|-------------------------------|\n"
            f"| From       | {req.sender_name}             |\n"
            f"| Headline   | {req.sender_headline}         |\n"
            f"| Mutual     | {req.mutual_connections}       |\n"
            f"| Risk       | {safety.risk_level}           |\n\n"
            f"---\n\n"
            f"## Message\n\n{req.message or '(No message)'}\n\n"
            f"---\n\n"
            f"## Decision\n\n"
            f"- [ ] ACCEPT\n"
            f"- [ ] DECLINE\n"
        )
        filepath.write_text(md, encoding="utf-8")

    # ── Audit logging ─────────────────────────────────────────────────

    def _log_action(self, action: str, target: str,
                    safety: SafetyCheck, decision: str,
                    details: str) -> None:
        """Record an action to the in-memory audit log."""
        entry = LinkedInActionLog(
            timestamp=datetime.now().isoformat(),
            action=action,
            target=target,
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

        log_dir = self._log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = log_dir / f"linkedin_agent_{timestamp}.json"

        try:
            data = [entry.to_dict() for entry in self._action_log]
            filepath.write_text(
                json.dumps(data, indent=2), encoding="utf-8",
            )
            log.info("LinkedIn Agent action log saved: %s", filepath.name)
        except Exception as exc:
            log.error("Failed to save LinkedIn action log: %s", exc)

    def get_action_log(self) -> list[dict]:
        """Return the current action log."""
        return [e.to_dict() for e in self._action_log]

    def get_rate_status(self) -> dict:
        """Return current rate limit status."""
        return {
            "messages": self._send_limiter.status(),
            "connections": self._connection_limiter.status(),
        }
