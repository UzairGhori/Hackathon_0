"""
AI Employee — Permission Manager (Platinum Tier)

Defines what each deployment role (cloud / local) is allowed to do.
Every external action in the system passes through this gate.

Design:
    Cloud AI CANNOT:    send payments, send final emails, post final content
    Cloud AI CAN:       create drafts, generate reports, prepare approvals
    Local AI CAN:       everything (human-supervised)

Action taxonomy:
    FINAL actions   — irreversible external effects (send, post, pay)
    DRAFT actions   — create local artifacts for review (draft, report, queue)
    READ actions    — read-only queries (fetch, query, poll)

Usage:
    from ai_employee.brain.permission_manager import permissions

    # Check before doing something
    if permissions.can("send_email"):
        sender.send(...)

    # Or enforce (raises PermissionDenied)
    permissions.enforce("register_payment")

    # Or use as decorator
    @permissions.require("post_tweet")
    def post_tweet(text): ...
"""

from __future__ import annotations

import functools
import json
import logging
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("ai_employee.permissions")


# ── Exceptions ───────────────────────────────────────────────────────────────

class PermissionDenied(Exception):
    """Raised when an action is blocked by the permission manager."""

    def __init__(self, action: str, role: str, reason: str = ""):
        self.action = action
        self.role = role
        self.reason = reason or f"Action '{action}' is not permitted for role '{role}'"
        super().__init__(self.reason)


# ── Enums ────────────────────────────────────────────────────────────────────

class Role(str, Enum):
    """Deployment role — determines permission set."""
    CLOUD = "cloud"
    LOCAL = "local"


class ActionType(str, Enum):
    """Coarse classification of an action's risk level."""
    FINAL = "final"      # Irreversible external effect
    DRAFT = "draft"      # Creates local artifact for review
    READ  = "read"       # Read-only query


class ActionCategory(str, Enum):
    """Domain category of the action."""
    EMAIL        = "email"
    SOCIAL       = "social"
    FINANCIAL    = "financial"
    MESSAGING    = "messaging"
    REPORTING    = "reporting"
    APPROVAL     = "approval"
    SYSTEM       = "system"


# ── Action registry ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionDef:
    """Definition of a single permissioned action."""
    name: str
    action_type: ActionType
    category: ActionCategory
    description: str
    cloud_allowed: bool
    local_allowed: bool


# Master registry of every action in the system.
# This is the SINGLE SOURCE OF TRUTH for what is and isn't allowed.

_ACTIONS: dict[str, ActionDef] = {}


def _register(name: str, action_type: ActionType, category: ActionCategory,
              description: str, cloud: bool, local: bool) -> None:
    _ACTIONS[name] = ActionDef(
        name=name, action_type=action_type, category=category,
        description=description, cloud_allowed=cloud, local_allowed=local,
    )


# ── EMAIL ────────────────────────────────────────────────────────────────
_register("send_email",       ActionType.FINAL, ActionCategory.EMAIL,
          "Send a final email via Gmail API",             cloud=False, local=True)
_register("draft_email",      ActionType.DRAFT, ActionCategory.EMAIL,
          "Create a Gmail draft for human review",        cloud=True,  local=True)
_register("read_email",       ActionType.READ,  ActionCategory.EMAIL,
          "Fetch unread emails from Gmail inbox",         cloud=True,  local=True)

# ── SOCIAL MEDIA ─────────────────────────────────────────────────────────
_register("post_facebook",    ActionType.FINAL, ActionCategory.SOCIAL,
          "Publish a post to Facebook Page",              cloud=False, local=True)
_register("post_instagram",   ActionType.FINAL, ActionCategory.SOCIAL,
          "Publish a post to Instagram Business",         cloud=False, local=True)
_register("post_tweet",       ActionType.FINAL, ActionCategory.SOCIAL,
          "Post a tweet to Twitter/X",                    cloud=False, local=True)
_register("post_linkedin",    ActionType.FINAL, ActionCategory.SOCIAL,
          "Publish a post on LinkedIn",                   cloud=False, local=True)
_register("draft_social",     ActionType.DRAFT, ActionCategory.SOCIAL,
          "Create a social media draft for review",       cloud=True,  local=True)
_register("read_social",      ActionType.READ,  ActionCategory.SOCIAL,
          "Fetch social media metrics and mentions",      cloud=True,  local=True)

# ── MESSAGING ────────────────────────────────────────────────────────────
_register("send_linkedin_message",      ActionType.FINAL, ActionCategory.MESSAGING,
          "Send a LinkedIn direct message",               cloud=False, local=True)
_register("send_linkedin_connection",   ActionType.FINAL, ActionCategory.MESSAGING,
          "Send a LinkedIn connection request",           cloud=False, local=True)
_register("send_whatsapp",             ActionType.FINAL, ActionCategory.MESSAGING,
          "Send a WhatsApp message",                      cloud=False, local=True)
