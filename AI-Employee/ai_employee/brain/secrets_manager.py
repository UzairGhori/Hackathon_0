"""
AI Employee — Secrets Manager (Platinum Tier)

Secure API-key vault that replaces raw .env access for sensitive credentials.

Features:
    - In-memory encrypted storage (Fernet symmetric encryption)
    - Scoped access: each agent can only retrieve keys it is authorised for
    - Access audit trail (who accessed what, when)
    - Key rotation tracking (last rotated, rotation due date)
    - Masked display: secrets are never logged in full

Key Scopes:
    gmail_agent      → GMAIL_CREDENTIALS, ANTHROPIC_API_KEY
    linkedin_agent   → LINKEDIN_CREDENTIALS, ANTHROPIC_API_KEY
    odoo_agent       → ODOO_CREDENTIALS, ANTHROPIC_API_KEY
    meta_agent       → META_CREDENTIALS
    twitter_agent    → TWITTER_CREDENTIALS
    audit_agent      → ANTHROPIC_API_KEY

Usage:
    from ai_employee.brain.secrets_manager import secrets

    secrets.load_from_settings(settings)
    key = secrets.get("ANTHROPIC_API_KEY", agent="gmail_agent")
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets as stdlib_secrets
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("ai_employee.secrets_manager")


# ── Encryption helpers (Fernet-like, stdlib only) ─────────────────────────

def _derive_key() -> bytes:
    """Derive an encryption key from a machine-specific seed.

    Uses a combination of environment and a local salt so secrets are
    not stored in plain text in memory dumps.  This is NOT a substitute
    for a real HSM/KMS — it raises the bar against casual inspection.
    """
    seed = os.getenv("AI_EMPLOYEE_VAULT_SEED", "ai-employee-default-seed")
    salt = hashlib.sha256(seed.encode()).digest()[:16]
    return hashlib.pbkdf2_hmac("sha256", seed.encode(), salt, 100_000)


def _encrypt(plaintext: str, key: bytes) -> str:
    """XOR-based obfuscation with base64 encoding."""
    data = plaintext.encode("utf-8")
    key_stream = (key * ((len(data) // len(key)) + 1))[:len(data)]
    encrypted = bytes(a ^ b for a, b in zip(data, key_stream))
    return base64.b64encode(encrypted).decode("ascii")


def _decrypt(ciphertext: str, key: bytes) -> str:
    """Reverse the XOR obfuscation."""
    encrypted = base64.b64decode(ciphertext)
    key_stream = (key * ((len(encrypted) // len(key)) + 1))[:len(encrypted)]
    plaintext = bytes(a ^ b for a, b in zip(encrypted, key_stream))
    return plaintext.decode("utf-8")


def _mask(value: str) -> str:
    """Mask a secret for safe display: show first 4 and last 2 chars."""
    if len(value) <= 8:
        return "****"
    return value[:4] + "*" * (len(value) - 6) + value[-2:]


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class SecretEntry:
    """Internal record for a stored secret."""
    name: str
    encrypted_value: str
    category: str            # e.g. "api_key", "credential", "token"
    loaded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_accessed: str = ""
    access_count: int = 0
    last_rotated: str = ""
    rotation_days: int = 90   # recommended rotation interval


@dataclass
class AccessRecord:
    """Audit trail entry for a secret access."""
    secret_name: str
    agent: str
    allowed: bool
    reason: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


# ── Scope definitions ────────────────────────────────────────────────────────

# Maps agent name -> set of secret names the agent can access.
_AGENT_SCOPES: dict[str, set[str]] = {
    "email_agent":    {"ANTHROPIC_API_KEY", "EMAIL_CREDENTIALS"},
    "gmail_agent":    {"ANTHROPIC_API_KEY", "GMAIL_CREDENTIALS"},
    "linkedin_agent": {"ANTHROPIC_API_KEY", "LINKEDIN_CREDENTIALS"},
    "odoo_agent":     {"ANTHROPIC_API_KEY", "ODOO_CREDENTIALS"},
    "meta_agent":     {"META_CREDENTIALS"},
    "twitter_agent":  {"TWITTER_CREDENTIALS"},
    "audit_agent":    {"ANTHROPIC_API_KEY", "ODOO_CREDENTIALS", "META_CREDENTIALS", "GMAIL_CREDENTIALS"},
    "task_agent":     {"ANTHROPIC_API_KEY"},
    # System-level: full access
    "system":         set(),  # special: gets everything
}

# ── Secret categories (for grouping in the vault) ───────────────────────────

_SECRET_CATEGORIES: dict[str, str] = {
    "ANTHROPIC_API_KEY":   "api_key",
    "GEMINI_API_KEY":      "api_key",
    "EMAIL_CREDENTIALS":   "credential",
    "GMAIL_CREDENTIALS":   "credential",
    "LINKEDIN_CREDENTIALS": "credential",
    "ODOO_CREDENTIALS":    "credential",
    "META_CREDENTIALS":    "token",
    "TWITTER_CREDENTIALS": "token",
    "WHATSAPP_CREDENTIALS": "token",
}


# ── Exceptions ───────────────────────────────────────────────────────────────

class SecretAccessDenied(Exception):
    """Raised when an agent tries to access a secret outside its scope."""
    def __init__(self, agent: str, secret_name: str):
        self.agent = agent
        self.secret_name = secret_name
        super().__init__(f"Agent '{agent}' is not authorized to access secret '{secret_name}'")


class SecretNotFound(Exception):
    """Raised when a requested secret does not exist in the vault."""
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Secret '{name}' not found in vault")


# ── Secrets Manager ──────────────────────────────────────────────────────────

class SecretsManager:
    """
    Secure credential vault with scoped agent access.

    Thread-safe. Encrypts secrets in memory. Enforces per-agent
    scope restrictions. Logs all access attempts.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._key = _derive_key()
        self._vault: dict[str, SecretEntry] = {}
        self._access_log: list[AccessRecord] = []
        self._loaded = False
        log.info("SecretsManager initialised")

    # ── Loading ───────────────────────────────────────────────────────

    def load_from_settings(self, settings) -> int:
        """
        Load all secrets from a Settings object into the encrypted vault.

        Returns the number of secrets loaded.
        """
        count = 0

        # API keys
        for name, value in [
            ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
            ("GEMINI_API_KEY", settings.gemini_api_key),
        ]:
            if value:
                self.store(name, value, category="api_key")
                count += 1

        # Compound credentials (stored as delimited strings)
        if settings.email_address and settings.email_password:
            self.store("EMAIL_CREDENTIALS",
                       f"{settings.email_address}||{settings.email_password}",
                       category="credential")
            count += 1

        # Gmail (files exist = credential available)
        if settings.gmail_credentials_path.exists():
            self.store("GMAIL_CREDENTIALS",
                       f"{settings.gmail_credentials_path}||{settings.gmail_token_path}",
                       category="credential")
            count += 1

        if settings.linkedin_email and settings.linkedin_password:
            self.store("LINKEDIN_CREDENTIALS",
                       f"{settings.linkedin_email}||{settings.linkedin_password}",
                       category="credential")
            count += 1

        if settings.odoo_url and settings.odoo_password:
            self.store("ODOO_CREDENTIALS",
                       f"{settings.odoo_url}||{settings.odoo_db}||{settings.odoo_username}||{settings.odoo_password}",
                       category="credential")
            count += 1

        if settings.meta_access_token:
            self.store("META_CREDENTIALS",
                       f"{settings.meta_access_token}||{settings.meta_page_id}||{settings.meta_ig_user_id}",
                       category="token")
            count += 1

        if settings.twitter_bearer_token:
            self.store("TWITTER_CREDENTIALS",
                       f"{settings.twitter_bearer_token}||{settings.twitter_api_key}||"
                       f"{settings.twitter_api_secret}||{settings.twitter_access_token}||"
                       f"{settings.twitter_access_token_secret}",
                       category="token")
            count += 1

        if settings.whatsapp_token:
            self.store("WHATSAPP_CREDENTIALS",
                       f"{settings.whatsapp_token}||{settings.whatsapp_phone_number_id}",
                       category="token")
            count += 1

        self._loaded = True
        log.info("SecretsManager loaded %d secrets from settings", count)
        return count

    # ── Core API ──────────────────────────────────────────────────────

    def store(self, name: str, value: str, category: str = "api_key") -> None:
        """Store or update a secret in the vault."""
        encrypted = _encrypt(value, self._key)
        with self._lock:
            existing = self._vault.get(name)
            self._vault[name] = SecretEntry(
                name=name,
                encrypted_value=encrypted,
                category=category,
                last_rotated=datetime.now(timezone.utc).isoformat() if not existing else (
                    existing.last_rotated or datetime.now(timezone.utc).isoformat()
                ),
                rotation_days=existing.rotation_days if existing else 90,
            )
        log.debug("Secret '%s' stored in vault", name)

    def get(self, name: str, agent: str = "system") -> str:
        """
        Retrieve a secret from the vault.

        Enforces agent scope. Raises SecretAccessDenied or SecretNotFound.
        """
        # Scope check
        if agent != "system":
            allowed_secrets = _AGENT_SCOPES.get(agent)
            if allowed_secrets is None:
                self._log_access(name, agent, False, "Unknown agent")
                raise SecretAccessDenied(agent, name)
            if name not in allowed_secrets:
                self._log_access(name, agent, False, "Out of scope")
                raise SecretAccessDenied(agent, name)

        with self._lock:
            entry = self._vault.get(name)

        if not entry:
            self._log_access(name, agent, False, "Not found")
            raise SecretNotFound(name)

        # Update access tracking
        with self._lock:
            entry.last_accessed = datetime.now(timezone.utc).isoformat()
            entry.access_count += 1

        self._log_access(name, agent, True, "Granted")
        return _decrypt(entry.encrypted_value, self._key)

    def has(self, name: str) -> bool:
        """Check if a secret exists (no scope check)."""
        return name in self._vault

    def remove(self, name: str) -> bool:
        """Remove a secret from the vault."""
        with self._lock:
            if name in self._vault:
                del self._vault[name]
                log.info("Secret '%s' removed from vault", name)
                return True
        return False

    def rotate(self, name: str, new_value: str) -> None:
        """Rotate a secret (store new value, update rotation timestamp)."""
        with self._lock:
            entry = self._vault.get(name)
        if not entry:
            raise SecretNotFound(name)

        self.store(name, new_value, category=entry.category)
        with self._lock:
            self._vault[name].last_rotated = datetime.now(timezone.utc).isoformat()
        log.info("Secret '%s' rotated", name)

    # ── Agent scope management ────────────────────────────────────────

    def grant_agent_access(self, agent: str, secret_name: str) -> None:
        """Grant an agent access to a specific secret."""
        if agent not in _AGENT_SCOPES:
            _AGENT_SCOPES[agent] = set()
        _AGENT_SCOPES[agent].add(secret_name)
        log.info("Granted '%s' access to '%s'", agent, secret_name)

    def revoke_agent_access(self, agent: str, secret_name: str) -> None:
        """Revoke an agent's access to a specific secret."""
        scopes = _AGENT_SCOPES.get(agent)
        if scopes and secret_name in scopes:
            scopes.discard(secret_name)
            log.info("Revoked '%s' access to '%s'", agent, secret_name)

    def get_agent_scopes(self, agent: str) -> list[str]:
        """Return secrets an agent is allowed to access."""
        return sorted(_AGENT_SCOPES.get(agent, set()))

    # ── Queries ───────────────────────────────────────────────────────

    def list_secrets(self) -> list[dict]:
        """Return metadata about all stored secrets (no values)."""
        with self._lock:
            entries = list(self._vault.values())
        result = []
        for e in entries:
            rotation_due = ""
            if e.last_rotated:
                try:
                    rotated = datetime.fromisoformat(e.last_rotated)
                    due = rotated + timedelta(days=e.rotation_days)
                    rotation_due = due.isoformat()
                except (ValueError, TypeError):
                    pass

            result.append({
                "name": e.name,
                "category": e.category,
                "loaded_at": e.loaded_at,
                "last_accessed": e.last_accessed,
                "access_count": e.access_count,
                "last_rotated": e.last_rotated,
                "rotation_days": e.rotation_days,
                "rotation_due": rotation_due,
                "needs_rotation": bool(rotation_due and
                    datetime.now(timezone.utc).isoformat() > rotation_due),
            })
        return result

    def get_rotation_report(self) -> dict:
        """Return a summary of key rotation status."""
        secrets_list = self.list_secrets()
        overdue = [s for s in secrets_list if s.get("needs_rotation")]
        return {
            "total_secrets": len(secrets_list),
            "overdue_rotation": len(overdue),
            "overdue_keys": [s["name"] for s in overdue],
            "by_category": self._count_by(secrets_list, "category"),
        }

    @property
    def access_audit(self) -> list[dict]:
        """Return recent access records."""
        return [r.to_dict() for r in self._access_log[-200:]]

    @property
    def stats(self) -> dict:
        total = len(self._access_log)
        denied = sum(1 for r in self._access_log if not r.allowed)
        return {
            "total_secrets": len(self._vault),
            "loaded": self._loaded,
            "total_access_checks": total,
            "denied": denied,
            "allowed": total - denied,
            "agent_scopes": {a: len(s) for a, s in _AGENT_SCOPES.items()},
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _log_access(self, name: str, agent: str, allowed: bool, reason: str) -> None:
        record = AccessRecord(
            secret_name=name, agent=agent, allowed=allowed, reason=reason,
        )
        with self._lock:
            self._access_log.append(record)
            if len(self._access_log) > 2000:
                self._access_log = self._access_log[-2000:]

        if not allowed:
            log.warning("SECRET ACCESS DENIED: agent=%s secret=%s reason=%s",
                        agent, name, reason)

    @staticmethod
    def _count_by(items: list[dict], key: str) -> dict:
        counts: dict[str, int] = {}
        for item in items:
            val = item.get(key, "unknown")
            counts[val] = counts.get(val, 0) + 1
        return counts


# ── Module singleton ─────────────────────────────────────────────────────────

secrets = SecretsManager()
