"""
AI Employee — CEO Weekly Executive Brief Generator

Aggregates data from Odoo accounting, Meta social media, Twitter/X,
LinkedIn lead generation, Gmail communications, and internal AI decision
logs into a comprehensive CEO-level Executive Brief.

Pipeline:
  1. COLLECT  — Fetch data from each source (with graceful fallback)
  2. ANALYZE  — Process raw data into 8 report sections
  3. RENDER   — Produce structured markdown brief
  4. SAVE     — Write CEO_Weekly_Brief.md to vault/Reports/

Sections: Revenue, Expenses, Profit, Social Media, Leads Generated,
Emails Summary, Risks, AI Decisions.

All data collection is wrapped in try/except per source, so the report
degrades gracefully when a service is unavailable.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ai_employee.integrations.odoo_client import OdooClient
from ai_employee.integrations.meta_client import MetaClient
from ai_employee.integrations.twitter_client import TwitterClient
from ai_employee.integrations.gmail_reader import GmailReader

log = logging.getLogger("ai_employee.agent.executive_brief")


# ── Action log data class ────────────────────────────────────────────

@dataclass
class BriefActionLog:
    """Log entry for every Executive Brief Generator action."""
    timestamp: str
    action: str
    target: str
    result: str
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Executive Brief Generator ────────────────────────────────────────

class ExecutiveBriefGenerator:
    """
    Comprehensive CEO Executive Brief generator that pulls data from
    Odoo, Meta, Twitter, LinkedIn, Gmail, and internal AI decision logs.

    Follows the same interface as other agents:
        execute(decision, content) -> dict
    """

    def __init__(
        self,
        odoo: OdooClient | None = None,
        meta: MetaClient | None = None,
        twitter: TwitterClient | None = None,
        gmail_reader: GmailReader | None = None,
        gmail_send_log_path: Path | None = None,
        linkedin_log_path: Path | None = None,
        audit_logger: Any = None,
        approval_manager: Any = None,
        memory: Any = None,
        output_dir: Path | None = None,
        log_dir: Path | None = None,
    ):
        self._odoo = odoo
        self._meta = meta
        self._twitter = twitter
        self._gmail_reader = gmail_reader
        self._gmail_send_log_path = gmail_send_log_path
        self._linkedin_log_path = linkedin_log_path
        self._audit_logger = audit_logger
        self._approval_manager = approval_manager
        self._memory = memory
        self._output_dir = output_dir or Path("vault/Reports")
        self._log_dir = log_dir or Path("ai_employee/logs")
        self._action_log: list[BriefActionLog] = []

    @property
    def name(self) -> str:
        return "executive_brief_generator"

    @property
    def enabled(self) -> bool:
        """True if any data source is available (graceful degradation)."""
        odoo_ok = getattr(self._odoo, "enabled", False) if self._odoo else False
        meta_ok = bool(
            self._meta
            and getattr(self._meta, "access_token", "")
            and self._meta.access_token != "your-long-lived-page-access-token"
        )
        twitter_ok = bool(
            self._twitter and getattr(self._twitter, "bearer_token", "")
        )
        gmail_ok = (
            getattr(self._gmail_reader, "enabled", False)
            if self._gmail_reader else False
        )
        linkedin_ok = bool(
            self._linkedin_log_path and self._linkedin_log_path.exists()
        )
        audit_ok = self._audit_logger is not None
        return odoo_ok or meta_ok or twitter_ok or gmail_ok or linkedin_ok or audit_ok

    # ── Standard agent interface ──────────────────────────────────────

    def execute(self, decision, content: str = "") -> dict:
        """Execute a task routed by the scheduler — generates the brief."""
        log.info("ExecutiveBriefGenerator executing task: generating CEO brief")
        try:
            path = self.generate_brief()
            return {
                "status": "success",
                "agent": self.name,
                "action": "ceo_weekly_brief",
                "output_file": str(path),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as exc:
            log.error("ExecutiveBriefGenerator failed: %s", exc)
            return {
                "status": "failed",
                "agent": self.name,
                "action": "ceo_weekly_brief",
                "error": str(exc),
                "timestamp": datetime.now().isoformat(),
            }

    # ══════════════════════════════════════════════════════════════════
    #  CORE: generate_brief
    # ══════════════════════════════════════════════════════════════════

    def generate_brief(self) -> Path:
        """
        Main pipeline — collect, analyze, render, save.

        Returns the path to the generated CEO_Weekly_Brief.md.
        """
        now = datetime.now()
        period_end = now.strftime("%Y-%m-%d")
        period_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        self._log_action("brief_start", "ceo_weekly_brief", "started",
                         f"Period: {period_start} to {period_end}")

        # ── 1. COLLECT ────────────────────────────────────────────────
        raw = self._collect_all()

        # ── 2. ANALYZE ────────────────────────────────────────────────
        sections = {
            "revenue": self._section_revenue(raw),
            "expenses": self._section_expenses(raw),
            "profit": self._section_profit(raw),
            "social_media": self._section_social_media(raw),
            "leads": self._section_leads(raw),
            "emails": self._section_emails(raw),
            "risks": self._section_risks(raw),
            "ai_decisions": self._section_ai_decisions(raw),
        }

        # ── 3. RENDER ────────────────────────────────────────────────
        markdown = self._render_brief(period_start, period_end, sections)

        # ── 4. SAVE ──────────────────────────────────────────────────
        self._output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._output_dir / "CEO_Weekly_Brief.md"
        output_path.write_text(markdown, encoding="utf-8")

        self._log_action("brief_complete", str(output_path), "success",
                         f"Sections: {len(sections)}")
        self._save_action_log()

        log.info("CEO Weekly Brief saved: %s", output_path)
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
            "twitter_summary": None,
            "unread_emails": None,
            "gmail_send_log": None,
            "linkedin_log": None,
            "audit_events": None,
            "approval_stats": None,
        }

        # ── Odoo — weekly accounting summary ──
        if self._odoo:
            try:
                raw["accounting_summary"] = self._odoo.get_weekly_accounting_summary()
                log.info("BRIEF COLLECT | Odoo weekly summary: OK")
            except Exception as exc:
                log.warning("BRIEF COLLECT | Odoo weekly summary failed: %s", exc)

            try:
                raw["financial_report"] = self._odoo.get_financial_report()
                log.info("BRIEF COLLECT | Odoo financial report: OK")
            except Exception as exc:
                log.warning("BRIEF COLLECT | Odoo financial report failed: %s", exc)

            try:
                raw["invoices"] = self._odoo.get_invoices(
                    type="out_invoice", state="posted",
                )
                log.info("BRIEF COLLECT | Odoo invoices: OK (%d)",
                         len(raw["invoices"] or []))
            except Exception as exc:
                log.warning("BRIEF COLLECT | Odoo invoices failed: %s", exc)

        # ── Meta — social media summary + metrics ──
        if self._meta:
            try:
                raw["social_summary"] = self._meta.generate_weekly_summary()
                log.info("BRIEF COLLECT | Meta weekly summary: OK")
            except Exception as exc:
                log.warning("BRIEF COLLECT | Meta weekly summary failed: %s", exc)

            try:
                raw["social_metrics"] = self._meta.get_social_metrics()
                log.info("BRIEF COLLECT | Meta metrics: OK")
            except Exception as exc:
                log.warning("BRIEF COLLECT | Meta metrics failed: %s", exc)

        # ── Twitter — weekly summary ──
        if self._twitter:
            try:
                raw["twitter_summary"] = self._twitter.generate_weekly_summary()
                log.info("BRIEF COLLECT | Twitter weekly summary: OK")
            except Exception as exc:
                log.warning("BRIEF COLLECT | Twitter weekly summary failed: %s", exc)

        # ── Gmail — unread emails ──
        if self._gmail_reader:
            try:
                raw["unread_emails"] = self._gmail_reader.fetch_unread(max_results=50)
                log.info("BRIEF COLLECT | Gmail unread: OK (%d)",
                         len(raw["unread_emails"] or []))
            except Exception as exc:
                log.warning("BRIEF COLLECT | Gmail unread failed: %s", exc)

        # ── Gmail — send log (sent/drafted counts) ──
        if self._gmail_send_log_path:
            try:
                if self._gmail_send_log_path.exists():
                    data = json.loads(
                        self._gmail_send_log_path.read_text(encoding="utf-8"),
                    )
                    raw["gmail_send_log"] = data if isinstance(data, list) else []
                    log.info("BRIEF COLLECT | Gmail send log: OK (%d entries)",
                             len(raw["gmail_send_log"]))
                else:
                    raw["gmail_send_log"] = []
            except Exception as exc:
                log.warning("BRIEF COLLECT | Gmail send log failed: %s", exc)

        # ── LinkedIn — action log (NDJSON, filter last 7 days) ──
        if self._linkedin_log_path:
            try:
                if self._linkedin_log_path.exists():
                    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
                    entries = []
                    for line in self._linkedin_log_path.read_text(
                        encoding="utf-8"
                    ).strip().splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            if isinstance(entry, dict) and entry.get("timestamp", "") >= week_ago:
                                entries.append(entry)
                        except json.JSONDecodeError:
                            continue
                    raw["linkedin_log"] = entries
                    log.info("BRIEF COLLECT | LinkedIn log: OK (%d entries this week)",
                             len(entries))
                else:
                    raw["linkedin_log"] = []
            except Exception as exc:
                log.warning("BRIEF COLLECT | LinkedIn log failed: %s", exc)

        # ── AI Decisions — audit events + approval stats ──
        if self._audit_logger:
            try:
                raw["audit_events"] = self._audit_logger.recent(500)
                log.info("BRIEF COLLECT | Audit events: OK (%d)",
                         len(raw["audit_events"] or []))
            except Exception as exc:
                log.warning("BRIEF COLLECT | Audit events failed: %s", exc)

        if self._approval_manager:
            try:
                raw["approval_stats"] = self._approval_manager.get_stats()
                log.info("BRIEF COLLECT | Approval stats: OK")
            except Exception as exc:
                log.warning("BRIEF COLLECT | Approval stats failed: %s", exc)

        return raw

    # ══════════════════════════════════════════════════════════════════
    #  STEP 2: ANALYSIS — 8 Sections
    # ══════════════════════════════════════════════════════════════════

    def _section_revenue(self, raw: dict) -> dict:
        """Extract revenue metrics from Odoo data."""
        result: dict = {
            "total_invoiced": 0,
            "total_invoiced_amount": "N/A",
            "payments_received": "N/A",
            "payments_received_count": 0,
            "outstanding_receivables": "N/A",
            "receivables_count": 0,
            "income_total": "N/A",
            "top_customers": [],
            "available": False,
        }

        acct = raw.get("accounting_summary")
        fin = raw.get("financial_report")

        if acct:
            result["available"] = True
            invoices = acct.get("invoices", {})
            result["total_invoiced"] = invoices.get("customer_count", 0)

            customer_items = [
                i for i in invoices.get("items", [])
                if i.get("type") == "out_invoice"
            ]
            total_amount = sum(i.get("amount_total", 0) for i in customer_items)
            result["total_invoiced_amount"] = f"${total_amount:,.2f}"

            payments = acct.get("payments", {})
            inbound = payments.get("inbound", {})
            result["payments_received"] = f"${inbound.get('total', 0):,.2f}"
            result["payments_received_count"] = inbound.get("count", 0)

            customer_map: dict[str, float] = {}
            for i in customer_items:
                name = i.get("partner", "Unknown")
                customer_map[name] = customer_map.get(name, 0) + i.get("amount_total", 0)
            top = sorted(customer_map.items(), key=lambda x: x[1], reverse=True)[:5]
            result["top_customers"] = [
                {"name": n, "amount": f"${a:,.2f}"} for n, a in top
            ]

        if fin:
            result["available"] = True
            receivables = fin.get("receivables", {})
            result["outstanding_receivables"] = f"${receivables.get('total', 0):,.2f}"
            result["receivables_count"] = receivables.get("count", 0)

            pl = fin.get("profit_loss", {})
            result["income_total"] = f"${pl.get('income_total', 0):,.2f}"

        return result

    def _section_expenses(self, raw: dict) -> dict:
        """Extract expense metrics from Odoo data."""
        result: dict = {
            "total_expenses": "N/A",
            "expense_count": 0,
            "vendor_bills": 0,
            "vendor_bills_amount": "N/A",
            "expense_total_pl": "N/A",
            "outstanding_payables": "N/A",
            "payables_count": 0,
            "breakdown": [],
            "available": False,
        }

        acct = raw.get("accounting_summary")
        fin = raw.get("financial_report")

        if acct:
            result["available"] = True
            expenses = acct.get("expenses", {})
            result["total_expenses"] = f"${expenses.get('total_amount', 0):,.2f}"
            result["expense_count"] = expenses.get("count", 0)

            invoices = acct.get("invoices", {})
            result["vendor_bills"] = invoices.get("vendor_count", 0)

            vendor_items = [
                i for i in invoices.get("items", [])
                if i.get("type") == "in_invoice"
            ]
            vendor_total = sum(i.get("amount_total", 0) for i in vendor_items)
            result["vendor_bills_amount"] = f"${vendor_total:,.2f}"

        if fin:
            result["available"] = True
            pl = fin.get("profit_loss", {})
            result["expense_total_pl"] = f"${pl.get('expense_total', 0):,.2f}"

            payables = fin.get("payables", {})
            result["outstanding_payables"] = f"${payables.get('total', 0):,.2f}"
            result["payables_count"] = payables.get("count", 0)

        return result

    def _section_profit(self, raw: dict) -> dict:
        """Compute profit from revenue and expenses + P&L data."""
        result: dict = {
            "net_profit": "N/A",
            "margin_pct": "N/A",
            "pl_income": "N/A",
            "pl_expenses": "N/A",
            "pl_summary": "N/A",
            "cash_position": "N/A",
            "available": False,
        }

        fin = raw.get("financial_report")
        if not fin:
            return result

        pl = fin.get("profit_loss", {})
        income = pl.get("income_total", 0)
        expense = pl.get("expense_total", 0)

        if isinstance(income, (int, float)) and isinstance(expense, (int, float)):
            result["available"] = True
            net = income - expense
            result["net_profit"] = f"${net:,.2f}"
            result["pl_income"] = f"${income:,.2f}"
            result["pl_expenses"] = f"${expense:,.2f}"
            result["pl_summary"] = f"Income ${income:,.2f} | Expenses ${expense:,.2f}"

            if income > 0:
                margin = (net / income) * 100
                result["margin_pct"] = f"{margin:.1f}%"
            else:
                result["margin_pct"] = "N/A (no income)"

        # Cash position from balance sheet
        bs = fin.get("balance_sheet", {})
        if bs:
            cash = bs.get("cash", bs.get("total_assets", 0))
            if isinstance(cash, (int, float)):
                result["cash_position"] = f"${cash:,.2f}"

        return result

    def _section_social_media(self, raw: dict) -> dict:
        """Extract social media metrics from Meta + Twitter data."""
        result: dict = {
            "facebook": {"posts": 0, "likes": 0, "comments": 0, "shares": 0, "engagements": 0},
            "instagram": {"posts": 0, "likes": 0, "comments": 0, "engagements": 0},
            "twitter": {
                "tweets": 0, "likes": 0, "retweets": 0, "replies": 0,
                "impressions": 0, "mentions": 0, "engagements": 0,
            },
            "top_post": "N/A",
            "total_engagements": 0,
            "available": False,
        }

        # ── Meta (Facebook + Instagram) ──
        summary = raw.get("social_summary")
        if summary:
            result["available"] = True
            fb = summary.get("facebook", {})
            ig = summary.get("instagram", {})

            fb_likes = fb.get("total_likes", 0)
            fb_comments = fb.get("total_comments", 0)
            fb_shares = fb.get("total_shares", 0)
            result["facebook"] = {
                "posts": fb.get("total_posts", 0),
                "likes": fb_likes,
                "comments": fb_comments,
                "shares": fb_shares,
                "engagements": fb_likes + fb_comments + fb_shares,
            }

            ig_likes = ig.get("total_likes", 0)
            ig_comments = ig.get("total_comments", 0)
            result["instagram"] = {
                "posts": ig.get("total_posts", 0),
                "likes": ig_likes,
                "comments": ig_comments,
                "engagements": ig_likes + ig_comments,
            }

            combined = summary.get("combined", {})
            meta_engagements = combined.get("total_engagements", 0)

            # Top post from Meta
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
        else:
            meta_engagements = 0

        # ── Twitter/X ──
        tw_summary = raw.get("twitter_summary")
        twitter_engagements = 0
        if tw_summary:
            result["available"] = True
            tw = tw_summary.get("twitter", {})
            tw_likes = tw.get("total_likes", 0)
            tw_retweets = tw.get("total_retweets", 0)
            tw_replies = tw.get("total_replies", 0)
            tw_impressions = tw.get("total_impressions", 0)
            twitter_engagements = tw_likes + tw_retweets + tw_replies

            mentions = tw_summary.get("mentions", {})
            result["twitter"] = {
                "tweets": tw.get("total_tweets", 0),
                "likes": tw_likes,
                "retweets": tw_retweets,
                "replies": tw_replies,
                "impressions": tw_impressions,
                "mentions": mentions.get("total_mentions", 0),
                "engagements": twitter_engagements,
            }

            # Check if Twitter top post beats Meta top post
            top_tweet = tw.get("top_tweet")
            if top_tweet:
                tw_top_eng = (
                    top_tweet.get("likes", 0)
                    + top_tweet.get("retweets", 0)
                    + top_tweet.get("replies", 0)
                )
                if result["top_post"] == "N/A" or tw_top_eng > 0:
                    tw_text = top_tweet.get("text", "")[:60]
                    # Only replace if Twitter post is actually better or Meta had none
                    if result["top_post"] == "N/A":
                        result["top_post"] = (
                            f"Twitter: \"{tw_text}\" ({tw_top_eng} engagements)"
                        )

        result["total_engagements"] = meta_engagements + twitter_engagements

        return result

    def _section_leads(self, raw: dict) -> dict:
        """Analyze LinkedIn lead generation from action log."""
        result: dict = {
            "connections_sent": 0,
            "connections_accepted": 0,
            "outreach_count": 0,
            "replies_received": 0,
            "conversion_rate": "N/A",
            "available": False,
        }

        entries = raw.get("linkedin_log")
        if not entries:
            return result

        result["available"] = True
        for entry in entries:
            action = entry.get("action", "")
            if action in ("connect", "connection_request", "send_connection"):
                result["connections_sent"] += 1
            elif action in ("accepted", "connection_accepted"):
                result["connections_accepted"] += 1
            elif action in ("message", "outreach", "send_message", "reply"):
                result["outreach_count"] += 1
            elif action in ("reply_received", "inmail_reply", "message_received"):
                result["replies_received"] += 1

        # Conversion rate: replies / outreach
        total_outreach = result["connections_sent"] + result["outreach_count"]
        total_responses = result["connections_accepted"] + result["replies_received"]
        if total_outreach > 0:
            rate = (total_responses / total_outreach) * 100
            result["conversion_rate"] = f"{rate:.1f}%"

        return result

    def _section_emails(self, raw: dict) -> dict:
        """Analyze email communications volume and key threads."""
        result: dict = {
            "total_unread": 0,
            "financial_flagged": 0,
            "requiring_attention": 0,
            "sent_count": 0,
            "drafted_count": 0,
            "notable_threads": [],
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

    def _section_risks(self, raw: dict) -> list[dict]:
        """Identify business risks from cross-section analysis."""
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
                    "severity": "HIGH",
                    "description": (
                        f"{len(overdue)} overdue invoice(s) totalling "
                        f"${total_overdue:,.2f}"
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
                        "severity": "HIGH",
                        "description": (
                            f"Negative P&L: ${net:,.2f} "
                            f"(income: ${income:,.2f}, expenses: ${expense:,.2f})"
                        ),
                    })

        # Expense spikes
        acct = raw.get("accounting_summary")
        if acct:
            expenses = acct.get("expenses", {})
            exp_total = expenses.get("total_amount", 0)
            if isinstance(exp_total, (int, float)) and exp_total > 0:
                exp_count = expenses.get("count", 0)
                risks.append({
                    "severity": "INFO",
                    "description": (
                        f"{exp_count} expense(s) recorded this week "
                        f"totalling ${exp_total:,.2f}"
                    ),
                })

        # Email backlog
        emails = raw.get("unread_emails")
        if emails and len(emails) > 20:
            risks.append({
                "severity": "MEDIUM",
                "description": (
                    f"{len(emails)} unread emails in inbox — potential backlog"
                ),
            })

        # Social media engagement drop (compare twitter/meta)
        tw_summary = raw.get("twitter_summary")
        if tw_summary:
            tw = tw_summary.get("twitter", {})
            if tw.get("total_tweets", 0) == 0:
                risks.append({
                    "severity": "LOW",
                    "description": "No tweets posted this week — social presence gap",
                })

        social_summary = raw.get("social_summary")
        if social_summary:
            combined = social_summary.get("combined", {})
            if combined.get("total_engagements", 0) == 0:
                risks.append({
                    "severity": "LOW",
                    "description": "Zero Meta engagements this week — review social strategy",
                })

        return risks

    def _section_ai_decisions(self, raw: dict) -> dict:
        """Analyze AI decision-making activity from audit log + approvals."""
        result: dict = {
            "total_decisions": 0,
            "auto_executed": 0,
            "escalated": 0,
            "approved": 0,
            "rejected": 0,
            "pending": 0,
            "top_categories": [],
            "notable": [],
            "available": False,
        }

        # From audit logger events
        events = raw.get("audit_events")
        if events:
            result["available"] = True
            category_counts: dict[str, int] = {}
            for evt in events:
                event_type = getattr(evt, "event", None)
                if event_type is None:
                    event_type = evt.get("event", "") if isinstance(evt, dict) else ""
                event_str = str(event_type)

                if "task_completed" in event_str:
                    result["auto_executed"] += 1
                    result["total_decisions"] += 1
                elif "task_failed" in event_str:
                    result["total_decisions"] += 1
                elif "agent_called" in event_str:
                    result["total_decisions"] += 1
                    # Track category by source/agent
                    source = (
                        getattr(evt, "source", "")
                        if hasattr(evt, "source")
                        else (evt.get("source", "") if isinstance(evt, dict) else "")
                    )
                    if source:
                        category_counts[source] = category_counts.get(source, 0) + 1
                elif "approval" in event_str:
                    result["escalated"] += 1
                    result["total_decisions"] += 1
                elif "error" in event_str or "retry" in event_str:
                    # Notable: record errors
                    summary = (
                        getattr(evt, "summary", "")
                        if hasattr(evt, "summary")
                        else (evt.get("summary", "") if isinstance(evt, dict) else "")
                    )
                    if summary and len(result["notable"]) < 5:
                        result["notable"].append(summary[:100])

            # Top categories by count
            top_cats = sorted(category_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            result["top_categories"] = [
                {"agent": name, "count": count} for name, count in top_cats
            ]

        # From approval manager stats
        stats = raw.get("approval_stats")
        if stats:
            result["available"] = True
            result["approved"] = stats.get("approved", stats.get("total_approved", 0))
            result["rejected"] = stats.get("rejected", stats.get("total_rejected", 0))
            result["pending"] = stats.get("pending", stats.get("total_pending", 0))
            result["escalated"] = max(
                result["escalated"],
                result["approved"] + result["rejected"] + result["pending"],
            )

        return result

    # ══════════════════════════════════════════════════════════════════
    #  STEP 3: RENDER
    # ══════════════════════════════════════════════════════════════════

    def _render_brief(
        self,
        period_start: str,
        period_end: str,
        sections: dict,
    ) -> str:
        """Produce the full markdown CEO Executive Brief."""
        lines: list[str] = []

        lines.append("# CEO Weekly Executive Brief")
        lines.append(f"**Period:** {period_start} — {period_end}")
        lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # ── Revenue ──
        lines.append("## Revenue")
        rev = sections["revenue"]
        if rev.get("available"):
            lines.append(f"- Total Invoiced: {rev.get('total_invoiced_amount', 'N/A')}")
            lines.append(f"- Payments Received: {rev.get('payments_received', 'N/A')} ({rev.get('payments_received_count', 0)} payments)")
            lines.append(f"- Outstanding Receivables: {rev.get('outstanding_receivables', 'N/A')}")
            if rev.get("top_customers"):
                lines.append("- Top Customers:")
                for c in rev["top_customers"]:
                    lines.append(f"  - {c['name']}: {c['amount']}")
        else:
            lines.append("- *No data available*")
        lines.append("")

        # ── Expenses ──
        lines.append("## Expenses")
        exp = sections["expenses"]
        if exp.get("available"):
            lines.append(f"- Total Expenses: {exp.get('total_expenses', 'N/A')} ({exp.get('expense_count', 0)} entries)")
            lines.append(f"- Vendor Bills: {exp.get('vendor_bills', 0)} ({exp.get('vendor_bills_amount', 'N/A')})")
            if exp.get("expense_total_pl") and exp["expense_total_pl"] != "N/A":
                lines.append(f"- P&L Expense Total: {exp['expense_total_pl']}")
            if exp.get("outstanding_payables") and exp["outstanding_payables"] != "N/A":
                lines.append(f"- Outstanding Payables: {exp['outstanding_payables']} ({exp.get('payables_count', 0)} bills)")
        else:
            lines.append("- *No data available*")
        lines.append("")

        # ── Profit ──
        lines.append("## Profit")
        profit = sections["profit"]
        if profit.get("available"):
            lines.append(f"- **Net Profit: {profit.get('net_profit', 'N/A')}**")
            lines.append(f"- Profit Margin: {profit.get('margin_pct', 'N/A')}")
            lines.append(f"- P&L Summary: {profit.get('pl_summary', 'N/A')}")
            if profit.get("cash_position") and profit["cash_position"] != "N/A":
                lines.append(f"- Cash Position: {profit['cash_position']}")
        else:
            lines.append("- *No data available*")
        lines.append("")

        # ── Social Media Performance ──
        lines.append("## Social Media Performance")
        social = sections["social_media"]
        if social.get("available"):
            fb = social["facebook"]
            ig = social["instagram"]
            tw = social["twitter"]

            lines.append("### Facebook")
            lines.append(f"- Posts: {fb.get('posts', 0)} | Likes: {fb.get('likes', 0)} | Comments: {fb.get('comments', 0)} | Shares: {fb.get('shares', 0)}")

            lines.append("### Instagram")
            lines.append(f"- Posts: {ig.get('posts', 0)} | Likes: {ig.get('likes', 0)} | Comments: {ig.get('comments', 0)}")

            lines.append("### Twitter/X")
            lines.append(f"- Tweets: {tw.get('tweets', 0)} | Likes: {tw.get('likes', 0)} | Retweets: {tw.get('retweets', 0)} | Replies: {tw.get('replies', 0)}")
            if tw.get("mentions", 0) > 0:
                lines.append(f"- Mentions: {tw['mentions']}")
            if tw.get("impressions", 0) > 0:
                lines.append(f"- Impressions: {tw['impressions']}")

            lines.append(f"- **Top Post:** {social.get('top_post', 'N/A')}")
            lines.append(f"- **Total Engagement:** {social.get('total_engagements', 0)} across all platforms")
        else:
            lines.append("- *No data available*")
        lines.append("")

        # ── Leads Generated ──
        lines.append("## Leads Generated")
        leads = sections["leads"]
        if leads.get("available"):
            lines.append(f"- Connection Requests Sent: {leads.get('connections_sent', 0)}")
            lines.append(f"- Connections Accepted: {leads.get('connections_accepted', 0)}")
            lines.append(f"- Outreach Messages: {leads.get('outreach_count', 0)}")
            lines.append(f"- Replies Received: {leads.get('replies_received', 0)}")
            lines.append(f"- Conversion Rate: {leads.get('conversion_rate', 'N/A')}")
        else:
            lines.append("- *No data available*")
        lines.append("")

        # ── Emails Summary ──
        lines.append("## Emails Summary")
        emails = sections["emails"]
        if emails.get("available"):
            lines.append(f"- Unread: {emails.get('total_unread', 0)}")
            lines.append(f"- Financial Flagged: {emails.get('financial_flagged', 0)}")
            lines.append(f"- Requiring Attention: {emails.get('requiring_attention', 0)}")
            lines.append(f"- Sent This Week: {emails.get('sent_count', 0)}")
            lines.append(f"- Drafted This Week: {emails.get('drafted_count', 0)}")
            notable = emails.get("notable_threads", [])
            if notable:
                lines.append("- Notable:")
                for t in notable:
                    flags_str = ", ".join(t.get("flags", []))
                    lines.append(f"  - [{flags_str}] {t.get('subject', '')} — from {t.get('sender', 'unknown')}")
        else:
            lines.append("- *No data available*")
        lines.append("")

        # ── Risks ──
        lines.append("## Risks")
        risks = sections["risks"]
        if risks:
            for r in risks:
                severity = r.get("severity", "INFO")
                lines.append(f"- **[{severity}]** {r.get('description', 'N/A')}")
        else:
            lines.append("- No risks identified this period")
        lines.append("")

        # ── AI Decisions ──
        lines.append("## AI Decisions")
        ai = sections["ai_decisions"]
        if ai.get("available"):
            total = ai.get("total_decisions", 0)
            auto = ai.get("auto_executed", 0)
            auto_pct = f"{(auto / total * 100):.0f}%" if total > 0 else "N/A"
            lines.append(f"- Total Decisions: {total}")
            lines.append(f"- Auto-Executed: {auto} ({auto_pct})")
            lines.append(f"- Escalated for Approval: {ai.get('escalated', 0)}")
            lines.append(f"- Approved: {ai.get('approved', 0)} | Rejected: {ai.get('rejected', 0)} | Pending: {ai.get('pending', 0)}")
            if ai.get("top_categories"):
                cats = ", ".join(f"{c['agent']}({c['count']})" for c in ai["top_categories"])
                lines.append(f"- Top Categories: {cats}")
            if ai.get("notable"):
                lines.append("- Notable:")
                for note in ai["notable"]:
                    lines.append(f"  - {note}")
        else:
            lines.append("- *No data available*")
        lines.append("")

        # ── Footer ──
        lines.append("---")
        lines.append("*Generated by AI Employee — Executive Brief Generator*")
        lines.append("")

        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════
    #  ACTION LOGGING
    # ══════════════════════════════════════════════════════════════════

    def _log_action(self, action: str, target: str,
                    result: str, details: str = "") -> None:
        """Record an action to the in-memory log."""
        entry = BriefActionLog(
            timestamp=datetime.now().isoformat(),
            action=action,
            target=target,
            result=result,
            details=details,
        )
        self._action_log.append(entry)
        log.info("BRIEF [%s] %s -> %s", action, target[:40], result)

    def _save_action_log(self) -> None:
        """Persist the action log to disk."""
        if not self._action_log:
            return

        self._log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self._log_dir / f"executive_brief_{timestamp}.json"

        try:
            data = [entry.to_dict() for entry in self._action_log]
            filepath.write_text(
                json.dumps(data, indent=2), encoding="utf-8",
            )
            log.info("Executive Brief action log saved: %s", filepath.name)
        except Exception as exc:
            log.error("Failed to save executive brief action log: %s", exc)

    def get_action_log(self) -> list[dict]:
        """Return the current action log as a list of dicts."""
        return [e.to_dict() for e in self._action_log]
