# Security Disclosure

> AI Employee — Platinum Tier | Agent Factory Hackathon 0

This document describes how the AI Employee handles credentials, secrets,
permissions, and sensitive data. It covers the security architecture,
credential storage practices, and responsible automation guidelines.

---

## 1. Credential Storage

### Environment Variables

All credentials are stored in a `.env` file at the project root and loaded
via `python-dotenv`. The `.env` file is **never committed** to version control.

```
.env              ← Real credentials (gitignored)
.env.example      ← Template with placeholder values (committed)
```

**Gitignore enforcement** (from `.gitignore`):

```gitignore
.env
credentials.json
token.json
token.pickle
*.token
*.key
```

### Credential Categories

| Credential | Storage | Access Pattern |
|-----------|---------|---------------|
| Anthropic API Key | `.env` → `ANTHROPIC_API_KEY` | Loaded once at boot via `Settings.load()` |
| Gmail OAuth2 | `credentials.json` + `token.json` | Files on disk, gitignored, token auto-refreshed |
| Gmail App Password | `.env` → `EMAIL_PASSWORD` | SMTP fallback only |
| LinkedIn | `.env` → `LINKEDIN_EMAIL/PASSWORD` | Loaded at boot |
| Odoo XML-RPC | `.env` → `ODOO_URL/DB/USERNAME/PASSWORD` | Loaded at boot |
| Meta Graph API | `.env` → `META_ACCESS_TOKEN/PAGE_ID/IG_USER_ID` | Loaded at boot |
| Twitter API v2 | `.env` → 5 variables (bearer + OAuth1) | Loaded at boot |
| WhatsApp Business | `.env` → `WHATSAPP_TOKEN/PHONE_NUMBER_ID` | Loaded at boot |

### SecretsManager

The `ai_employee/brain/secrets_manager.py` module provides centralized
credential management:

- Loads all secrets from `Settings` at boot
- Tracks which secrets are available
- Controls access per role (CEO vs Cloud AI)
- Never logs or prints credential values

### No Hardcoded Secrets

A codebase search for hardcoded secrets confirms: **zero credentials are
hardcoded in source code**. All sensitive values flow through environment
variables.

---

## 2. OAuth2 Flows

### Gmail API (OAuth2)

The Gmail integration uses Google's OAuth2 flow:

1. `credentials.json` — OAuth2 client configuration (downloaded from Google Cloud Console)
2. First run triggers browser-based consent flow
3. `token.json` — Stores refresh token for subsequent runs
4. Token auto-refreshes when expired
5. Both files are **gitignored** and stay on the local machine only

**Scopes requested:**
- `https://www.googleapis.com/auth/gmail.readonly` (read inbox)
- `https://www.googleapis.com/auth/gmail.send` (send emails)

### Meta Graph API

Uses a long-lived Page Access Token:
- Token stored in `.env` as `META_ACCESS_TOKEN`
- Token must be refreshed manually when it expires (60-day tokens)
- Only Page-level permissions are used (no user data access)

### Twitter API v2

Uses OAuth 1.0a for user-context actions:
- Bearer token for read-only endpoints
- API key + secret + access token + access token secret for write actions
- All stored in `.env`, never in code

---

## 3. Permission Boundaries

### Human-in-the-Loop Approval System

The approval system prevents unauthorized actions:

```
┌─────────────────────────────────────────────────┐
│              PERMISSION MATRIX                    │
├─────────────────────────────────────────────────┤
│                                                   │
│  AUTO-EXECUTE (no approval needed):               │
│    ✓ Read inbox / fetch messages                  │
│    ✓ Classify and prioritize tasks                │
│    ✓ Generate draft responses                     │
│    ✓ Fetch metrics and reports                    │
│    ✓ Move files between vault directories         │
│    ✓ Generate CEO briefings                       │
│                                                   │
│  REQUIRES APPROVAL:                               │
│    ✗ Send emails to new/unknown recipients        │
│    ✗ Post to social media (Facebook, Twitter, IG) │
│    ✗ Accept LinkedIn connections                  │
│    ✗ Confirm or modify Odoo invoices              │
│    ✗ Register payments                            │
│    ✗ Any action flagged as high-risk              │
│                                                   │
└─────────────────────────────────────────────────┘
```

### Approval Workflow

1. Agent determines action requires approval
2. Creates file in `AI_Employee_Vault/Needs_Approval/`
3. Dashboard shows pending approval on `/approvals` page
4. Human reviews and approves/rejects via Dashboard API
5. Approved actions re-enter execution pipeline
6. Rejected actions are logged and archived
7. Unapproved actions expire after 24 hours

### Role-Based Access Control

The `RoleManager` enforces role-based access:

| Role | Permissions | Use Case |
|------|------------|----------|
| `ceo` | Full access to all agents and data domains | Local machine operator |
| `cloud_ai` | Restricted — no direct send/post, draft-only mode | Cloud VM automation |

The cloud role can only create drafts; actual sending/posting requires
the local CEO role to approve.

---

## 4. Rate Limiting

Anti-spam safeguards prevent API abuse:

| Service | Limit | Configurable Via |
|---------|-------|-----------------|
| LinkedIn messages | 15/hour, 80/day | `LINKEDIN_MAX_MSG_PER_HOUR/DAY` |
| LinkedIn connections | 25/day | `LINKEDIN_MAX_CONN_PER_DAY` |
| Gmail processing | 10 emails/cycle | Hardcoded safety cap |
| Meta posting | Approval-gated | Human review required |
| Twitter posting | Approval-gated | Human review required |

