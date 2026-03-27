# Platinum Tier — Hybrid Architecture

> Local Brain + Cloud Muscle: The AI Employee runs its intelligence locally under
> human supervision while cloud infrastructure handles always-on automation.

---

## 1. Design Principles

| Principle | Rationale |
|-----------|-----------|
| **Brain stays local** | Claude Code + approval decisions remain on the operator's machine — zero trust delegation |
| **Automation lives in cloud** | Watchers, schedulers, MCP servers, Odoo run 24/7 on a VM |
| **GitHub is the nervous system** | All sync flows through git — auditable, revertible, conflict-safe |
| **Vault is the shared state** | The `vault/` directory is the single source of truth, synced via git |
| **Graceful degradation** | Cloud operates autonomously; local enhances with intelligence when online |

---

## 2. Tier Comparison

| Capability | Silver | Gold | **Platinum** |
|------------|--------|------|-------------|
| Pipeline orchestration | Single script | `AIEmployee` class | Split orchestration (cloud continuous + local intelligent) |
| Uptime | Manual runs | While terminal open | **24/7 cloud + on-demand local** |
| Approvals | File-based | Dashboard + API | **Local dashboard with cloud queue** |
| MCP Servers | N/A | Local subprocesses | **Cloud HTTP microservices** |
| Odoo | Local client | Local JSON-RPC | **Cloud-hosted Odoo + MCP** |
| Sync | N/A | N/A | **GitHub push/pull with conflict resolution** |
| Intelligence | Template | Claude API | **Local Claude Code (brain) + cloud Claude API (agents)** |

---

## 3. Component Placement

### 3.1 Local Machine — The Brain

```
LOCAL (Operator's Workstation)
├── Claude Code CLI .................. Human-in-the-loop intelligence
├── Approval Dashboard ............... FastAPI on localhost:8080
│   ├── /approvals ................... Review & approve/reject
│   ├── /ceo ......................... Executive dashboard
│   └── /briefings ................... Weekly reports
├── Decision Engine .................. Task classification + routing
├── Ralph Loop ....................... Autonomous task execution (on-demand)
├── Vault (local copy) ............... Git-synced working directory
│   ├── Inbox/ ....................... Receives tasks from cloud
│   ├── Needs_Action/ ................ Human review items
│   ├── Needs_Approval/ .............. Pending approvals
│   └── Done/ ........................ Completed tasks
├── Sync Agent ....................... Git pull/push daemon
└── Health Dashboard ................. Monitors cloud VM status
```

**Why local:**
- Approvals require human presence — latency to a human is the bottleneck, not the machine
- Claude Code provides interactive debugging, ad-hoc task execution
- Decision engine benefits from operator context (e.g., "ignore this sender")
- Sensitive credentials (Gmail OAuth tokens) stay on local disk
- CEO dashboard is for the operator's eyes only

### 3.2 Cloud VM — The Muscle

```
CLOUD VM (Always-On Ubuntu/Debian)
├── Cloud Orchestrator ............... Headless AIEmployee (continuous loop)
│   ├── phase_gmail() ................ Poll Gmail every 5 min
│   ├── phase_linkedin() ............. Poll LinkedIn every 5 min
│   ├── phase_meta() ................. Poll Meta every 5 min
│   ├── phase_twitter() .............. Poll Twitter every 5 min
│   ├── phase_odoo() ................. Poll Odoo every 5 min
│   ├── phase_triage() ............... Auto-classify incoming tasks
│   └── phase_audit() ................ Weekly briefing generation
│
├── MCP Servers (HTTP) ............... Always-on tool servers
│   ├── communication-server ......... :9001 (Gmail + LinkedIn tools)
│   ├── meta-social-server ........... :9002 (Facebook + Instagram tools)
│   ├── twitter-social-server ........ :9003 (Twitter/X tools)
│   └── odoo-accounting-server ....... :9004 (Odoo JSON-RPC tools)
│
├── Odoo Instance .................... :8069 (PostgreSQL-backed ERP)
│   ├── Accounting module
│   ├── CRM module
│   └── Inventory module
│
├── Monitoring Stack
│   ├── Health Monitor ............... Watchdog for all services
│   ├── Audit Logger ................. NDJSON append-only trail
│   ├── Error Handler ................ Classify + recover
│   ├── Retry Manager ................ Backoff + budget
│   └── Fallback System .............. Chain routing
│
├── Vault (cloud copy) ............... Git-synced working directory
│   ├── Inbox/ ....................... Cloud writes new tasks here
│   ├── Needs_Approval/ .............. Items awaiting human decision
│   └── Done/ ........................ Cloud writes completed tasks here
│
├── Sync Agent ....................... Git push/pull daemon
└── Nginx Reverse Proxy .............. TLS termination for MCP + Odoo
```