_register("draft_message",             ActionType.DRAFT, ActionCategory.MESSAGING,
          "Create a message draft for review",            cloud=True,  local=True)
_register("read_messages",             ActionType.READ,  ActionCategory.MESSAGING,
          "Fetch incoming messages",                      cloud=True,  local=True)

# ── FINANCIAL ────────────────────────────────────────────────────────────
_register("register_payment",  ActionType.FINAL, ActionCategory.FINANCIAL,
          "Register a payment in Odoo accounting",        cloud=False, local=True)
_register("confirm_invoice",   ActionType.FINAL, ActionCategory.FINANCIAL,
          "Post/confirm a draft invoice in Odoo",         cloud=False, local=True)
_register("create_invoice",    ActionType.FINAL, ActionCategory.FINANCIAL,
          "Create a new invoice in Odoo",                 cloud=False, local=True)
_register("create_journal_entry", ActionType.FINAL, ActionCategory.FINANCIAL,
          "Create a manual journal entry in Odoo",        cloud=False, local=True)
_register("write_odoo_record", ActionType.FINAL, ActionCategory.FINANCIAL,
          "Modify an existing Odoo record",               cloud=False, local=True)
_register("read_odoo",         ActionType.READ,  ActionCategory.FINANCIAL,
          "Query Odoo records (invoices, accounts, P&L)", cloud=True,  local=True)
_register("draft_financial",   ActionType.DRAFT, ActionCategory.FINANCIAL,
          "Create a financial action draft for approval",  cloud=True,  local=True)

# ── REPORTING ────────────────────────────────────────────────────────────
_register("generate_report",   ActionType.DRAFT, ActionCategory.REPORTING,
          "Generate a CEO briefing or analytics report",  cloud=True,  local=True)
_register("generate_summary",  ActionType.DRAFT, ActionCategory.REPORTING,
          "Generate a weekly/daily summary",              cloud=True,  local=True)

# ── APPROVAL ─────────────────────────────────────────────────────────────
_register("queue_approval",    ActionType.DRAFT, ActionCategory.APPROVAL,
          "Submit an action for human approval",          cloud=True,  local=True)
_register("execute_approved",  ActionType.FINAL, ActionCategory.APPROVAL,
          "Execute a previously approved action",         cloud=False, local=True)
_register("decide_approval",   ActionType.FINAL, ActionCategory.APPROVAL,
          "Approve or reject a pending request",          cloud=False, local=True)

# ── SYSTEM ───────────────────────────────────────────────────────────────
_register("write_vault_file",  ActionType.DRAFT, ActionCategory.SYSTEM,
          "Write a file to the vault (inbox, draft, etc.)", cloud=True, local=True)
_register("git_push",          ActionType.DRAFT, ActionCategory.SYSTEM,
          "Push vault changes via git",                   cloud=True,  local=True)


# ── Permission decision ─────────────────────────────────────────────────────

@dataclass
class PermissionDecision:
    """Result of a permission check."""
    action: str
    role: str
    allowed: bool
    action_type: str
    category: str
    reason: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    draft_alternative: str = ""   # If denied, the draft action to use instead


# ── Permission Manager ───────────────────────────────────────────────────────

