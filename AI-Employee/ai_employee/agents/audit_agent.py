"""
AI Employee — Weekly Business Audit Agent

Aggregates data from Odoo accounting, Meta social media, and Gmail
communications into a unified CEO-level Weekly Briefing report.

Pipeline:
  1. COLLECT  — Fetch data from each source (with graceful fallback)
  2. ANALYZE  — Process raw data into report sections
  3. RENDER   — Produce structured markdown briefing
  4. SAVE     — Write Weekly_Briefing.md to vault/Reports/

All data collection is wrapped in try/except per source, so the report
degrades gracefully when a service is unavailable.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

from ai_employee.integrations.odoo_client import OdooClient
from ai_employee.integrations.meta_client import MetaClient
from ai_employee.integrations.gmail_reader import GmailReader

log = logging.getLogger("ai_employee.agent.audit")


# ── Audit log data class ──────────────────────────────────────────────

@dataclass
class AuditActionLog:
    """Audit log entry for every Audit Agent action."""
    timestamp: str
    action: str
    target: str
    result: str
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Weekly Business Audit Agent ───────────────────────────────────────

class AuditAgent:
    """
    CEO Briefing agent that pulls data from Odoo, Meta, and Gmail,
    then generates a unified Weekly_Briefing.md.

    Follows the same interface as other agents:
        execute(decision, content) -> dict
    """

    def __init__(
        self,
        odoo: OdooClient,
        meta: MetaClient,
        gmail_reader: GmailReader,
        gmail_send_log_path: Path,
        output_dir: Path,
        log_dir: Path,
    ):
        self._odoo = odoo
        self._meta = meta
        self._gmail_reader = gmail_reader
        self._gmail_send_log_path = gmail_send_log_path
        self._output_dir = output_dir
        self._log_dir = log_dir
        self._action_log: list[AuditActionLog] = []

    @property
    def name(self) -> str:
        return "audit_agent"

    @property
    def enabled(self) -> bool:
        """True if any data source is available (graceful degradation)."""
        odoo_ok = getattr(self._odoo, "enabled", False)
        meta_ok = bool(
            getattr(self._meta, "access_token", "")
            and self._meta.access_token != "your-long-lived-page-access-token"
        )
        gmail_ok = getattr(self._gmail_reader, "enabled", False)
        return odoo_ok or meta_ok or gmail_ok

    # ── Standard agent interface ──────────────────────────────────────

    def execute(self, decision, content: str = "") -> dict:
        """Execute a task routed by the scheduler — generates the briefing."""
        log.info("AuditAgent executing task: generating weekly briefing")
        try:
            path = self.generate_weekly_briefing()
            return {
                "status": "success",
                "agent": self.name,
                "action": "weekly_briefing",
                "output_file": str(path),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as exc:
            log.error("AuditAgent failed: %s", exc)
            return {
                "status": "failed",
                "agent": self.name,
                "action": "weekly_briefing",
                "error": str(exc),
                "timestamp": datetime.now().isoformat(),
            }

    # ══════════════════════════════════════════════════════════════════
    #  CORE: generate_weekly_briefing
    # ══════════════════════════════════════════════════════════════════

    def generate_weekly_briefing(self) -> Path:
        """
        Main pipeline — collect, analyze, render, save.

        Returns the path to the generated Weekly_Briefing.md.
        """
        now = datetime.now()
        period_end = now.strftime("%Y-%m-%d")
        period_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        self._log_action("briefing_start", "weekly_briefing", "started",
                         f"Period: {period_start} to {period_end}")

        # ── 1. COLLECT ────────────────────────────────────────────────
        raw = self._collect_all()

        # ── 2. ANALYZE ────────────────────────────────────────────────
        sections = {
            "revenue": self._analyze_revenue(raw),
            "expenses": self._analyze_expenses(raw),
            "marketing": self._analyze_marketing(raw),
            "communications": self._analyze_communications(raw),
            "risks": self._identify_risks(raw),
            "opportunities": self._identify_opportunities(raw),
        }

        # ── 3. RENDER ────────────────────────────────────────────────
        markdown = self._render_briefing(period_start, period_end, sections)

        # ── 4. SAVE ──────────────────────────────────────────────────
        self._output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._output_dir / "Weekly_Briefing.md"
        output_path.write_text(markdown, encoding="utf-8")

        self._log_action("briefing_complete", str(output_path), "success",
                         f"Sections: {len(sections)}")
        self._save_action_log()

        log.info("Weekly briefing saved: %s", output_path)
        return output_path

    # ══════════════════════════════════════════════════════════════════
    #  STEP 1: DATA COLLECTION
    # ══════════════════════════════════════════════════════════════════

    def _collect_all(self) -> dict:
        """Fetch data from all sources. Each source is independent."""
        raw: dict = {
            "accounting_summary": None,
            "financial_report": None,
            "invoices": None,
            "social_summary": None,
            "social_metrics": None,
            "unread_emails": None,
            "gmail_send_log": None,
        }

        # Odoo — weekly accounting summary
        try:
            raw["accounting_summary"] = self._odoo.get_weekly_accounting_summary()
            log.info("AUDIT COLLECT | Odoo weekly summary: OK")
        except Exception as exc:
            log.warning("AUDIT COLLECT | Odoo weekly summary failed: %s", exc)

        # Odoo — financial report (P&L, balance sheet, receivables, payables)
        try:
            raw["financial_report"] = self._odoo.get_financial_report()
            log.info("AUDIT COLLECT | Odoo financial report: OK")
        except Exception as exc:
            log.warning("AUDIT COLLECT | Odoo financial report failed: %s", exc)

        # Odoo — posted customer invoices (for overdue detection)
        try:
            raw["invoices"] = self._odoo.get_invoices(
                type="out_invoice", state="posted",
            )
            log.info("AUDIT COLLECT | Odoo invoices: OK (%d)",
                     len(raw["invoices"] or []))
        except Exception as exc:
            log.warning("AUDIT COLLECT | Odoo invoices failed: %s", exc)

        # Meta — weekly social summary
        try:
            raw["social_summary"] = self._meta.generate_weekly_summary()
            log.info("AUDIT COLLECT | Meta weekly summary: OK")
        except Exception as exc:
            log.warning("AUDIT COLLECT | Meta weekly summary failed: %s", exc)

        # Meta — current engagement metrics
        try:
            raw["social_metrics"] = self._meta.get_social_metrics()
            log.info("AUDIT COLLECT | Meta metrics: OK")
        except Exception as exc:
            log.warning("AUDIT COLLECT | Meta metrics failed: %s", exc)

        # Gmail — unread emails
        try:
            raw["unread_emails"] = self._gmail_reader.fetch_unread(max_results=50)
            log.info("AUDIT COLLECT | Gmail unread: OK (%d)",
                     len(raw["unread_emails"] or []))
        except Exception as exc:
            log.warning("AUDIT COLLECT | Gmail unread failed: %s", exc)

        # Gmail — send log (sent/drafted counts)
        try:
            if self._gmail_send_log_path.exists():
                data = json.loads(
                    self._gmail_send_log_path.read_text(encoding="utf-8"),
                )
                raw["gmail_send_log"] = data if isinstance(data, list) else []
                log.info("AUDIT COLLECT | Gmail send log: OK (%d entries)",
                         len(raw["gmail_send_log"]))
            else:
                raw["gmail_send_log"] = []
        except Exception as exc:
            log.warning("AUDIT COLLECT | Gmail send log failed: %s", exc)

        return raw

    # ══════════════════════════════════════════════════════════════════
    #  STEP 2: ANALYSIS
    # ══════════════════════════════════════════════════════════════════

    def _analyze_revenue(self, raw: dict) -> dict:
        """Extract revenue metrics from Odoo data."""
        result: dict = {
            "total_invoiced": "N/A",
            "payments_received": "N/A",
            "outstanding_receivables": "N/A",
            "top_customers": [],
            "available": False,
        }

        acct = raw.get("accounting_summary")
        fin = raw.get("financial_report")

        if acct:
            result["available"] = True
            invoices = acct.get("invoices", {})
            result["total_invoiced"] = invoices.get("customer_count", 0)

            # Sum customer invoice amounts
            customer_items = [
                i for i in invoices.get("items", [])
                if i.get("type") == "out_invoice"
            ]
            total_amount = sum(i.get("amount_total", 0) for i in customer_items)
            result["total_invoiced_amount"] = f"{total_amount:,.2f}"

            # Payments received
            payments = acct.get("payments", {})
            inbound = payments.get("inbound", {})
            result["payments_received"] = f"{inbound.get('total', 0):,.2f}"
            result["payments_received_count"] = inbound.get("count", 0)

            # Top customers by invoice amount
            customer_map: dict[str, float] = {}
            for i in customer_items:
                name = i.get("partner", "Unknown")
                customer_map[name] = customer_map.get(name, 0) + i.get("amount_total", 0)
            top = sorted(customer_map.items(), key=lambda x: x[1], reverse=True)[:5]
            result["top_customers"] = [
                {"name": n, "amount": f"{a:,.2f}"} for n, a in top
            ]

        if fin:
            result["available"] = True
            receivables = fin.get("receivables", {})
            result["outstanding_receivables"] = f"{receivables.get('total', 0):,.2f}"
            result["receivables_count"] = receivables.get("count", 0)

            pl = fin.get("profit_loss", {})
            result["income_total"] = f"{pl.get('income_total', 0):,.2f}"

        return result

    def _analyze_expenses(self, raw: dict) -> dict:
        """Extract expense metrics from Odoo data."""
        result: dict = {
            "total_expenses": "N/A",
            "vendor_bills": "N/A",
            "breakdown": [],
            "available": False,
        }

        acct = raw.get("accounting_summary")
        fin = raw.get("financial_report")

        if acct:
            result["available"] = True
            expenses = acct.get("expenses", {})
            result["total_expenses"] = f"{expenses.get('total_amount', 0):,.2f}"
            result["expense_count"] = expenses.get("count", 0)

            invoices = acct.get("invoices", {})
            result["vendor_bills"] = invoices.get("vendor_count", 0)

            vendor_items = [
                i for i in invoices.get("items", [])
                if i.get("type") == "in_invoice"
            ]
            vendor_total = sum(i.get("amount_total", 0) for i in vendor_items)
            result["vendor_bills_amount"] = f"{vendor_total:,.2f}"

        if fin:
            result["available"] = True
            pl = fin.get("profit_loss", {})
            result["expense_total_pl"] = f"{pl.get('expense_total', 0):,.2f}"

            payables = fin.get("payables", {})
            result["outstanding_payables"] = f"{payables.get('total', 0):,.2f}"
            result["payables_count"] = payables.get("count", 0)

        return result

    def _analyze_marketing(self, raw: dict) -> dict:
        """Extract social media metrics from Meta data."""
        result: dict = {
            "facebook": {"posts": 0, "engagements": 0},
            "instagram": {"posts": 0, "engagements": 0},
            "top_post": "N/A",
            "total_engagements": 0,
            "available": False,
        }

        summary = raw.get("social_summary")
        if summary:
            result["available"] = True
            fb = summary.get("facebook", {})
            ig = summary.get("instagram", {})

            result["facebook"] = {
                "posts": fb.get("total_posts", 0),
                "likes": fb.get("total_likes", 0),
                "comments": fb.get("total_comments", 0),
                "shares": fb.get("total_shares", 0),
                "engagements": (
                    fb.get("total_likes", 0)
                    + fb.get("total_comments", 0)
                    + fb.get("total_shares", 0)
                ),
            }
            result["instagram"] = {
                "posts": ig.get("total_posts", 0),
                "likes": ig.get("total_likes", 0),
                "comments": ig.get("total_comments", 0),
                "engagements": (
                    ig.get("total_likes", 0)
                    + ig.get("total_comments", 0)
                ),
            }

            combined = summary.get("combined", {})
            result["total_engagements"] = combined.get("total_engagements", 0)

            # Top post
            fb_top = fb.get("top_post")
            ig_top = ig.get("top_post")
            if fb_top:
                result["top_post"] = (
                    f"Facebook: \"{fb_top.get('message', '')[:60]}\" "
                    f"({fb_top.get('likes', 0)} likes)"
                )
            elif ig_top:
                result["top_post"] = (
                    f"Instagram: \"{ig_top.get('caption', '')[:60]}\" "
                    f"({ig_top.get('like_count', 0)} likes)"
                )

            result["period"] = summary.get("period", {})

        return result

    def _analyze_communications(self, raw: dict) -> dict:
        """Analyze email communications volume and key threads."""
        result: dict = {
            "total_unread": 0,
            "financial_flagged": 0,
            "requiring_attention": 0,
            "notable_threads": [],
            "sent_count": 0,
            "drafted_count": 0,
            "available": False,
        }

        emails = raw.get("unread_emails")
        if emails:
            result["available"] = True
            result["total_unread"] = len(emails)

            financial_keywords = [
                "invoice", "payment", "billing", "receipt", "refund",
                "overdue", "balance", "statement", "tax", "payroll",
            ]
            sensitive_keywords = [
                "urgent", "important", "confidential", "action required",
                "deadline", "critical", "asap",
            ]

            for email in emails:
                subject = getattr(email, "subject", "") or ""
                subject_lower = subject.lower()

                is_financial = any(k in subject_lower for k in financial_keywords)
                is_sensitive = any(k in subject_lower for k in sensitive_keywords)

                if is_financial:
                    result["financial_flagged"] += 1
                if is_sensitive:
                    result["requiring_attention"] += 1

                if is_financial or is_sensitive:
                    sender = getattr(email, "sender", "") or ""
                    result["notable_threads"].append({
                        "subject": subject[:80],
                        "sender": sender[:50],
                        "flags": (
                            (["financial"] if is_financial else [])
                            + (["sensitive"] if is_sensitive else [])
                        ),
                    })

            result["notable_threads"] = result["notable_threads"][:10]

        # Sent/drafted counts from send log
        send_log = raw.get("gmail_send_log")
        if send_log:
            result["available"] = True
            week_ago = (datetime.now() - timedelta(days=7)).isoformat()
            recent = [
                e for e in send_log
                if isinstance(e, dict) and e.get("timestamp", "") >= week_ago
            ]
            result["sent_count"] = sum(
                1 for e in recent if e.get("status") == "sent"
            )
            result["drafted_count"] = sum(
                1 for e in recent if e.get("status") == "drafted"
            )

        return result

    def _identify_risks(self, raw: dict) -> list[dict]:
        """Identify business risks from collected data."""
        risks: list[dict] = []

        # Overdue invoices
        invoices = raw.get("invoices")
        if invoices:
            today = datetime.now().strftime("%Y-%m-%d")
            overdue = [
                inv for inv in invoices
                if (getattr(inv, "amount_residual", 0) > 0
                    and getattr(inv, "invoice_date_due", "")
                    and inv.invoice_date_due < today)
            ]
            if overdue:
                total_overdue = sum(inv.amount_residual for inv in overdue)
                risks.append({
                    "category": "overdue_invoices",
                    "severity": "high",
                    "count": len(overdue),
                    "total_amount": f"{total_overdue:,.2f}",
                    "description": (
                        f"{len(overdue)} overdue invoice(s) totalling "
                        f"{total_overdue:,.2f}"
                    ),
                })

        # Negative cash flow
        fin = raw.get("financial_report")
        if fin:
            pl = fin.get("profit_loss", {})
            income = pl.get("income_total", 0)
            expense = pl.get("expense_total", 0)
            if isinstance(income, (int, float)) and isinstance(expense, (int, float)):
                net = income - expense
                if net < 0:
                    risks.append({
                        "category": "negative_cash_flow",
                        "severity": "high",
                        "description": (
                            f"Net P&L is negative: {net:,.2f} "
                            f"(income: {income:,.2f}, expenses: {expense:,.2f})"
                        ),
                    })

        # Expense spikes (weekly expenses > 0 is flagged as informational)
        acct = raw.get("accounting_summary")
        if acct:
            expenses = acct.get("expenses", {})
            exp_total = expenses.get("total_amount", 0)
            if isinstance(exp_total, (int, float)) and exp_total > 0:
                exp_count = expenses.get("count", 0)
                risks.append({
                    "category": "expense_activity",
                    "severity": "info",
                    "description": (
                        f"{exp_count} expense(s) recorded this week "
                        f"totalling {exp_total:,.2f}"
                    ),
                })

        # Unanswered emails
        emails = raw.get("unread_emails")
        if emails and len(emails) > 20:
            risks.append({
                "category": "email_backlog",
                "severity": "medium",
                "count": len(emails),
                "description": (
                    f"{len(emails)} unread emails in inbox — potential backlog"
                ),
            })

        return risks

    def _identify_opportunities(self, raw: dict) -> list[dict]:
        """Identify business opportunities from collected data."""
        opportunities: list[dict] = []

        # Top-performing social content
        summary = raw.get("social_summary")
        if summary:
            fb = summary.get("facebook", {})
            ig = summary.get("instagram", {})

            fb_top = fb.get("top_post")
            if fb_top:
                likes = fb_top.get("likes", 0)
                if likes > 0:
                    opportunities.append({
                        "category": "top_social_content",
                        "platform": "facebook",
                        "description": (
                            f"Top FB post: \"{fb_top.get('message', '')[:60]}\" "
                            f"with {likes} likes — consider repurposing"
                        ),
                    })

            ig_top = ig.get("top_post")
            if ig_top:
                likes = ig_top.get("like_count", 0)
                if likes > 0:
                    opportunities.append({
                        "category": "top_social_content",
                        "platform": "instagram",
                        "description": (
                            f"Top IG post: \"{ig_top.get('caption', '')[:60]}\" "
                            f"with {likes} likes — consider repurposing"
                        ),
                    })

        # New customer invoices (revenue growth)
        acct = raw.get("accounting_summary")
        if acct:
            invoices = acct.get("invoices", {})
            customer_items = [
                i for i in invoices.get("items", [])
                if i.get("type") == "out_invoice"
            ]
            if customer_items:
                total = sum(i.get("amount_total", 0) for i in customer_items)
                opportunities.append({
                    "category": "revenue_activity",
                    "description": (
                        f"{len(customer_items)} new customer invoice(s) this week "
                        f"totalling {total:,.2f}"
                    ),
                })

        # High-value contacts from emails
        emails = raw.get("unread_emails")
        if emails:
            financial_keywords = [
                "partnership", "proposal", "opportunity", "contract",
                "deal", "investment",
            ]
            high_value = []
            for email in emails:
                subject = (getattr(email, "subject", "") or "").lower()
                if any(k in subject for k in financial_keywords):
                    high_value.append({
                        "subject": getattr(email, "subject", "")[:60],
                        "sender": getattr(email, "sender", "")[:40],
                    })
            if high_value:
                opportunities.append({
                    "category": "high_value_contacts",
                    "count": len(high_value),
                    "contacts": high_value[:5],
                    "description": (
                        f"{len(high_value)} email(s) with potential business opportunities"
                    ),
                })

        return opportunities

    # ══════════════════════════════════════════════════════════════════
    #  STEP 3: RENDER
    # ══════════════════════════════════════════════════════════════════

    def _render_briefing(
        self,
        period_start: str,
        period_end: str,
        sections: dict,
    ) -> str:
        """Produce the full markdown briefing."""
        lines: list[str] = []

        lines.append("# CEO Weekly Briefing")
        lines.append(f"**Period:** {period_start} — {period_end}")
        lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # ── Revenue ──
        lines.append("## Revenue")
        rev = sections["revenue"]
        if rev.get("available"):
            lines.append(f"- Total invoiced: {rev.get('total_invoiced_amount', rev['total_invoiced'])}")
            lines.append(f"- Payments received: {rev.get('payments_received')} ({rev.get('payments_received_count', 0)} payments)")
            lines.append(f"- Outstanding receivables: {rev.get('outstanding_receivables')}")
            if rev.get("income_total"):
                lines.append(f"- P&L income total: {rev['income_total']}")
            if rev.get("top_customers"):
                lines.append("- Top customers:")
                for c in rev["top_customers"]:
                    lines.append(f"  - {c['name']}: {c['amount']}")
        else:
            lines.append("- *Odoo data unavailable*")
        lines.append("")

        # ── Expenses ──
        lines.append("## Expenses")
        exp = sections["expenses"]
        if exp.get("available"):
            lines.append(f"- Total expenses: {exp.get('total_expenses')} ({exp.get('expense_count', 0)} entries)")
            lines.append(f"- Vendor bills: {exp.get('vendor_bills')} (amount: {exp.get('vendor_bills_amount', 'N/A')})")
            if exp.get("expense_total_pl"):
                lines.append(f"- P&L expense total: {exp['expense_total_pl']}")
            if exp.get("outstanding_payables"):
                lines.append(f"- Outstanding payables: {exp['outstanding_payables']} ({exp.get('payables_count', 0)} bills)")
        else:
            lines.append("- *Odoo data unavailable*")
        lines.append("")

        # ── Marketing Performance ──
        lines.append("## Marketing Performance")
        mkt = sections["marketing"]
        if mkt.get("available"):
            fb = mkt["facebook"]
            ig = mkt["instagram"]
            lines.append(f"- Facebook: {fb.get('posts', 0)} posts, {fb.get('engagements', 0)} engagements (likes: {fb.get('likes', 0)}, comments: {fb.get('comments', 0)}, shares: {fb.get('shares', 0)})")
            lines.append(f"- Instagram: {ig.get('posts', 0)} posts, {ig.get('engagements', 0)} engagements (likes: {ig.get('likes', 0)}, comments: {ig.get('comments', 0)})")
            lines.append(f"- Total engagements: {mkt.get('total_engagements', 0)}")
            lines.append(f"- Top performing post: {mkt.get('top_post', 'N/A')}")
            period = mkt.get("period", {})
            if period:
                lines.append(f"- Period: {period.get('from', 'N/A')} to {period.get('to', 'N/A')}")
        else:
            lines.append("- *Meta social data unavailable*")
        lines.append("")

        # ── Key Emails ──
        lines.append("## Key Emails")
        comms = sections["communications"]
        if comms.get("available"):
            lines.append(f"- Total unread: {comms.get('total_unread', 0)}")
            lines.append(f"- Financial flagged: {comms.get('financial_flagged', 0)}")
            lines.append(f"- Requiring attention: {comms.get('requiring_attention', 0)}")
            lines.append(f"- Sent this week: {comms.get('sent_count', 0)}")
            lines.append(f"- Drafted this week: {comms.get('drafted_count', 0)}")
            notable = comms.get("notable_threads", [])
            if notable:
                lines.append("- Notable threads:")
                for t in notable:
                    flags_str = ", ".join(t.get("flags", []))
                    lines.append(f"  - [{flags_str}] {t.get('subject', '')} — from {t.get('sender', 'unknown')}")
        else:
            lines.append("- *Gmail data unavailable*")
        lines.append("")

        # ── Risks ──
        lines.append("## Risks")
        risks = sections["risks"]
        if risks:
            for r in risks:
                severity = r.get("severity", "info").upper()
                lines.append(f"- **[{severity}]** {r.get('description', 'N/A')}")
        else:
            lines.append("- No risks identified this period")
        lines.append("")

        # ── Opportunities ──
        lines.append("## Opportunities")
        opps = sections["opportunities"]
        if opps:
            for o in opps:
                lines.append(f"- {o.get('description', 'N/A')}")
        else:
            lines.append("- No new opportunities identified this period")
        lines.append("")

        # ── Footer ──
        lines.append("---")
        lines.append("*Generated by AI Employee — Gold Tier*")
        lines.append("")

        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════
    #  AUDIT LOGGING
    # ══════════════════════════════════════════════════════════════════

    def _log_action(self, action: str, target: str,
                    result: str, details: str = "") -> None:
        """Record an action to the in-memory audit log."""
        entry = AuditActionLog(
            timestamp=datetime.now().isoformat(),
            action=action,
            target=target,
            result=result,
            details=details,
        )
        self._action_log.append(entry)
        log.info("AUDIT [%s] %s -> %s", action, target[:40], result)

    def _save_action_log(self) -> None:
        """Persist the action log to disk."""
        if not self._action_log:
            return

        self._log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self._log_dir / f"audit_agent_{timestamp}.json"

        try:
            data = [entry.to_dict() for entry in self._action_log]
            filepath.write_text(
                json.dumps(data, indent=2), encoding="utf-8",
            )
            log.info("Audit Agent action log saved: %s", filepath.name)
        except Exception as exc:
            log.error("Failed to save audit action log: %s", exc)

    def get_action_log(self) -> list[dict]:
        """Return the current action log as a list of dicts."""
        return [e.to_dict() for e in self._action_log]
