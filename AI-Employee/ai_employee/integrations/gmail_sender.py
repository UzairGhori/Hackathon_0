"""
AI Employee — Gmail Sender (Google Gmail API)

Sends emails, creates drafts, and replies to threads using the
Google Gmail API (OAuth2). Replaces SMTP for outbound email.

All actions are logged for audit trail and safety compliance.
"""

import base64
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

log = logging.getLogger("ai_employee.gmail_sender")

SCOPES_SEND = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]


@dataclass
class SendResult:
    """Result of an email send/draft operation."""
    success: bool
    action: str           # "sent", "drafted", "failed"
    message_id: str = ""
    thread_id: str = ""
    recipient: str = ""
    subject: str = ""
    error: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class GmailSender:
    """Sends emails and creates drafts via the Google Gmail API."""

    def __init__(self, credentials_path: Path, token_path: Path,
                 send_log_path: Path):
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._send_log_path = send_log_path
        self._service = None
        self._user_email = ""

    @property
    def enabled(self) -> bool:
        return self._credentials_path.exists()

    def authenticate(self) -> bool:
        """Authenticate with Google Gmail API for sending."""
        if not self._credentials_path.exists():
            log.warning("Gmail API credentials.json not found at %s",
                        self._credentials_path)
            return False

        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            creds = None

            if self._token_path.exists():
                creds = Credentials.from_authorized_user_file(
                    str(self._token_path), SCOPES_SEND,
                )

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self._credentials_path), SCOPES_SEND,
                    )
                    creds = flow.run_local_server(port=0)

                self._token_path.parent.mkdir(parents=True, exist_ok=True)
                self._token_path.write_text(creds.to_json(), encoding="utf-8")

            self._service = build("gmail", "v1", credentials=creds)

            # Get the authenticated user's email
            profile = self._service.users().getProfile(userId="me").execute()
            self._user_email = profile.get("emailAddress", "")
            log.info("Gmail Sender authenticated as %s", self._user_email)
            return True

        except ImportError:
            log.error(
                "Google API libraries not installed. "
                "Run: pip install google-api-python-client "
                "google-auth-httplib2 google-auth-oauthlib"
            )
            return False
        except Exception as exc:
            log.error("Gmail Sender authentication failed: %s", exc)
            return False

    def send(self, to: str, subject: str, body: str,
             html: bool = False, thread_id: str = "",
             in_reply_to: str = "") -> SendResult:
        """
        Send an email via Gmail API.

        Args:
            to:          Recipient email address.
            subject:     Email subject.
            body:        Email body (plain text or HTML).
            html:        If True, body is treated as HTML.
            thread_id:   If set, sends as part of an existing thread.
            in_reply_to: Message-ID header for threading.

        Returns:
            SendResult with status details.
        """
        if not self._service:
            if not self.authenticate():
                return SendResult(
                    success=False, action="failed",
                    recipient=to, subject=subject,
                    error="Gmail API not authenticated",
                    timestamp=datetime.now().isoformat(),
                )

        try:
            message = self._build_message(to, subject, body, html,
                                          in_reply_to)
            send_body = {"raw": message}
            if thread_id:
                send_body["threadId"] = thread_id

            sent = self._service.users().messages().send(
                userId="me", body=send_body,
            ).execute()

            result = SendResult(
                success=True,
                action="sent",
                message_id=sent.get("id", ""),
                thread_id=sent.get("threadId", ""),
                recipient=to,
                subject=subject,
                timestamp=datetime.now().isoformat(),
            )

            log.info("Email sent to %s: %s (ID: %s)",
                     to, subject, result.message_id)
            self._log_action(result)
            return result

        except Exception as exc:
            result = SendResult(
                success=False,
                action="failed",
                recipient=to,
                subject=subject,
                error=str(exc),
                timestamp=datetime.now().isoformat(),
            )
            log.error("Failed to send email to %s: %s", to, exc)
            self._log_action(result)
            return result

    def create_draft(self, to: str, subject: str, body: str,
                     html: bool = False, thread_id: str = "",
                     in_reply_to: str = "") -> SendResult:
        """
        Create a draft in Gmail (for human review before sending).

        Returns:
            SendResult with draft details.
        """
        if not self._service:
            if not self.authenticate():
                return SendResult(
                    success=False, action="failed",
                    recipient=to, subject=subject,
                    error="Gmail API not authenticated",
                    timestamp=datetime.now().isoformat(),
                )

        try:
            message = self._build_message(to, subject, body, html,
                                          in_reply_to)
            draft_body = {"message": {"raw": message}}
            if thread_id:
                draft_body["message"]["threadId"] = thread_id

            draft = self._service.users().drafts().create(
                userId="me", body=draft_body,
            ).execute()

            msg = draft.get("message", {})
            result = SendResult(
                success=True,
                action="drafted",
                message_id=msg.get("id", ""),
                thread_id=msg.get("threadId", ""),
                recipient=to,
                subject=subject,
                timestamp=datetime.now().isoformat(),
            )

            log.info("Draft created for %s: %s (ID: %s)",
                     to, subject, result.message_id)
            self._log_action(result)
            return result

        except Exception as exc:
            result = SendResult(
                success=False,
                action="failed",
                recipient=to,
                subject=subject,
                error=str(exc),
                timestamp=datetime.now().isoformat(),
            )
            log.error("Failed to create draft for %s: %s", to, exc)
            self._log_action(result)
            return result

    def send_approval_request(self, manager_email: str,
                              task_title: str,
                              plan_summary: str) -> SendResult:
        """Send an approval request email to the manager."""
        subject = f"[AI Employee] Approval Required: {task_title}"
        body = (
            f"Hi Manager,\n\n"
            f"The AI Employee has generated a task that requires your "
            f"approval before execution.\n\n"
            f"Task: {task_title}\n\n"
            f"Plan Summary:\n{plan_summary}\n\n"
            f"Please reply with APPROVED or REJECTED.\n\n"
            f"---\n"
            f"AI Employee — Gold Tier\n"
        )
        return self.send(manager_email, subject, body)

    # ── Private helpers ──────────────────────────────────────────────────

    def _build_message(self, to: str, subject: str, body: str,
                       html: bool = False,
                       in_reply_to: str = "") -> str:
        """Build a base64url-encoded MIME message for the Gmail API."""
        msg = MIMEMultipart("alternative")
        msg["To"] = to
        msg["From"] = self._user_email or "me"
        msg["Subject"] = subject

        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to

        content_type = "html" if html else "plain"
        msg.attach(MIMEText(body, content_type))

        raw = base64.urlsafe_b64encode(
            msg.as_bytes()
        ).decode("ascii")
        return raw

    def _log_action(self, result: SendResult) -> None:
        """Append the send result to the audit log."""
        try:
            self._send_log_path.parent.mkdir(parents=True, exist_ok=True)

            log_entries = []
            if self._send_log_path.exists():
                try:
                    log_entries = json.loads(
                        self._send_log_path.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, ValueError):
                    log_entries = []

            log_entries.append(result.to_dict())

            self._send_log_path.write_text(
                json.dumps(log_entries, indent=2), encoding="utf-8",
            )
        except Exception as exc:
            log.error("Failed to log send action: %s", exc)
