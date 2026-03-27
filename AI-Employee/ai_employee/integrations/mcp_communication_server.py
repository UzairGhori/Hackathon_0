"""
AI Employee — Communication MCP Server

Standalone Model Context Protocol server that exposes Gmail and LinkedIn
communication operations as MCP tools. Runs over stdio transport.

Tools exposed:
  - send_email           — Send an email via Gmail API
  - create_email_draft   — Create a Gmail draft for review
  - read_inbox           — Fetch unread emails from Gmail
  - get_email_thread     — Get all messages in an email thread
  - mark_email_read      — Mark a specific email as read
  - send_linkedin_message       — Send a message to a LinkedIn connection
  - create_linkedin_post        — Create a LinkedIn post draft
  - publish_linkedin_post       — Publish a post to LinkedIn
  - send_connection_request     — Send a LinkedIn connection request

Usage:
    python -m ai_employee.integrations.mcp_communication_server

Environment variables (loaded from .env):
    GMAIL_CREDENTIALS_PATH  — Path to Gmail OAuth credentials.json
    GMAIL_TOKEN_PATH        — Path to Gmail OAuth token.json
    GMAIL_SEND_LOG_PATH     — Path for send audit log
    GMAIL_PROCESSED_IDS     — Path for processed email IDs tracking
    LINKEDIN_EMAIL          — LinkedIn login email
    LINKEDIN_PASSWORD       — LinkedIn login password
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from ai_employee.integrations.gmail_sender import GmailSender
from ai_employee.integrations.gmail_reader import GmailReader
from ai_employee.integrations.linkedin_client import LinkedInClient

# Load .env from project root
_root = Path(__file__).resolve().parent.parent.parent
_dotenv = _root / ".env"
if _dotenv.exists():
    load_dotenv(_dotenv)

# ── Instantiate clients ──────────────────────────────────────────────

_gmail_sender = GmailSender(
    credentials_path=Path(os.getenv("GMAIL_CREDENTIALS_PATH", _root / "credentials.json")),
    token_path=Path(os.getenv("GMAIL_TOKEN_PATH", _root / "token.json")),
    send_log_path=Path(os.getenv("GMAIL_SEND_LOG_PATH", _root / "ai_employee" / "logs" / "gmail_send_log.json")),
)

_gmail_reader = GmailReader(
    credentials_path=Path(os.getenv("GMAIL_CREDENTIALS_PATH", _root / "credentials.json")),
    token_path=Path(os.getenv("GMAIL_TOKEN_PATH", _root / "token.json")),
    processed_ids_path=Path(os.getenv("GMAIL_PROCESSED_IDS", _root / "vault" / "gmail_processed_ids.json")),
)

_linkedin = LinkedInClient(
    email=os.getenv("LINKEDIN_EMAIL", ""),
    password=os.getenv("LINKEDIN_PASSWORD", ""),
    drafts_dir=Path(_root / "vault" / "Needs_Action"),
    log_path=Path(_root / "ai_employee" / "logs" / "linkedin_actions.json"),
)

# ── Create the MCP server ────────────────────────────────────────────

mcp = FastMCP(
    "communication",
    instructions="Gmail and LinkedIn communication integration for emails, messages, posts, and connections",
)


# ══════════════════════════════════════════════════════════════════════
#  GMAIL — SEND
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def send_email(to: str, subject: str, body: str) -> str:
    """
    Send an email via Gmail API.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body (plain text).

    Returns:
        JSON with success status, message_id, and details.
    """
    result = _gmail_sender.send(to=to, subject=subject, body=body)
    return json.dumps(result.to_dict(), indent=2)


@mcp.tool()
def create_email_draft(to: str, subject: str, body: str) -> str:
    """
    Create a Gmail draft for human review before sending.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body (plain text).

    Returns:
        JSON with success status and draft details.
    """
    result = _gmail_sender.create_draft(to=to, subject=subject, body=body)
    return json.dumps(result.to_dict(), indent=2)


# ══════════════════════════════════════════════════════════════════════
#  GMAIL — READ
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def read_inbox(max_results: int = 10) -> str:
    """
    Fetch unread emails from the Gmail inbox.

    Args:
        max_results: Maximum number of unread emails to return.

    Returns:
        JSON array of email objects with sender, subject, body, etc.
    """
    emails = _gmail_reader.fetch_unread(max_results=max_results)
    return json.dumps([e.to_dict() for e in emails], indent=2, default=str)


@mcp.tool()
def get_email_thread(thread_id: str) -> str:
    """
    Get all messages in an email thread for full conversation context.

    Args:
        thread_id: The Gmail thread ID to retrieve.

    Returns:
        JSON array of email messages in the thread.
    """
    emails = _gmail_reader.get_thread(thread_id=thread_id)
    return json.dumps([e.to_dict() for e in emails], indent=2, default=str)


@mcp.tool()
def mark_email_read(message_id: str) -> str:
    """
    Mark a specific email as read in Gmail.

    Args:
        message_id: The Gmail message ID to mark as read.

    Returns:
        JSON with success status.
    """
    ok = _gmail_reader.mark_as_read(message_id=message_id)
    return json.dumps({"success": ok, "message_id": message_id})


# ══════════════════════════════════════════════════════════════════════
#  LINKEDIN — MESSAGING
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def send_linkedin_message(
    recipient_name: str,
    recipient_profile_url: str,
    message: str,
) -> str:
    """
    Send a message to a LinkedIn connection.

    Args:
        recipient_name: Display name of the recipient.
        recipient_profile_url: LinkedIn profile URL of the recipient.
        message: Message text to send.

    Returns:
        JSON with success status and action details.
    """
    result = _linkedin.send_message(
        recipient_name=recipient_name,
        recipient_profile_url=recipient_profile_url,
        message=message,
    )
    return json.dumps(result.to_dict(), indent=2)


# ══════════════════════════════════════════════════════════════════════
#  LINKEDIN — POSTING
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def create_linkedin_post(
    content: str,
    hashtags: list[str] | None = None,
) -> str:
    """
    Create a LinkedIn post draft as a markdown file for review.

    Args:
        content: Post content text.
        hashtags: Optional list of hashtags (without # prefix).

    Returns:
        JSON with success status and draft file path.
    """
    draft_path = _linkedin.create_draft(content=content, hashtags=hashtags)
    if draft_path:
        return json.dumps({
            "success": True,
            "action": "drafted",
            "draft_path": str(draft_path),
        }, indent=2)
    return json.dumps({
        "success": False,
        "error": "Failed to create LinkedIn draft",
    })


@mcp.tool()
def publish_linkedin_post(content: str) -> str:
    """
    Publish a post directly to LinkedIn.

    Args:
        content: Post content text to publish.

    Returns:
        JSON with success status.
    """
    ok = _linkedin.publish(content=content)
    return json.dumps({"success": ok, "action": "published" if ok else "failed"})


# ══════════════════════════════════════════════════════════════════════
#  LINKEDIN — CONNECTIONS
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def send_connection_request(
    profile_url: str,
    name: str,
    note: str = "",
) -> str:
    """
    Send a connection request on LinkedIn.

    Args:
        profile_url: LinkedIn profile URL of the person to connect with.
        name: Display name of the person.
        note: Optional personalized note (max 300 characters).

    Returns:
        JSON with success status and action details.
    """
    result = _linkedin.send_connection_request(
        profile_url=profile_url,
        name=name,
        note=note,
    )
    return json.dumps(result.to_dict(), indent=2)


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run(transport="stdio")
