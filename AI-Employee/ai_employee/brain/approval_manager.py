"""
AI Employee — Approval Manager

Centralized system that manages the full lifecycle of approval requests:

  1. RECEIVE  — Accept approval requests from any agent or pipeline stage
  2. CLASSIFY — Categorize by type (financial, content, communication)
  3. QUEUE    — Add to priority queue with expiry
  4. NOTIFY   — Alert manager via dashboard / email / file
  5. DECIDE   — Process approve/reject decisions
  6. EXECUTE  — Route approved actions back to the originating agent
  7. LOG      — Full audit trail for compliance

Sensitive actions that require approval:
  - Financial payments, invoices, budget approvals
  - Sending proposals or external communications
  - Publishing content (LinkedIn posts, email campaigns)
  - Any action with risk_score > 0.4

Usage:
  The ApprovalManager is instantiated once in AIEmployee.__init__ and
  shared across all agents and pipeline stages.
"""

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from ai_employee.brain.approval_queue import (
    ApprovalQueue, ApprovalRequest,
    ApprovalCategory, ApprovalStatus, ApprovalSource, ApprovalPriority,
)

log = logging.getLogger("ai_employee.approval_manager")


# ── Keyword → category mapping ──────────────────────────────────────────

FINANCIAL_KEYWORDS = [
    "invoice", "payment", "transfer", "wire", "bank", "budget",
    "expense", "refund", "billing", "payroll", "tax", "purchase",
    "financial", "credit card", "debit",
]

CONTENT_KEYWORDS = [
    "post", "publish", "article", "blog", "campaign", "newsletter",
    "announcement", "press release", "linkedin", "social media",
]

COMMUNICATION_KEYWORDS = [
    "email", "send", "reply", "respond", "outreach", "message",
    "proposal", "pitch", "introduction", "follow up",
]


# ── Approval Manager ────────────────────────────────────────────────────

