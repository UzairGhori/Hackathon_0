"""
AI Employee — Role Manager (Platinum Tier)

Role-based access control with data domain separation.

Data Domains:
    PERSONAL    — Employee contacts, personal preferences, HR data
    BUSINESS    — Tasks, projects, social media, vault contents
    FINANCE     — Invoices, payments, P&L, balance sheets (Odoo)

Roles:
    CEO         — Full access to all domains, unlimited approval authority
    MANAGER     — Business + Finance read, approval up to a threshold
    OPERATOR    — Business only, no financial write, low approval limit
    VIEWER      — Read-only across Business domain
    CLOUD_AI    — The AI running on cloud: Business read + draft, no FINAL

Each agent is bound to one or more domains. The RoleManager enforces that
an agent can only touch data in its allowed domains, and that approval
amounts respect the role's limit.

Usage:
    from ai_employee.brain.role_manager import role_manager

    role_manager.set_active_role("manager")
    role_manager.enforce_domain_access("gmail_agent", DataDomain.BUSINESS)
    role_manager.enforce_approval_limit("manager", 5000.00)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

log = logging.getLogger("ai_employee.role_manager")


# ── Enums ────────────────────────────────────────────────────────────────────

class DataDomain(str, Enum):
    """Data isolation domains."""
    PERSONAL = "personal"
    BUSINESS = "business"
    FINANCE  = "finance"


class RoleName(str, Enum):
    """Built-in role identifiers."""
    CEO      = "ceo"
    MANAGER  = "manager"
    OPERATOR = "operator"
    VIEWER   = "viewer"
    CLOUD_AI = "cloud_ai"


# ── Role definition ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RoleDef:
    """Immutable definition of a role's capabilities."""
    name: RoleName
    display_name: str
    allowed_domains: frozenset[DataDomain]
    writable_domains: frozenset[DataDomain]
    approval_limit: float            # max USD value this role can approve (0 = none)
    can_approve: bool                # whether this role can approve actions at all
    can_execute_final: bool          # whether this role can run FINAL actions
    description: str = ""


# ── Built-in roles ──────────────────────────────────────────────────────────

_ROLES: dict[str, RoleDef] = {}


def _register_role(
    name: RoleName, display: str,
    read: set[DataDomain], write: set[DataDomain],
    approval_limit: float, can_approve: bool, can_final: bool,
    desc: str = "",
) -> None:
    _ROLES[name.value] = RoleDef(
        name=name, display_name=display,
        allowed_domains=frozenset(read),
        writable_domains=frozenset(write),
        approval_limit=approval_limit,
        can_approve=can_approve,
        can_execute_final=can_final,
        description=desc,
    )


_register_role(
    RoleName.CEO, "Chief Executive Officer",
    read={DataDomain.PERSONAL, DataDomain.BUSINESS, DataDomain.FINANCE},
    write={DataDomain.PERSONAL, DataDomain.BUSINESS, DataDomain.FINANCE},
    approval_limit=float("inf"), can_approve=True, can_final=True,
    desc="Full access to all domains and unlimited approval authority",
)
_register_role(
    RoleName.MANAGER, "Manager",
    read={DataDomain.BUSINESS, DataDomain.FINANCE},
    write={DataDomain.BUSINESS, DataDomain.FINANCE},
    approval_limit=10_000.00, can_approve=True, can_final=True,
    desc="Business + Finance access, approval up to $10,000",
)
_register_role(
    RoleName.OPERATOR, "Operator",
    read={DataDomain.BUSINESS},
    write={DataDomain.BUSINESS},
    approval_limit=500.00, can_approve=True, can_final=True,
    desc="Business domain only, low approval limit",
)
_register_role(
    RoleName.VIEWER, "Viewer",
    read={DataDomain.BUSINESS},
    write=set(),
    approval_limit=0, can_approve=False, can_final=False,
    desc="Read-only access to Business domain",
)
_register_role(
    RoleName.CLOUD_AI, "Cloud AI Worker",
    read={DataDomain.BUSINESS, DataDomain.FINANCE},
    write={DataDomain.BUSINESS},
    approval_limit=0, can_approve=False, can_final=False,
    desc="AI on cloud: read Business+Finance, write Business drafts only",
)


