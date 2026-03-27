"""
AI Employee — Approval Queue

Priority queue dedicated to approval requests. Tracks the full lifecycle
of every action that requires human authorization before execution.

Features:
  - Priority ordering: CRITICAL > HIGH > MEDIUM > LOW
  - Category classification: financial, content, communication, general
  - Expiry/timeout — stale requests auto-expire after configurable duration
  - Persistent to JSON (survives restarts)
  - Thread-safe operations
  - Full audit trail per request
"""

import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import IntEnum
from pathlib import Path

log = logging.getLogger("ai_employee.approval_queue")


# ── Priority ordering ───────────────────────────────────────────────────

class ApprovalPriority(IntEnum):
    """Numeric rank for heap ordering (lower = higher priority)."""
    CRITICAL = 0
    HIGH     = 1
    MEDIUM   = 2
    LOW      = 3

    @classmethod
    def from_label(cls, label: str) -> "ApprovalPriority":
        return cls[label.upper()] if label.upper() in cls.__members__ else cls.MEDIUM


# ── Approval categories ─────────────────────────────────────────────────

class ApprovalCategory:
    """Types of actions requiring approval."""
    FINANCIAL     = "financial"       # payments, invoices, budgets
    CONTENT       = "content"         # posts, publications, outreach
    COMMUNICATION = "communication"   # emails, messages to external contacts
    GENERAL       = "general"         # everything else


# ── Approval status ─────────────────────────────────────────────────────

class ApprovalStatus:
    PENDING   = "pending"
    APPROVED  = "approved"
    REJECTED  = "rejected"
    EXPIRED   = "expired"


# ── Source tracking ─────────────────────────────────────────────────────

class ApprovalSource:
    """Where the approval request originated."""
    TASK_QUEUE = "task_queue"       # From scheduler/planner pipeline
    GMAIL      = "gmail_agent"      # Flagged email reply
    LINKEDIN   = "linkedin_agent"   # Flagged LinkedIn action
    MANUAL     = "manual"           # Created manually


# ── Approval request ────────────────────────────────────────────────────

@dataclass
class ApprovalRequest:
    """A single approval request with full lifecycle tracking."""

    # Identity
    request_id: str
    title: str
    description: str

    # Classification
    category: ApprovalCategory = ApprovalCategory.GENERAL
    priority: str = "MEDIUM"         # CRITICAL | HIGH | MEDIUM | LOW
    risk_level: str = "medium"       # low | medium | high | critical

    # Source tracking
    source: str = ApprovalSource.TASK_QUEUE
    source_agent: str = ""           # which agent generated this
    task_id: str = ""                # linked task_queue task_id

    # Content
    proposed_action: str = ""        # what the AI wants to do
    context: str = ""                # background info for decision
    safety_flags: list[str] = field(default_factory=list)

    # Status
    status: str = ApprovalStatus.PENDING
    decision_by: str = ""            # who approved/rejected
    decision_reason: str = ""        # why
    decision_at: str = ""            # when

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    expires_at: str = ""             # ISO timestamp or empty for no expiry
    notified_at: str = ""            # when manager was notified

    # Metadata
    metadata: dict = field(default_factory=dict)

    # Audit trail
    history: list[dict] = field(default_factory=list)

    # ── Properties ────────────────────────────────────────────────────

    @property
    def priority_rank(self) -> int:
        return ApprovalPriority.from_label(self.priority).value

    @property
    def is_pending(self) -> bool:
        return self.status == ApprovalStatus.PENDING

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.now().isoformat() > self.expires_at

    # ── Lifecycle ─────────────────────────────────────────────────────

    def approve(self, by: str = "manager", reason: str = "") -> None:
        self.status = ApprovalStatus.APPROVED
        self.decision_by = by
        self.decision_reason = reason
        self.decision_at = datetime.now().isoformat()
        self._add_history("approved", f"Approved by {by}: {reason}")

    def reject(self, by: str = "manager", reason: str = "") -> None:
        self.status = ApprovalStatus.REJECTED
        self.decision_by = by
        self.decision_reason = reason
        self.decision_at = datetime.now().isoformat()
        self._add_history("rejected", f"Rejected by {by}: {reason}")

    def expire(self) -> None:
        self.status = ApprovalStatus.EXPIRED
        self.decision_at = datetime.now().isoformat()
        self._add_history("expired", "Request expired without decision")

    def _add_history(self, action: str, detail: str) -> None:
        self.history.append({
            "action": action,
            "detail": detail,
            "timestamp": datetime.now().isoformat(),
        })

    # ── Serialization ─────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    def summary(self) -> dict:
        """Compact summary for API/dashboard."""
        return {
            "request_id": self.request_id,
            "title": self.title,
            "category": self.category,
            "priority": self.priority,
            "risk_level": self.risk_level,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "safety_flags_count": len(self.safety_flags),
        }

    # ── Ordering ──────────────────────────────────────────────────────

    def __lt__(self, other: "ApprovalRequest") -> bool:
        if self.priority_rank != other.priority_rank:
            return self.priority_rank < other.priority_rank
        return self.created_at < other.created_at


# ── Approval Queue ──────────────────────────────────────────────────────

