"""
AI Employee — Gmail Reader (Google Gmail API)

Watches the Gmail inbox for new unread emails, extracts structured data,
and tracks which emails have already been processed to avoid duplicates.

Uses OAuth2 with credentials.json + token.json for authentication.

Setup:
  1. Create a Google Cloud project
  2. Enable the Gmail API
  3. Download credentials.json → place in project root
  4. First run will open a browser for OAuth consent → saves token.json
"""

import base64
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path

log = logging.getLogger("ai_employee.gmail_reader")

# Gmail API scopes — read-only for the reader
SCOPES_READ = ["https://www.googleapis.com/auth/gmail.readonly",
               "https://www.googleapis.com/auth/gmail.modify"]


@dataclass
class EmailMessage:
    """Structured representation of an extracted Gmail message."""
    message_id: str
    thread_id: str
    sender: str
    sender_email: str
    to: str
    subject: str
    body_plain: str
    body_html: str
    date: str
    labels: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    snippet: str = ""
    is_reply: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        """Convert to a markdown task file for the intelligence engine."""
        return (
            f"# Email: {self.subject}\n\n"
            f"From: {self.sender} <{self.sender_email}>\n"
            f"To: {self.to}\n"
            f"Date: {self.date}\n\n"
            f"---\n\n"
            f"{self.body_plain or self.snippet}\n"
        )


