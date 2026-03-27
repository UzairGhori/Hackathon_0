"""
AI Employee — Odoo Community JSON-RPC Client

Connects to a self-hosted Odoo 19+ instance via JSON-RPC and provides
typed methods for accounting operations:

  - Authentication
  - CRUD (search_read, create, write, unlink)
  - Invoices (customer/vendor, draft/posted, confirm, payment)
  - Customers / Partners
  - Financial Reports (P&L, Balance Sheet, Account Balances)

All methods return structured results and log errors gracefully.
Disable by leaving ODOO_URL or ODOO_PASSWORD empty in .env.

Protocol Reference:
  https://www.odoo.com/documentation/19.0/developer/reference/external_api.html
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Any

import requests

log = logging.getLogger("ai_employee.integration.odoo")

# ── Result data classes ─────────────────────────────────────────────────


@dataclass
class OdooActionResult:
    """Structured result returned by every Odoo operation."""
    success: bool
    action: str
    data: Any = None
    record_id: int | None = None
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OdooInvoice:
    """Typed representation of an Odoo account.move (invoice)."""
    id: int
    name: str                    # e.g. "INV/2025/0001"
    partner_id: int
    partner_name: str
    move_type: str               # "out_invoice", "in_invoice", etc.
    state: str                   # "draft", "posted", "cancel"
    date: str
    invoice_date_due: str
    amount_total: float
    amount_residual: float       # amount still owed
    currency: str
    lines: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_odoo(cls, rec: dict) -> "OdooInvoice":
        """Build from an Odoo search_read record."""
        return cls(
            id=rec.get("id", 0),
            name=rec.get("name", ""),
            partner_id=rec.get("partner_id", [0, ""])[0] if isinstance(rec.get("partner_id"), (list, tuple)) else rec.get("partner_id", 0),
            partner_name=rec.get("partner_id", [0, ""])[1] if isinstance(rec.get("partner_id"), (list, tuple)) else "",
            move_type=rec.get("move_type", ""),
            state=rec.get("state", ""),
            date=str(rec.get("date", "")),
            invoice_date_due=str(rec.get("invoice_date_due", "")),
            amount_total=float(rec.get("amount_total", 0)),
            amount_residual=float(rec.get("amount_residual", 0)),
            currency=rec.get("currency_id", [0, ""])[1] if isinstance(rec.get("currency_id"), (list, tuple)) else str(rec.get("currency_id", "")),
        )


# ── Odoo JSON-RPC Client ───────────────────────────────────────────────


class OdooClient:
    """
    Connects to Odoo Community via JSON-RPC and provides typed methods
    for accounting operations.

    Usage::

        client = OdooClient("http://localhost:8069", "mydb", "admin", "admin")
        if client.authenticate():
            invoices = client.get_invoices(type="out_invoice", state="posted")
    """

    def __init__(self, url: str, db: str, username: str, password: str):
        self._url = url.rstrip("/")
        self._db = db
        self._username = username
        self._password = password
        self._uid: int | None = None
        self._rpc_id = 0

    @property
    def enabled(self) -> bool:
        """True if all required connection parameters are set."""
        return bool(self._url and self._db and self._username and self._password)

    # ══════════════════════════════════════════════════════════════════
    #  LOW-LEVEL JSON-RPC
    # ══════════════════════════════════════════════════════════════════

    def _call_rpc(self, service: str, method: str, args: list) -> Any:
        """Send a single JSON-RPC 2.0 request to Odoo."""
        self._rpc_id += 1
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": service,
                "method": method,
                "args": args,
            },
            "id": self._rpc_id,
        }
        try:
            resp = requests.post(
                f"{self._url}/jsonrpc",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()

            if "error" in result:
                err = result["error"]
                msg = err.get("data", {}).get("message", "") or err.get("message", str(err))
                log.error("Odoo RPC error [%s.%s]: %s", service, method, msg)
                raise RuntimeError(f"Odoo RPC error: {msg}")

            return result.get("result")

        except requests.RequestException as exc:
            log.error("Odoo connection error: %s", exc)
            raise

    def _execute_kw(self, model: str, method: str,
                    args: list | None = None,
                    kwargs: dict | None = None) -> Any:
        """Call object.execute_kw (requires authentication)."""
        self._ensure_authenticated()
        return self._call_rpc("object", "execute_kw", [
            self._db,
            self._uid,
            self._password,
            model,
            method,
            args or [],
            kwargs or {},
        ])

    def _ensure_authenticated(self) -> None:
        """Authenticate if we haven't yet."""
        if self._uid is None:
            if not self.authenticate():
                raise RuntimeError("Odoo authentication failed")

    # ══════════════════════════════════════════════════════════════════
    #  AUTHENTICATION
    # ══════════════════════════════════════════════════════════════════

    def authenticate(self) -> bool:
        """
        Authenticate with Odoo via common.authenticate.
        Returns True on success, False on failure.
        """
        if not self.enabled:
            log.warning("Odoo client not configured — skipping auth")
            return False

        try:
            uid = self._call_rpc("common", "authenticate", [
                self._db, self._username, self._password, {},
            ])
            if uid and isinstance(uid, int):
                self._uid = uid
                log.info("Odoo authenticated as uid=%d (db=%s)", uid, self._db)
                return True
            log.error("Odoo authentication failed — invalid credentials")
            return False
        except Exception as exc:
            log.error("Odoo authentication error: %s", exc)
            return False

    # ══════════════════════════════════════════════════════════════════
    #  GENERIC CRUD
    # ══════════════════════════════════════════════════════════════════

    def search_read(self, model: str, domain: list | None = None,
                    fields: list[str] | None = None,
                    limit: int = 80, order: str = "") -> list[dict]:
        """Search and read records from any Odoo model."""
        kwargs: dict[str, Any] = {"limit": limit}
        if fields:
            kwargs["fields"] = fields
        if order:
            kwargs["order"] = order
        return self._execute_kw(model, "search_read", [domain or []], kwargs)

    def create(self, model: str, values: dict) -> int | None:
        """Create a single record. Returns the new record ID."""
        try:
            result = self._execute_kw(model, "create", [values])
            record_id = result if isinstance(result, int) else result[0] if isinstance(result, list) else None
            log.info("Odoo created %s record id=%s", model, record_id)
            return record_id
        except Exception as exc:
            log.error("Odoo create %s failed: %s", model, exc)
            return None

    def write(self, model: str, record_ids: list[int],
              values: dict) -> bool:
        """Update existing records. Returns True on success."""
        try:
            self._execute_kw(model, "write", [record_ids, values])
            log.info("Odoo updated %s ids=%s", model, record_ids)
            return True
        except Exception as exc:
            log.error("Odoo write %s failed: %s", model, exc)
            return False

    def unlink(self, model: str, record_ids: list[int]) -> bool:
        """Delete records. Returns True on success."""
        try:
            self._execute_kw(model, "unlink", [record_ids])
            log.info("Odoo deleted %s ids=%s", model, record_ids)
            return True
        except Exception as exc:
            log.error("Odoo unlink %s failed: %s", model, exc)
            return False

    # ══════════════════════════════════════════════════════════════════
    #  INVOICES
    # ══════════════════════════════════════════════════════════════════

    _INVOICE_FIELDS = [
        "id", "name", "partner_id", "move_type", "state",
        "date", "invoice_date_due", "amount_total",
        "amount_residual", "currency_id",
    ]

    def get_invoices(self, type: str = "out_invoice",
                     state: str = "", limit: int = 50) -> list[OdooInvoice]:
        """Fetch invoices filtered by type and state."""
        domain: list = [("move_type", "=", type)]
        if state:
            domain.append(("state", "=", state))
        recs = self.search_read(
            "account.move", domain, self._INVOICE_FIELDS,
            limit=limit, order="date desc",
        )
        return [OdooInvoice.from_odoo(r) for r in recs]

    def get_invoice_detail(self, invoice_id: int) -> dict | None:
        """Fetch a single invoice with its lines."""
        recs = self.search_read(
            "account.move",
            [("id", "=", invoice_id)],
            self._INVOICE_FIELDS + ["invoice_line_ids"],
            limit=1,
        )
        if not recs:
            return None

        inv = recs[0]
        # Fetch lines
        line_ids = inv.get("invoice_line_ids", [])
        lines = []
        if line_ids:
            lines = self.search_read(
                "account.move.line",
                [("id", "in", line_ids)],
                ["product_id", "name", "quantity", "price_unit",
                 "price_subtotal", "tax_ids"],
                limit=200,
            )
        inv["lines"] = lines
        return inv

    def create_invoice(self, partner_id: int, lines: list[dict],
                       type: str = "out_invoice",
                       date: str = "") -> int | None:
        """
        Create a draft invoice.

        Each line dict should have: name, quantity, price_unit
        Optionally: product_id, tax_ids, account_id
        """
        invoice_date = date or datetime.now().strftime("%Y-%m-%d")
        invoice_lines = []
        for ln in lines:
            vals = {
                "name": ln.get("name", "Item"),
                "quantity": ln.get("quantity", 1),
                "price_unit": ln.get("price_unit", 0),
            }
            if "product_id" in ln:
                vals["product_id"] = ln["product_id"]
            if "account_id" in ln:
                vals["account_id"] = ln["account_id"]
            if "tax_ids" in ln:
                vals["tax_ids"] = [(6, 0, ln["tax_ids"])]
            invoice_lines.append((0, 0, vals))

        values = {
            "move_type": type,
            "partner_id": partner_id,
            "invoice_date": invoice_date,
            "invoice_line_ids": invoice_lines,
        }
        return self.create("account.move", values)

    def confirm_invoice(self, invoice_id: int) -> bool:
        """Post/confirm a draft invoice (action_post)."""
        try:
            self._execute_kw("account.move", "action_post", [[invoice_id]])
            log.info("Odoo invoice %d confirmed", invoice_id)
            return True
        except Exception as exc:
            log.error("Odoo confirm invoice %d failed: %s", invoice_id, exc)
            return False

    def register_payment(self, invoice_id: int, amount: float,
                         date: str = "",
                         journal_id: int | None = None) -> int | None:
        """
        Register a payment against an invoice via the payment wizard.
        Returns the payment record ID on success.
        """
        payment_date = date or datetime.now().strftime("%Y-%m-%d")
        values: dict[str, Any] = {
            "payment_type": "inbound",
            "partner_type": "customer",
            "amount": amount,
            "payment_date": payment_date,
        }
        if journal_id:
            values["journal_id"] = journal_id

        try:
            # Use the reconciliation wizard
            ctx = {"active_model": "account.move", "active_ids": [invoice_id]}
            wizard_id = self._call_rpc("object", "execute_kw", [
                self._db, self._uid, self._password,
                "account.payment.register", "create",
                [values], {"context": ctx},
            ])
            if wizard_id:
                wiz_id = wizard_id if isinstance(wizard_id, int) else wizard_id[0]
                self._call_rpc("object", "execute_kw", [
                    self._db, self._uid, self._password,
                    "account.payment.register", "action_create_payments",
                    [[wiz_id]], {"context": ctx},
                ])
                log.info("Odoo payment registered for invoice %d", invoice_id)
                return wiz_id
            return None
        except Exception as exc:
            log.error("Odoo register payment failed: %s", exc)
            return None

    # ══════════════════════════════════════════════════════════════════
    #  CUSTOMERS / PARTNERS
    # ══════════════════════════════════════════════════════════════════

    def get_customers(self, limit: int = 50) -> list[dict]:
        """Fetch customer partners."""
        return self.search_read(
            "res.partner",
            [("customer_rank", ">", 0)],
            ["id", "name", "email", "phone", "vat", "city",
             "country_id", "customer_rank"],
            limit=limit,
            order="name asc",
        )

    def create_customer(self, name: str, email: str = "",
                        phone: str = "", vat: str = "") -> int | None:
        """Create a new customer partner."""
        values: dict[str, Any] = {
            "name": name,
            "customer_rank": 1,
        }
        if email:
            values["email"] = email
        if phone:
            values["phone"] = phone
        if vat:
            values["vat"] = vat
        return self.create("res.partner", values)

    # ══════════════════════════════════════════════════════════════════
    #  PAYMENTS
    # ══════════════════════════════════════════════════════════════════

    def get_payments(self, type: str = "inbound",
                     limit: int = 50) -> list[dict]:
        """Fetch payment records."""
        domain: list = []
        if type:
            domain.append(("payment_type", "=", type))
        return self.search_read(
            "account.payment",
            domain,
            ["id", "name", "partner_id", "payment_type", "amount",
             "date", "state", "journal_id", "ref"],
            limit=limit,
            order="date desc",
        )

    # ══════════════════════════════════════════════════════════════════
    #  JOURNAL ENTRIES
    # ══════════════════════════════════════════════════════════════════

    def get_journal_entries(self, type: str = "",
                           limit: int = 50) -> list[dict]:
        """Fetch journal entries (account.move where move_type=entry)."""
        domain: list = [("move_type", "=", "entry")]
        if type:
            domain.append(("journal_id.type", "=", type))
        return self.search_read(
            "account.move",
            domain,
            ["id", "name", "ref", "date", "state", "amount_total",
             "journal_id"],
            limit=limit,
            order="date desc",
        )

    def create_journal_entry(self, ref: str, lines: list[dict],
                             date: str = "") -> int | None:
        """
        Create a manual journal entry.

        Each line dict should have: account_id, name, debit, credit
        """
        entry_date = date or datetime.now().strftime("%Y-%m-%d")
        move_lines = []
        for ln in lines:
            move_lines.append((0, 0, {
                "account_id": ln["account_id"],
                "name": ln.get("name", "/"),
                "debit": ln.get("debit", 0),
                "credit": ln.get("credit", 0),
            }))
        return self.create("account.move", {
            "move_type": "entry",
            "date": entry_date,
            "ref": ref,
            "line_ids": move_lines,
        })

    # ══════════════════════════════════════════════════════════════════
    #  FINANCIAL REPORTS
    # ══════════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════════
    #  EXPENSES (hr.expense)
    # ══════════════════════════════════════════════════════════════════

    def get_expenses(self, state: str = "",
                     limit: int = 50) -> list[dict]:
        """
        Fetch expense records from hr.expense.

        Args:
            state: Filter by state — "draft", "reported", "approved",
                   "done", "refused", or "" for all.
            limit: Max records.
        """
        domain: list = []
        if state:
            domain.append(("state", "=", state))
        return self.search_read(
            "hr.expense",
            domain,
            ["id", "name", "employee_id", "product_id", "unit_amount",
             "total_amount", "date", "state", "payment_mode",
             "reference", "description"],
            limit=limit,
            order="date desc",
        )

    def create_expense(self, name: str, amount: float,
                       employee_id: int,
                       date: str = "",
                       product_id: int | None = None,
                       description: str = "") -> int | None:
        """Create a new draft expense record."""
        expense_date = date or datetime.now().strftime("%Y-%m-%d")
        values: dict[str, Any] = {
            "name": name,
            "unit_amount": amount,
            "employee_id": employee_id,
            "date": expense_date,
        }
        if product_id:
            values["product_id"] = product_id
        if description:
            values["description"] = description
        return self.create("hr.expense", values)

    # ══════════════════════════════════════════════════════════════════
    #  COMBINED FINANCIAL REPORT
    # ══════════════════════════════════════════════════════════════════

    def get_financial_report(self) -> dict:
        """
        Combined financial report: P&L + Balance Sheet + receivables
        + payables + recent invoices/expenses summary.
        """
        report: dict[str, Any] = {
            "generated_at": datetime.now().isoformat(),
        }

        # P&L
        try:
            report["profit_loss"] = self.get_profit_loss_summary()
        except Exception as exc:
            report["profit_loss"] = {"error": str(exc)}

        # Balance Sheet
        try:
            report["balance_sheet"] = self.get_balance_sheet_summary()
        except Exception as exc:
            report["balance_sheet"] = {"error": str(exc)}

        # Receivables (unpaid customer invoices)
        try:
            recv = self.get_invoices(type="out_invoice", state="posted", limit=100)
            open_recv = [inv for inv in recv if inv.amount_residual > 0]
            report["receivables"] = {
                "count": len(open_recv),
                "total": round(sum(inv.amount_residual for inv in open_recv), 2),
                "invoices": [inv.to_dict() for inv in open_recv[:20]],
            }
        except Exception as exc:
            report["receivables"] = {"error": str(exc)}

        # Payables (unpaid vendor bills)
        try:
            payable = self.get_invoices(type="in_invoice", state="posted", limit=100)
            open_pay = [inv for inv in payable if inv.amount_residual > 0]
            report["payables"] = {
                "count": len(open_pay),
                "total": round(sum(inv.amount_residual for inv in open_pay), 2),
                "bills": [inv.to_dict() for inv in open_pay[:20]],
            }
        except Exception as exc:
            report["payables"] = {"error": str(exc)}

        # Recent expenses
        try:
            expenses = self.get_expenses(limit=20)
            report["recent_expenses"] = {
                "count": len(expenses),
                "total": round(sum(float(e.get("total_amount", 0)) for e in expenses), 2),
                "items": expenses,
            }
        except Exception:
            report["recent_expenses"] = {"count": 0, "total": 0, "items": []}

        return report

    # ══════════════════════════════════════════════════════════════════
    #  WEEKLY ACCOUNTING SUMMARY
    # ══════════════════════════════════════════════════════════════════

    def get_weekly_accounting_summary(self) -> dict:
        """
        Accounting summary for the last 7 days: invoices created,
        payments received, expenses logged, and journal entries.
        """
        from datetime import timedelta

        today = datetime.now().date()
        week_ago = today - timedelta(days=7)
        date_start = week_ago.strftime("%Y-%m-%d")
        date_end = today.strftime("%Y-%m-%d")

        summary: dict[str, Any] = {
            "period_start": date_start,
            "period_end": date_end,
            "generated_at": datetime.now().isoformat(),
        }

        # Invoices created this week
        try:
            inv_domain: list = [
                ("create_date", ">=", f"{date_start} 00:00:00"),
                ("create_date", "<=", f"{date_end} 23:59:59"),
                ("move_type", "in", ["out_invoice", "in_invoice"]),
            ]
            invoices = self.search_read(
                "account.move", inv_domain,
                ["id", "name", "partner_id", "move_type", "state",
                 "amount_total", "date"],
                limit=200, order="date desc",
            )
            customer_inv = [i for i in invoices if i.get("move_type") == "out_invoice"]
            vendor_inv = [i for i in invoices if i.get("move_type") == "in_invoice"]
            summary["invoices"] = {
                "total_created": len(invoices),
                "customer_invoices": len(customer_inv),
                "customer_total": round(sum(float(i.get("amount_total", 0)) for i in customer_inv), 2),
                "vendor_bills": len(vendor_inv),
                "vendor_total": round(sum(float(i.get("amount_total", 0)) for i in vendor_inv), 2),
                "items": invoices[:30],
            }
        except Exception as exc:
            summary["invoices"] = {"error": str(exc)}

        # Payments this week
        try:
            pay_domain: list = [
                ("date", ">=", date_start),
                ("date", "<=", date_end),
            ]
            payments = self.search_read(
                "account.payment", pay_domain,
                ["id", "name", "partner_id", "payment_type", "amount",
                 "date", "state", "ref"],
                limit=200, order="date desc",
            )
            inbound = [p for p in payments if p.get("payment_type") == "inbound"]
            outbound = [p for p in payments if p.get("payment_type") == "outbound"]
            summary["payments"] = {
                "total_count": len(payments),
                "inbound_count": len(inbound),
                "inbound_total": round(sum(float(p.get("amount", 0)) for p in inbound), 2),
                "outbound_count": len(outbound),
                "outbound_total": round(sum(float(p.get("amount", 0)) for p in outbound), 2),
                "items": payments[:30],
            }
        except Exception as exc:
            summary["payments"] = {"error": str(exc)}

        # Expenses this week
        try:
            exp_domain: list = [
                ("date", ">=", date_start),
                ("date", "<=", date_end),
            ]
            expenses = self.search_read(
                "hr.expense", exp_domain,
                ["id", "name", "employee_id", "total_amount", "date",
                 "state"],
                limit=200, order="date desc",
            )
            summary["expenses"] = {
                "total_count": len(expenses),
                "total_amount": round(sum(float(e.get("total_amount", 0)) for e in expenses), 2),
                "items": expenses[:30],
            }
        except Exception:
            summary["expenses"] = {"total_count": 0, "total_amount": 0, "items": []}

        # Journal entries this week
        try:
            je_domain: list = [
                ("date", ">=", date_start),
                ("date", "<=", date_end),
                ("move_type", "=", "entry"),
            ]
            entries = self.search_read(
                "account.move", je_domain,
                ["id", "name", "ref", "date", "state", "amount_total"],
                limit=200, order="date desc",
            )
            summary["journal_entries"] = {
                "total_count": len(entries),
                "total_amount": round(sum(float(e.get("amount_total", 0)) for e in entries), 2),
                "items": entries[:30],
            }
        except Exception as exc:
            summary["journal_entries"] = {"error": str(exc)}

        return summary

    # ══════════════════════════════════════════════════════════════════
    #  CHART OF ACCOUNTS / BALANCES
    # ══════════════════════════════════════════════════════════════════

    def get_account_balance(self, account_code: str = "") -> list[dict]:
        """
        Fetch account balances from chart of accounts.
        If account_code is given, filter by prefix.
        """
        domain: list = []
        if account_code:
            domain.append(("code", "=like", f"{account_code}%"))
        return self.search_read(
            "account.account",
            domain,
            ["id", "code", "name", "account_type", "current_balance"],
            limit=200,
            order="code asc",
        )

    def get_profit_loss_summary(self) -> dict:
        """
        Build a P&L summary by reading income and expense account balances.

        Odoo account_type values for P&L:
          - income / income_other  → Revenue
          - expense / expense_depreciation / expense_direct_cost → Expense
        """
        income_types = ("income", "income_other")
        expense_types = ("expense", "expense_depreciation", "expense_direct_cost")

        accounts = self.search_read(
            "account.account", [],
            ["code", "name", "account_type", "current_balance"],
            limit=500,
        )

        income_total = 0.0
        expense_total = 0.0
        income_lines: list[dict] = []
        expense_lines: list[dict] = []

        for acc in accounts:
            atype = acc.get("account_type", "")
            balance = float(acc.get("current_balance", 0))
            entry = {
                "code": acc.get("code", ""),
                "name": acc.get("name", ""),
                "balance": balance,
            }
            if atype in income_types:
                income_total += balance
                income_lines.append(entry)
            elif atype in expense_types:
                expense_total += balance
                expense_lines.append(entry)

        return {
            "income_total": round(income_total, 2),
            "expense_total": round(expense_total, 2),
            "net_profit": round(income_total - expense_total, 2),
            "income_accounts": income_lines,
            "expense_accounts": expense_lines,
            "generated_at": datetime.now().isoformat(),
        }

    def get_balance_sheet_summary(self) -> dict:
        """
        Build a balance sheet summary from asset, liability, and equity accounts.

        Odoo account_type values:
          - asset_receivable / asset_cash / asset_current /
            asset_non_current / asset_prepayments / asset_fixed → Assets
          - liability_payable / liability_credit_card /
            liability_current / liability_non_current → Liabilities
          - equity / equity_unaffected → Equity
        """
        asset_types = (
            "asset_receivable", "asset_cash", "asset_current",
            "asset_non_current", "asset_prepayments", "asset_fixed",
        )
        liability_types = (
            "liability_payable", "liability_credit_card",
            "liability_current", "liability_non_current",
        )
        equity_types = ("equity", "equity_unaffected")

        accounts = self.search_read(
            "account.account", [],
            ["code", "name", "account_type", "current_balance"],
            limit=500,
        )

        assets_total = 0.0
        liabilities_total = 0.0
        equity_total = 0.0
        asset_lines: list[dict] = []
        liability_lines: list[dict] = []
        equity_lines: list[dict] = []

        for acc in accounts:
            atype = acc.get("account_type", "")
            balance = float(acc.get("current_balance", 0))
            entry = {
                "code": acc.get("code", ""),
                "name": acc.get("name", ""),
                "balance": balance,
            }
            if atype in asset_types:
                assets_total += balance
                asset_lines.append(entry)
            elif atype in liability_types:
                liabilities_total += balance
                liability_lines.append(entry)
            elif atype in equity_types:
                equity_total += balance
                equity_lines.append(entry)

        return {
            "assets_total": round(assets_total, 2),
            "liabilities_total": round(liabilities_total, 2),
            "equity_total": round(equity_total, 2),
            "asset_accounts": asset_lines,
            "liability_accounts": liability_lines,
            "equity_accounts": equity_lines,
            "generated_at": datetime.now().isoformat(),
        }
