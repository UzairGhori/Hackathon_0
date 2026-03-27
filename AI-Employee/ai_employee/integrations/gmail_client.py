"""
AI Employee — Gmail Integration Client

Handles sending emails through Gmail SMTP using App Passwords.
Used by the EmailAgent for outbound communications.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger("ai_employee.gmail")


class GmailClient:
    """Send emails through Gmail SMTP."""

    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 587

    def __init__(self, email_address: str, app_password: str):
        self._address = email_address
        self._password = app_password
        self._enabled = bool(email_address and app_password)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, to: str, subject: str, body: str, html: bool = False) -> bool:
        """
        Send an email. Returns True on success.

        Args:
            to:      Recipient email address.
            subject: Email subject line.
            body:    Plain text or HTML body.
            html:    If True, body is treated as HTML.
        """
        if not self._enabled:
            log.warning("Gmail client not configured — skipping send")
            return False

        msg = MIMEMultipart("alternative")
        msg["From"] = self._address
        msg["To"] = to
        msg["Subject"] = subject

        content_type = "html" if html else "plain"
        msg.attach(MIMEText(body, content_type))

        try:
            with smtplib.SMTP(self.SMTP_HOST, self.SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self._address, self._password)
                server.sendmail(self._address, to, msg.as_string())

            log.info("Email sent to %s: %s", to, subject)
            return True
        except smtplib.SMTPAuthenticationError:
            log.error("Gmail authentication failed — check EMAIL_PASSWORD (must be App Password)")
            return False
        except Exception as exc:
            log.error("Failed to send email to %s: %s", to, exc)
            return False

    def send_approval_request(self, to: str, task_title: str, plan_summary: str) -> bool:
        """Send a manager approval request email."""
        subject = f"[AI Employee] Approval Required: {task_title}"
        body = f"""Hi Manager,

The AI Employee has generated a task plan that requires your approval before execution.

Task: {task_title}

Plan Summary:
{plan_summary}

Please reply with APPROVED or REJECTED.

---
AI Employee — Gold Tier
"""
        return self.send(to, subject, body)