# ── Agent-to-Domain mapping ─────────────────────────────────────────────────

_AGENT_DOMAINS: dict[str, set[DataDomain]] = {
    "email_agent":    {DataDomain.BUSINESS, DataDomain.PERSONAL},
    "gmail_agent":    {DataDomain.BUSINESS, DataDomain.PERSONAL},
    "linkedin_agent": {DataDomain.BUSINESS},
    "odoo_agent":     {DataDomain.FINANCE},
    "meta_agent":     {DataDomain.BUSINESS},
    "twitter_agent":  {DataDomain.BUSINESS},
    "audit_agent":    {DataDomain.BUSINESS, DataDomain.FINANCE},
    "task_agent":     {DataDomain.BUSINESS},
}


# ── Exceptions ───────────────────────────────────────────────────────────────

class DomainAccessDenied(Exception):
    """Raised when an agent tries to access a domain outside its scope."""
    def __init__(self, agent: str, domain: DataDomain, role: str):
        self.agent = agent
        self.domain = domain
        self.role = role
        super().__init__(
            f"Agent '{agent}' cannot access {domain.value} domain under role '{role}'"
        )


class ApprovalLimitExceeded(Exception):
    """Raised when an action exceeds the role's approval limit."""
    def __init__(self, role: str, amount: float, limit: float):
        self.role = role
        self.amount = amount
        self.limit = limit
        super().__init__(
            f"Role '{role}' approval limit ${limit:,.2f} exceeded by ${amount:,.2f}"
        )


# ── Access log entry ─────────────────────────────────────────────────────────

@dataclass
class AccessRecord:
    """Audit record for a domain access check."""
    agent: str
    domain: str
    role: str
    allowed: bool
    reason: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


# ── Role Manager ─────────────────────────────────────────────────────────────