**Why cloud:**
- 24/7 polling of Gmail, LinkedIn, Meta, Twitter, Odoo — no missed events
- MCP servers need persistent connections to external APIs
- Odoo is a heavy PostgreSQL-backed app — dedicated resources
- Monitoring must run continuously to detect failures
- Error recovery needs immediate response (no waiting for human to open laptop)

### 3.3 GitHub — The Nervous System

```
GITHUB REPOSITORY
├── main branch ...................... Production state
│   ├── vault/ ....................... Synced task state
│   │   ├── Inbox/
│   │   ├── Needs_Action/
│   │   ├── Needs_Approval/
│   │   ├── Done/
│   │   └── Reports/
│   ├── ai_employee/ ................. Application code
│   ├── logs/ ........................ Audit trail (NDJSON)
│   └── config/ ...................... Non-secret configuration
│
├── cloud/auto branch ................ Cloud automated commits
│   └── Auto-commits from cloud orchestrator
│
└── GitHub Actions
    ├── sync-check.yml ............... Validates vault consistency
    ├── deploy-cloud.yml ............. SSH deploy to VM on code push
    └── health-ping.yml .............. Cron job pinging cloud /health
```

---

## 4. Service Architecture

### 4.1 Cloud Orchestrator

The cloud runs a **headless** variant of `AIEmployee` — same class, different mode:

```python
# cloud_main.py — Cloud entry point
class CloudOrchestrator(AIEmployee):
    """Headless orchestrator — no dashboard, no Ralph, no local approval UI."""

    def __init__(self, settings):
        super().__init__(settings)
        self.mode = "cloud"
        # Disable local-only features
        self._dashboard = None
        self._ralph = None

    def phase_check_approvals(self):
        """Cloud can't approve — it queues to Needs_Approval/ and syncs."""
        items = self.approval_manager.get_pending()
        for item in items:
            self._write_approval_file(item)  # vault/Needs_Approval/
            self._sync_agent.push("approval queued")

    def _on_phase_complete(self, phase_name, result):
        """After each phase, commit + push vault changes."""
        self._sync_agent.commit_and_push(f"cloud: {phase_name} complete")
```

### 4.2 MCP Servers — HTTP Migration

Current: subprocesses communicating via stdio JSON-RPC 2.0.
Platinum: HTTP microservices behind Nginx.

```
Current (Gold):
  AIEmployee → subprocess.Popen → stdin/stdout → FastMCP

Platinum (Cloud):
  Local AIEmployee → HTTPS :443 → Nginx → :9001-9004 → FastMCP (HTTP transport)
  Cloud Orchestrator → localhost:9001-9004 → FastMCP (HTTP transport)
```

Each MCP server wraps the existing FastMCP with an HTTP adapter:

```python
# mcp_http_adapter.py
from fastapi import FastAPI
from ai_employee.integrations.mcp_meta_server import mcp as meta_mcp

app = FastAPI()

@app.post("/tools/call")
async def call_tool(request: ToolCallRequest):
    """HTTP wrapper around FastMCP tool execution."""
    tool = meta_mcp._tool_manager._tools[request.tool_name]
    result = await tool.run(request.arguments)
    return {"result": {"content": [{"text": str(result)}]}}

@app.get("/health")
async def health():
    return {"status": "ok", "server": "meta-social"}
```