---

## 5. Circuit Breakers

The `StatusAggregator` implements the circuit breaker pattern for all
services and agents:

- **Closed** (normal): Requests flow through normally
- **Open** (tripped): After N consecutive failures, all requests are blocked
- **Half-open** (recovering): After timeout, one test request is allowed

Default configuration:
- `CIRCUIT_BREAKER_THRESHOLD=3` — Open after 3 consecutive failures
- `CIRCUIT_BREAKER_TIMEOUT=60` — Retry after 60 seconds

This prevents cascading failures when an external API is down.

---

## 6. Audit Logging

### AuditLogger

All system actions are recorded in a structured audit trail:

```
ai_employee/logs/audit_YYYYMMDD.json
```

**Event types logged:**
- `SYSTEM_BOOT` / `SYSTEM_SHUTDOWN`
- `CYCLE_STARTED` / `CYCLE_COMPLETED`
- `TASK_RECEIVED` / `TASK_COMPLETED` / `TASK_FAILED`
- `AGENT_CALLED` / agent result
- `TOOL_USED` (MCP tool invocations)
- `APPROVAL_REQUESTED`
- `ERROR_OCCURRED`
- `RETRY_ATTEMPTED` / `FALLBACK_USED`

Each entry includes:
- Timestamp (UTC)
- Event type and severity
- Source agent/service
- Action details
- Duration (ms)
- Error information (if applicable)

### Log Retention

- Runtime logs: `ai_employee/logs/*.log` (gitignored)
- Audit logs: `ai_employee/logs/*.json` (gitignored)
- Watcher results: `ai_employee/logs/watcher_results.json`
- Recommended retention: **90+ days** per hackathon guidelines

### SystemLogger

Structured event logging with queryable API:
- Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
- In-memory capture for dashboard display
- Filter by source, level, time range

---

## 7. Network Security

### Outbound Connections Only

The AI Employee makes outbound HTTPS connections only:

| Endpoint | Purpose | Port |
|----------|---------|------|
| `api.anthropic.com` | Claude AI reasoning | 443 |
| `gmail.googleapis.com` | Email processing | 443 |
| `graph.facebook.com` | Meta social media | 443 |
| `api.twitter.com` | Twitter/X | 443 |
| Odoo server (configurable) | ERP accounting | 8069 |

### Dashboard Binding

The web dashboard binds to `127.0.0.1:8080` by default (localhost only).
It is **not exposed to the internet** unless explicitly configured via
`DASHBOARD_HOST=0.0.0.0`.

### MCP Server Communication

MCP servers communicate via stdio (stdin/stdout), not network sockets.
They run as local subprocesses — no network ports exposed.

---

## 8. Git Sync Security

### What Gets Synced

```
SYNCED (committed to git):
  vault/Inbox/*.md              — Task files (no secrets)
  vault/Needs_Action/*.md       — Triaged tasks
  vault/Done/*.md               — Completed tasks
  vault/Reports/*.md            — CEO briefings
  AI_Employee_Vault/            — Approval workflow

NOT SYNCED (gitignored):
  .env                          — Credentials
  credentials.json              — Gmail OAuth2
  token.json                    — Gmail refresh token
  vault/memory.db               — Runtime state
  ai_employee/logs/             — Audit logs
```

### Conflict Resolution

The `scripts/conflict_resolver.py` handles git merge conflicts with
deterministic rules:
- `Inbox/` — keep both versions (no data loss)
- `Needs_Action/` — keep the most recent version
- `Done/` — keep both versions
- `Needs_Approval/` — keep the local version (human takes precedence)

---

## 9. Data Privacy

### What the AI Employee Reads

- Email subjects and bodies (Gmail API)
- LinkedIn message content
- Social media metrics (public data)
- Odoo financial data (your own ERP)

### What the AI Employee Does NOT Do

- Store raw email content long-term (processed and moved to Done/)
- Access contacts beyond what's needed for the current task
- Share data with third parties
- Retain API responses beyond the current cycle
- Access personal files outside the vault directory

---

## 10. Responsible Automation Guidelines

Per the hackathon document's ethics guidelines, the AI Employee:

**WILL NOT autonomously act on:**
- Emotional or personal relationship matters
- Legal decisions or contract signing
- Medical or health-related actions
- Financial transactions above approval thresholds
- Communications with new/unknown parties without approval

**Oversight schedule recommended:**
- **Daily**: Check the Dashboard for pending approvals
- **Weekly**: Review CEO briefing reports and audit logs
- **Monthly**: Review approval history and agent performance
- **Quarterly**: Rotate API keys and review security configuration

---

## 11. Incident Response

If a security incident is suspected:

1. **Stop the system**: `./startup.sh stop` or `Ctrl+C`
2. **Check audit logs**: `ai_employee/logs/audit_*.json`
3. **Review approval history**: Dashboard → Approvals
4. **Rotate credentials**: Update `.env` with new API keys
5. **Review git history**: `git log --oneline` for unexpected commits
6. **Re-authenticate OAuth2**: Delete `token.json` and re-run

---

## Contact

For security concerns related to this hackathon submission, please
open an issue on the GitHub repository.