class RoleManager:
    """
    Central role-based access control authority.

    Thread-safe. Manages the active role, enforces domain access for agents,
    and validates approval amounts against role limits.
    """

    def __init__(self, role: str = "ceo"):
        self._lock = threading.Lock()
        self._active_role_name = role
        self._access_log: list[AccessRecord] = []

        if role not in _ROLES:
            log.warning("Unknown role '%s' — defaulting to 'viewer'", role)
            self._active_role_name = "viewer"

        log.info("RoleManager initialised — active role: %s", self._active_role_name)

    # ── Active role ───────────────────────────────────────────────────

    @property
    def active_role(self) -> RoleDef:
        return _ROLES[self._active_role_name]

    @property
    def active_role_name(self) -> str:
        return self._active_role_name

    def set_active_role(self, role: str) -> None:
        if role not in _ROLES:
            raise ValueError(f"Unknown role: {role}")
        with self._lock:
            old = self._active_role_name
            self._active_role_name = role
        log.info("Active role changed: %s -> %s", old, role)

    # ── Domain access ─────────────────────────────────────────────────

    def can_access_domain(self, agent: str, domain: DataDomain) -> bool:
        """Check if the given agent can access the specified domain."""
        role = self.active_role

        # Agent must be registered and domain-mapped
        agent_domains = _AGENT_DOMAINS.get(agent)
        if agent_domains is None:
            return False

        # Agent must operate in this domain
        if domain not in agent_domains:
            return False

        # Role must allow reading this domain
        return domain in role.allowed_domains

    def can_write_domain(self, agent: str, domain: DataDomain) -> bool:
        """Check if the given agent can write to the specified domain."""
        if not self.can_access_domain(agent, domain):
            return False
        return domain in self.active_role.writable_domains

    def enforce_domain_access(
        self, agent: str, domain: DataDomain, write: bool = False,
    ) -> AccessRecord:
        """
        Enforce domain access — raises DomainAccessDenied if not allowed.
        Returns the AccessRecord on success.
        """
        allowed = self.can_write_domain(agent, domain) if write else self.can_access_domain(agent, domain)
        mode = "write" if write else "read"

        record = AccessRecord(
            agent=agent, domain=domain.value,
            role=self._active_role_name, allowed=allowed,
            reason=f"{mode} access {'granted' if allowed else 'denied'}",
        )
        self._record(record)

        if not allowed:
            raise DomainAccessDenied(agent, domain, self._active_role_name)

        return record

    # ── Approval limits ───────────────────────────────────────────────

    def can_approve_amount(self, amount: float) -> bool:
        """Check if the current role can approve the given dollar amount."""
        role = self.active_role
        if not role.can_approve:
            return False
        return amount <= role.approval_limit

    def enforce_approval_limit(self, amount: float) -> None:
        """Raise ApprovalLimitExceeded if the amount exceeds the role's limit."""
        role = self.active_role
        if not role.can_approve:
            raise ApprovalLimitExceeded(
                self._active_role_name, amount, 0,
            )
        if amount > role.approval_limit:
            raise ApprovalLimitExceeded(
                self._active_role_name, amount, role.approval_limit,
            )

    def can_execute_final(self) -> bool:
        """Whether the active role can execute FINAL (irreversible) actions."""
        return self.active_role.can_execute_final

    # ── Agent domain mapping ──────────────────────────────────────────

    def get_agent_domains(self, agent: str) -> set[DataDomain]:
        """Return the data domains an agent operates in."""
        return _AGENT_DOMAINS.get(agent, set())

    def register_agent_domain(self, agent: str, domain: DataDomain) -> None:
        """Register an agent to operate in an additional domain."""
        if agent not in _AGENT_DOMAINS:
            _AGENT_DOMAINS[agent] = set()
        _AGENT_DOMAINS[agent].add(domain)
        log.info("Agent '%s' registered for domain '%s'", agent, domain.value)

    # ── Queries ───────────────────────────────────────────────────────

    def get_role(self, name: str) -> Optional[RoleDef]:
        return _ROLES.get(name)

    def get_all_roles(self) -> dict[str, dict]:
        """Return all roles as serializable dicts."""
        result = {}
        for name, rdef in _ROLES.items():
            result[name] = {
                "name": rdef.name.value,
                "display_name": rdef.display_name,
                "allowed_domains": [d.value for d in rdef.allowed_domains],
                "writable_domains": [d.value for d in rdef.writable_domains],
                "approval_limit": rdef.approval_limit,
                "can_approve": rdef.can_approve,
                "can_execute_final": rdef.can_execute_final,
                "description": rdef.description,
            }
        return result

    def get_agent_domain_map(self) -> dict[str, list[str]]:
        """Return all agent→domain mappings."""
        return {
            agent: [d.value for d in domains]
            for agent, domains in _AGENT_DOMAINS.items()
        }

    @property
    def access_log(self) -> list[dict]:
        """Return recent access records."""
        return [r.to_dict() for r in self._access_log[-100:]]

    @property
    def stats(self) -> dict:
        total = len(self._access_log)
        denied = sum(1 for r in self._access_log if not r.allowed)
        return {
            "active_role": self._active_role_name,
            "total_checks": total,
            "denied": denied,
            "allowed": total - denied,
            "registered_agents": len(_AGENT_DOMAINS),
            "registered_roles": len(_ROLES),
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _record(self, record: AccessRecord) -> None:
        with self._lock:
            self._access_log.append(record)
            if len(self._access_log) > 1000:
                self._access_log = self._access_log[-1000:]

        if not record.allowed:
            log.warning(
                "DOMAIN ACCESS DENIED: agent=%s domain=%s role=%s",
                record.agent, record.domain, record.role,
            )


# ── Module singleton ─────────────────────────────────────────────────────────

role_manager = RoleManager()
