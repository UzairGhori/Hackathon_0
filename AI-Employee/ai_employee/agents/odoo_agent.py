"""
AI Employee — Odoo Accounting Agent

Autonomous agent that orchestrates accounting workflows via Odoo:

  1. MONITOR  — Check for overdue invoices and pending payments
  2. ANALYZE  — Assess financial state via P&L and balance sheet
  3. SAFETY   — ALL financial operations require human approval
  4. ACT      — Create invoices, register payments, generate reports

Safety Rules:
  - ALL financial operations are flagged as is_financial=True
  - NEVER auto-execute financial transactions without approval
  - ALL amounts above zero are considered high-risk
  - LOG every action to the audit trail
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from ai_employee.brain.decision_engine import DecisionEngine, TaskDecision
from ai_employee.integrations.odoo_client import OdooClient, OdooInvoice

log = logging.getLogger("ai_employee.agent.odoo")


# ── Safety data classes ─────────────────────────────────────────────────

@dataclass
class OdooSafetyCheck:
    """Result of the safety analysis on an Odoo operation."""
    is_safe: bool
    is_financial: bool = True    # Always True for accounting ops
    flags: list[str] = field(default_factory=list)
    risk_level: str = "high"     # Financial ops default to high

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OdooActionLog:
    """Audit log entry for every Odoo Agent action."""
    timestamp: str
    action: str       # "invoice_created", "payment_registered", "report_generated", etc.
    target: str       # description of what was acted upon
    safety: dict
    decision: str     # "auto_execute", "needs_approval", "flagged"
    result: str       # "success", "failed", "pending_approval"
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Odoo Accounting Agent ──────────────────────────────────────────────

class OdooAgent:
    """
    Accounting agent that interacts with Odoo Community.

    Follows the same interface as GmailAgent and LinkedInAgent:
    execute(decision, content) -> dict
    """

    def __init__(
        self,
        odoo: OdooClient,
        output_dir: Path = Path("."),
        decision_engine: DecisionEngine | None = None,
        api_key: str = "",
        log_dir: Path = Path("."),
    ):
        self._odoo = odoo
        self._output_dir = output_dir
        self._engine = decision_engine
        self._api_key = api_key
        self._log_dir = log_dir
        self._action_log: list[OdooActionLog] = []

    @property
    def name(self) -> str:
        return "odoo_agent"

    @property
    def enabled(self) -> bool:
        return self._odoo.enabled

    # ── Pipeline entry point ─────────────────────────────────────────

    def execute(self, decision: TaskDecision, content: str) -> dict:
        """
        Execute a task routed by the scheduler.
        Financial tasks always require approval.
        """
        log.info("OdooAgent executing task: %s", decision.title)

        safety = self._safety_check("execute_task", 0)

        # All financial tasks need approval
        self._log_action(
            action="task_received",
            target=decision.title,
            safety=safety,
            decision="needs_approval",
            result="pending_approval",
            details=f"Category: {decision.category} | Priority: {decision.priority}",
        )

        # Generate a financial summary as the default action
        summary = self.get_financial_summary()

        self._save_action_log()

        return {
            "status": "needs_approval",
            "agent": self.name,
            "task": decision.title,
            "safety": safety.to_dict(),
            "financial_summary": summary,
            "timestamp": datetime.now().isoformat(),
        }

    # ── Accounting pipeline ──────────────────────────────────────────

    def process_accounting(self, max_items: int = 20) -> list[dict]:
        """
        Full accounting pipeline:
          1. Check overdue invoices
          2. Generate financial summary
          3. Save action log

        Returns a list of result dicts for each finding.
        """
        if not self.enabled:
            log.warning("Odoo Agent not enabled — missing credentials")
            return []

        results = []

        # Step 1: Check overdue invoices
        overdue = self.check_overdue_invoices()
        for item in overdue:
            results.append(item)

        # Step 2: Financial summary
        summary = self.get_financial_summary()
        results.append({
            "action": "financial_summary",
            "data": summary,
        })

        # Save audit log
        self._save_action_log()

        log.info(
            "Odoo processed: %d overdue invoices found, financial summary generated",
            len(overdue),
        )
        return results

    def check_overdue_invoices(self) -> list[dict]:
        """
        Check for overdue customer invoices (posted, with balance > 0,
        past due date).
        """
        if not self.enabled:
            return []

        try:
            invoices = self._odoo.get_invoices(
                type="out_invoice", state="posted", limit=50,
            )
        except Exception as exc:
            log.error("Odoo: failed to fetch invoices: %s", exc)
            return []

        today = datetime.now().strftime("%Y-%m-%d")
        overdue: list[dict] = []

        for inv in invoices:
            if inv.amount_residual > 0 and inv.invoice_date_due and inv.invoice_date_due < today:
                entry = {
                    "action": "overdue_invoice",
                    "invoice_id": inv.id,
                    "name": inv.name,
                    "partner": inv.partner_name,
                    "amount_due": inv.amount_residual,
                    "due_date": inv.invoice_date_due,
                    "days_overdue": (
                        datetime.strptime(today, "%Y-%m-%d")
                        - datetime.strptime(inv.invoice_date_due, "%Y-%m-%d")
                    ).days,
                    "currency": inv.currency,
                }
                overdue.append(entry)

                safety = self._safety_check(
                    "overdue_invoice", inv.amount_residual,
                )
                self._log_action(
                    action="overdue_detected",
                    target=f"{inv.name} — {inv.partner_name}",
                    safety=safety,
                    decision="flagged",
                    result="overdue",
                    details=(
                        f"Amount: {inv.amount_residual} {inv.currency} | "
                        f"Due: {inv.invoice_date_due}"
                    ),
                )

        if overdue:
            log.warning("Odoo: %d overdue invoices detected", len(overdue))
        return overdue

    def get_financial_summary(self) -> dict:
        """
        Generate a combined P&L + balance sheet summary.
        Returns a dict suitable for reporting or dashboard display.
        """
        if not self.enabled:
            return {"error": "Odoo agent not configured"}

        summary: dict = {
            "generated_at": datetime.now().isoformat(),
            "profit_loss": {},
            "balance_sheet": {},
        }

        try:
            summary["profit_loss"] = self._odoo.get_profit_loss_summary()
        except Exception as exc:
            log.error("Odoo P&L failed: %s", exc)
            summary["profit_loss"] = {"error": str(exc)}

        try:
            summary["balance_sheet"] = self._odoo.get_balance_sheet_summary()
        except Exception as exc:
            log.error("Odoo balance sheet failed: %s", exc)
            summary["balance_sheet"] = {"error": str(exc)}

        self._log_action(
            action="report_generated",
            target="financial_summary",
            safety=self._safety_check("report", 0),
            decision="auto_execute",
            result="success",
            details="P&L + Balance Sheet summary generated",
        )

        return summary

    # ── Safety engine ────────────────────────────────────────────────

    def _safety_check(self, action: str, amount: float) -> OdooSafetyCheck:
        """
        Run safety analysis on an Odoo operation.
        ALL financial operations are flagged — none are auto-safe.
        """
        flags = ["FINANCIAL: accounting operation"]

        if amount > 0:
            flags.append(f"AMOUNT: {amount:.2f}")

        # Read-only operations (reports, listings) are lower risk
        read_only_actions = ("report", "list", "summary", "check")
        is_read = any(action.startswith(a) for a in read_only_actions)

        if is_read:
            risk_level = "medium"
        elif amount > 10_000:
            risk_level = "critical"
            flags.append("HIGH_VALUE: amount > 10,000")
        elif amount > 0:
            risk_level = "high"
        else:
            risk_level = "high"

        return OdooSafetyCheck(
            is_safe=False,   # Financial ops are never auto-safe
            is_financial=True,
            flags=flags,
            risk_level=risk_level,
        )

    # ── Audit logging ────────────────────────────────────────────────

    def _log_action(self, action: str, target: str,
                    safety: OdooSafetyCheck, decision: str,
                    result: str, details: str = "") -> None:
        """Record an action to the in-memory audit log."""
        entry = OdooActionLog(
            timestamp=datetime.now().isoformat(),
            action=action,
            target=target,
            safety=safety.to_dict(),
            decision=decision,
            result=result,
            details=details,
        )
        self._action_log.append(entry)

    def _save_action_log(self) -> None:
        """Persist the action log to disk."""
        if not self._action_log:
            return

        self._log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self._log_dir / f"odoo_agent_{timestamp}.json"

        try:
            data = [entry.to_dict() for entry in self._action_log]
            filepath.write_text(
                json.dumps(data, indent=2), encoding="utf-8",
            )
            log.info("Odoo Agent action log saved: %s", filepath.name)
        except Exception as exc:
            log.error("Failed to save Odoo action log: %s", exc)

    def get_action_log(self) -> list[dict]:
        """Return the current action log as a list of dicts."""
        return [e.to_dict() for e in self._action_log]
