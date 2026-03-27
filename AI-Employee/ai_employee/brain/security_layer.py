"""
AI Employee — Security Isolation Layer (Platinum Tier)

Unified security facade that ties together:
    - RoleManager   (role-based access, data domain separation)
    - SecretsManager (encrypted API key vault, scoped access)
    - PermissionManager (action-level cloud/local gating)

This is the SINGLE ENTRY POINT for all security checks in the system.
Every agent action passes through here before touching external services.

Data Domains:
    PERSONAL — Employee contacts, personal preferences
    BUSINESS — Tasks, social media, vault contents
    FINANCE  — Invoices, payments, accounting (Odoo)

Security Layers:
    1. Domain Isolation  — Agent can only touch its assigned domains
    2. Action Gating     — Cloud AI cannot execute FINAL actions
    3. Approval Limits   — Financial actions capped per role
    4. Secret Scoping    — Agents get only their own API keys
    5. Audit Trail       — Every check is logged

Usage:
    from ai_employee.brain.security_layer import security

    # Before an agent acts:
    security.enforce("gmail_agent", "send_email", domain="business")

    # Get a secret (scoped):
    api_key = security.get_secret("ANTHROPIC_API_KEY", agent="gmail_agent")

    # Check approval amount:
    security.enforce_approval(5000.00)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from ai_employee.brain.role_manager import (
    RoleManager, DataDomain, DomainAccessDenied, ApprovalLimitExceeded,
    role_manager as _default_role_mgr,
)
from ai_employee.brain.secrets_manager import (
    SecretsManager, SecretAccessDenied, SecretNotFound,
    secrets as _default_secrets,
)
from ai_employee.brain.permission_manager import (
    PermissionManager, PermissionDenied,
    permissions as _default_perms,
)

log = logging.getLogger("ai_employee.security_layer")


# ── Exceptions ───────────────────────────────────────────────────────────────

class SecurityViolation(Exception):
    """Raised when any security check fails."""
    def __init__(self, agent: str, action: str, reason: str, layer: str):
        self.agent = agent
        self.action = action
        self.reason = reason
        self.layer = layer
        super().__init__(
            f"[{layer}] Security violation: agent='{agent}' action='{action}' — {reason}"
        )


# ── Audit record ─────────────────────────────────────────────────────────────

@dataclass
class SecurityEvent:
    """Record of a security check."""
    agent: str
    action: str
    domain: str
    layer: str                    # "domain", "action", "approval", "secret"
    allowed: bool
    role: str
    reason: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Security Layer ───────────────────────────────────────────────────────────

class SecurityLayer:
    """
    Unified security facade.

    Combines domain isolation, action gating, approval limits,
    and secret scoping into a single, auditable API.
    Thread-safe.
    """

    def __init__(
        self,
        role_manager: Optional[RoleManager] = None,
        secrets_manager: Optional[SecretsManager] = None,
        permission_manager: Optional[PermissionManager] = None,
        audit_logger=None,
    ):
        self._roles = role_manager or _default_role_mgr
        self._secrets = secrets_manager or _default_secrets
        self._perms = permission_manager or _default_perms
        self._audit = audit_logger
        self._lock = threading.Lock()
        self._events: list[SecurityEvent] = []
        log.info("SecurityLayer initialised (role=%s)", self._roles.active_role_name)

    # ── References ────────────────────────────────────────────────────

    @property
    def role_manager(self) -> RoleManager:
        return self._roles

    @property
    def secrets_manager(self) -> SecretsManager:
        return self._secrets

    @property
    def permission_manager(self) -> PermissionManager:
        return self._perms

    # ── Primary enforcement API ───────────────────────────────────────

    def enforce(
        self,
        agent: str,
        action: str,
        domain: Optional[str] = None,
        write: bool = False,
        amount: Optional[float] = None,
    ) -> SecurityEvent:
        """
        Full security enforcement.

        Checks (in order):
            1. Domain isolation  (if domain specified)
            2. Action gating     (PermissionManager)
            3. Approval limit    (if amount specified)

        Raises SecurityViolation on any failure.
        Returns a SecurityEvent on success.
        """
        resolved_domain = domain or self._infer_domain(agent, action)

        # Layer 1: Domain isolation
        if resolved_domain:
            try:
                dd = DataDomain(resolved_domain)
                self._roles.enforce_domain_access(agent, dd, write=write)
            except DomainAccessDenied as exc:
                event = self._log_event(
                    agent, action, resolved_domain, "domain", False,
                    str(exc),
                )
                raise SecurityViolation(agent, action, str(exc), "domain") from exc

        # Layer 2: Action gating
        try:
            self._perms.enforce(action)
        except PermissionDenied as exc:
            event = self._log_event(
                agent, action, resolved_domain or "", "action", False,
                str(exc),
            )
            raise SecurityViolation(agent, action, str(exc), "action") from exc

        # Layer 3: Approval limit
        if amount is not None:
            try:
                self._roles.enforce_approval_limit(amount)
            except ApprovalLimitExceeded as exc:
                event = self._log_event(
                    agent, action, resolved_domain or "", "approval", False,
                    str(exc), metadata={"amount": amount},
                )
                raise SecurityViolation(agent, action, str(exc), "approval") from exc

        return self._log_event(
            agent, action, resolved_domain or "", "all", True,
            "All security checks passed",
            metadata={"write": write, "amount": amount},
        )

    def check(
        self,
        agent: str,
        action: str,
        domain: Optional[str] = None,
        write: bool = False,
        amount: Optional[float] = None,
    ) -> SecurityEvent:
        """
        Non-raising security check (returns event with allowed=True/False).
        """
        try:
            return self.enforce(agent, action, domain, write, amount)
        except SecurityViolation as exc:
            # Already logged inside enforce()
            return SecurityEvent(
                agent=agent, action=action, domain=domain or "",
                layer=exc.layer, allowed=False,
                role=self._roles.active_role_name,
                reason=exc.reason,
            )

    def can(
        self,
        agent: str,
        action: str,
        domain: Optional[str] = None,
    ) -> bool:
        """Simple boolean: can this agent perform this action?"""
        event = self.check(agent, action, domain)
        return event.allowed

    # ── Secret access ─────────────────────────────────────────────────

    def get_secret(self, name: str, agent: str = "system") -> str:
        """
        Retrieve a secret from the vault with scope enforcement.

        Raises SecretAccessDenied or SecretNotFound.
        """
        try:
            value = self._secrets.get(name, agent=agent)
            self._log_event(agent, f"get_secret:{name}", "", "secret", True, "Granted")
            return value
        except (SecretAccessDenied, SecretNotFound) as exc:
            self._log_event(agent, f"get_secret:{name}", "", "secret", False, str(exc))
            raise

    def has_secret(self, name: str) -> bool:
        """Check if a secret exists (no scope check)."""
        return self._secrets.has(name)

    # ── Approval ──────────────────────────────────────────────────────

    def enforce_approval(self, amount: float) -> None:
        """Enforce the active role's approval limit."""
        self._roles.enforce_approval_limit(amount)

    def can_approve(self, amount: float) -> bool:
        """Check if the active role can approve the given amount."""
        return self._roles.can_approve_amount(amount)

    # ── Role management ───────────────────────────────────────────────

    def set_role(self, role: str) -> None:
        """Change the active security role."""
        self._roles.set_active_role(role)
        log.info("Security role changed to '%s'", role)

    @property
    def active_role(self) -> str:
        return self._roles.active_role_name

    # ── Queries ───────────────────────────────────────────────────────

    @property
    def events(self) -> list[dict]:
        """Return recent security events."""
        return [e.to_dict() for e in self._events[-200:]]

    @property
    def violations(self) -> list[dict]:
        """Return recent security violations."""
        return [
            e.to_dict() for e in self._events[-500:]
            if not e.allowed
        ]

    @property
    def stats(self) -> dict:
        """Aggregated security statistics."""
        total = len(self._events)
        denied = sum(1 for e in self._events if not e.allowed)
        by_layer: dict[str, int] = {}
        by_agent: dict[str, int] = {}
        for e in self._events:
            if not e.allowed:
                by_layer[e.layer] = by_layer.get(e.layer, 0) + 1
                by_agent[e.agent] = by_agent.get(e.agent, 0) + 1

        return {
            "active_role": self._roles.active_role_name,
            "total_checks": total,
            "allowed": total - denied,
            "denied": denied,
            "violations_by_layer": by_layer,
            "violations_by_agent": by_agent,
            "role_stats": self._roles.stats,
            "secret_stats": self._secrets.stats,
            "permission_stats": self._perms.get_stats(),
        }

    def get_security_report(self) -> dict:
        """Comprehensive security posture report."""
        return {
            "summary": self.stats,
            "roles": self._roles.get_all_roles(),
            "agent_domains": self._roles.get_agent_domain_map(),
            "secrets": self._secrets.list_secrets(),
            "rotation_report": self._secrets.get_rotation_report(),
            "recent_violations": self.violations[-20:],
            "allowed_actions": self._perms.get_allowed_actions(),
            "denied_actions": self._perms.get_denied_actions(),
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _infer_domain(self, agent: str, action: str) -> str:
        """Infer the data domain from the agent and action name."""
        # Check action-level hints
        action_def = self._perms.get_action_def(action)
        if action_def:
            category = action_def.category.value
            domain_map = {
                "financial": "finance",
                "email": "business",
                "social": "business",
                "messaging": "business",
                "reporting": "business",
                "approval": "business",
                "system": "business",
            }
            return domain_map.get(category, "business")

        # Fall back to agent domain
        domains = self._roles.get_agent_domains(agent)
        if domains:
            # Prefer business > finance > personal
            for pref in (DataDomain.BUSINESS, DataDomain.FINANCE, DataDomain.PERSONAL):
                if pref in domains:
                    return pref.value
        return "business"

    def _log_event(
        self, agent: str, action: str, domain: str,
        layer: str, allowed: bool, reason: str,
        metadata: Optional[dict] = None,
    ) -> SecurityEvent:
        event = SecurityEvent(
            agent=agent, action=action, domain=domain,
            layer=layer, allowed=allowed,
            role=self._roles.active_role_name,
            reason=reason, metadata=metadata or {},
        )

        with self._lock:
            self._events.append(event)
            if len(self._events) > 5000:
                self._events = self._events[-5000:]

        if not allowed:
            log.warning(
                "SECURITY VIOLATION [%s]: agent=%s action=%s domain=%s — %s",
                layer, agent, action, domain, reason,
            )

        # Forward to audit logger if available
        if self._audit and not allowed:
            try:
                self._audit.log_system_event(
                    event="security_violation",
                    summary=f"[{layer}] {agent}: {action} — {reason}",
                    metadata=event.to_dict(),
                )
            except Exception:
                pass

        return event


# ── Module singleton ─────────────────────────────────────────────────────────

security = SecurityLayer()