class PermissionManager:
    """
    Central authority for action permissions.

    Instantiated once at startup.  All agents, clients, and controllers
    call `can()` / `enforce()` before performing actions.
    """

    def __init__(self, role: Role | str | None = None):
        if role is None:
            role = os.getenv("SYNC_ROLE", "local")
        self._role = Role(role) if isinstance(role, str) else role
        self._lock = threading.Lock()
        self._log: list[PermissionDecision] = []
        self._denied_count = 0
        self._allowed_count = 0

        log.info("PermissionManager initialised — role=%s", self._role.value)

    @property
    def role(self) -> Role:
        return self._role

    @property
    def is_cloud(self) -> bool:
        return self._role == Role.CLOUD

    @property
    def is_local(self) -> bool:
        return self._role == Role.LOCAL

    # ── Core API ─────────────────────────────────────────────────────────

    def check(self, action: str, context: dict | None = None) -> PermissionDecision:
        """
        Check whether an action is permitted under the current role.

        Returns a PermissionDecision — never raises.
        """
        action_def = _ACTIONS.get(action)
        if not action_def:
            decision = PermissionDecision(
                action=action, role=self._role.value, allowed=False,
                action_type="unknown", category="unknown",
                reason=f"Unknown action '{action}' — denied by default",
            )
            self._record(decision)
            return decision

        allowed = (
            action_def.cloud_allowed if self.is_cloud
            else action_def.local_allowed
        )

        # Determine the draft alternative for denied final actions
        draft_alt = ""
        if not allowed and action_def.action_type == ActionType.FINAL:
            draft_alt = _DRAFT_ALTERNATIVES.get(action, "")

        reason = (
            f"Allowed: {action_def.action_type.value} action '{action}' "
            f"permitted for {self._role.value}"
        ) if allowed else (
            f"Denied: {action_def.action_type.value} action '{action}' "
            f"is NOT permitted for {self._role.value}"
            f"{f' — use {draft_alt} instead' if draft_alt else ''}"
        )

        decision = PermissionDecision(
            action=action, role=self._role.value, allowed=allowed,
            action_type=action_def.action_type.value,
            category=action_def.category.value,
            reason=reason, draft_alternative=draft_alt,
        )
        self._record(decision)
        return decision

    def can(self, action: str) -> bool:
        """Simple boolean: is this action allowed?"""
        return self.check(action).allowed

    def enforce(self, action: str, context: dict | None = None) -> PermissionDecision:
        """
        Check permission and raise PermissionDenied if not allowed.

        Returns the PermissionDecision if allowed.
        """
        decision = self.check(action, context)
        if not decision.allowed:
            raise PermissionDenied(
                action=action, role=self._role.value,
                reason=decision.reason,
            )
        return decision

    def require(self, action: str) -> Callable:
        """
        Decorator that enforces a permission before calling the function.

        Usage:
            @permissions.require("send_email")
            def send(self, to, subject, body): ...
        """
        def decorator(fn: Callable) -> Callable:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                self.enforce(action)
                return fn(*args, **kwargs)
            return wrapper
        return decorator

    def get_draft_alternative(self, action: str) -> str:
        """Return the draft action name to use instead of a denied final action."""
        return _DRAFT_ALTERNATIVES.get(action, "")

    # ── Queries ──────────────────────────────────────────────────────────

    def get_allowed_actions(self) -> list[str]:
        """Return all actions permitted for the current role."""
        return [
            name for name, adef in _ACTIONS.items()
            if (adef.cloud_allowed if self.is_cloud else adef.local_allowed)
        ]

    def get_denied_actions(self) -> list[str]:
        """Return all actions denied for the current role."""
        return [
            name for name, adef in _ACTIONS.items()
            if not (adef.cloud_allowed if self.is_cloud else adef.local_allowed)
        ]

    def get_final_actions(self) -> list[str]:
        """Return all FINAL-type actions (the dangerous ones)."""
        return [
            name for name, adef in _ACTIONS.items()
            if adef.action_type == ActionType.FINAL
        ]

    def get_action_def(self, action: str) -> ActionDef | None:
        return _ACTIONS.get(action)

    def get_all_actions(self) -> dict[str, dict]:
        """Return all registered actions as dicts (for API/dashboard)."""
        return {
            name: {
                "name": adef.name,
                "type": adef.action_type.value,
                "category": adef.category.value,
                "description": adef.description,
                "cloud_allowed": adef.cloud_allowed,
                "local_allowed": adef.local_allowed,
            }
            for name, adef in _ACTIONS.items()
        }

    def get_stats(self) -> dict:
        """Return permission check statistics."""
        return {
            "role": self._role.value,
            "total_checks": self._allowed_count + self._denied_count,
            "allowed": self._allowed_count,
            "denied": self._denied_count,
            "recent_denials": [
                asdict(d) for d in self._log[-20:]
                if not d.allowed
            ],
        }

    # ── Internal ─────────────────────────────────────────────────────────

    def _record(self, decision: PermissionDecision) -> None:
        with self._lock:
            if decision.allowed:
                self._allowed_count += 1
            else:
                self._denied_count += 1
                log.warning(
                    "PERMISSION DENIED: [%s] %s — %s",
                    decision.role, decision.action, decision.reason,
                )
            # Keep last 500 decisions in memory
            self._log.append(decision)
            if len(self._log) > 500:
                self._log = self._log[-500:]


# ── Draft alternatives map ───────────────────────────────────────────────────
#
# When a FINAL action is denied, this maps it to the DRAFT action
# the cloud should use instead.

_DRAFT_ALTERNATIVES: dict[str, str] = {
    # Email
    "send_email":                "draft_email",

    # Social
    "post_facebook":             "draft_social",
    "post_instagram":            "draft_social",
    "post_tweet":                "draft_social",
    "post_linkedin":             "draft_social",

    # Messaging
    "send_linkedin_message":     "draft_message",
    "send_linkedin_connection":  "draft_message",
    "send_whatsapp":             "draft_message",

    # Financial
    "register_payment":          "draft_financial",
    "confirm_invoice":           "draft_financial",
    "create_invoice":            "draft_financial",
    "create_journal_entry":      "draft_financial",
    "write_odoo_record":         "draft_financial",

    # Approval execution
    "execute_approved":          "queue_approval",
    "decide_approval":           "queue_approval",
}


# ── Module-level singleton ───────────────────────────────────────────────────
#
# Imported as:  from ai_employee.brain.permission_manager import permissions

permissions = PermissionManager()
