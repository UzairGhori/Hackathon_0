"""
AI Employee — LinkedIn Reply Generator

Generates smart, professional replies and outreach messages for LinkedIn
using Claude API. Falls back to templates when Claude is unavailable.

Reply styles:
  - professional   — formal business tone
  - friendly       — warm but professional networking tone
  - outreach       — cold outreach / connection request note
  - follow_up      — follow-up to previous conversation
  - decline        — polite decline/not interested

Safety:
  - Never generates spam-like content
  - Flags overly promotional messages for review
  - All generated content logged for audit
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

log = logging.getLogger("ai_employee.linkedin_reply_gen")


# ── Reply styles ────────────────────────────────────────────────────────

REPLY_STYLES = {
    "professional", "friendly", "outreach",
    "follow_up", "decline",
}

STYLE_PROMPTS = {
    "professional": (
        "Write a formal, professional LinkedIn reply. "
        "Be concise, respectful, and business-appropriate. "
        "Keep it under 150 words."
    ),
    "friendly": (
        "Write a warm but professional LinkedIn message. "
        "Be personable and approachable while maintaining professionalism. "
        "Keep it under 120 words."
    ),
    "outreach": (
        "Write a compelling LinkedIn connection request or outreach message. "
        "Reference their background, find common ground, and propose value. "
        "NEVER be salesy or spammy. Keep it under 100 words."
    ),
    "follow_up": (
        "Write a brief follow-up message referencing the previous conversation. "
        "Be respectful of their time, add value, and suggest a clear next step. "
        "Keep it under 100 words."
    ),
    "decline": (
        "Write a polite decline message. Be gracious, thank them for reaching out, "
        "and leave the door open for future contact. Keep it under 80 words."
    ),
}


# ── Data structures ─────────────────────────────────────────────────────

@dataclass
class GeneratedReply:
    """Result of a reply generation."""
    content: str
    style: str
    confidence: float          # 0.0–1.0, how appropriate the reply is
    is_safe: bool              # False if flagged as potentially spammy
    flags: list[str]
    generation_method: str     # "claude" or "template"
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OutreachDraft:
    """A professional outreach message draft."""
    recipient_name: str
    recipient_headline: str
    subject: str
    body: str
    connection_note: str       # Short note for connection request (300 char limit)
    style: str
    is_safe: bool
    flags: list[str]
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


# ── Spam detection keywords ─────────────────────────────────────────────

SPAM_KEYWORDS = [
    "buy now", "limited offer", "act fast", "guaranteed results",
    "make money", "get rich", "click here", "free trial",
    "no obligation", "once in a lifetime", "exclusive deal",
    "double your", "triple your", "passive income",
    "mlm", "multi-level", "network marketing opportunity",
]

OVERLY_PROMOTIONAL = [
    "schedule a demo", "book a call", "sign up today",
    "special discount", "limited time", "don't miss out",
    "exclusive access", "early bird",
]


# ── Reply Generator ─────────────────────────────────────────────────────

class LinkedInReplyGenerator:
    """
    Generates smart LinkedIn replies and outreach messages using Claude API.
    Falls back to professional templates when Claude is unavailable.
    """

    def __init__(self, api_key: str = "", log_dir: Path | None = None):
        self._api_key = api_key
        self._log_dir = log_dir
        self._generation_log: list[dict] = []

    @property
    def ai_enabled(self) -> bool:
        return bool(self._api_key)

    # ── Reply generation ──────────────────────────────────────────────

    def generate_reply(
        self,
        message_content: str,
        sender_name: str,
        sender_headline: str = "",
        style: str = "professional",
        context: str = "",
    ) -> GeneratedReply:
        """
        Generate a smart reply to a LinkedIn message.

        Args:
            message_content: The message to reply to.
            sender_name:     Who sent the message.
            sender_headline: Their LinkedIn headline for context.
            style:           Reply style (professional/friendly/follow_up/decline).
            context:         Additional context (previous messages, notes).

        Returns:
            GeneratedReply with content and safety assessment.
        """
        if style not in REPLY_STYLES:
            style = "professional"

        if self._api_key:
            reply = self._generate_with_claude(
                message_content, sender_name, sender_headline,
                style, context,
            )
        else:
            reply = self._generate_template_reply(
                message_content, sender_name, style,
            )

        # Safety check the generated content
        flags = self._check_safety(reply.content)
        reply.flags = flags
        reply.is_safe = len(flags) == 0

        # Log the generation
        self._log_generation(reply, sender_name, message_content)

        return reply

    # ── Outreach drafting ─────────────────────────────────────────────

    def draft_outreach(
        self,
        recipient_name: str,
        recipient_headline: str,
        purpose: str,
        style: str = "outreach",
        context: str = "",
    ) -> OutreachDraft:
        """
        Draft a professional outreach message for a LinkedIn connection.

        Args:
            recipient_name:     Who to reach out to.
            recipient_headline: Their LinkedIn headline.
            purpose:            Why you're reaching out.
            style:              Message style.
            context:            Additional context.

        Returns:
            OutreachDraft with message body and connection note.
        """
        if self._api_key:
            draft = self._draft_outreach_with_claude(
                recipient_name, recipient_headline, purpose,
                style, context,
            )
        else:
            draft = self._draft_outreach_template(
                recipient_name, recipient_headline, purpose,
            )

        # Safety check
        combined = f"{draft.body} {draft.connection_note}"
        flags = self._check_safety(combined)
        draft.flags = flags
        draft.is_safe = len(flags) == 0

        return draft

    # ── Connection request note ───────────────────────────────────────

    def generate_connection_note(
        self,
        recipient_name: str,
        recipient_headline: str = "",
        reason: str = "",
    ) -> str:
        """
        Generate a short connection request note (max 300 chars).
        """
        if self._api_key:
            try:
                return self._generate_connection_note_claude(
                    recipient_name, recipient_headline, reason,
                )
            except Exception:
                pass

        # Template fallback
        if reason:
            note = (
                f"Hi {recipient_name}, {reason}. "
                f"I'd love to connect and exchange ideas. "
                f"Looking forward to being in touch!"
            )
        else:
            note = (
                f"Hi {recipient_name}, I came across your profile and "
                f"found your work interesting. Would love to connect!"
            )

        return note[:300]

    # ── Claude-powered generation ─────────────────────────────────────

    def _generate_with_claude(
        self, message: str, sender: str, headline: str,
        style: str, context: str,
    ) -> GeneratedReply:
        """Generate a reply using Claude API."""
        try:
            import anthropic

            style_instruction = STYLE_PROMPTS.get(style, STYLE_PROMPTS["professional"])

            prompt = (
                f"You are a professional LinkedIn networking assistant. "
                f"{style_instruction}\n\n"
                f"Reply to this LinkedIn message:\n\n"
                f"From: {sender}"
            )
            if headline:
                prompt += f" ({headline})"
            prompt += f"\nMessage: {message}\n"
            if context:
                prompt += f"\nAdditional context: {context}\n"
            prompt += (
                "\nWrite ONLY the reply message body — no subject line, "
                "no greeting prefix like 'Here is a reply:', just the actual "
                "message text. Do NOT include any meta-commentary."
            )

            client = anthropic.Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )

            content = response.content[0].text.strip()
            log.info("Claude generated LinkedIn reply (%d chars, style=%s)",
                     len(content), style)

            return GeneratedReply(
                content=content,
                style=style,
                confidence=0.85,
                is_safe=True,
                flags=[],
                generation_method="claude",
                timestamp=datetime.now().isoformat(),
            )

        except Exception as exc:
            log.warning("Claude reply generation failed: %s — using template", exc)
            return self._generate_template_reply(message, sender, style)

    def _draft_outreach_with_claude(
        self, name: str, headline: str, purpose: str,
        style: str, context: str,
    ) -> OutreachDraft:
        """Draft an outreach message using Claude."""
        try:
            import anthropic

            prompt = (
                f"You are a professional LinkedIn outreach assistant. "
                f"Draft a compelling but NOT spammy outreach message.\n\n"
                f"Recipient: {name}\n"
                f"Their headline: {headline}\n"
                f"Purpose: {purpose}\n"
            )
            if context:
                prompt += f"Context: {context}\n"
            prompt += (
                "\nProvide TWO things:\n"
                "1. FULL MESSAGE (under 200 words) — a professional outreach message\n"
                "2. CONNECTION NOTE (under 250 characters) — a short note for the "
                "connection request\n\n"
                "Format:\n"
                "MESSAGE:\n[your message]\n\n"
                "NOTE:\n[your connection note]"
            )

            client = anthropic.Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=768,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()

            # Parse the response
            body = text
            note = ""
            if "NOTE:" in text:
                parts = text.split("NOTE:", 1)
                body = parts[0].replace("MESSAGE:", "").strip()
                note = parts[1].strip()[:300]
            elif "MESSAGE:" in text:
                body = text.replace("MESSAGE:", "").strip()

            return OutreachDraft(
                recipient_name=name,
                recipient_headline=headline,
                subject=f"Connection with {name}",
                body=body,
                connection_note=note or f"Hi {name}, would love to connect!",
                style=style,
                is_safe=True,
                flags=[],
                timestamp=datetime.now().isoformat(),
            )

        except Exception as exc:
            log.warning("Claude outreach draft failed: %s — using template", exc)
            return self._draft_outreach_template(name, headline, purpose)

    def _generate_connection_note_claude(
        self, name: str, headline: str, reason: str,
    ) -> str:
        """Generate a connection note using Claude."""
        import anthropic

        prompt = (
            f"Write a LinkedIn connection request note (UNDER 250 characters). "
            f"Be genuine, not salesy.\n\n"
            f"To: {name} ({headline})\n"
        )
        if reason:
            prompt += f"Reason: {reason}\n"
        prompt += "\nWrite ONLY the note text, nothing else."

        client = anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=128,
            messages=[{"role": "user", "content": prompt}],
        )

        return response.content[0].text.strip()[:300]

    # ── Template fallbacks ────────────────────────────────────────────

    def _generate_template_reply(
        self, message: str, sender: str, style: str,
    ) -> GeneratedReply:
        """Generate a template-based reply when Claude is unavailable."""
        templates = {
            "professional": (
                f"Thank you for your message, {sender}. "
                f"I appreciate you reaching out. I've reviewed your message "
                f"and will follow up with a detailed response shortly.\n\n"
                f"Best regards"
            ),
            "friendly": (
                f"Hi {sender}! Thanks so much for reaching out. "
                f"Great to hear from you. Let me take a look at this "
                f"and get back to you soon.\n\n"
                f"Talk soon!"
            ),
            "outreach": (
                f"Hi {sender}, I came across your profile and found "
                f"your background really interesting. I'd love to connect "
                f"and explore potential synergies.\n\n"
                f"Looking forward to connecting!"
            ),
            "follow_up": (
                f"Hi {sender}, just wanted to follow up on our previous "
                f"conversation. Hope all is well on your end. "
                f"Would be great to continue our discussion when you have a moment.\n\n"
                f"Best"
            ),
            "decline": (
                f"Hi {sender}, thank you for reaching out. "
                f"I appreciate the opportunity, but I'll have to pass at this time. "
                f"I wish you all the best and hope we can stay in touch.\n\n"
                f"Kind regards"
            ),
        }

        content = templates.get(style, templates["professional"])

        return GeneratedReply(
            content=content,
            style=style,
            confidence=0.6,
            is_safe=True,
            flags=[],
            generation_method="template",
            timestamp=datetime.now().isoformat(),
        )

    def _draft_outreach_template(
        self, name: str, headline: str, purpose: str,
    ) -> OutreachDraft:
        """Template-based outreach draft."""
        body = (
            f"Hi {name},\n\n"
            f"I came across your profile and was impressed by your work"
        )
        if headline:
            body += f" as {headline}"
        body += ".\n\n"
        if purpose:
            body += f"{purpose}\n\n"
        body += (
            "I'd love to connect and explore how we might be able to "
            "collaborate or exchange insights.\n\n"
            "Looking forward to hearing from you!\n\n"
            "Best regards"
        )

        note = f"Hi {name}, your profile caught my eye. Would love to connect!"

        return OutreachDraft(
            recipient_name=name,
            recipient_headline=headline,
            subject=f"Connection with {name}",
            body=body,
            connection_note=note[:300],
            style="outreach",
            is_safe=True,
            flags=[],
            timestamp=datetime.now().isoformat(),
        )

    # ── Safety checks ─────────────────────────────────────────────────

    @staticmethod
    def _check_safety(content: str) -> list[str]:
        """Check generated content for spam or overly promotional language."""
        flags = []
        content_lower = content.lower()

        for kw in SPAM_KEYWORDS:
            if kw in content_lower:
                flags.append(f"SPAM: '{kw}' detected")

        for kw in OVERLY_PROMOTIONAL:
            if kw in content_lower:
                flags.append(f"PROMOTIONAL: '{kw}' detected")

        if len(content) > 2000:
            flags.append("LENGTH: Message exceeds 2000 characters")

        # Check for excessive exclamation marks (spammy)
        if content.count("!") > 5:
            flags.append("TONE: Excessive exclamation marks")

        # Check for ALL CAPS words (spammy)
        words = content.split()
        caps_words = [w for w in words if w.isupper() and len(w) > 3]
        if len(caps_words) > 3:
            flags.append("TONE: Multiple ALL-CAPS words detected")

        return flags

    # ── Logging ───────────────────────────────────────────────────────

    def _log_generation(self, reply: GeneratedReply,
                        recipient: str, original: str) -> None:
        """Log the generation for audit trail."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "recipient": recipient,
            "original_message_preview": original[:100],
            "reply_preview": reply.content[:100],
            "style": reply.style,
            "method": reply.generation_method,
            "is_safe": reply.is_safe,
            "flags": reply.flags,
        }
        self._generation_log.append(entry)

    def save_generation_log(self) -> None:
        """Persist the generation log to disk."""
        if not self._generation_log or not self._log_dir:
            return

        self._log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self._log_dir / f"linkedin_reply_gen_{timestamp}.json"

        try:
            filepath.write_text(
                json.dumps(self._generation_log, indent=2), encoding="utf-8",
            )
            log.info("Reply generation log saved: %s", filepath.name)
        except Exception as exc:
            log.error("Failed to save generation log: %s", exc)

    def get_generation_log(self) -> list[dict]:
        """Return the current generation log."""
        return list(self._generation_log)
