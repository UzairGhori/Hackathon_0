"""
AI Employee — Draft Mode Controller (Platinum Tier)

The safety gate between Cloud automation and the outside world.

On the Cloud VM, every attempt to perform a FINAL action (send email,
post content, register payment) is intercepted and converted into a
draft that sits in the vault until a human on the Local machine approves it.

Architecture:
    Cloud code calls → DraftModeController.send_email(...)
        → PermissionManager.check("send_email")
            → DENIED (cloud role)
        → Controller writes draft to vault/Drafts/
        → Controller queues approval in AI_Employee_Vault/Needs_Approval/
        → Git sync delivers it to the local machine
        → Human approves on localhost:8080
        → Local code calls → DraftModeController.send_email(...)
            → PermissionManager.check("send_email")
                → ALLOWED (local role)
            → GmailSender.send() executes

Usage:
    from ai_employee.brain.draft_mode_controller import draft_controller

    # Instead of calling gmail_sender.send() directly:
    result = draft_controller.send_email(
        to="customer@example.com",
        subject="Invoice Attached",
        body="Please find your invoice.",
    )
    # On cloud: returns {"action": "drafted", "draft_id": "..."}
    # On local: returns SendResult from gmail_sender.send()

    # Works the same for all final actions:
    draft_controller.post_facebook(message="New product launch!")
    draft_controller.post_tweet(text="Exciting news!")
    draft_controller.register_payment(invoice_id=42, amount=1500.00)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_employee.brain.permission_manager import (
    PermissionManager, PermissionDenied, Role, permissions,
)

log = logging.getLogger("ai_employee.draft_mode")


# ── Draft record ─────────────────────────────────────────────────────────────

@dataclass
class DraftRecord:
    """A saved draft of a blocked final action."""
    draft_id: str
    action: str               # "send_email", "post_facebook", etc.
    category: str             # "email", "social", "financial", "messaging"
    status: str               # "pending", "approved", "rejected", "executed"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Payload — everything needed to replay this action on local
    payload: dict = field(default_factory=dict)

    # Metadata
    source_agent: str = ""
    risk_level: str = "medium"
    preview: str = ""          # Human-readable summary (first 200 chars)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Draft Mode Controller ────────────────────────────────────────────────────

class DraftModeController:
    """
    Central safety gate for all external actions.

    Every method mirrors a real integration client method but adds
    a permission check.  On cloud, FINAL actions are converted to
    vault drafts.  On local, they pass through to the real client.
    """

    def __init__(
        self,
        permission_mgr: PermissionManager | None = None,
        vault_root: Path | None = None,
    ):
        self._perms = permission_mgr or permissions
        self._vault = vault_root or Path(
            os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parent.parent.parent))
        ) / "vault"
        self._drafts_dir = self._vault / "Drafts"
        self._approval_dir = (
            self._vault.parent / "AI_Employee_Vault" / "Needs_Approval"
        )
        self._lock = threading.Lock()
        self._draft_count = 0

        # Lazy-loaded real clients (only instantiated when needed on local)
        self._gmail_sender = None
        self._meta_client = None
        self._twitter_client = None
        self._linkedin_client = None
        self._odoo_client = None
        self._whatsapp_send_fn = None

        self._drafts_dir.mkdir(parents=True, exist_ok=True)
        self._approval_dir.mkdir(parents=True, exist_ok=True)

        log.info(
            "DraftModeController initialised — role=%s, drafts_dir=%s",
            self._perms.role.value, self._drafts_dir,
        )

    # ── Client injection (called by AIEmployee.__init__) ─────────────────

    def set_gmail_sender(self, sender: Any) -> None:
        self._gmail_sender = sender

    def set_meta_client(self, client: Any) -> None:
        self._meta_client = client

    def set_twitter_client(self, client: Any) -> None:
        self._twitter_client = client

    def set_linkedin_client(self, client: Any) -> None:
        self._linkedin_client = client

    def set_odoo_client(self, client: Any) -> None:
        self._odoo_client = client

    def set_whatsapp_send(self, fn: Any) -> None:
        self._whatsapp_send_fn = fn

    # ═════════════════════════════════════════════════════════════════════
    #  EMAIL
    # ═════════════════════════════════════════════════════════════════════

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        thread_id: str = "",
        in_reply_to: str = "",
        source_agent: str = "gmail_agent",
    ) -> dict:
        """
        Send an email — or create a draft if on cloud.

        On cloud: writes draft file + approval request, returns draft record.
        On local: calls GmailSender.send(), returns SendResult as dict.
        """
        if self._perms.can("send_email"):
            # LOCAL — execute for real
            if not self._gmail_sender:
                return {"success": False, "action": "failed",
                        "error": "GmailSender not configured"}
            result = self._gmail_sender.send(
                to=to, subject=subject, body=body, html=html,
                thread_id=thread_id, in_reply_to=in_reply_to,
            )
            return result.to_dict() if hasattr(result, "to_dict") else result

        # CLOUD — create draft instead
        # First, try to create an actual Gmail draft (safe — not sent)
        draft_result = None
        if self._perms.can("draft_email") and self._gmail_sender:
            try:
                draft_result = self._gmail_sender.create_draft(
                    to=to, subject=subject, body=body, html=html,
                    thread_id=thread_id, in_reply_to=in_reply_to,
                )
            except Exception as exc:
                log.warning("Gmail draft creation failed: %s", exc)

        # Write vault draft + approval file
        draft = self._create_draft(
            action="send_email",
            category="email",
            source_agent=source_agent,
            preview=f"To: {to} | Subject: {subject}",
            risk_level="medium",
            payload={
                "to": to,
                "subject": subject,
                "body": body,
                "html": html,
                "thread_id": thread_id,
                "in_reply_to": in_reply_to,
                "gmail_draft_id": (
                    draft_result.message_id
                    if draft_result and draft_result.success else ""
                ),
            },
        )

        return {
            "success": True,
            "action": "drafted",
            "draft_id": draft.draft_id,
            "recipient": to,
            "subject": subject,
            "message": f"Email draft created — awaiting local approval",
            "gmail_draft_id": draft.payload.get("gmail_draft_id", ""),
        }

    # ═════════════════════════════════════════════════════════════════════
    #  FACEBOOK
    # ═════════════════════════════════════════════════════════════════════

    def post_facebook(
        self,
        message: str,
        link: str = "",
        source_agent: str = "meta_agent",
    ) -> dict:
        """Post to Facebook — or draft if on cloud."""
        if self._perms.can("post_facebook"):
            if not self._meta_client:
                return {"success": False, "error": "MetaClient not configured"}
            return self._meta_client.post_facebook(message=message, link=link)

        draft = self._create_draft(
            action="post_facebook",
            category="social",
            source_agent=source_agent,
            preview=f"Facebook: {message[:150]}",
            risk_level="medium",
            payload={"message": message, "link": link, "platform": "facebook"},
        )
        return {
            "success": True, "action": "drafted",
            "draft_id": draft.draft_id, "platform": "facebook",
            "message": "Facebook post drafted — awaiting local approval",
        }

    # ═════════════════════════════════════════════════════════════════════
    #  INSTAGRAM
    # ═════════════════════════════════════════════════════════════════════

    def post_instagram(
        self,
        image_url: str,
        caption: str = "",
        source_agent: str = "meta_agent",
    ) -> dict:
        """Post to Instagram — or draft if on cloud."""
        if self._perms.can("post_instagram"):
            if not self._meta_client:
                return {"success": False, "error": "MetaClient not configured"}
            return self._meta_client.post_instagram(
                image_url=image_url, caption=caption,
            )

        draft = self._create_draft(
            action="post_instagram",
            category="social",
            source_agent=source_agent,
            preview=f"Instagram: {caption[:150]}",
            risk_level="medium",
            payload={
                "image_url": image_url, "caption": caption,
                "platform": "instagram",
            },
        )
        return {
            "success": True, "action": "drafted",
            "draft_id": draft.draft_id, "platform": "instagram",
            "message": "Instagram post drafted — awaiting local approval",
        }

    # ═════════════════════════════════════════════════════════════════════
    #  TWITTER
    # ═════════════════════════════════════════════════════════════════════

    def post_tweet(
        self,
        text: str,
        source_agent: str = "twitter_agent",
    ) -> dict:
        """Post a tweet — or draft if on cloud."""
        if self._perms.can("post_tweet"):
            if not self._twitter_client:
                return {"success": False, "error": "TwitterClient not configured"}
            return self._twitter_client.post_tweet(text=text)

        draft = self._create_draft(
            action="post_tweet",
            category="social",
            source_agent=source_agent,
            preview=f"Tweet: {text[:200]}",
            risk_level="medium",
            payload={"text": text, "platform": "twitter"},
        )
        return {
            "success": True, "action": "drafted",
            "draft_id": draft.draft_id, "platform": "twitter",
            "message": "Tweet drafted — awaiting local approval",
        }

    # ═════════════════════════════════════════════════════════════════════
    #  LINKEDIN
    # ═════════════════════════════════════════════════════════════════════

    def post_linkedin(
        self,
        content: str,
        source_agent: str = "linkedin_agent",
    ) -> dict:
        """Publish to LinkedIn — or draft if on cloud."""
        if self._perms.can("post_linkedin"):
            if not self._linkedin_client:
                return {"success": False, "error": "LinkedInClient not configured"}
            ok = self._linkedin_client.publish(content=content)
            return {"success": ok, "action": "published", "platform": "linkedin"}

        draft = self._create_draft(
            action="post_linkedin",
            category="social",
            source_agent=source_agent,
            preview=f"LinkedIn: {content[:150]}",
            risk_level="medium",
            payload={"content": content, "platform": "linkedin"},
        )
        return {
            "success": True, "action": "drafted",
            "draft_id": draft.draft_id, "platform": "linkedin",
            "message": "LinkedIn post drafted — awaiting local approval",
        }

    def send_linkedin_message(
        self,
        recipient_name: str,
        recipient_url: str,
        message: str,
        thread_id: str = "",
        source_agent: str = "linkedin_agent",
    ) -> dict:
        """Send a LinkedIn message — or draft if on cloud."""
        if self._perms.can("send_linkedin_message"):
            if not self._linkedin_client:
                return {"success": False, "error": "LinkedInClient not configured"}
            result = self._linkedin_client.send_message(
                recipient_name=recipient_name,
                recipient_profile_url=recipient_url,
                message=message, thread_id=thread_id,
            )
            return asdict(result) if hasattr(result, "__dataclass_fields__") else result

        draft = self._create_draft(
            action="send_linkedin_message",
            category="messaging",
            source_agent=source_agent,
            preview=f"LinkedIn msg to {recipient_name}: {message[:100]}",
            risk_level="medium",
            payload={
                "recipient_name": recipient_name,
                "recipient_url": recipient_url,
                "message": message,
                "thread_id": thread_id,
            },
        )
        return {
            "success": True, "action": "drafted",
            "draft_id": draft.draft_id,
            "message": f"LinkedIn message to {recipient_name} drafted — awaiting local approval",
        }

    def send_linkedin_connection(
        self,
        profile_url: str,
        name: str,
        note: str = "",
        source_agent: str = "linkedin_agent",
    ) -> dict:
        """Send LinkedIn connection request — or draft if on cloud."""
        if self._perms.can("send_linkedin_connection"):
            if not self._linkedin_client:
                return {"success": False, "error": "LinkedInClient not configured"}
            result = self._linkedin_client.send_connection_request(
                profile_url=profile_url, name=name, note=note,
            )
            return asdict(result) if hasattr(result, "__dataclass_fields__") else result

        draft = self._create_draft(
            action="send_linkedin_connection",
            category="messaging",
            source_agent=source_agent,
            preview=f"Connection request to {name}",
            risk_level="low",
            payload={
                "profile_url": profile_url, "name": name, "note": note,
            },
        )
        return {
            "success": True, "action": "drafted",
            "draft_id": draft.draft_id,
            "message": f"Connection request to {name} drafted — awaiting local approval",
        }

    # ═════════════════════════════════════════════════════════════════════
    #  WHATSAPP
    # ═════════════════════════════════════════════════════════════════════

    def send_whatsapp(
        self,
        to: str,
        body: str,
        source_agent: str = "whatsapp_watcher",
    ) -> dict:
        """Send a WhatsApp message — or draft if on cloud."""
        if self._perms.can("send_whatsapp"):
            if not self._whatsapp_send_fn:
                return {"success": False, "error": "WhatsApp sender not configured"}
            return self._whatsapp_send_fn(to=to, body=body)

        draft = self._create_draft(
            action="send_whatsapp",
            category="messaging",
            source_agent=source_agent,
            preview=f"WhatsApp to {to}: {body[:100]}",
            risk_level="medium",
            payload={"to": to, "body": body},
        )
        return {
            "success": True, "action": "drafted",
            "draft_id": draft.draft_id,
            "message": f"WhatsApp message to {to} drafted — awaiting local approval",
        }

    # ═════════════════════════════════════════════════════════════════════
    #  ODOO FINANCIAL
    # ═════════════════════════════════════════════════════════════════════

    def register_payment(
        self,
        invoice_id: int,
        amount: float,
        date: str = "",
        journal_id: int | None = None,
        source_agent: str = "odoo_agent",
    ) -> dict:
        """Register a payment — or draft if on cloud."""
        if self._perms.can("register_payment"):
            if not self._odoo_client:
                return {"success": False, "error": "OdooClient not configured"}
            result = self._odoo_client.register_payment(
                invoice_id=invoice_id, amount=amount,
                date=date, journal_id=journal_id,
            )
            return {
                "success": result is not None,
                "action": "payment_registered",
                "payment_id": result,
            }

        draft = self._create_draft(
            action="register_payment",
            category="financial",
            source_agent=source_agent,
            preview=f"Payment: {amount} for invoice #{invoice_id}",
            risk_level="critical",
            payload={
                "invoice_id": invoice_id, "amount": amount,
                "date": date, "journal_id": journal_id,
            },
        )
        return {
            "success": True, "action": "drafted",
            "draft_id": draft.draft_id,
            "message": f"Payment of {amount} drafted — awaiting local approval",
        }

    def confirm_invoice(
        self,
        invoice_id: int,
        source_agent: str = "odoo_agent",
    ) -> dict:
        """Confirm/post an invoice — or draft if on cloud."""
        if self._perms.can("confirm_invoice"):
            if not self._odoo_client:
                return {"success": False, "error": "OdooClient not configured"}
            ok = self._odoo_client.confirm_invoice(invoice_id)
            return {"success": ok, "action": "invoice_confirmed"}

        draft = self._create_draft(
            action="confirm_invoice",
            category="financial",
            source_agent=source_agent,
            preview=f"Confirm invoice #{invoice_id}",
            risk_level="high",
            payload={"invoice_id": invoice_id},
        )
        return {
            "success": True, "action": "drafted",
            "draft_id": draft.draft_id,
            "message": f"Invoice #{invoice_id} confirmation drafted — awaiting local approval",
        }

    def create_invoice(
        self,
        partner_id: int,
        lines: list[dict],
        inv_type: str = "out_invoice",
        date: str = "",
        source_agent: str = "odoo_agent",
    ) -> dict:
        """Create an invoice — or draft if on cloud."""
        if self._perms.can("create_invoice"):
            if not self._odoo_client:
                return {"success": False, "error": "OdooClient not configured"}
            result = self._odoo_client.create_invoice(
                partner_id=partner_id, lines=lines, type=inv_type, date=date,
            )
            return {"success": result is not None, "action": "invoice_created",
                    "invoice_id": result}

        total = sum(
            ln.get("price_unit", 0) * ln.get("quantity", 1)
            for ln in lines
        )
        draft = self._create_draft(
            action="create_invoice",
            category="financial",
            source_agent=source_agent,
            preview=f"Invoice for partner #{partner_id}: {total:.2f}",
            risk_level="high",
            payload={
                "partner_id": partner_id, "lines": lines,
                "type": inv_type, "date": date,
            },
        )
        return {
            "success": True, "action": "drafted",
            "draft_id": draft.draft_id,
            "message": f"Invoice creation drafted — awaiting local approval",
        }

    def write_odoo_record(
        self,
        model: str,
        record_ids: list[int],
        values: dict,
        source_agent: str = "odoo_agent",
    ) -> dict:
        """Write to Odoo record — or draft if on cloud."""
        if self._perms.can("write_odoo_record"):
            if not self._odoo_client:
                return {"success": False, "error": "OdooClient not configured"}
            ok = self._odoo_client.write(model=model, record_ids=record_ids,
                                         values=values)
            return {"success": ok, "action": "record_updated"}

        draft = self._create_draft(
            action="write_odoo_record",
            category="financial",
            source_agent=source_agent,
            preview=f"Update {model} ids={record_ids}",
            risk_level="high",
            payload={
                "model": model, "record_ids": record_ids, "values": values,
            },
        )
        return {
            "success": True, "action": "drafted",
            "draft_id": draft.draft_id,
            "message": f"Odoo record update drafted — awaiting local approval",
        }

    # ═════════════════════════════════════════════════════════════════════
    #  APPROVAL EXECUTION
    # ═════════════════════════════════════════════════════════════════════

    def execute_approved(
        self,
        draft_id: str,
    ) -> dict:
        """
        Execute a previously drafted action after local approval.

        Called on the LOCAL machine after a human approves a draft.
        Reads the draft JSON, replays the action through the real client.
        """
        self._perms.enforce("execute_approved")

        draft_file = self._drafts_dir / f"{draft_id}.json"
        if not draft_file.exists():
            return {"success": False, "error": f"Draft {draft_id} not found"}

        try:
            draft_data = json.loads(draft_file.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return {"success": False, "error": f"Cannot read draft: {exc}"}

        action = draft_data.get("action", "")
        payload = draft_data.get("payload", {})

        # Dispatch to the real execution method
        result = self._replay_action(action, payload)

        # Update draft status
        draft_data["status"] = "executed" if result.get("success") else "failed"
        draft_data["executed_at"] = datetime.now(timezone.utc).isoformat()
        draft_data["execution_result"] = result
        try:
            draft_file.write_text(
                json.dumps(draft_data, indent=2, default=str), encoding="utf-8",
            )
        except OSError:
            pass

        log.info("Draft %s executed: %s → %s", draft_id, action,
                 "success" if result.get("success") else "failed")
        return result

    def _replay_action(self, action: str, payload: dict) -> dict:
        """Replay a drafted action using the real integration client."""
        dispatch = {
            "send_email":               self._replay_send_email,
            "post_facebook":            self._replay_post_facebook,
            "post_instagram":           self._replay_post_instagram,
            "post_tweet":               self._replay_post_tweet,
            "post_linkedin":            self._replay_post_linkedin,
            "send_linkedin_message":    self._replay_send_linkedin_msg,
            "send_linkedin_connection": self._replay_send_linkedin_conn,
            "send_whatsapp":            self._replay_send_whatsapp,
            "register_payment":         self._replay_register_payment,
            "confirm_invoice":          self._replay_confirm_invoice,
            "create_invoice":           self._replay_create_invoice,
            "write_odoo_record":        self._replay_write_odoo,
        }
        fn = dispatch.get(action)
        if not fn:
            return {"success": False, "error": f"Unknown action: {action}"}
        try:
            return fn(payload)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ── Replay helpers (call real clients) ───────────────────────────────

    def _replay_send_email(self, p: dict) -> dict:
        if not self._gmail_sender:
            return {"success": False, "error": "GmailSender not available"}
        r = self._gmail_sender.send(
            to=p["to"], subject=p["subject"], body=p["body"],
            html=p.get("html", False), thread_id=p.get("thread_id", ""),
            in_reply_to=p.get("in_reply_to", ""),
        )
        return r.to_dict() if hasattr(r, "to_dict") else {"success": r}

    def _replay_post_facebook(self, p: dict) -> dict:
        if not self._meta_client:
            return {"success": False, "error": "MetaClient not available"}
        return self._meta_client.post_facebook(
            message=p["message"], link=p.get("link", ""),
        )

    def _replay_post_instagram(self, p: dict) -> dict:
        if not self._meta_client:
            return {"success": False, "error": "MetaClient not available"}
        return self._meta_client.post_instagram(
            image_url=p["image_url"], caption=p.get("caption", ""),
        )

    def _replay_post_tweet(self, p: dict) -> dict:
        if not self._twitter_client:
            return {"success": False, "error": "TwitterClient not available"}
        return self._twitter_client.post_tweet(text=p["text"])

    def _replay_post_linkedin(self, p: dict) -> dict:
        if not self._linkedin_client:
            return {"success": False, "error": "LinkedInClient not available"}
        ok = self._linkedin_client.publish(content=p["content"])
        return {"success": ok, "action": "published"}

    def _replay_send_linkedin_msg(self, p: dict) -> dict:
        if not self._linkedin_client:
            return {"success": False, "error": "LinkedInClient not available"}
        r = self._linkedin_client.send_message(
            recipient_name=p["recipient_name"],
            recipient_profile_url=p["recipient_url"],
            message=p["message"], thread_id=p.get("thread_id", ""),
        )
        return asdict(r) if hasattr(r, "__dataclass_fields__") else {"success": True}

    def _replay_send_linkedin_conn(self, p: dict) -> dict:
        if not self._linkedin_client:
            return {"success": False, "error": "LinkedInClient not available"}
        r = self._linkedin_client.send_connection_request(
            profile_url=p["profile_url"], name=p["name"],
            note=p.get("note", ""),
        )
        return asdict(r) if hasattr(r, "__dataclass_fields__") else {"success": True}

    def _replay_send_whatsapp(self, p: dict) -> dict:
        if not self._whatsapp_send_fn:
            return {"success": False, "error": "WhatsApp sender not available"}
        return self._whatsapp_send_fn(to=p["to"], body=p["body"])

    def _replay_register_payment(self, p: dict) -> dict:
        if not self._odoo_client:
            return {"success": False, "error": "OdooClient not available"}
        r = self._odoo_client.register_payment(
            invoice_id=p["invoice_id"], amount=p["amount"],
            date=p.get("date", ""), journal_id=p.get("journal_id"),
        )
        return {"success": r is not None, "payment_id": r}

    def _replay_confirm_invoice(self, p: dict) -> dict:
        if not self._odoo_client:
            return {"success": False, "error": "OdooClient not available"}
        ok = self._odoo_client.confirm_invoice(p["invoice_id"])
        return {"success": ok}

    def _replay_create_invoice(self, p: dict) -> dict:
        if not self._odoo_client:
            return {"success": False, "error": "OdooClient not available"}
        r = self._odoo_client.create_invoice(
            partner_id=p["partner_id"], lines=p["lines"],
            type=p.get("type", "out_invoice"), date=p.get("date", ""),
        )
        return {"success": r is not None, "invoice_id": r}

    def _replay_write_odoo(self, p: dict) -> dict:
        if not self._odoo_client:
            return {"success": False, "error": "OdooClient not available"}
        ok = self._odoo_client.write(
            model=p["model"], record_ids=p["record_ids"], values=p["values"],
        )
        return {"success": ok}

    # ═════════════════════════════════════════════════════════════════════
    #  DRAFT CREATION (internal)
    # ═════════════════════════════════════════════════════════════════════

    def _create_draft(
        self,
        action: str,
        category: str,
        source_agent: str,
        preview: str,
        risk_level: str,
        payload: dict,
    ) -> DraftRecord:
        """Write draft JSON + approval markdown to vault."""
        with self._lock:
            self._draft_count += 1

        # Generate unique draft ID
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:8]
        draft_id = f"draft_{action}_{ts}_{content_hash}"

        draft = DraftRecord(
            draft_id=draft_id,
            action=action,
            category=category,
            status="pending",
            payload=payload,
            source_agent=source_agent,
            risk_level=risk_level,
            preview=preview[:200],
        )

        # 1. Write draft JSON (machine-readable, for execute_approved)
        json_path = self._drafts_dir / f"{draft_id}.json"
        json_path.write_text(
            json.dumps(draft.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )

        # 2. Write approval markdown (human-readable, for local dashboard)
        self._write_approval_file(draft)

        log.info(
            "Draft created: [%s] %s — %s (risk=%s)",
            draft_id, action, preview[:60], risk_level,
        )
        return draft

    def _write_approval_file(self, draft: DraftRecord) -> None:
        """Write a human-readable approval file for the local dashboard."""
        filepath = self._approval_dir / f"Approval_{draft.draft_id}.md"

        # Format payload for display (redact sensitive values)
        payload_display = []
        for key, value in draft.payload.items():
            if key in ("body", "message", "content", "text", "caption", "note"):
                display_val = str(value)[:300]
            elif key in ("amount", "invoice_id", "partner_id"):
                display_val = str(value)
            else:
                display_val = str(value)[:100]
            payload_display.append(f"| {key} | {display_val} |")

        payload_table = "\n".join(payload_display)

        action_labels = {
            "send_email": "Send Email",
            "post_facebook": "Publish Facebook Post",
            "post_instagram": "Publish Instagram Post",
            "post_tweet": "Post Tweet",
            "post_linkedin": "Publish LinkedIn Post",
            "send_linkedin_message": "Send LinkedIn Message",
            "send_linkedin_connection": "Send Connection Request",
            "send_whatsapp": "Send WhatsApp Message",
            "register_payment": "Register Payment",
            "confirm_invoice": "Confirm Invoice",
            "create_invoice": "Create Invoice",
            "create_journal_entry": "Create Journal Entry",
            "write_odoo_record": "Modify Odoo Record",
        }

        risk_emoji = {
            "low": "LOW", "medium": "MEDIUM",
            "high": "HIGH", "critical": "CRITICAL",
        }

        md = (
            f"# Approval Request — {action_labels.get(draft.action, draft.action)}\n\n"
            f"---\n\n"
            f"| Field           | Value                                |\n"
            f"|-----------------|--------------------------------------|\n"
            f"| Request ID      | `{draft.draft_id}`                   |\n"
            f"| Category        | **{draft.category.upper()}**         |\n"
            f"| Priority        | **{risk_emoji.get(draft.risk_level, 'MEDIUM')}** |\n"
            f"| Risk Level      | {draft.risk_level}                   |\n"
            f"| Source          | Cloud AI                              |\n"
            f"| Agent           | `{draft.source_agent}`               |\n"
            f"| Created         | {draft.created_at[:19]}              |\n\n"
            f"---\n\n"
            f"## Summary\n\n"
            f"{draft.preview}\n\n"
            f"## Action Details\n\n"
            f"| Parameter | Value |\n"
            f"|-----------|-------|\n"
            f"{payload_table}\n\n"
            f"## Safety Note\n\n"
            f"This action was **blocked by the Cloud Draft Mode Safety System**.\n"
            f"The Cloud AI is not permitted to execute `{draft.action}` actions.\n"
            f"Only a human on the Local machine can approve this.\n\n"
            f"---\n\n"
            f"<!-- DECISION BELOW THIS LINE -->\n\n"
            f"**Manager Decision:** PENDING\n\n"
            f"*(Replace PENDING with APPROVED or REJECTED)*\n"
        )

        filepath.write_text(md, encoding="utf-8")

    # ═════════════════════════════════════════════════════════════════════
    #  QUERIES
    # ═════════════════════════════════════════════════════════════════════

    def get_pending_drafts(self) -> list[dict]:
        """Return all pending draft records."""
        drafts = []
        for path in sorted(self._drafts_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text("utf-8"))
                if data.get("status") == "pending":
                    drafts.append(data)
            except (json.JSONDecodeError, OSError):
                continue
        return drafts

    def get_draft(self, draft_id: str) -> dict | None:
        """Return a single draft by ID."""
        path = self._drafts_dir / f"{draft_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def get_stats(self) -> dict:
        """Return draft mode statistics."""
        all_drafts = list(self._drafts_dir.glob("*.json"))
        statuses: dict[str, int] = {}
        categories: dict[str, int] = {}
        for path in all_drafts:
            try:
                data = json.loads(path.read_text("utf-8"))
                status = data.get("status", "unknown")
                cat = data.get("category", "unknown")
                statuses[status] = statuses.get(status, 0) + 1
                categories[cat] = categories.get(cat, 0) + 1
            except (json.JSONDecodeError, OSError):
                continue

        return {
            "role": self._perms.role.value,
            "total_drafts": len(all_drafts),
            "drafts_created_this_session": self._draft_count,
            "by_status": statuses,
            "by_category": categories,
            "permissions": self._perms.get_stats(),
        }


# ── Module-level singleton ───────────────────────────────────────────────────

draft_controller = DraftModeController()
