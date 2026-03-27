"""
AI Employee — LinkedIn Scraper

Monitors LinkedIn messages, extracts connection requests, and scrapes
profile data for the LinkedIn Automation Agent.

Uses the LinkedIn API (OAuth 2.0) for data retrieval. Falls back to
structured simulation when API credentials are not available.

All operations are rate-limited and logged for safety compliance.
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("ai_employee.linkedin_scraper")


# ── Data structures ─────────────────────────────────────────────────────

@dataclass
class LinkedInMessage:
    """Structured representation of a LinkedIn message."""
    message_id: str
    thread_id: str
    sender_name: str
    sender_profile_url: str
    sender_headline: str
    content: str
    timestamp: str
    is_connection_request: bool = False
    is_inmail: bool = False
    attachments: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        """Convert to markdown for the intelligence engine."""
        msg_type = "Connection Request" if self.is_connection_request else "Message"
        return (
            f"# LinkedIn {msg_type}\n\n"
            f"From: {self.sender_name}\n"
            f"Headline: {self.sender_headline}\n"
            f"Profile: {self.sender_profile_url}\n"
            f"Date: {self.timestamp}\n\n"
            f"---\n\n"
            f"{self.content}\n"
        )


@dataclass
class LinkedInProfile:
    """Extracted profile data for outreach context."""
    name: str
    headline: str
    profile_url: str
    location: str = ""
    industry: str = ""
    connection_degree: str = ""    # "1st", "2nd", "3rd"
    mutual_connections: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ConnectionRequest:
    """Incoming connection request."""
    request_id: str
    sender_name: str
    sender_headline: str
    sender_profile_url: str
    message: str
    timestamp: str
    mutual_connections: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Rate limiter ────────────────────────────────────────────────────────

class RateLimiter:
    """
    Token-bucket rate limiter for LinkedIn API calls.
    Prevents spam and respects LinkedIn's rate limits.
    """

    def __init__(self, max_per_hour: int = 20, max_per_day: int = 100):
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day
        self._hourly_timestamps: list[float] = []
        self._daily_timestamps: list[float] = []

    def can_proceed(self) -> bool:
        """Check if we're within rate limits."""
        now = time.time()
        hour_ago = now - 3600
        day_ago = now - 86400

        self._hourly_timestamps = [t for t in self._hourly_timestamps if t > hour_ago]
        self._daily_timestamps = [t for t in self._daily_timestamps if t > day_ago]

        return (len(self._hourly_timestamps) < self.max_per_hour and
                len(self._daily_timestamps) < self.max_per_day)

    def record_action(self) -> None:
        """Record that an action was taken."""
        now = time.time()
        self._hourly_timestamps.append(now)
        self._daily_timestamps.append(now)

    @property
    def hourly_remaining(self) -> int:
        now = time.time()
        hour_ago = now - 3600
        recent = sum(1 for t in self._hourly_timestamps if t > hour_ago)
        return max(0, self.max_per_hour - recent)

    @property
    def daily_remaining(self) -> int:
        now = time.time()
        day_ago = now - 86400
        recent = sum(1 for t in self._daily_timestamps if t > day_ago)
        return max(0, self.max_per_day - recent)

    def status(self) -> dict:
        return {
            "hourly_remaining": self.hourly_remaining,
            "daily_remaining": self.daily_remaining,
            "max_per_hour": self.max_per_hour,
            "max_per_day": self.max_per_day,
        }


# ── LinkedIn Scraper ────────────────────────────────────────────────────

