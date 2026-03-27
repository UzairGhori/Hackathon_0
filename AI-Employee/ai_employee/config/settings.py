"""
AI Employee — Centralized Configuration

Loads environment variables and provides typed access to all settings.
Every module imports Settings instead of reading os.environ directly.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv


def _project_root() -> Path:
    """Return the top-level project directory (parent of ai_employee/)."""
    return Path(__file__).resolve().parent.parent.parent


@dataclass
class Settings:
    """Immutable snapshot of all configuration values."""

    # --- Paths -----------------------------------------------------------
    project_root: Path = field(default_factory=_project_root)

    @property
    def vault_dir(self) -> Path:
        return self.project_root / "vault"

    @property
    def inbox_dir(self) -> Path:
        return self.vault_dir / "Inbox"

    @property
    def needs_action_dir(self) -> Path:
        return self.vault_dir / "Needs_Action"

    @property
    def done_dir(self) -> Path:
        return self.vault_dir / "Done"

    @property
    def log_dir(self) -> Path:
        return self.project_root / "ai_employee" / "logs"

    @property
    def approval_dir(self) -> Path:
        return self.project_root / "AI_Employee_Vault" / "Needs_Approval"

    @property
    def memory_file(self) -> Path:
        return self.vault_dir / "memory.json"

    @property
    def memory_db_path(self) -> Path:
        return self.vault_dir / "memory.db"

    @property
    def gmail_credentials_path(self) -> Path:
        return self.project_root / self.gmail_credentials_file

    @property
    def gmail_token_path(self) -> Path:
        return self.project_root / self.gmail_token_file

    @property
    def gmail_processed_ids_path(self) -> Path:
        return self.vault_dir / "gmail_processed_ids.json"

    @property
    def gmail_send_log_path(self) -> Path:
        return self.log_dir / "gmail_send_log.json"

    @property
    def approval_queue_path(self) -> Path:
        return self.vault_dir / "approval_queue.json"

    @property
    def briefing_dir(self) -> Path:
        return self.vault_dir / "Reports"

    @property
    def linkedin_processed_ids_path(self) -> Path:
        return self.vault_dir / "linkedin_processed_ids.json"

    @property
    def linkedin_action_log_path(self) -> Path:
        return self.log_dir / "linkedin_action_log.json"

    @property
    def odoo_action_log_path(self) -> Path:
        return self.log_dir / "odoo_action_log.json"

    # --- Credentials (loaded from .env) ----------------------------------
    email_address: str = ""
    email_password: str = ""
    linkedin_email: str = ""
    linkedin_password: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""

    # --- Odoo Community (Self-Hosted Accounting) -------------------------
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_username: str = ""
    odoo_password: str = ""

    # --- Meta Graph API (Facebook + Instagram) ----------------------------
    meta_access_token: str = ""
    meta_page_id: str = ""
    meta_ig_user_id: str = ""

    # --- Twitter (X) API v2 -----------------------------------------------
    twitter_bearer_token: str = ""
    twitter_api_key: str = ""
    twitter_api_secret: str = ""
    twitter_access_token: str = ""
    twitter_access_token_secret: str = ""

    # --- WhatsApp Business Cloud API ----------------------------------------
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""

    # --- Gmail API (OAuth2) -----------------------------------------------
    gmail_credentials_file: str = "credentials.json"
    gmail_token_file: str = "token.json"

    # --- LinkedIn rate limits ---------------------------------------------
    linkedin_max_messages_per_hour: int = 15
    linkedin_max_messages_per_day: int = 80
    linkedin_max_connections_per_day: int = 25

    # --- Operational settings --------------------------------------------
    cycle_interval_minutes: int = 5
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080
    max_retries: int = 10
    retry_delay: float = 0.5
    health_check_interval: int = 60

    # --- Circuit breaker settings ----------------------------------------
    circuit_breaker_threshold: int = 3
    circuit_breaker_timeout: float = 60.0

    @classmethod
    def load(cls) -> "Settings":
        """Load settings from environment variables and .env file."""
        root = _project_root()
        dotenv_path = root / ".env"
        if dotenv_path.exists():
            load_dotenv(dotenv_path)

        return cls(
            project_root=root,
            email_address=os.getenv("EMAIL_ADDRESS", ""),
            email_password=os.getenv("EMAIL_PASSWORD", ""),
            linkedin_email=os.getenv("LINKEDIN_EMAIL", ""),
            linkedin_password=os.getenv("LINKEDIN_PASSWORD", ""),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            gmail_credentials_file=os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json"),
            gmail_token_file=os.getenv("GMAIL_TOKEN_FILE", "token.json"),
            linkedin_max_messages_per_hour=int(os.getenv("LINKEDIN_MAX_MSG_PER_HOUR", "15")),
            linkedin_max_messages_per_day=int(os.getenv("LINKEDIN_MAX_MSG_PER_DAY", "80")),
            linkedin_max_connections_per_day=int(os.getenv("LINKEDIN_MAX_CONN_PER_DAY", "25")),
            cycle_interval_minutes=int(os.getenv("CYCLE_INTERVAL", "5")),
            dashboard_host=os.getenv("DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=int(os.getenv("DASHBOARD_PORT", "8080")),
            circuit_breaker_threshold=int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "3")),
            circuit_breaker_timeout=float(os.getenv("CIRCUIT_BREAKER_TIMEOUT", "60.0")),
            odoo_url=os.getenv("ODOO_URL", ""),
            odoo_db=os.getenv("ODOO_DB", ""),
            odoo_username=os.getenv("ODOO_USERNAME", ""),
            odoo_password=os.getenv("ODOO_PASSWORD", ""),
            meta_access_token=os.getenv("META_ACCESS_TOKEN", ""),
            meta_page_id=os.getenv("META_PAGE_ID", ""),
            meta_ig_user_id=os.getenv("META_IG_USER_ID", ""),
            twitter_bearer_token=os.getenv("TWITTER_BEARER_TOKEN", ""),
            twitter_api_key=os.getenv("TWITTER_API_KEY", ""),
            twitter_api_secret=os.getenv("TWITTER_API_SECRET", ""),
            twitter_access_token=os.getenv("TWITTER_ACCESS_TOKEN", ""),
            twitter_access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET", ""),
            whatsapp_token=os.getenv("WHATSAPP_TOKEN", ""),
            whatsapp_phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
        )

    def ensure_dirs(self) -> None:
        """Create all required directories if they don't exist."""
        for d in [
            self.inbox_dir,
            self.needs_action_dir,
            self.done_dir,
            self.log_dir,
            self.approval_dir,
            self.briefing_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    def validate(self) -> list[str]:
        """Return a list of configuration warnings (empty = all good)."""
        warnings = []
        if not self.email_address:
            warnings.append("EMAIL_ADDRESS not set — email agent disabled")
        if not self.email_password:
            warnings.append("EMAIL_PASSWORD not set — email agent disabled")
        if not self.linkedin_email:
            warnings.append("LINKEDIN_EMAIL not set — LinkedIn agent disabled")
        if not self.anthropic_api_key:
            warnings.append("ANTHROPIC_API_KEY not set — Task Intelligence uses local fallback")
        if not self.gemini_api_key:
            warnings.append("GEMINI_API_KEY not set — AI features use local fallback")
        if not self.gmail_credentials_path.exists():
            warnings.append("Gmail credentials.json not found — Gmail Agent disabled")
        if not self.odoo_url or not self.odoo_password:
            warnings.append("ODOO_URL/ODOO_PASSWORD not set — Odoo agent disabled")
        if not self.meta_access_token:
            warnings.append("META_ACCESS_TOKEN not set — Meta social agent disabled")
        if not self.twitter_bearer_token:
            warnings.append("TWITTER_BEARER_TOKEN not set — Twitter agent disabled")
        if not self.whatsapp_token or not self.whatsapp_phone_number_id:
            warnings.append("WHATSAPP_TOKEN/WHATSAPP_PHONE_NUMBER_ID not set — WhatsApp watcher disabled")
        return warnings