### 4.3 MCP Router — Dual Mode

The `MCPRouter` gains a transport abstraction:

```python
class MCPTransport(Protocol):
    async def call(self, server: str, request: ToolCallRequest) -> ToolCallResult: ...

class StdioTransport(MCPTransport):
    """Gold tier — local subprocess."""

class HTTPTransport(MCPTransport):
    """Platinum tier — cloud HTTP endpoint."""
    def __init__(self, base_urls: dict[str, str]):
        # {"meta-social": "https://cloud-vm:443/meta", ...}
        self.base_urls = base_urls

class MCPRouter:
    def __init__(self, transport: MCPTransport):
        self.transport = transport
```

### 4.4 Approval Flow

```
Cloud detects item needing approval
  → Writes to vault/Needs_Approval/approve_<id>.md
  → git commit + push
  → GitHub notifies (webhook or poll)

Local sync agent pulls
  → Approval dashboard shows new item
  → Human approves/rejects via localhost:8080
  → Writes decision to vault/Needs_Approval/approve_<id>.md (status: approved)
  → git commit + push

Cloud sync agent pulls
  → Reads approved file
  → Executes approved action (send email, post tweet, etc.)
  → Moves file to vault/Done/
  → git commit + push
```

---

## 5. Network Topology

```
┌─────────────────────────────────────────────────────────────┐
│                     CLOUD VM (Ubuntu)                        │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Communication │  │  Meta Social  │  │   Twitter    │      │
│  │  MCP :9001    │  │  MCP :9002   │  │  MCP :9003   │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                  │                  │              │
│  ┌──────┴──────────────────┴──────────────────┴───────┐     │
│  │              Nginx Reverse Proxy :443               │     │
│  │         (TLS + API Key Authentication)              │     │
│  └──────────────────────┬─────────────────────────────┘     │
│                         │                                    │
│  ┌──────────────┐  ┌────┴─────────┐  ┌──────────────┐      │
│  │ Odoo ERP     │  │   Cloud      │  │  Monitoring   │      │
│  │ :8069        │  │ Orchestrator │  │  Stack        │      │
│  │ (PostgreSQL) │  │  (headless)  │  │              │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│                         │                                    │
│  ┌──────────────────────┴─────────────────────────────┐     │
│  │              Vault (git working copy)               │     │
│  │     Inbox/ | Needs_Approval/ | Done/ | Reports/     │     │
│  └─────────────────────┬──────────────────────────────┘     │
│                        │ git push/pull                       │
└────────────────────────┼────────────────────────────────────┘
                         │
              ┌──────────┴──────────┐
              │       GitHub        │
              │   (main branch)     │
              │   vault/ synced     │
              │   code deployed     │
              └──────────┬──────────┘
                         │
┌────────────────────────┼────────────────────────────────────┐
│                        │ git push/pull                       │
│  ┌─────────────────────┴──────────────────────────────┐     │
│  │              Vault (git working copy)               │     │
│  │     Inbox/ | Needs_Action/ | Needs_Approval/ | Done/│     │
│  └─────────────────────┬──────────────────────────────┘     │
│                        │                                     │
│  ┌──────────────┐  ┌───┴──────────┐  ┌──────────────┐      │
│  │ Claude Code  │  │   Local      │  │  Approval    │      │
│  │ CLI          │  │ AIEmployee   │  │  Dashboard   │      │
│  │ (ad-hoc)     │  │ (on-demand)  │  │  :8080       │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│                                                              │
│                   LOCAL MACHINE (Brain)                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. Port Map

| Service | Host | Port | Access |
|---------|------|------|--------|
| Nginx (TLS) | Cloud VM | 443 | Local machine, external |
| Communication MCP | Cloud VM | 9001 | Internal (behind Nginx) |
| Meta Social MCP | Cloud VM | 9002 | Internal |
| Twitter Social MCP | Cloud VM | 9003 | Internal |
| Odoo Accounting MCP | Cloud VM | 9004 | Internal |
| Odoo Web | Cloud VM | 8069 | Internal + Nginx proxy |
| Cloud Orchestrator | Cloud VM | — | No port (headless loop) |
| Health Endpoint | Cloud VM | 9090 | Nginx-proxied |
| Approval Dashboard | Local | 8080 | localhost only |
| SSH | Cloud VM | 22 | Local machine (key-auth) |

---

## 7. Security Model

### 7.1 Authentication Layers

```
Layer 1: SSH (Local → Cloud)
  └── Ed25519 key pair, no password auth