class ApprovalQueue:
    """
    Thread-safe, persistent priority queue for approval requests.

    Requests are ordered by priority: CRITICAL first, LOW last.
    Supports expiry — stale requests auto-expire when checked.
    Persisted to JSON for restart survival.
    """

    def __init__(self, persist_path: Path | None = None,
                 default_expiry_hours: int = 24):
        self._requests: list[ApprovalRequest] = []
        self._index: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path
        self._default_expiry_hours = default_expiry_hours
        self._load()

    # ── Core operations ───────────────────────────────────────────────

    def submit(self, request: ApprovalRequest) -> bool:
        """
        Submit a new approval request to the queue.
        Returns False if a request with the same ID already exists.
        """
        with self._lock:
            if request.request_id in self._index:
                log.debug("Approval %s already exists — skipping",
                          request.request_id)
                return False

            # Set default expiry if not specified
            if not request.expires_at and self._default_expiry_hours > 0:
                expiry = datetime.now() + timedelta(hours=self._default_expiry_hours)
                request.expires_at = expiry.isoformat()

            request._add_history("submitted", "Request submitted to approval queue")

            self._requests.append(request)
            self._index[request.request_id] = request
            self._requests.sort()
            self._save()

            log.info("Approval submitted: [%s] '%s' (priority=%s, category=%s, expires=%s)",
                     request.request_id, request.title,
                     request.priority, request.category,
                     request.expires_at[:16] if request.expires_at else "never")
            return True

    def get(self, request_id: str) -> ApprovalRequest | None:
        """Retrieve a specific approval request by ID."""
        return self._index.get(request_id)

    def approve(self, request_id: str, by: str = "manager",
                reason: str = "") -> ApprovalRequest | None:
        """Approve a pending request. Returns the request or None if not found."""
        with self._lock:
            req = self._index.get(request_id)
            if not req or req.status != ApprovalStatus.PENDING:
                return None
            req.approve(by, reason)
            self._save()
            log.info("Approved: [%s] '%s' by %s", request_id, req.title, by)
            return req

    def reject(self, request_id: str, by: str = "manager",
               reason: str = "") -> ApprovalRequest | None:
        """Reject a pending request. Returns the request or None if not found."""
        with self._lock:
            req = self._index.get(request_id)
            if not req or req.status != ApprovalStatus.PENDING:
                return None
            req.reject(by, reason)
            self._save()
            log.info("Rejected: [%s] '%s' by %s", request_id, req.title, by)
            return req

    def process_expiry(self) -> list[ApprovalRequest]:
        """Expire all requests past their expiry time. Returns expired list."""
        expired = []
        with self._lock:
            for req in self._requests:
                if req.is_pending and req.is_expired:
                    req.expire()
                    expired.append(req)

            if expired:
                self._save()
                log.info("Expired %d approval requests", len(expired))

        return expired

    # ── Queries ───────────────────────────────────────────────────────

    def pending(self) -> list[ApprovalRequest]:
        """Return all pending requests, sorted by priority."""
        with self._lock:
            return sorted(r for r in self._requests
                          if r.status == ApprovalStatus.PENDING)

    def by_status(self, status: str) -> list[ApprovalRequest]:
        with self._lock:
            return sorted(r for r in self._requests if r.status == status)

    def by_category(self, category: str) -> list[ApprovalRequest]:
        with self._lock:
            return [r for r in self._requests if r.category == category]

    def by_source(self, source: str) -> list[ApprovalRequest]:
        with self._lock:
            return [r for r in self._requests if r.source == source]

    def all_requests(self) -> list[ApprovalRequest]:
        with self._lock:
            return sorted(self._requests)

    @property
    def pending_count(self) -> int:
        return sum(1 for r in self._requests
                   if r.status == ApprovalStatus.PENDING)

    @property
    def size(self) -> int:
        return len(self._requests)

    def summary(self) -> dict:
        """Return a summary of the approval queue state."""
        with self._lock:
            by_status: dict[str, int] = {}
            by_category: dict[str, int] = {}
            by_priority: dict[str, int] = {}

            for r in self._requests:
                by_status[r.status] = by_status.get(r.status, 0) + 1
                if r.is_pending:
                    by_category[r.category] = by_category.get(r.category, 0) + 1
                    by_priority[r.priority] = by_priority.get(r.priority, 0) + 1

            return {
                "total": len(self._requests),
                "pending": sum(1 for r in self._requests
                               if r.status == ApprovalStatus.PENDING),
                "approved": sum(1 for r in self._requests
                                if r.status == ApprovalStatus.APPROVED),
                "rejected": sum(1 for r in self._requests
                                if r.status == ApprovalStatus.REJECTED),
                "expired": sum(1 for r in self._requests
                               if r.status == ApprovalStatus.EXPIRED),
                "by_category": by_category,
                "by_priority": by_priority,
            }

    # ── Persistence ───────────────────────────────────────────────────

    def _save(self) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = [r.to_dict() for r in self._requests]
            self._persist_path.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not persist approval queue: %s", exc)

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for item in raw:
                req = ApprovalRequest(**{
                    k: v for k, v in item.items()
                    if k in ApprovalRequest.__dataclass_fields__
                })
                self._requests.append(req)
                self._index[req.request_id] = req
            self._requests.sort()
            log.info("Approval queue loaded: %d requests (%d pending)",
                     len(self._requests), self.pending_count)
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            log.warning("Could not load approval queue, starting fresh: %s", exc)
