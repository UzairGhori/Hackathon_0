"""
AI Employee — Odoo Community MCP Server

Standalone Model Context Protocol server that exposes Odoo Community
accounting operations as MCP tools. Runs over stdio transport.

Tools exposed:
  - list_invoices, create_invoice, get_invoice, confirm_invoice
  - list_payments, register_payment
  - list_customers, create_customer
  - get_expenses, create_expense
  - get_account_balance
  - list_journal_entries, create_journal_entry
  - get_profit_loss, get_balance_sheet
  - get_financial_report       (combined P&L + balance sheet + receivables/payables)
  - weekly_accounting_summary  (last 7 days: invoices, payments, expenses, entries)

Usage:
    python -m ai_employee.integrations.odoo_mcp_server

Environment variables (loaded from .env):
    ODOO_URL        — Odoo server URL (e.g. http://localhost:8069)
    ODOO_DB         — Odoo database name
    ODOO_USERNAME   — Odoo login username
    ODOO_PASSWORD   — Odoo login password
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from ai_employee.integrations.odoo_client import OdooClient

# Load .env from project root
_root = Path(__file__).resolve().parent.parent.parent
_dotenv = _root / ".env"
if _dotenv.exists():
    load_dotenv(_dotenv)

# ── Instantiate the Odoo client ────────────────────────────────────────

_odoo = OdooClient(
    url=os.getenv("ODOO_URL", ""),
    db=os.getenv("ODOO_DB", ""),
    username=os.getenv("ODOO_USERNAME", ""),
    password=os.getenv("ODOO_PASSWORD", ""),
)

# ── Create the MCP server ──────────────────────────────────────────────

mcp = FastMCP(
    "odoo-accounting",
    instructions="Odoo Community self-hosted accounting integration",
)


# ══════════════════════════════════════════════════════════════════════
#  INVOICE TOOLS
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def list_invoices(
    type: str = "out_invoice",
    state: str = "",
    limit: int = 20,
) -> str:
    """
    List invoices from Odoo.

    Args:
        type: Invoice type — "out_invoice" (customer) or "in_invoice" (vendor).
        state: Filter by state — "draft", "posted", "cancel", or "" for all.
        limit: Maximum number of invoices to return.

    Returns:
        JSON array of invoice summaries.
    """
    invoices = _odoo.get_invoices(type=type, state=state, limit=limit)
    return json.dumps([inv.to_dict() for inv in invoices], indent=2)


@mcp.tool()
def get_invoice(invoice_id: int) -> str:
    """
    Get full details of a specific invoice, including line items.

    Args:
        invoice_id: The Odoo record ID of the invoice.

    Returns:
        JSON object with invoice details and lines.
    """
    result = _odoo.get_invoice_detail(invoice_id)
    if result is None:
        return json.dumps({"error": f"Invoice {invoice_id} not found"})
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def create_invoice(
    partner_id: int,
    lines: list[dict],
    type: str = "out_invoice",
    date: str = "",
) -> str:
    """
    Create a new draft invoice.

    Args:
        partner_id: Customer/vendor partner ID.
        lines: List of line items, each with keys: name, quantity, price_unit.
               Optional keys: product_id, account_id, tax_ids.
        type: "out_invoice" for customer, "in_invoice" for vendor.
        date: Invoice date (YYYY-MM-DD). Defaults to today.

    Returns:
        JSON with the new invoice ID or error.
    """
    invoice_id = _odoo.create_invoice(
        partner_id=partner_id, lines=lines, type=type, date=date,
    )
    if invoice_id:
        return json.dumps({"success": True, "invoice_id": invoice_id})
    return json.dumps({"success": False, "error": "Failed to create invoice"})


@mcp.tool()
def confirm_invoice(invoice_id: int) -> str:
    """
    Confirm/post a draft invoice, making it official.

    Args:
        invoice_id: The Odoo record ID of the draft invoice to confirm.

    Returns:
        JSON with success status.
    """
    ok = _odoo.confirm_invoice(invoice_id)
    return json.dumps({"success": ok, "invoice_id": invoice_id})


# ══════════════════════════════════════════════════════════════════════
#  PAYMENT TOOLS
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def list_payments(type: str = "inbound", limit: int = 20) -> str:
    """
    List payment records from Odoo.

    Args:
        type: "inbound" (customer payments) or "outbound" (vendor payments).
        limit: Maximum number of payments to return.

    Returns:
        JSON array of payment records.
    """
    payments = _odoo.get_payments(type=type, limit=limit)
    return json.dumps(payments, indent=2, default=str)


@mcp.tool()
def register_payment(
    invoice_id: int,
    amount: float,
    date: str = "",
) -> str:
    """
    Register a payment against an invoice.

    Args:
        invoice_id: The invoice to pay.
        amount: Payment amount.
        date: Payment date (YYYY-MM-DD). Defaults to today.

    Returns:
        JSON with the payment wizard ID or error.
    """
    result = _odoo.register_payment(
        invoice_id=invoice_id, amount=amount, date=date,
    )
    if result:
        return json.dumps({"success": True, "payment_id": result})
    return json.dumps({"success": False, "error": "Failed to register payment"})


# ══════════════════════════════════════════════════════════════════════
#  CUSTOMER / PARTNER TOOLS
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def list_customers(limit: int = 20) -> str:
    """
    List customer partners from Odoo.

    Args:
        limit: Maximum number of customers to return.

    Returns:
        JSON array of customer records.
    """
    customers = _odoo.get_customers(limit=limit)
    return json.dumps(customers, indent=2, default=str)


@mcp.tool()
def create_customer(
    name: str,
    email: str = "",
    phone: str = "",
    vat: str = "",
) -> str:
    """
    Create a new customer in Odoo.

    Args:
        name: Customer name.
        email: Customer email address.
        phone: Customer phone number.
        vat: Tax identification number.

    Returns:
        JSON with the new customer ID or error.
    """
    cid = _odoo.create_customer(name=name, email=email, phone=phone, vat=vat)
    if cid:
        return json.dumps({"success": True, "customer_id": cid})
    return json.dumps({"success": False, "error": "Failed to create customer"})


# ══════════════════════════════════════════════════════════════════════
#  EXPENSE TOOLS
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_expenses(state: str = "", limit: int = 20) -> str:
    """
    List expense records from Odoo (hr.expense).

    Args:
        state: Filter by state — "draft", "reported", "approved",
               "done", "refused", or "" for all.
        limit: Maximum number of expenses to return.

    Returns:
        JSON array of expense records with employee, amount, date, state.
    """
    expenses = _odoo.get_expenses(state=state, limit=limit)
    return json.dumps(expenses, indent=2, default=str)


@mcp.tool()
def create_expense(
    name: str,
    amount: float,
    employee_id: int,
    date: str = "",
    description: str = "",
) -> str:
    """
    Create a new draft expense record.

    Args:
        name: Expense description/label.
        amount: Expense amount.
        employee_id: ID of the employee who incurred the expense.
        date: Expense date (YYYY-MM-DD). Defaults to today.
        description: Additional notes.

    Returns:
        JSON with the new expense ID or error.
    """
    eid = _odoo.create_expense(
        name=name, amount=amount, employee_id=employee_id,
        date=date, description=description,
    )
    if eid:
        return json.dumps({"success": True, "expense_id": eid})
    return json.dumps({"success": False, "error": "Failed to create expense"})


# ══════════════════════════════════════════════════════════════════════
#  COMBINED FINANCIAL REPORT
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_financial_report() -> str:
    """
    Generate a comprehensive financial report combining:
    - Profit & Loss summary
    - Balance Sheet summary
    - Open receivables (unpaid customer invoices)
    - Open payables (unpaid vendor bills)
    - Recent expenses

    Returns:
        JSON with all sections combined into one report.
    """
    return json.dumps(_odoo.get_financial_report(), indent=2, default=str)


# ══════════════════════════════════════════════════════════════════════
#  WEEKLY ACCOUNTING SUMMARY
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def weekly_accounting_summary() -> str:
    """
    Generate an accounting summary for the last 7 days including:
    - Invoices created (customer + vendor)
    - Payments received and sent
    - Expenses logged
    - Journal entries posted

    Returns:
        JSON with counts, totals, and itemized data for the past week.
    """
    return json.dumps(
        _odoo.get_weekly_accounting_summary(), indent=2, default=str,
    )


# ══════════════════════════════════════════════════════════════════════
#  CHART OF ACCOUNTS / BALANCE
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_account_balance(account_code: str = "") -> str:
    """
    Get account balances from the chart of accounts.

    Args:
        account_code: Filter by account code prefix (e.g. "1" for assets,
                      "4" for revenue). Empty for all accounts.

    Returns:
        JSON array of account records with balances.
    """
    accounts = _odoo.get_account_balance(account_code=account_code)
    return json.dumps(accounts, indent=2, default=str)


# ══════════════════════════════════════════════════════════════════════
#  JOURNAL ENTRY TOOLS
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def list_journal_entries(type: str = "", limit: int = 20) -> str:
    """
    List journal entries from Odoo.

    Args:
        type: Filter by journal type (e.g. "sale", "purchase", "bank",
              "cash", "general"). Empty for all.
        limit: Maximum number of entries to return.

    Returns:
        JSON array of journal entry records.
    """
    entries = _odoo.get_journal_entries(type=type, limit=limit)
    return json.dumps(entries, indent=2, default=str)


@mcp.tool()
def create_journal_entry(
    ref: str,
    lines: list[dict],
    date: str = "",
) -> str:
    """
    Create a manual journal entry.

    Args:
        ref: Reference/description for the journal entry.
        lines: List of line items, each with keys: account_id, name, debit, credit.
               Debits and credits must balance.
        date: Entry date (YYYY-MM-DD). Defaults to today.

    Returns:
        JSON with the new journal entry ID or error.
    """
    entry_id = _odoo.create_journal_entry(ref=ref, lines=lines, date=date)
    if entry_id:
        return json.dumps({"success": True, "entry_id": entry_id})
    return json.dumps({"success": False, "error": "Failed to create journal entry"})


# ══════════════════════════════════════════════════════════════════════
#  FINANCIAL REPORT TOOLS
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_profit_loss() -> str:
    """
    Generate a Profit & Loss summary from Odoo account balances.

    Returns:
        JSON with income_total, expense_total, net_profit, and account details.
    """
    return json.dumps(_odoo.get_profit_loss_summary(), indent=2)


@mcp.tool()
def get_balance_sheet() -> str:
    """
    Generate a Balance Sheet summary from Odoo account balances.

    Returns:
        JSON with assets_total, liabilities_total, equity_total, and account details.
    """
    return json.dumps(_odoo.get_balance_sheet_summary(), indent=2)


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run(transport="stdio")