Layer 2: TLS (Local → Nginx)
  └── Let's Encrypt cert, HTTPS only

Layer 3: API Key (Local → MCP Servers)
  └── X-API-Key header, rotated monthly
  └── Nginx validates before proxying to backend

Layer 4: OAuth / Tokens (MCP → External APIs)
  └── Gmail: OAuth2 refresh token (stored on cloud)
  └── LinkedIn: Session cookie (stored on cloud)
  └── Meta: Long-lived page token (stored on cloud)
  └── Twitter: OAuth 1.0a (stored on cloud)
  └── Odoo: Username/password (localhost on cloud)
```

### 7.2 Secret Management

```
Cloud VM:
  └── /etc/ai-employee/.env ........... All API tokens + credentials
      ├── chmod 600, owned by ai-employee user
      ├── NOT in git repository
      └── Backed up encrypted to cloud storage

Local Machine:
  └── .env ............................. Only ANTHROPIC_API_KEY + CLOUD_API_KEY
      ├── Gmail OAuth token (local only, not synced)
      └── No external API secrets needed (cloud handles calls)

GitHub:
  └── Secrets (Actions) ............... SSH_PRIVATE_KEY, CLOUD_HOST, DEPLOY_KEY
```

### 7.3 Vault Security

```
Sensitive data rules:
  ├── .gitignore: *.token, *.key, .env, credentials.json
  ├── Vault files: only task content, no credentials
  ├── Approval files: contain action description, not auth tokens
  └── Audit logs: auto-redacted (passwords/tokens stripped)
```

---

## 8. Failure Modes & Recovery

| Failure | Impact | Recovery |
|---------|--------|----------|
| Cloud VM down | No polling, no automation | Local can run Gold-tier mode standalone; GitHub Actions health-ping alerts operator |
| Local machine offline | No approvals, no brain | Cloud continues autonomous operation; approvals queue in Needs_Approval/ |
| GitHub down | No sync | Both sides buffer commits locally; auto-retry push every 60s |
| Nginx down | MCP servers unreachable from local | Cloud orchestrator uses localhost; local falls back to Gold-tier subprocess MCP |
| Odoo down | No accounting data | Circuit breaker trips; audit agent skips finance section; alert via health monitor |
| Network partition | Cloud and local diverge | Git merge on reconnect; approval files use UUIDs (no conflicts) |
| Git merge conflict | Vault state inconsistent | Auto-resolve: cloud wins for Inbox/Done, local wins for Needs_Approval |

---

## 9. Operational Modes

### Mode 1: Full Platinum (Cloud + Local)
- Cloud runs continuous loop
- Local provides intelligence + approvals
- Git syncs every 30 seconds

### Mode 2: Cloud-Only (Operator Away)
- Cloud runs fully autonomous
- Approvals auto-expire after 24h (configurable)
- High-risk actions (financial > $1000, external posts) queue indefinitely
- Low-risk actions auto-approve after safety check passes

### Mode 3: Local-Only (Cloud Down)
- Identical to Gold tier — full AIEmployee on local machine
- MCP servers run as local subprocesses (stdio)
- Dashboard on localhost:8080
- No 24/7 polling (runs when terminal is open)

### Mode 4: Maintenance
- Cloud stops orchestrator loop
- `git pull` latest code from GitHub
- Run migrations/updates
- Restart services via systemd

---

## 10. Cloud VM Setup (Reference)

### Systemd Services

```ini
# /etc/systemd/system/ai-employee-orchestrator.service
[Unit]
Description=AI Employee Cloud Orchestrator
After=network.target postgresql.service odoo.service
Requires=ai-employee-mcp@communication.service