class ApprovalManager:
    """
    Centralized approval manager for the AI Employee system.

    Handles creating, tracking, deciding, and executing approval requests
    from all sources (task pipeline, Gmail agent, LinkedIn agent).
    """

    def __init__(
        self,
        queue: ApprovalQueue,
        approval_dir: Path,
        log_dir: Path,
        agent_map: dict | None = None,
        gmail_sender=None,
        manager_email: str = "",
    ):
        self._queue = queue
        self._approval_dir = approval_dir
        self._log_dir = log_dir
        self._agents = agent_map or {}
        self._gmail_sender = gmail_sender
        self._manager_email = manager_email
        self._action_log: list[dict] = []

        self._approval_dir.mkdir(parents=True, exist_ok=True)

    @property
    def queue(self) -> ApprovalQueue:
        return self._queue

    # ── Create approval requests ──────────────────────────────────────

    def request_approval(
        self,
        request_id: str,
        title: str,
        description: str,
        proposed_action: str,
        source: str = ApprovalSource.TASK_QUEUE,
        source_agent: str = "",
        task_id: str = "",
        priority: str = "MEDIUM",
        risk_level: str = "medium",
        safety_flags: list[str] | None = None,
        context: str = "",
        metadata: dict | None = None,
    ) -> ApprovalRequest | None:
        """
        Submit a new approval request.

        This is the main entry point — called by agents and the scheduler
        when an action needs human authorization.

        Returns the ApprovalRequest if submitted, None if duplicate.
        """
        # Auto-classify category
        category = self._classify_category(
            f"{title} {description} {proposed_action}"
        )

        request = ApprovalRequest(
            request_id=request_id,
            title=title,
            description=description,
            category=category,
            priority=priority,
            risk_level=risk_level,
            source=source,
            source_agent=source_agent,
            task_id=task_id,
            proposed_action=proposed_action,
            context=context,
            safety_flags=safety_flags or [],
            metadata=metadata or {},
        )

        submitted = self._queue.submit(request)
        if not submitted:
            return None

        # Generate approval file for file-based review
        self._write_approval_file(request)

        # Send email notification if configured
        self._notify_manager(request)

        # Log the action
        self._log_action("submitted", request)

        return request

    def request_financial_approval(
        self,
        request_id: str,
        title: str,
        amount: str = "",
        recipient: str = "",
        description: str = "",
        source_agent: str = "",
        context: str = "",
    ) -> ApprovalRequest | None:
        """Convenience method for financial approval requests."""
        return self.request_approval(
            request_id=request_id,
            title=title,
            description=description,
            proposed_action=f"Financial transaction: {title}",
            source=ApprovalSource.TASK_QUEUE,
            source_agent=source_agent,
            priority="CRITICAL",
            risk_level="high",
            safety_flags=[f"FINANCIAL: {title}"],
            context=context,
            metadata={"amount": amount, "recipient": recipient},
        )

    def request_content_approval(
        self,
        request_id: str,
        title: str,
        content_preview: str = "",
        platform: str = "",
        source_agent: str = "",
        context: str = "",
    ) -> ApprovalRequest | None:
        """Convenience method for content publishing approval requests."""
        return self.request_approval(
            request_id=request_id,
            title=title,
            description=f"Publishing content on {platform}",
            proposed_action=content_preview[:500],
            source=ApprovalSource.LINKEDIN if "linkedin" in platform.lower()
                   else ApprovalSource.GMAIL,
            source_agent=source_agent,
            priority="HIGH",
            risk_level="medium",
            safety_flags=[f"CONTENT: Publishing to {platform}"],
            context=context,
            metadata={"platform": platform},
        )

    def request_communication_approval(
        self,
        request_id: str,
        title: str,
        recipient: str = "",
        message_preview: str = "",
        source_agent: str = "",
        safety_flags: list[str] | None = None,
        context: str = "",
    ) -> ApprovalRequest | None:
        """Convenience method for outbound communication approvals."""
        return self.request_approval(
            request_id=request_id,
            title=title,
            description=f"Sending communication to {recipient}",
            proposed_action=message_preview[:500],
            source=ApprovalSource.GMAIL if "email" in source_agent.lower()
                   else ApprovalSource.LINKEDIN,
            source_agent=source_agent,
            priority="HIGH",
            risk_level="medium",
            safety_flags=safety_flags or [],
            context=context,
            metadata={"recipient": recipient},
        )

    # ── Process decisions ─────────────────────────────────────────────

    def approve(self, request_id: str, by: str = "manager",
                reason: str = "") -> dict:
        """
        Approve a pending request and execute the action.

        Returns a result dict with execution status.
        """
        req = self._queue.approve(request_id, by, reason)
        if not req:
            return {"status": "error", "message": f"Request {request_id} not found or not pending"}

        self._log_action("approved", req)

        # Execute the approved action
        execution_result = self._execute_approved(req)

        return {
            "status": "approved",
            "request_id": request_id,
            "title": req.title,
            "category": req.category,
            "decided_by": by,
            "execution": execution_result,
            "timestamp": datetime.now().isoformat(),
        }

    def reject(self, request_id: str, by: str = "manager",
               reason: str = "") -> dict:
        """Reject a pending request."""
        req = self._queue.reject(request_id, by, reason)
        if not req:
            return {"status": "error", "message": f"Request {request_id} not found or not pending"}

        self._log_action("rejected", req)

        return {
            "status": "rejected",
            "request_id": request_id,
            "title": req.title,
            "category": req.category,
            "decided_by": by,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        }

    # ── Process file-based approvals (backwards compatible) ───────────

    def check_file_approvals(self) -> list[dict]:
        """
        Check approval directory for file-based manager decisions.
        This maintains backwards compatibility with the markdown file workflow.

        Returns a list of result dicts for each processed decision.
        """
        results = []

        for req in self._queue.pending():
            approval_file = self._approval_dir / f"Approval_{req.request_id}.md"
            if not approval_file.exists():
                continue

            try:
                content = approval_file.read_text(encoding="utf-8")
            except OSError:
                continue

            # Parse decision from file
            decision = self._parse_file_decision(content)
            if not decision:
                continue

            if decision == "approved":
                result = self.approve(req.request_id, by="manager (file)", reason="Approved via file")
            else:
                result = self.reject(req.request_id, by="manager (file)", reason="Rejected via file")

            results.append(result)

        return results

    def process_expiry(self) -> list[dict]:
        """Process expired requests. Returns list of expired request summaries."""
        expired = self._queue.process_expiry()
        results = []
        for req in expired:
            self._log_action("expired", req)
            results.append({
                "status": "expired",
                "request_id": req.request_id,
                "title": req.title,
                "created_at": req.created_at,
                "expired_at": req.decision_at,
            })
        return results

    # ── Queries ───────────────────────────────────────────────────────

    def get_pending(self) -> list[dict]:
        """Return all pending approvals as dicts for the API/dashboard."""
        return [r.to_dict() for r in self._queue.pending()]

    def get_all(self) -> list[dict]:
        """Return all approval requests."""
        return [r.summary() for r in self._queue.all_requests()]

    def get_request(self, request_id: str) -> dict | None:
        """Get full details of a specific request."""
        req = self._queue.get(request_id)
        return req.to_dict() if req else None

    def get_stats(self) -> dict:
        """Return approval system statistics."""
        summary = self._queue.summary()
        summary["recent_decisions"] = self._action_log[-10:]
        return summary

    # ── Internal helpers ──────────────────────────────────────────────

    def _classify_category(self, text: str) -> str:
        """Auto-classify the approval category based on content."""
        text_lower = text.lower()

        for kw in FINANCIAL_KEYWORDS:
            if kw in text_lower:
                return ApprovalCategory.FINANCIAL

        for kw in CONTENT_KEYWORDS:
            if kw in text_lower:
                return ApprovalCategory.CONTENT

        for kw in COMMUNICATION_KEYWORDS:
            if kw in text_lower:
                return ApprovalCategory.COMMUNICATION

        return ApprovalCategory.GENERAL

    def _execute_approved(self, req: ApprovalRequest) -> dict:
        """
        Execute an approved action by routing back to the originating agent.
        """
        if not self._agents:
            return {"status": "no_agents", "message": "No agents configured for execution"}

        agent = self._agents.get(req.source_agent)
        if not agent:
            log.warning("No agent '%s' found for approved request %s",
                        req.source_agent, req.request_id)
            return {"status": "agent_not_found", "agent": req.source_agent}

        try:
            # Build a minimal decision for agent execution
            from ai_employee.brain.decision_engine import TaskDecision, Action
            decision = TaskDecision(
                task_id=req.task_id or req.request_id,
                title=req.title,
                category=req.category,
                priority=req.priority,
                action=Action.AUTO_EXECUTE,
                confidence=1.0,
                reasoning=f"Approved by {req.decision_by}: {req.decision_reason}",
                assigned_agent=req.source_agent,
                steps=["Execute approved action"],
                risk_score=0.0,
            )

            result = agent.execute(decision, req.proposed_action or req.description)
            log.info("Executed approved action: [%s] '%s' -> %s",
                     req.request_id, req.title, result.get("status", "unknown"))
            return result

        except Exception as exc:
            log.error("Failed to execute approved action [%s]: %s",
                      req.request_id, exc)
            return {"status": "execution_failed", "error": str(exc)}

    def _write_approval_file(self, req: ApprovalRequest) -> None:
        """Write an approval request as a markdown file for file-based review."""
        filepath = self._approval_dir / f"Approval_{req.request_id}.md"

        flags_md = "\n".join(f"  - {f}" for f in req.safety_flags) or "  - None"

        md = (
            f"# Approval Request — {req.title}\n\n"
            f"---\n\n"
            f"| Field           | Value                                |\n"
            f"|-----------------|--------------------------------------|\n"
            f"| Request ID      | `{req.request_id}`                   |\n"
            f"| Category        | **{req.category.upper()}**           |\n"
            f"| Priority        | **{req.priority}**                   |\n"
            f"| Risk Level      | {req.risk_level}                     |\n"
            f"| Source           | {req.source}                        |\n"
            f"| Agent           | `{req.source_agent}`                 |\n"
            f"| Created         | {req.created_at[:19]}                |\n"
            f"| Expires         | {req.expires_at[:19] if req.expires_at else 'Never'} |\n\n"
            f"---\n\n"
            f"## Description\n\n{req.description}\n\n"
            f"## Proposed Action\n\n{req.proposed_action}\n\n"
        )

        if req.context:
            md += f"## Context\n\n{req.context}\n\n"

        md += (
            f"## Safety Flags\n\n{flags_md}\n\n"
            f"---\n\n"
            f"<!-- DECISION BELOW THIS LINE -->\n\n"
            f"**Manager Decision:** PENDING\n\n"
            f"*(Replace PENDING with APPROVED or REJECTED)*\n"
        )

        filepath.write_text(md, encoding="utf-8")
        log.info("Approval file written: %s", filepath.name)

    def _notify_manager(self, req: ApprovalRequest) -> None:
        """Send notification to the manager about a new approval request."""
        # Email notification (if Gmail sender is configured)
        if self._gmail_sender and self._manager_email:
            try:
                subject = f"[AI Employee] Approval Required: {req.title}"
                body = (
                    f"A new action requires your approval.\n\n"
                    f"Title: {req.title}\n"
                    f"Category: {req.category}\n"
                    f"Priority: {req.priority}\n"
                    f"Risk Level: {req.risk_level}\n\n"
                    f"Description:\n{req.description}\n\n"
                    f"Proposed Action:\n{req.proposed_action[:300]}\n\n"
                )
                if req.safety_flags:
                    body += "Safety Flags:\n"
                    for flag in req.safety_flags:
                        body += f"  - {flag}\n"
                    body += "\n"
                body += (
                    f"Please approve or reject via the dashboard or "
                    f"by editing the approval file.\n\n"
                    f"---\nAI Employee — Gold Tier\n"
                )
                self._gmail_sender.send(self._manager_email, subject, body)
                req.notified_at = datetime.now().isoformat()
                log.info("Manager notified via email for %s", req.request_id)
            except Exception as exc:
                log.warning("Failed to notify manager via email: %s", exc)

    @staticmethod
    def _parse_file_decision(content: str) -> str | None:
        """Parse decision from a markdown approval file."""
        if "<!-- DECISION BELOW THIS LINE -->" in content:
            decision_text = content.split(
                "<!-- DECISION BELOW THIS LINE -->", 1
            )[1].strip().upper()
        else:
            decision_text = content.upper()

        if "APPROVED" in decision_text or "YES" in decision_text:
            return "approved"
        if "REJECTED" in decision_text or "NO" in decision_text:
            return "rejected"
        return None

    # ── Audit logging ─────────────────────────────────────────────────

    def _log_action(self, action: str, req: ApprovalRequest) -> None:
        """Record an action to the audit log."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "request_id": req.request_id,
            "title": req.title,
            "category": req.category,
            "priority": req.priority,
            "source": req.source,
            "decided_by": req.decision_by,
            "reason": req.decision_reason,
        }
        self._action_log.append(entry)

    def save_audit_log(self) -> None:
        """Persist the audit log to disk."""
        if not self._action_log:
            return

        self._log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self._log_dir / f"approval_audit_{timestamp}.json"

        try:
            filepath.write_text(
                json.dumps(self._action_log, indent=2), encoding="utf-8",
            )
            log.info("Approval audit log saved: %s", filepath.name)
        except Exception as exc:
            log.error("Failed to save approval audit log: %s", exc)

    def get_audit_log(self) -> list[dict]:
        return list(self._action_log)
