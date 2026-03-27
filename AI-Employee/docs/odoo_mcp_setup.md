# Odoo MCP Server — Integration & Setup Guide (Platinum Tier)

> **Prerequisite:** Odoo 18.0 Community installed and running on Cloud VM.
> See [odoo_deployment_guide.md](odoo_deployment_guide.md) for installation.

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [Connection Flow](#2-connection-flow)
3. [Odoo Module Requirements](#3-odoo-module-requirements)
4. [MCP Server Configuration](#4-mcp-server-configuration)
5. [Tool Reference — All 18 Tools](#5-tool-reference--all-18-tools)
6. [Capability: Accounting](#6-capability-accounting)
7. [Capability: Invoices](#7-capability-invoices)
8. [Capability: Expenses](#8-capability-expenses)
9. [Capability: Financial Reports](#9-capability-financial-reports)
10. [Draft Mode Safety Integration](#10-draft-mode-safety-integration)
11. [OdooWatcher — Cloud Automation](#11-odoowatcher--cloud-automation)
12. [Testing Each Capability](#12-testing-each-capability)
13. [Claude Code Integration](#13-claude-code-integration)
14. [Troubleshooting](#14-troubleshooting)
15. [End-to-End Walkthrough](#15-end-to-end-walkthrough)

---

## 1. Architecture

```
┌───────────────────────────── Cloud VM ────────────────────────────────────┐
│                                                                           │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │  PostgreSQL 16                                                    │     │
│  │  └── ai_employee_accounting (database)                            │     │
│  └────────────────────┬─────────────────────────────────────────────┘     │
│                       │                                                   │
│                       │ localhost:5432                                     │
│                       ▼                                                   │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │  Odoo 18.0 Community Edition                                      │     │
│  │  http://127.0.0.1:8069                                            │     │
│  │                                                                    │     │
│  │  Modules:  account, hr_expense, contacts, hr                      │     │
│  │  API:      /jsonrpc (JSON-RPC 2.0)                                │     │
│  └────────────────────┬─────────────────────────────────────────────┘     │
│                       │                                                   │
│                       │ JSON-RPC 2.0 over HTTP                            │
│                       ▼                                                   │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │  OdooClient (ai_employee/integrations/odoo_client.py)             │     │
│  │  Python wrapper — search_read, create, write, unlink              │     │
│  │  + 20 domain-specific methods                                     │     │
│  └────────────────────┬─────────────────────────────────────────────┘     │
│                       │                                                   │
│                       │ Direct Python calls                               │
│                       ▼                                                   │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │  Odoo MCP Server (FastMCP over stdio)                             │     │
│  │  ai_employee.integrations.odoo_mcp_server                         │     │
│  │  18 tools — managed by supervisord                                │     │
│  └────────────────────┬─────────────────────────────────────────────┘     │
│                       │                                                   │
│   ┌───────────────────┼────────────────────────────────────────┐         │
│   │                   │                                        │         │
│   ▼                   ▼                                        ▼         │
│  OdooAgent        OdooWatcher                          MCPRouter         │
│  (pipeline)       (10-min poll)                     (tool dispatch)      │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

                            │
                            │ Git Sync (vault/ changes)
                            ▼

┌───────────────────────── Local Machine ───────────────────────────────────┐
│                                                                           │
│  Claude Code ──▶ MCP Client ──▶ Odoo MCP Server (stdio)                 │
│                                                                           │
│  AIEmployee ──▶ OdooAgent ──▶ DraftModeController ──▶ ApprovalQueue     │
│                                                                           │
│  Dashboard (localhost:8080) ──▶ /approvals ──▶ Human CEO                 │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Connection Flow

### 2.1 JSON-RPC Protocol

All communication between `OdooClient` and Odoo uses **JSON-RPC 2.0** over HTTP POST to `/jsonrpc`.

```
OdooClient                            Odoo Server
    │                                      │
    │  POST /jsonrpc                       │
    │  {                                   │
    │    "jsonrpc": "2.0",                 │
    │    "method": "call",                 │
    │    "params": {                       │
    │      "service": "object",            │
    │      "method": "execute_kw",         │
    │      "args": [                       │
    │        "ai_employee_accounting",     │
    │        2,  (UID)                     │
    │        "password",                   │
    │        "account.move",               │
    │        "search_read",                │
    │        [[["state","=","posted"]]],   │
    │        {"fields":["name","amount"],   │
    │         "limit": 20}                 │
    │      ]                               │
    │    },                                │
    │    "id": 123                         │
    │  }                                   │
    │─────────────────────────────────────▶│
    │                                      │
    │  {"jsonrpc":"2.0","id":123,          │
    │   "result": [...]}                   │
    │◀─────────────────────────────────────│
```

### 2.2 Authentication Flow

```
1. Client calls: common.authenticate(db, username, password, {})
2. Odoo returns: UID (integer) or False
3. Client caches UID for all subsequent execute_kw calls
4. UID is passed as the second arg in every execute_kw call
```

### 2.3 MCP Tool Call Flow

```
Claude / AI Agent
    │
    │  MCP tool_call: "list_invoices"
    │  args: {"type": "out_invoice", "limit": 10}
    │
    ▼
Odoo MCP Server (FastMCP)
    │
    │  Calls: _odoo.get_invoices(type="out_invoice", limit=10)
    │
    ▼
OdooClient
    │
    │  JSON-RPC: execute_kw("account.move", "search_read",
    │     [[["move_type","=","out_invoice"]]],
    │     {"fields": [...], "limit": 10, "order": "create_date desc"})
    │
    ▼
Odoo 18.0 → PostgreSQL → result
    │
    ▼
JSON response → OdooInvoice dataclass → JSON string → MCP response
```

---

## 3. Odoo Module Requirements

### 3.1 Required Modules and Their Odoo Models

| Capability        | Odoo Module     | Models Used                                           |
|-------------------|-----------------|-------------------------------------------------------|
| **Accounting**    | `account`       | `account.account`, `account.journal`, `account.move.line` |
| **Invoices**      | `account`       | `account.move` (move_type: out_invoice, in_invoice)   |
| **Payments**      | `account_payment` | `account.payment`, `account.payment.register`       |
| **Expenses**      | `hr_expense`    | `hr.expense`                                          |
| **Journal Entries** | `account`     | `account.move` (move_type: entry)                     |
| **Financial Reports** | `account`  | `account.account` (aggregated by account_type)        |
| **Customers**     | `contacts`      | `res.partner` (customer_rank > 0)                     |
| **Employees**     | `hr`            | `hr.employee` (for expense assignment)                |

### 3.2 Installation Command

```bash
sudo -u odoo /opt/odoo/venv/bin/python /opt/odoo/odoo-server/odoo-bin \
    -c /etc/odoo/odoo.conf \
    -d ai_employee_accounting \
    -i account,hr_expense,contacts,hr \
    --stop-after-init \
    --without-demo=all
```

### 3.3 Post-Install Configuration

After installing modules, configure via Odoo Web UI (`http://VM_IP:8069`):

1. **Chart of Accounts**
   - Accounting > Configuration > Settings > Fiscal Localization
   - Choose your country's chart or "Generic Chart of Accounts"
   - Click **Install**

2. **Journals** (verify these exist)
   - Accounting > Configuration > Journals
   - Required: **Bank**, **Cash**, **Sales**, **Purchase**, **Miscellaneous**

3. **Payment Methods**
   - Accounting > Configuration > Payment Methods
   - Ensure "Manual" payment method is enabled

4. **Fiscal Year**
   - Accounting > Configuration > Settings
   - Set fiscal year start date

5. **Expenses Categories** (optional)
   - Expenses > Configuration > Expense Categories
   - Add categories: Travel, Meals, Office Supplies, Software, etc.

---

## 4. MCP Server Configuration

### 4.1 Environment Variables

The MCP server reads from `/etc/ai-employee/.env`:

```bash
# ── Odoo Connection ─────────────────────────────────────────────
ODOO_URL=http://127.0.0.1:8069
ODOO_DB=ai_employee_accounting
ODOO_USERNAME=admin
ODOO_PASSWORD=<your-password>
```

### 4.2 Server Entry Point

**Module:** `ai_employee.integrations.odoo_mcp_server`

**Source:** `ai_employee/integrations/odoo_mcp_server.py`

The server:
1. Loads `.env` from the project root
2. Creates an `OdooClient` instance with credentials
3. Creates a `FastMCP` server named `"odoo-accounting"`
4. Registers 18 tools that delegate to `OdooClient` methods
5. Runs on stdio transport (stdin/stdout JSON-RPC)

### 4.3 Supervisord Configuration

Already defined in `ai_employee/process_manager.config`:

```ini
[program:mcp-odoo-accounting]
command         = /opt/ai-employee/venv/bin/python -m ai_employee.integrations.odoo_mcp_server
directory       = /opt/ai-employee
user            = ai-employee
autostart       = true
autorestart     = true
startsecs       = 3
startretries    = 3
stopwaitsecs    = 10
stopsignal      = TERM
redirect_stderr = true
stdout_logfile  = /opt/ai-employee/ai_employee/logs/mcp_odoo.log
stdout_logfile_maxbytes = 5MB
stdout_logfile_backups  = 2
```

### 4.4 Systemd Alternative

```bash
sudo systemctl enable --now ai-employee-mcp@odoo_mcp_server
```

Uses the template unit defined in `process_manager.config`:

```ini
ExecStart=/opt/ai-employee/venv/bin/python -m ai_employee.integrations.%i
```

### 4.5 MCP Client Configuration (Claude Code)

File: `ai_employee/integrations/odoo_mcp_config.json`

```json
{
  "mcpServers": {
    "odoo-accounting": {
      "command": "python",
      "args": ["-m", "ai_employee.integrations.odoo_mcp_server"],
      "cwd": "/opt/ai-employee"
    }
  }
}
```

### 4.6 Tool Registry Integration

The `ToolRegistry` in `ai_employee/integrations/tool_registry.py` auto-discovers all tools:

```python
# Automatic discovery via importlib
import ai_employee.integrations.odoo_mcp_server as mod
mcp_instance = getattr(mod, "mcp")
tools = mcp_instance._tool_manager._tools
# Registers under category: ACCOUNTING
```

### 4.7 MCPRouter Integration

The `MCPRouter` in `ai_employee/integrations/mcp_router.py` routes tool calls:

```python
# Server registration
router.register_server("odoo-accounting", ToolCategory.ACCOUNTING)

# Routing: tool name → server
router.route("list_invoices")       # → odoo-accounting
router.route("get_financial_report") # → odoo-accounting
router.route("register_payment")     # → odoo-accounting
```

---

## 5. Tool Reference — All 18 Tools

### Quick Reference Table

| # | Tool                         | Category   | Read/Write | Odoo Model              | Draft Mode |
|---|------------------------------|-----------|------------|-------------------------|------------|
| 1 | `list_invoices`              | Invoices   | Read       | `account.move`          | N/A        |
| 2 | `get_invoice`                | Invoices   | Read       | `account.move`          | N/A        |
| 3 | `create_invoice`             | Invoices   | **Write**  | `account.move`          | **Drafts** |
| 4 | `confirm_invoice`            | Invoices   | **Write**  | `account.move`          | **Drafts** |
| 5 | `list_payments`              | Payments   | Read       | `account.payment`       | N/A        |
| 6 | `register_payment`           | Payments   | **Write**  | `account.payment.register` | **Drafts** |
| 7 | `list_customers`             | Partners   | Read       | `res.partner`           | N/A        |
| 8 | `create_customer`            | Partners   | **Write**  | `res.partner`           | **Drafts** |
| 9 | `get_expenses`               | Expenses   | Read       | `hr.expense`            | N/A        |
| 10 | `create_expense`            | Expenses   | **Write**  | `hr.expense`            | **Drafts** |
| 11 | `list_journal_entries`      | Accounting | Read       | `account.move`          | N/A        |
| 12 | `create_journal_entry`      | Accounting | **Write**  | `account.move`          | **Drafts** |
| 13 | `get_account_balance`       | Reports    | Read       | `account.account`       | N/A        |
| 14 | `get_profit_loss`           | Reports    | Read       | `account.account`       | N/A        |
| 15 | `get_balance_sheet`         | Reports    | Read       | `account.account`       | N/A        |
| 16 | `get_financial_report`      | Reports    | Read       | Multiple                | N/A        |
| 17 | `weekly_accounting_summary` | Reports    | Read       | Multiple                | N/A        |
| 18 | `create_journal_entry`      | Accounting | **Write**  | `account.move`          | **Drafts** |

**7 write tools** are intercepted by the Draft Mode Safety System on cloud.
**11 read tools** execute freely on both cloud and local.

---

## 6. Capability: Accounting

### 6.1 Chart of Accounts

```
Tool: get_account_balance
```

Query account balances filtered by code prefix:

| Code Prefix | Account Type        | Example               |
|-------------|---------------------|-----------------------|
| `1`         | Assets              | Cash, Receivables     |
| `2`         | Liabilities         | Payables, Loans       |
| `3`         | Equity              | Retained Earnings     |
| `4`         | Revenue / Income    | Sales, Services       |
| `5`         | Cost of Revenue     | COGS, Direct Costs    |
| `6`         | Expenses            | Rent, Salaries, Utils |

**Example call:**

```json
{
  "tool": "get_account_balance",
  "args": { "account_code": "1" }
}
```

**Response:**

```json
[
  {
    "id": 5,
    "code": "1100",
    "name": "Accounts Receivable",
    "account_type": "asset_receivable",
    "balance": 15000.00
  },
  {
    "id": 7,
    "code": "1200",
    "name": "Bank Account",
    "account_type": "asset_cash",
    "balance": 42500.00
  }
]
```

### 6.2 Journal Entries

```
Tools: list_journal_entries, create_journal_entry
```

Manual GL entries with balanced debit/credit lines.

**List entries:**

```json
{
  "tool": "list_journal_entries",
  "args": { "type": "general", "limit": 10 }
}
```

**Create entry:**

```json
{
  "tool": "create_journal_entry",
  "args": {
    "ref": "Monthly depreciation - March 2026",
    "lines": [
      { "account_id": 42, "name": "Depreciation expense", "debit": 500.00, "credit": 0 },
      { "account_id": 43, "name": "Accumulated depreciation", "debit": 0, "credit": 500.00 }
    ],
    "date": "2026-03-31"
  }
}
```

**Validation:** Odoo requires total debits = total credits. Unbalanced entries are rejected.

---

## 7. Capability: Invoices

### 7.1 Invoice Lifecycle in Odoo

```
Draft → Posted (Confirmed) → Paid
                  ↓
               Cancelled
```

### 7.2 List Invoices

```json
{
  "tool": "list_invoices",
  "args": {
    "type": "out_invoice",
    "state": "posted",
    "limit": 20
  }
}
```

**Parameters:**

| Param   | Values                                 | Default        |
|---------|----------------------------------------|----------------|
| `type`  | `out_invoice` (customer), `in_invoice` (vendor) | `out_invoice` |
| `state` | `draft`, `posted`, `cancel`, `""` (all) | `""`          |
| `limit` | 1-100                                  | 20             |

**Response fields:** id, name, partner_name, move_type, state, amount_total, amount_residual, invoice_date, invoice_date_due, currency

### 7.3 Get Invoice Detail

```json
{
  "tool": "get_invoice",
  "args": { "invoice_id": 42 }
}
```

Returns full invoice including line items:

```json
{
  "id": 42,
  "name": "INV/2026/0042",
  "partner_id": [15, "Acme Corp"],
  "invoice_date": "2026-03-15",
  "amount_total": 2500.00,
  "amount_residual": 2500.00,
  "state": "posted",
  "invoice_line_ids": [
    {
      "name": "Consulting Services",
      "quantity": 10,
      "price_unit": 250.00,
      "price_subtotal": 2500.00,
      "product_id": [5, "Consulting"]
    }
  ]
}
```

### 7.4 Create Invoice

```json
{
  "tool": "create_invoice",
  "args": {
    "partner_id": 15,
    "lines": [
      {
        "name": "Web Development - Phase 1",
        "quantity": 40,
        "price_unit": 150.00
      },
      {
        "name": "Server Hosting (March)",
        "quantity": 1,
        "price_unit": 200.00
      }
    ],
    "type": "out_invoice",
    "date": "2026-03-23"
  }
}
```

**Response:** `{"success": true, "invoice_id": 43}`

The invoice is created in **Draft** state. It must be confirmed before it becomes official.

### 7.5 Confirm Invoice

```json
{
  "tool": "confirm_invoice",
  "args": { "invoice_id": 43 }
}
```

Calls `action_post` on the invoice, moving it from Draft → Posted. This:
- Assigns a sequence number (e.g., `INV/2026/0043`)
- Creates journal entries
- Updates receivable/payable accounts
- **Irreversible** — confirmed invoices cannot be edited

### 7.6 Register Payment

```json
{
  "tool": "register_payment",
  "args": {
    "invoice_id": 43,
    "amount": 6200.00,
    "date": "2026-03-23"
  }
}
```

Uses Odoo's `account.payment.register` wizard:
- Links payment to the invoice
- Reconciles the receivable/payable
- Updates `amount_residual` on the invoice

---

## 8. Capability: Expenses

### 8.1 Expense Lifecycle in Odoo

```
Draft → Reported → Approved → Done (Posted)
                      ↓
                   Refused
```

### 8.2 List Expenses

```json
{
  "tool": "get_expenses",
  "args": { "state": "approved", "limit": 50 }
}
```

**Parameters:**

| Param   | Values                                             | Default |
|---------|----------------------------------------------------|---------|
| `state` | `draft`, `reported`, `approved`, `done`, `refused`, `""` | `""`  |
| `limit` | 1-100                                              | 20      |

**Response fields:** id, name, employee_id, total_amount, state, date, description, product_id

### 8.3 Create Expense

```json
{
  "tool": "create_expense",
  "args": {
    "name": "Client dinner - Q1 review",
    "amount": 185.50,
    "employee_id": 3,
    "date": "2026-03-20",
    "description": "Dinner with Acme Corp at Restaurant XYZ"
  }
}
```

**Response:** `{"success": true, "expense_id": 12}`

The expense is created in **Draft** state. The employee then submits it for manager approval through Odoo's HR Expense workflow.

---

## 9. Capability: Financial Reports

### 9.1 Profit & Loss

```json
{ "tool": "get_profit_loss" }
```

Aggregates accounts by type:

```json
{
  "income_total": 125000.00,
  "expense_total": 87500.00,
  "net_profit": 37500.00,
  "income_accounts": [
    { "code": "4000", "name": "Sales Revenue", "balance": 100000.00 },
    { "code": "4100", "name": "Service Revenue", "balance": 25000.00 }
  ],
  "expense_accounts": [
    { "code": "6000", "name": "Salaries", "balance": 50000.00 },
    { "code": "6100", "name": "Rent", "balance": 12000.00 },
    { "code": "6200", "name": "Office Supplies", "balance": 3500.00 }
  ]
}
```

**Account types classified as income:** `income`, `income_other`
**Account types classified as expense:** `expense`, `expense_depreciation`, `expense_direct_cost`

### 9.2 Balance Sheet

```json
{ "tool": "get_balance_sheet" }
```

```json
{
  "assets_total": 215000.00,
  "liabilities_total": 95000.00,
  "equity_total": 120000.00,
  "asset_accounts": [
    { "code": "1100", "name": "Accounts Receivable", "balance": 15000.00 },
    { "code": "1200", "name": "Bank Account", "balance": 200000.00 }
  ],
  "liability_accounts": [
    { "code": "2000", "name": "Accounts Payable", "balance": 45000.00 },
    { "code": "2100", "name": "Tax Payable", "balance": 50000.00 }
  ],
  "equity_accounts": [
    { "code": "3000", "name": "Share Capital", "balance": 100000.00 },
    { "code": "3100", "name": "Retained Earnings", "balance": 20000.00 }
  ]
}
```

**Asset types:** `asset_receivable`, `asset_cash`, `asset_current`, `asset_non_current`, `asset_prepayments`, `asset_fixed`
**Liability types:** `liability_payable`, `liability_credit_card`, `liability_current`, `liability_non_current`
**Equity types:** `equity`, `equity_unaffected`

### 9.3 Combined Financial Report

```json
{ "tool": "get_financial_report" }
```

Returns all sections in one call:

```json
{
  "profit_loss": { "income_total": ..., "expense_total": ..., "net_profit": ... },
  "balance_sheet": { "assets_total": ..., "liabilities_total": ..., "equity_total": ... },
  "receivables": [ { "partner": "Acme", "amount_due": 5000, "overdue_days": 15 } ],
  "payables": [ { "partner": "Supplier X", "amount_due": 3000 } ],
  "recent_expenses": [ { "name": "Travel", "amount": 500, "state": "approved" } ]
}
```

### 9.4 Weekly Accounting Summary

```json
{ "tool": "weekly_accounting_summary" }
```

Returns activity for the last 7 days:

```json
{
  "period": { "from": "2026-03-16", "to": "2026-03-23" },
  "invoices": {
    "customer_count": 5,
    "customer_total": 12500.00,
    "vendor_count": 3,
    "vendor_total": 4200.00,
    "items": [...]
  },
  "payments": {
    "received_count": 4,
    "received_total": 9800.00,
    "sent_count": 2,
    "sent_total": 3100.00,
    "items": [...]
  },
  "expenses": {
    "count": 3,
    "total": 950.00,
    "items": [...]
  },
  "journal_entries": {
    "count": 2,
    "items": [...]
  }
}
```

This is used by:
- **Audit Agent** for weekly CEO briefings
- **CEO Dashboard** at `/ceo` → Accounting section
- **OdooWatcher** for periodic financial health checks

---

## 10. Draft Mode Safety Integration

### 10.1 How It Works

On the **Cloud VM** (`SYNC_ROLE=cloud`), all write operations are intercepted by the `DraftModeController`:

```
Cloud Agent calls:  draft_controller.create_invoice(partner_id=15, lines=[...])
                          │
                          ▼
                    PermissionManager.can("create_invoice")
                          │
                          ▼ DENIED (cloud role)
                    _create_draft()
                          │
                    ┌─────┴──────────────────────────────────────────────┐
                    │                                                     │
                    ▼                                                     ▼
    vault/Drafts/draft_create_invoice_...json        AI_Employee_Vault/Needs_Approval/
    (machine-readable replay payload)                 Approval_draft_create_invoice_...md
                                                      (human-readable approval request)
                    │                                                     │
                    └─────────────────────┬───────────────────────────────┘
                                          │
                                          ▼ Git Sync (5 min)
                                          │
                                    Local Machine
                                          │
                                          ▼
                                 Dashboard /approvals
                                          │
                                    Human: APPROVE
                                          │
                                          ▼
                           draft_controller.execute_approved(draft_id)
                                          │
                                          ▼
                                 _replay_create_invoice()
                                          │
                                          ▼
                               OdooClient.create_invoice()
                                          │
                                          ▼
                                      Odoo ERP
```

### 10.2 Action-to-Permission Mapping

| MCP Tool               | Permission Action      | Cloud Behavior          |
|-------------------------|------------------------|-------------------------|
| `create_invoice`        | `create_invoice`       | Drafts to vault         |
| `confirm_invoice`       | `confirm_invoice`      | Drafts to vault         |
| `register_payment`      | `register_payment`     | Drafts to vault         |
| `create_journal_entry`  | `create_journal_entry` | Drafts to vault         |
| `write_odoo_record`     | `write_odoo_record`    | Drafts to vault         |
| `create_expense`        | *(not yet registered)* | Executes directly       |
| `create_customer`       | *(not yet registered)* | Executes directly       |
| `list_invoices`         | `read_odoo`            | Executes directly       |
| `get_profit_loss`       | `read_odoo`            | Executes directly       |

### 10.3 Draft File Format

**vault/Drafts/draft_create_invoice_20260323_095500_a1b2c3d4.json:**

```json
{
  "draft_id": "draft_create_invoice_20260323_095500_a1b2c3d4",
  "action": "create_invoice",
  "category": "financial",
  "status": "pending",
  "created_at": "2026-03-23T09:55:00+00:00",
  "payload": {
    "partner_id": 15,
    "lines": [
      { "name": "Consulting", "quantity": 10, "price_unit": 250.00 }
    ],
    "type": "out_invoice",
    "date": "2026-03-23"
  },
  "source_agent": "odoo_agent",
  "risk_level": "high",
  "preview": "Invoice for partner #15: 2500.00"
}
```

---

## 11. OdooWatcher — Cloud Automation

### 11.1 What It Does

The `OdooWatcher` runs every 10 minutes on the cloud VM and:

1. Calls `OdooAgent.process_accounting(max_items=20)`
2. Detects overdue invoices (posted, unpaid, past due date)
3. Writes overdue alerts to `vault/Needs_Action/Overdue_<invoice>.md`
4. Generates financial summaries
5. Saves audit logs to `ai_employee/logs/`

### 11.2 Overdue Alert Format

File: `vault/Needs_Action/Overdue_INV_2026_0042.md`

```markdown
# Overdue Invoice Alert

| Field         | Value                    |
|---------------|--------------------------|
| Invoice       | INV/2026/0042            |
| Customer      | Acme Corp                |
| Amount Due    | $2,500.00                |
| Due Date      | 2026-03-08               |
| Days Overdue  | 15                       |
| Status        | posted                   |

## Recommended Action

Follow up with Acme Corp on payment for invoice INV/2026/0042.
```

### 11.3 Configuration

| Env Variable              | Default | Description                    |
|---------------------------|---------|--------------------------------|
| `WATCHER_ODOO_INTERVAL`   | 600     | Poll interval in seconds (10m) |
| `CIRCUIT_BREAKER_THRESHOLD`| 3      | Failures before trip           |
| `CIRCUIT_BREAKER_TIMEOUT`  | 60     | Seconds before recovery        |

---

## 12. Testing Each Capability

### 12.1 Test from CLI (Python)

```bash
cd /opt/ai-employee
source venv/bin/activate
source /etc/ai-employee/.env

python3 << 'PYEOF'
from ai_employee.integrations.odoo_client import OdooClient
import json

client = OdooClient(
    url="http://127.0.0.1:8069",
    db="ai_employee_accounting",
    username="admin",
    password="<your-password>",
)

# ── Test 1: Authentication ────────────────────────────────────
uid = client._ensure_authenticated()
print(f"1. AUTH: UID = {uid}")
assert uid and uid > 0, "Authentication failed"

# ── Test 2: List Invoices ─────────────────────────────────────
invoices = client.get_invoices(type="out_invoice", limit=5)
print(f"2. INVOICES: Found {len(invoices)} customer invoices")

# ── Test 3: List Customers ────────────────────────────────────
customers = client.get_customers(limit=5)
print(f"3. CUSTOMERS: Found {len(customers)} customers")

# ── Test 4: List Expenses ─────────────────────────────────────
expenses = client.get_expenses(limit=5)
print(f"4. EXPENSES: Found {len(expenses)} expenses")

# ── Test 5: Profit & Loss ─────────────────────────────────────
pnl = client.get_profit_loss_summary()
if pnl:
    print(f"5. P&L: Income={pnl.get('income_total',0)}, "
          f"Expenses={pnl.get('expense_total',0)}, "
          f"Net={pnl.get('net_profit',0)}")
else:
    print("5. P&L: No data (chart of accounts may need setup)")

# ── Test 6: Balance Sheet ─────────────────────────────────────
bs = client.get_balance_sheet_summary()
if bs:
    print(f"6. BS: Assets={bs.get('assets_total',0)}, "
          f"Liabilities={bs.get('liabilities_total',0)}, "
          f"Equity={bs.get('equity_total',0)}")
else:
    print("6. BS: No data")

# ── Test 7: Account Balances ──────────────────────────────────
accounts = client.get_account_balance(account_code="")
print(f"7. ACCOUNTS: Found {len(accounts)} accounts in chart")

# ── Test 8: Journal Entries ───────────────────────────────────
entries = client.get_journal_entries(limit=5)
print(f"8. JOURNAL: Found {len(entries)} entries")

# ── Test 9: Payments ──────────────────────────────────────────
payments = client.get_payments(limit=5)
print(f"9. PAYMENTS: Found {len(payments)} payments")

# ── Test 10: Financial Report (combined) ──────────────────────
report = client.get_financial_report()
sections = [k for k in report.keys()] if report else []
print(f"10. REPORT: Sections = {sections}")

# ── Test 11: Weekly Summary ───────────────────────────────────
weekly = client.get_weekly_accounting_summary()
print(f"11. WEEKLY: Keys = {list(weekly.keys()) if weekly else 'empty'}")

print("\n=== All tests completed ===")
PYEOF
```

### 12.2 Test MCP Server (stdio)

```bash
cd /opt/ai-employee

# Send a JSON-RPC request via stdin
echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_invoices","arguments":{"type":"out_invoice","limit":5}},"id":1}' \
    | python3 -m ai_employee.integrations.odoo_mcp_server
```

### 12.3 Test MCP via Tool Registry

```bash
python3 << 'PYEOF'
from ai_employee.integrations.tool_registry import ToolRegistry

registry = ToolRegistry()
tools = registry.get_all_tools()

odoo_tools = [t for t in tools if "odoo" in t.server_name.lower()
              or t.category.value == "ACCOUNTING"]
print(f"Odoo tools discovered: {len(odoo_tools)}")
for t in odoo_tools:
    print(f"  - {t.name} ({t.server_name})")
PYEOF
```

### 12.4 Test OdooAgent Pipeline

```bash
python3 << 'PYEOF'
from ai_employee.config.settings import Settings
from ai_employee.integrations.odoo_client import OdooClient
from ai_employee.agents.odoo_agent import OdooAgent

settings = Settings.load()
client = OdooClient(
    url=settings.odoo_url, db=settings.odoo_db,
    username=settings.odoo_username, password=settings.odoo_password,
)

agent = OdooAgent(
    odoo_client=client,
    output_dir=str(settings.needs_action_dir),
    log_dir=str(settings.log_dir),
)

if agent.enabled:
    results = agent.process_accounting(max_items=10)
    print(f"Agent results: {len(results)} items")
    for r in results:
        print(f"  - {r.get('action')}: {r.get('details', '')[:80]}")
else:
    print("OdooAgent disabled (check credentials)")
PYEOF
```

---

## 13. Claude Code Integration

### 13.1 Using Odoo Tools in Claude Code

Once the MCP server is configured in Claude Code's MCP settings, you can directly use the tools:

```
User:  "Show me all unpaid customer invoices"
Claude: [calls list_invoices with state="posted", type="out_invoice"]
        → Returns: 5 invoices, total $25,000 receivable

User:  "Generate a P&L report"
Claude: [calls get_profit_loss]
        → Returns: Income $125K, Expenses $87.5K, Net Profit $37.5K

User:  "Create an invoice for Acme Corp, 10 hours of consulting at $250/hr"
Claude: [calls create_invoice with partner_id, lines]
        → Returns: Invoice #INV/2026/0043 created (draft)

User:  "What expenses need approval?"
Claude: [calls get_expenses with state="reported"]
        → Returns: 3 expenses pending manager approval
```

### 13.2 MCP Configuration for Claude Code

Add to your Claude Code MCP settings (`.claude/mcp_config.json`):

```json
{
  "mcpServers": {
    "odoo-accounting": {
      "command": "python",
      "args": ["-m", "ai_employee.integrations.odoo_mcp_server"],
      "cwd": "D:\\Quarter-4\\Hackathon_0\\AI-Employee",
      "env": {
        "ODOO_URL": "http://127.0.0.1:8069",
        "ODOO_DB": "ai_employee_accounting",
        "ODOO_USERNAME": "admin",
        "ODOO_PASSWORD": "${ODOO_PASSWORD}"
      }
    }
  }
}
```

For cloud deployment where Odoo is remote:

```json
{
  "mcpServers": {
    "odoo-accounting": {
      "command": "python",
      "args": ["-m", "ai_employee.integrations.odoo_mcp_server"],
      "cwd": "/opt/ai-employee",
      "env": {
        "ODOO_URL": "http://127.0.0.1:8069",
        "ODOO_DB": "ai_employee_accounting"
      }
    }
  }
}
```

---

## 14. Troubleshooting

### 14.1 Connection Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ConnectionRefusedError` | Odoo not running | `sudo systemctl start odoo` |
| `authenticate returns False` | Wrong credentials | Check `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD` |
| `Database not found` | Wrong db name | `sudo -u postgres psql -l` to list databases |
| `Access Denied` | User lacks permissions | Check user's access rights in Odoo |
| `Timeout after 30s` | Odoo overloaded | Check workers, increase `limit_time_real` |
| `Module not found` | Module not installed | Run `-i account,hr_expense --stop-after-init` |
| `JSON-RPC error: -32602` | Invalid parameters | Check field names match Odoo 18 schema |

### 14.2 MCP Server Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| MCP server won't start | Missing dependencies | `pip install mcp dotenv` in venv |
| `ModuleNotFoundError: mcp` | Wrong Python env | Use `/opt/ai-employee/venv/bin/python` |
| Tools not discovered | Import error | Check `from ai_employee.integrations.odoo_client import OdooClient` |
| Slow tool responses | Large result sets | Reduce `limit` parameter |
| MCP returns empty | OdooClient auth failed | Test auth manually (Section 12.1) |

### 14.3 Financial Data Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| P&L shows all zeros | No transactions posted | Create and confirm test invoices |
| Balance sheet empty | Chart of accounts not set | Install fiscal localization package |
| Expenses fail to create | `hr_expense` not installed | Install Expenses module |
| Payment wizard fails | Invoice not posted | Confirm the invoice first |
| Account balance wrong | Unposted entries | Check for draft journal entries |

### 14.4 Log Files

| Component | Log Path |
|-----------|----------|
| Odoo server | `/var/log/odoo/odoo-server.log` |
| MCP server | `/opt/ai-employee/ai_employee/logs/mcp_odoo.log` |
| OdooWatcher | `/opt/ai-employee/ai_employee/logs/cloud_watchers.log` |
| OdooAgent | `/opt/ai-employee/ai_employee/logs/odoo_agent_*.json` |
| Audit trail | `/opt/ai-employee/ai_employee/logs/audit_log.json` |

### 14.5 Diagnostic Script

```bash
#!/usr/bin/env bash
echo "=== Odoo MCP Integration Diagnostics ==="

echo -n "1. PostgreSQL: "
sudo -u postgres pg_isready -q && echo "OK" || echo "FAIL"

echo -n "2. Odoo service: "
systemctl is-active --quiet odoo && echo "OK" || echo "FAIL"

echo -n "3. Odoo HTTP: "
curl -sf http://127.0.0.1:8069/web/webclient/version_info > /dev/null && echo "OK" || echo "FAIL"

echo -n "4. Odoo JSON-RPC: "
RESP=$(curl -sf -X POST http://127.0.0.1:8069/jsonrpc \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"call","params":{"service":"common","method":"version","args":[]},"id":1}')
echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['server_version'])" 2>/dev/null || echo "FAIL"

echo -n "5. MCP server process: "
pgrep -f "odoo_mcp_server" > /dev/null && echo "OK" || echo "NOT RUNNING"

echo -n "6. Nginx proxy: "
curl -sf -o /dev/null https://odoo.yourdomain.com && echo "OK" || echo "FAIL"

echo -n "7. Database exists: "
sudo -u postgres psql -lqt | grep -q "ai_employee_accounting" && echo "OK" || echo "FAIL"

echo -n "8. Accounting module: "
sudo -u postgres psql -d ai_employee_accounting -tAc \
    "SELECT count(*) FROM ir_module_module WHERE name='account' AND state='installed';" 2>/dev/null | grep -q "1" && echo "OK" || echo "NOT INSTALLED"

echo "=== Done ==="
```

---

## 15. End-to-End Walkthrough

### Scenario: CEO Requests Weekly Financial Review

**Step 1 — Cloud OdooWatcher polls (automatic, every 10 min)**

```
OdooWatcher.poll()
  → OdooAgent.process_accounting()
    → check_overdue_invoices()
       Found: INV/2026/0042 — Acme Corp — $2,500 — 15 days overdue
       → writes vault/Needs_Action/Overdue_INV_2026_0042.md
    → get_financial_summary()
       → OdooClient.get_profit_loss_summary()
       → OdooClient.get_balance_sheet_summary()
       → saves to logs/odoo_agent_20260323.json
```

**Step 2 — Git sync delivers alerts to local machine (5 min cycle)**

```
git_sync.sh (cloud) → git push → GitHub
git_sync.sh (local) → git pull → vault/Needs_Action/Overdue_INV_2026_0042.md appears
```

**Step 3 — CEO opens dashboard on local machine**

```
http://localhost:8080/ceo

CEO Dashboard shows:
  Accounting Section:
    P&L:     Income $125K | Expenses $87.5K | Net $37.5K
    BS:      Assets $215K | Liabilities $95K | Equity $120K
    Overdue: 1 invoice ($2,500 — Acme Corp)

  Weekly Summary:
    5 invoices created ($12,500)
    4 payments received ($9,800)
    3 expenses logged ($950)
```

**Step 4 — CEO decides to send payment reminder (via dashboard)**

```
CEO clicks "Send Reminder" for Acme Corp overdue invoice
  → draft_controller.send_email(to="acme@example.com", subject="Payment Reminder")
  → On local: PermissionManager allows "send_email" (local role)
  → GmailSender.send() executes
  → Email sent to Acme Corp
```

**Step 5 — Acme Corp pays, OdooWatcher detects next cycle**

```
OdooWatcher.poll()
  → check_overdue_invoices()
  → INV/2026/0042 now has amount_residual = 0 (paid)
  → No overdue alert generated
  → vault/Needs_Action/Overdue_INV_2026_0042.md no longer appears
```

---

## Summary

| Component               | Location                                         | Purpose                    |
|-------------------------|--------------------------------------------------|----------------------------|
| `odoo_client.py`         | `ai_employee/integrations/`                     | JSON-RPC wrapper (20+ methods) |
| `odoo_mcp_server.py`     | `ai_employee/integrations/`                     | FastMCP server (18 tools)  |
| `odoo_agent.py`          | `ai_employee/agents/`                           | Pipeline agent             |
| `odoo_mcp_config.json`   | `ai_employee/integrations/`                     | MCP client config          |
| `process_manager.config` | `ai_employee/`                                  | Supervisord config         |
| `cloud_watchers.py`      | `ai_employee/`                                  | OdooWatcher (10-min poll)  |
| `permission_manager.py`  | `ai_employee/brain/`                            | Action permissions         |
| `draft_mode_controller.py`| `ai_employee/brain/`                           | Safety gate for writes     |

**Total capabilities:** 18 MCP tools across Accounting, Invoices, Expenses, and Financial Reports — with Draft Mode Safety on all write operations.