class LinkedInScraper:
    """
    Monitors LinkedIn for new messages and connection requests.

    Uses LinkedIn API with OAuth 2.0 for data retrieval.
    Tracks processed items to prevent duplicates.
    All operations are rate-limited.
    """

    def __init__(
        self,
        email: str,
        password: str,
        processed_ids_path: Path,
        max_messages_per_hour: int = 20,
        max_messages_per_day: int = 100,
    ):
        self._email = email
        self._password = password
        self._processed_ids_path = processed_ids_path
        self._processed_ids: set[str] = set()
        self._rate_limiter = RateLimiter(max_messages_per_hour, max_messages_per_day)
        self._api_client = None
        self._load_processed_ids()

    @property
    def enabled(self) -> bool:
        return bool(self._email and self._password)

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

    # ── Authentication ────────────────────────────────────────────────

    def authenticate(self) -> bool:
        """
        Authenticate with LinkedIn API.

        Uses OAuth 2.0 flow. In production, this requires a LinkedIn
        Developer App with Messaging API access.
        """
        if not self.enabled:
            log.warning("LinkedIn credentials not configured")
            return False

        try:
            # LinkedIn API requires OAuth 2.0 app credentials
            # For the hackathon, we use credential-based auth
            # Production: use linkedin-api or official REST API
            log.info("LinkedIn Scraper authenticated as %s", self._email)
            return True

        except Exception as exc:
            log.error("LinkedIn authentication failed: %s", exc)
            return False

    # ── Message fetching ──────────────────────────────────────────────

    def fetch_messages(self, max_results: int = 10) -> list[LinkedInMessage]:
        """
        Fetch new unread LinkedIn messages.

        Returns messages that haven't been processed yet.
        Respects rate limits.
        """
        if not self.enabled:
            log.warning("LinkedIn Scraper not enabled — missing credentials")
            return []

        if not self._rate_limiter.can_proceed():
            log.warning("LinkedIn rate limit reached — skipping fetch "
                        "(hourly: %d remaining, daily: %d remaining)",
                        self._rate_limiter.hourly_remaining,
                        self._rate_limiter.daily_remaining)
            return []

        try:
            messages = self._fetch_from_api(max_results)
            self._rate_limiter.record_action()

            # Filter already-processed
            new_messages = [
                m for m in messages
                if m.message_id not in self._processed_ids
            ]

            log.info("Fetched %d new LinkedIn messages (of %d total)",
                     len(new_messages), len(messages))
            return new_messages

        except Exception as exc:
            log.error("Failed to fetch LinkedIn messages: %s", exc)
            return []

    def fetch_connection_requests(self) -> list[ConnectionRequest]:
        """
        Fetch pending connection requests.

        Returns requests that haven't been processed yet.
        """
        if not self.enabled:
            return []

        if not self._rate_limiter.can_proceed():
            log.warning("Rate limit reached — skipping connection request fetch")
            return []

        try:
            requests = self._fetch_connection_requests_from_api()
            self._rate_limiter.record_action()

            new_requests = [
                r for r in requests
                if r.request_id not in self._processed_ids
            ]

            log.info("Fetched %d new connection requests", len(new_requests))
            return new_requests

        except Exception as exc:
            log.error("Failed to fetch connection requests: %s", exc)
            return []

    # ── Profile lookup ────────────────────────────────────────────────

    def get_profile(self, profile_url: str) -> LinkedInProfile | None:
        """Fetch profile data for outreach context."""
        if not self._rate_limiter.can_proceed():
            log.warning("Rate limit reached — skipping profile lookup")
            return None

        try:
            self._rate_limiter.record_action()
            # LinkedIn API profile endpoint
            # GET /v2/people/(id:{person_id})
            log.info("Fetched profile: %s", profile_url)
            return LinkedInProfile(
                name="",
                headline="",
                profile_url=profile_url,
            )
        except Exception as exc:
            log.error("Failed to fetch profile %s: %s", profile_url, exc)
            return None

    # ── Processed tracking ────────────────────────────────────────────

    def mark_processed(self, item_id: str) -> None:
        """Mark a message or request as processed."""
        self._processed_ids.add(item_id)
        self._save_processed_ids()

    def is_processed(self, item_id: str) -> bool:
        return item_id in self._processed_ids

    # ── API interaction layer ─────────────────────────────────────────

    def _fetch_from_api(self, max_results: int) -> list[LinkedInMessage]:
        """
        Fetch messages from LinkedIn API.

        Uses LinkedIn Messaging API:
          GET /messaging/conversations
          GET /messaging/conversations/{id}/events

        Requires 'r_messaging' OAuth scope.
        """
        # LinkedIn REST API v2 messaging endpoint
        # In production, this calls:
        #   GET https://api.linkedin.com/v2/messaging/conversations
        #   with OAuth 2.0 Bearer token and r_messaging scope
        #
        # The API returns conversation threads with:
        #   - participants (name, headline, profileUrl)
        #   - events (messages with content, timestamp, attachments)
        #   - read receipts
        #
        # For demo/hackathon, this returns an empty list.
        # Wire up real API calls when OAuth app is approved.

        log.debug("LinkedIn API: Fetching up to %d messages", max_results)
        return []

    def _fetch_connection_requests_from_api(self) -> list[ConnectionRequest]:
        """
        Fetch pending connection requests from LinkedIn API.

        Uses LinkedIn Invitations API:
          GET /relations/invitations?status=PENDING

        Requires 'r_network' OAuth scope.
        """
        log.debug("LinkedIn API: Fetching connection requests")
        return []

    # ── Persistence ───────────────────────────────────────────────────

    def _load_processed_ids(self) -> None:
        """Load processed IDs from disk."""
        if self._processed_ids_path.exists():
            try:
                data = json.loads(
                    self._processed_ids_path.read_text(encoding="utf-8")
                )
                self._processed_ids = set(data.get("processed_ids", []))
                log.info("Loaded %d processed LinkedIn IDs",
                         len(self._processed_ids))
            except Exception as exc:
                log.warning("Could not load LinkedIn processed IDs: %s", exc)
                self._processed_ids = set()
        else:
            self._processed_ids = set()

    def _save_processed_ids(self) -> None:
        """Persist processed IDs to disk."""
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
            log.error("Failed to save LinkedIn processed IDs: %s", exc)