[Service]
Type=simple
User=ai-employee
WorkingDirectory=/opt/ai-employee
EnvironmentFile=/etc/ai-employee/.env
ExecStart=/opt/ai-employee/venv/bin/python -m ai_employee.cloud_main --loop
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/ai-employee-mcp@.service
[Unit]
Description=AI Employee MCP Server - %i
After=network.target

[Service]
Type=simple
User=ai-employee
WorkingDirectory=/opt/ai-employee
EnvironmentFile=/etc/ai-employee/.env
ExecStart=/opt/ai-employee/venv/bin/python -m ai_employee.integrations.mcp_%i_server --http --port %i
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Nginx Config

```nginx
upstream mcp_communication { server 127.0.0.1:9001; }
upstream mcp_meta          { server 127.0.0.1:9002; }
upstream mcp_twitter       { server 127.0.0.1:9003; }
upstream mcp_odoo          { server 127.0.0.1:9004; }

server {
    listen 443 ssl;
    server_name ai-employee.example.com;

    ssl_certificate     /etc/letsencrypt/live/ai-employee.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ai-employee.example.com/privkey.pem;

    # API key validation
    set $api_key_valid 0;
    if ($http_x_api_key = "ROTATE_ME_MONTHLY") { set $api_key_valid 1; }
    if ($api_key_valid = 0) { return 403; }

    location /mcp/communication/ { proxy_pass http://mcp_communication/; }
    location /mcp/meta/          { proxy_pass http://mcp_meta/;          }
    location /mcp/twitter/       { proxy_pass http://mcp_twitter/;       }
    location /mcp/odoo/          { proxy_pass http://mcp_odoo/;          }
    location /odoo/              { proxy_pass http://127.0.0.1:8069/;    }

    location /health {
        proxy_pass http://127.0.0.1:9090/health;
    }
}
```

---

## 11. Implementation Roadmap

### Phase 1: Foundation (Week 1)
1. Create `cloud_main.py` — headless CloudOrchestrator subclass
2. Create `sync_agent.py` — git pull/push daemon with conflict resolution
3. Add `--cloud` CLI flag to `ai_employee/main.py`
4. Set up GitHub repo with vault/ tracking

### Phase 2: Cloud MCP (Week 2)
5. Create `mcp_http_adapter.py` — FastAPI wrapper for FastMCP servers
6. Add `MCPTransport` protocol + `HTTPTransport` implementation
7. Update `MCPRouter` to accept pluggable transport
8. Deploy MCP servers to cloud VM with systemd

### Phase 3: Odoo Cloud (Week 2)
9. Install Odoo on cloud VM (Docker or native)
10. Configure `odoo_client.py` to use cloud URL
11. Wire Odoo MCP server on cloud

### Phase 4: Sync & Approvals (Week 3)
12. Implement vault sync protocol (see `sync_strategy.md`)
13. Implement approval file format (structured YAML frontmatter)
14. Add auto-expire logic for unattended approvals
15. Wire cloud approval queue to sync agent

### Phase 5: Security & Hardening (Week 3)
16. Set up Nginx + TLS + API key auth
17. Move all API credentials to cloud `/etc/ai-employee/.env`
18. Set up SSH key auth (no password)
19. Configure firewall (only 22, 443 open)

### Phase 6: Monitoring & Deployment (Week 4)
20. Set up GitHub Actions: deploy-cloud.yml, health-ping.yml
21. Cloud health endpoint at :9090
22. Local health dashboard polls cloud status
23. Alerting: email/SMS on cloud service failure