class GmailReader:
    """Reads and extracts emails from Gmail using the Google Gmail API."""

    def __init__(self, credentials_path: Path, token_path: Path,
                 processed_ids_path: Path):
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._processed_ids_path = processed_ids_path
        self._service = None
        self._processed_ids: set[str] = set()
        self._load_processed_ids()

    @property
    def enabled(self) -> bool:
        return self._credentials_path.exists()

    def authenticate(self) -> bool:
        """
        Authenticate with Google Gmail API using OAuth2.

        On first run, opens a browser for consent.
        Subsequent runs use the saved token.json.
        """
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

            # Load existing token
            if self._token_path.exists():
                creds = Credentials.from_authorized_user_file(
                    str(self._token_path), SCOPES_READ,
                )

            # Refresh or create new credentials
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self._credentials_path), SCOPES_READ,
                    )
                    creds = flow.run_local_server(port=0)

                # Save token for future runs
                self._token_path.parent.mkdir(parents=True, exist_ok=True)
                self._token_path.write_text(creds.to_json(), encoding="utf-8")

            self._service = build("gmail", "v1", credentials=creds)
            log.info("Gmail API authenticated successfully")
            return True

        except ImportError:
            log.error(
                "Google API libraries not installed. "
                "Run: pip install google-api-python-client "
                "google-auth-httplib2 google-auth-oauthlib"
            )
            return False
        except Exception as exc:
            log.error("Gmail API authentication failed: %s", exc)
            return False

    def fetch_unread(self, max_results: int = 10) -> list[EmailMessage]:
        """
        Fetch unread emails from the inbox.

        Returns a list of EmailMessage objects for emails that haven't
        been processed yet.
        """
        if not self._service:
            if not self.authenticate():
                return []

        try:
            results = self._service.users().messages().list(
                userId="me",
                q="is:unread in:inbox",
                maxResults=max_results,
            ).execute()

            messages = results.get("messages", [])
            if not messages:
                log.info("No unread emails found")
                return []

            emails = []
            for msg_ref in messages:
                msg_id = msg_ref["id"]

                # Skip already-processed emails
                if msg_id in self._processed_ids:
                    continue

                email = self._extract_message(msg_id)
                if email:
                    emails.append(email)

            log.info("Fetched %d new unread emails", len(emails))
            return emails

        except Exception as exc:
            log.error("Failed to fetch unread emails: %s", exc)
            return []

    def mark_as_read(self, message_id: str) -> bool:
        """Mark an email as read in Gmail."""
        if not self._service:
            return False

        try:
            self._service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            log.info("Marked email %s as read", message_id)
            return True
        except Exception as exc:
            log.error("Failed to mark email %s as read: %s", message_id, exc)
            return False

    def mark_processed(self, message_id: str) -> None:
        """Record that an email has been processed to avoid re-processing."""
        self._processed_ids.add(message_id)
        self._save_processed_ids()

    def get_thread(self, thread_id: str) -> list[EmailMessage]:
        """Fetch all messages in a thread for context."""
        if not self._service:
            return []

        try:
            thread = self._service.users().threads().get(
                userId="me", id=thread_id, format="full",
            ).execute()

            emails = []
            for msg in thread.get("messages", []):
                email = self._parse_message(msg)
                if email:
                    emails.append(email)

            return emails
        except Exception as exc:
            log.error("Failed to fetch thread %s: %s", thread_id, exc)
            return []

    # ── Private helpers ──────────────────────────────────────────────────

    def _extract_message(self, message_id: str) -> EmailMessage | None:
        """Fetch and parse a single message by ID."""
        try:
            msg = self._service.users().messages().get(
                userId="me", id=message_id, format="full",
            ).execute()
            return self._parse_message(msg)
        except Exception as exc:
            log.error("Failed to extract message %s: %s", message_id, exc)
            return None

    def _parse_message(self, msg: dict) -> EmailMessage | None:
        """Parse a Gmail API message response into an EmailMessage."""
        try:
            headers = {
                h["name"].lower(): h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }

            sender_full = headers.get("from", "")
            sender_name, sender_email = parseaddr(sender_full)

            # Extract body
            body_plain, body_html = self._extract_body(msg.get("payload", {}))

            # Extract attachment names
            attachments = self._extract_attachment_names(msg.get("payload", {}))

            # Check if this is a reply
            is_reply = bool(headers.get("in-reply-to"))

            return EmailMessage(
                message_id=msg["id"],
                thread_id=msg.get("threadId", ""),
                sender=sender_name or sender_email,
                sender_email=sender_email,
                to=headers.get("to", ""),
                subject=headers.get("subject", "(No Subject)"),
                body_plain=body_plain,
                body_html=body_html,
                date=headers.get("date", ""),
                labels=msg.get("labelIds", []),
                attachments=attachments,
                snippet=msg.get("snippet", ""),
                is_reply=is_reply,
            )
        except Exception as exc:
            log.error("Failed to parse message: %s", exc)
            return None

    def _extract_body(self, payload: dict) -> tuple[str, str]:
        """Extract plain text and HTML body from a message payload."""
        plain = ""
        html = ""

        mime_type = payload.get("mimeType", "")

        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                plain = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        elif mime_type == "text/html":
            data = payload.get("body", {}).get("data", "")
            if data:
                html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        elif "parts" in payload:
            for part in payload["parts"]:
                p, h = self._extract_body(part)
                if p and not plain:
                    plain = p
                if h and not html:
                    html = h

        return plain, html

    @staticmethod
    def _extract_attachment_names(payload: dict) -> list[str]:
        """Extract attachment filenames from the message payload."""
        names = []

        filename = payload.get("filename")
        if filename:
            names.append(filename)

        for part in payload.get("parts", []):
            fn = part.get("filename")
            if fn:
                names.append(fn)
            # Recurse into nested parts
            for sub in part.get("parts", []):
                sfn = sub.get("filename")
                if sfn:
                    names.append(sfn)

        return names

    def _load_processed_ids(self) -> None:
        """Load the set of already-processed message IDs from disk."""
        if self._processed_ids_path.exists():
            try:
                data = json.loads(
                    self._processed_ids_path.read_text(encoding="utf-8")
                )
                self._processed_ids = set(data.get("processed_ids", []))
                log.info("Loaded %d processed email IDs",
                         len(self._processed_ids))
            except Exception as exc:
                log.warning("Could not load processed IDs: %s", exc)
                self._processed_ids = set()
        else:
            self._processed_ids = set()

    def _save_processed_ids(self) -> None:
        """Persist the set of processed message IDs to disk."""
        try:
            self._processed_ids_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "processed_ids": list(self._processed_ids),
                "count": len(self._processed_ids),
                "last_updated": datetime.now().isoformat(),
            }
            self._processed_ids_path.write_text(
                json.dumps(data, indent=2), encoding="utf-8",
            )
        except Exception as exc:
            log.error("Failed to save processed IDs: %s", exc)
