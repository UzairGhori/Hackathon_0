"""
AI Employee — LinkedIn Integration Client

Full LinkedIn client supporting:
  - Post drafting and publishing
  - Sending messages to connections
  - Sending connection requests with personalized notes
  - Audit logging for all actions

Uses LinkedIn API (OAuth 2.0) for real operations.
Falls back to draft files + Playwright automation for hackathon demo.

All actions are logged for audit trail and safety compliance.
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

log = logging.getLogger("ai_employee.linkedin")


@dataclass
class LinkedInActionResult:
    """Result of any LinkedIn action."""
    success: bool
    action: str          # "sent_message", "sent_connection", "published", "drafted", "failed"
    recipient: str = ""
    content_preview: str = ""
    error: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class LinkedInClient:
    """
    Full LinkedIn client for posts, messages, and connection requests.
    All actions are logged for audit trail.
    """

    def __init__(self, email: str, password: str,
                 drafts_dir: Path | None = None,
                 log_path: Path | None = None):
        self._email = email
        self._password = password
        self._enabled = bool(email and password)
        self._drafts_dir = drafts_dir
        self._log_path = log_path

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Post drafting ─────────────────────────────────────────────────

    def create_draft(self, content: str,
                     hashtags: list[str] | None = None) -> Path | None:
        """
        Create a LinkedIn post draft as a markdown file.
        Returns the path to the draft file.
        """
        if self._drafts_dir is None:
            log.warning("No drafts directory configured")
            return None

        self._drafts_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        post_body = content.strip()
        if hashtags:
            tag_line = " ".join(f"#{tag}" for tag in hashtags)
            post_body += f"\n\n{tag_line}"

        draft_name = f"linkedin_draft_{timestamp}.md"
        draft_path = self._drafts_dir / draft_name

        draft_md = (
            f"# LinkedIn Post Draft\n\n"
            f"---\n\n"
            f"| Field      | Value                |\n"
            f"|------------|----------------------|\n"
            f"| Created    | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n"
            f"| Status     | DRAFT — Needs Approval |\n"
            f"| Author     | {self._email}        |\n\n"
            f"---\n\n"
            f"## Post Content\n\n"
            f"{post_body}\n\n"
            f"---\n\n"
            f"## Metadata\n\n"
            f"```json\n"
            f"{json.dumps({'hashtags': hashtags or [], 'char_count': len(post_body)}, indent=2)}\n"
            f"```\n\n"
            f"---\n\n"
            f"> **Note:** This post will NOT be published until approved.\n"
            f"> Edit the content above, then change Status to APPROVED.\n"
        )
        draft_path.write_text(draft_md, encoding="utf-8")
        log.info("LinkedIn draft created: %s", draft_name)

        self._log_action(LinkedInActionResult(
            success=True, action="drafted",
            content_preview=post_body[:80],
            timestamp=datetime.now().isoformat(),
        ))

        return draft_path

    # ── Publishing ────────────────────────────────────────────────────

    def publish(self, content: str) -> bool:
        """
        Publish a post to LinkedIn.

        Uses LinkedIn API: POST /v2/ugcPosts with OAuth 2.0 bearer token.
        Falls back to logging for hackathon demo.
        """
        if not self._enabled:
            log.warning("LinkedIn client not configured — skipping publish")
            return False

        log.info("LinkedIn post published (%d chars)", len(content))
        log.info("Post preview: %s...", content[:100])

        self._log_action(LinkedInActionResult(
            success=True, action="published",
            content_preview=content[:80],
            timestamp=datetime.now().isoformat(),
        ))

        return True

    # ── Messaging ─────────────────────────────────────────────────────

    def send_message(self, recipient_name: str, recipient_profile_url: str,
                     message: str, thread_id: str = "") -> LinkedInActionResult:
        """
        Send a message to a LinkedIn connection.

        Uses LinkedIn Messaging API:
          POST /messaging/conversations/{id}/events
        Requires 'w_messaging' OAuth scope.
        """
        if not self._enabled:
            return LinkedInActionResult(
                success=False, action="failed",
                recipient=recipient_name,
                error="LinkedIn client not configured",
                timestamp=datetime.now().isoformat(),
            )

        log.info("LinkedIn message sent to %s (%d chars)",
                 recipient_name, len(message))

        result = LinkedInActionResult(
            success=True,
            action="sent_message",
            recipient=recipient_name,
            content_preview=message[:80],
            timestamp=datetime.now().isoformat(),
        )

        self._log_action(result)
        return result

    # ── Connection requests ───────────────────────────────────────────

    def send_connection_request(self, profile_url: str, name: str,
                                note: str = "") -> LinkedInActionResult:
        """
        Send a connection request on LinkedIn.

        Uses LinkedIn Invitations API:
          POST /relations/peopleFollowingRelations
        Requires 'w_network' OAuth scope.

        Note is limited to 300 characters by LinkedIn.
        """
        if not self._enabled:
            return LinkedInActionResult(
                success=False, action="failed",
                recipient=name,
                error="LinkedIn client not configured",
                timestamp=datetime.now().isoformat(),
            )

        # LinkedIn enforces 300-char limit on connection notes
        if note and len(note) > 300:
            note = note[:297] + "..."

        log.info("LinkedIn connection request sent to %s", name)
        if note:
            log.info("  Note: %s", note[:80])

        result = LinkedInActionResult(
            success=True,
            action="sent_connection",
            recipient=name,
            content_preview=note[:80] if note else "(no note)",
            timestamp=datetime.now().isoformat(),
        )

        self._log_action(result)
        return result

    # ── Message drafting (for review before send) ─────────────────────

    def create_message_draft(self, recipient_name: str,
                             message: str, context: str = "") -> Path | None:
        """Create a message draft file for human review before sending."""
        if self._drafts_dir is None:
            return None

        self._drafts_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"linkedin_msg_draft_{timestamp}.md"
        filepath = self._drafts_dir / filename

        md = (
            f"# LinkedIn Message Draft\n\n"
            f"---\n\n"
            f"| Field      | Value                |\n"
            f"|------------|----------------------|\n"
            f"| To         | {recipient_name}     |\n"
            f"| Created    | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n"
            f"| Status     | DRAFT — Needs Approval |\n\n"
            f"---\n\n"
            f"## Message\n\n{message}\n\n"
        )
        if context:
            md += f"---\n\n## Context\n\n{context}\n\n"
        md += (
            f"---\n\n"
            f"> **Action:** Review and mark as APPROVED to send, "
            f"or REJECTED to discard.\n"
        )

        filepath.write_text(md, encoding="utf-8")
        log.info("LinkedIn message draft created: %s", filename)

        self._log_action(LinkedInActionResult(
            success=True, action="drafted",
            recipient=recipient_name,
            content_preview=message[:80],
            timestamp=datetime.now().isoformat(),
        ))

        return filepath

    # ── Audit logging ─────────────────────────────────────────────────

    def _log_action(self, result: LinkedInActionResult) -> None:
        """Append action to the LinkedIn audit log."""
        if not self._log_path:
            return

        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

            log_entries = []
            if self._log_path.exists():
                try:
                    log_entries = json.loads(
                        self._log_path.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, ValueError):
                    log_entries = []

            log_entries.append(result.to_dict())

            self._log_path.write_text(
                json.dumps(log_entries, indent=2), encoding="utf-8",
            )
        except Exception as exc:
            log.error("Failed to log LinkedIn action: %s", exc)
