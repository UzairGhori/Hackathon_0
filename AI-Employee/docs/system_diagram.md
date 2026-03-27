# Platinum Tier — System Diagram

> Visual reference for the hybrid architecture. All diagrams use ASCII art
> for maximum portability (renders in any terminal, editor, or markdown viewer).

---

## 1. High-Level Overview

```
╔══════════════════════════════════════════════════════════════════════════╗
║                        PLATINUM TIER ARCHITECTURE                       ║
║                                                                         ║
║   LOCAL (Brain)              GITHUB (Sync)          CLOUD (Muscle)      ║
║  ┌─────────────┐          ┌──────────────┐       ┌─────────────────┐   ║
║  │ Claude Code  │◄────────►│              │◄─────►│ Cloud           │   ║
║  │ Approvals    │  git     │  main branch │  git  │ Orchestrator    │   ║
║  │ Dashboard    │  push    │              │  push │ MCP Servers     │   ║
║  │ Decision Eng │  pull    │  vault/      │  pull │ Odoo ERP        │   ║
║  │ Ralph Loop   │          │  logs/       │       │ Monitoring      │   ║
║  └─────────────┘          └──────────────┘       └─────────────────┘   ║
║                                                                         ║
║  Human sits here            Audit trail             Runs 24/7           ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## 2. Cloud VM Internal Architecture

```
┌─────────────────────────────── CLOUD VM ─────────────────────────────────┐
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                     Nginx Reverse Proxy (:443)                      │ │
│  │  ┌──────────┬──────────┬──────────┬──────────┬──────────┐          │ │
│  │  │/mcp/comm │/mcp/meta │/mcp/twit │/mcp/odoo │ /health  │          │ │
│  │  └────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬─────┘          │ │
│  │       │          │          │          │          │                  │ │
│  └───────┼──────────┼──────────┼──────────┼──────────┼──────────────── │ │
│          │          │          │          │          │                  │ │
│          ▼          ▼          ▼          ▼          ▼                  │ │
│  ┌──────────┐┌──────────┐┌──────────┐┌──────────┐┌──────────┐         │ │
│  │  Comms   ││  Meta    ││ Twitter  ││  Odoo    ││ Health   │         │ │
│  │  MCP     ││  MCP     ││  MCP     ││  MCP     ││ Endpoint │         │ │
│  │  :9001   ││  :9002   ││  :9003   ││  :9004   ││  :9090   │         │ │
│  │          ││          ││          ││          ││          │         │ │
│  │ Gmail    ││ Facebook ││ Twitter  ││ Invoices ││ Status   │         │ │
│  │ LinkedIn ││ Instagram││ Mentions ││ P&L      ││ Metrics  │         │ │
│  └──────────┘└──────────┘└──────────┘└────┬─────┘└──────────┘         │ │
│                                           │                            │ │
│          ┌────────────────────────────────┐│                           │ │
│          │                                ││                           │ │
│          ▼                                ▼│                           │ │
│  ┌──────────────────┐          ┌──────────────────┐                    │ │
│  │ Cloud            │          │ Odoo ERP          │                    │ │
│  │ Orchestrator     │◄────────►│ :8069             │                    │ │
│  │                  │          │                    │                    │ │
│  │ ┌──────────────┐ │          │ ┌──────────────┐  │                    │ │
│  │ │ phase_gmail  │ │          │ │ Accounting   │  │                    │ │
│  │ │ phase_linked │ │          │ │ CRM          │  │                    │ │
│  │ │ phase_meta   │ │          │ │ Inventory    │  │                    │ │
│  │ │ phase_twitter│ │          │ └──────────────┘  │                    │ │
│  │ │ phase_odoo   │ │          │                    │                    │ │
│  │ │ phase_triage │ │          │ PostgreSQL 15      │                    │ │
│  │ │ phase_audit  │ │          └──────────────────┘                    │ │
│  │ └──────────────┘ │                                                  │ │
│  │                  │                                                  │ │
│  │ ┌──────────────┐ │          ┌──────────────────┐                    │ │
│  │ │ Monitoring   │ │          │ Sync Agent       │                    │ │
│  │ │ ├HealthMon   │ │          │                  │                    │ │
│  │ │ ├AuditLogger │ │          │ git pull (30s)   │                    │ │
│  │ │ ├ErrorHandler│ │          │ git push (after  │                    │ │
│  │ │ ├RetryMgr    │ │◄────────►│   each phase)    │                    │ │
│  │ │ └FallbackSys │ │          │                  │                    │ │
│  │ └──────────────┘ │          │ Conflict resolver │                    │ │
│  └──────────────────┘          └────────┬─────────┘                    │ │
│                                         │                              │ │
│  ┌──────────────────────────────────────┴───────────────────────────┐  │ │
│  │                    Vault (git working copy)                      │  │ │
│  │  Inbox/  │  Needs_Approval/  │  Done/  │  Reports/  │  logs/    │  │ │
│  └──────────────────────────────────────────────────────────────────┘  │ │
└───────────────────────────────────────────────────────────────────────── ┘
```

---

## 3. Local Machine Internal Architecture

```
┌──────────────────────────── LOCAL MACHINE ───────────────────────────────┐
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │                   Claude Code CLI (Interactive)                   │    │
│  │                                                                  │    │
│  │  > ai-employee --dashboard          (start approval UI)         │    │
│  │  > ai-employee --ralph "task"       (autonomous task exec)      │    │
│  │  > ai-employee --health             (cloud health check)        │    │
│  │  > ai-employee --stats              (analytics snapshot)        │    │
│  │  > ai-employee --sync               (force vault sync)         │    │
│  └──────────┬───────────────────────────────────────────────────────┘    │
│             │                                                            │
│             ▼                                                            │
│  ┌──────────────────┐     ┌────────────────────┐                        │
│  │ Local AIEmployee │     │ Approval Dashboard │                        │
│  │                  │     │ :8080              │                        │
│  │ ┌──────────────┐ │     │                    │                        │
│  │ │DecisionEngine│ │     │ ┌────────────────┐ │                        │
│  │ │              │ │     │ │ /approvals     │ │                        │
│  │ │ Classify     │ │     │ │ /ceo           │ │                        │
│  │ │ Route        │ │     │ │ /briefings     │ │                        │
│  │ │ Prioritize   │ │     │ │ /logs          │ │                        │
│  │ └──────────────┘ │     │ │ /system        │ │                        │
│  │                  │     │ └────────────────┘ │                        │
│  │ ┌──────────────┐ │     │                    │                        │
│  │ │ Ralph Loop   │ │     │ Shows:             │                        │
│  │ │ (on-demand)  │ │     │  - Cloud status    │                        │
│  │ │              │ │     │  - Pending items   │                        │
│  │ │ Observe      │ │     │  - Audit trail     │                        │
│  │ │ Think        │ │     │  - CEO metrics     │                        │
│  │ │ Plan         │ │     └────────────────────┘                        │
│  │ │ Act ────────────────► Cloud MCP (HTTPS)                            │
│  │ │ Evaluate     │ │                                                    │
│  │ └──────────────┘ │                                                    │
│  └──────────────────┘                                                    │
│                                                                          │
│  ┌──────────────────┐     ┌────────────────────┐                        │
│  │ Sync Agent       │     │ Cloud Health Poll  │                        │
│  │                  │     │                    │                        │
│  │ git pull (30s)   │     │ HTTPS GET /health  │                        │
│  │ git push (after  │     │ every 60s          │                        │
│  │   approval)      │     │                    │                        │
│  │                  │     │ Alert if:          │                        │
│  │ Conflict: local  │     │  - VM unreachable  │                        │
│  │ wins for         │     │  - Service down    │                        │
│  │ Needs_Approval/  │     │  - Disk >90%       │                        │
│  └────────┬─────────┘     └────────────────────┘                        │
│           │                                                              │
│  ┌────────┴─────────────────────────────────────────────────────────┐    │
│  │                    Vault (git working copy)                      │    │
│  │  Inbox/ │ Needs_Action/ │ Needs_Approval/ │ Done/ │ Reports/    │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Data Flow — Task Lifecycle

```
 EXTERNAL                    CLOUD                    GITHUB              LOCAL
    │                          │                        │                   │
    │  New email arrives       │                        │                   │
    ├─────────────────────────►│                        │                   │
    │                          │                        │                   │
    │                    ┌─────┴──────┐                 │                   │
    │                    │ phase_gmail │                 │                   │
    │                    │ reads email │                 │                   │
    │                    │ classifies  │                 │                   │
    │                    └─────┬──────┘                 │                   │
    │                          │                        │                   │
    │                    ┌─────┴──────┐                 │                   │
    │                    │ Safety     │                 │                   │
    │                    │ Check      │                 │                   │
    │                    └─────┬──────┘                 │                   │
    │                          │                        │                   │
    │              ┌───────────┴───────────┐            │                   │
    │              │                       │            │                   │
    │        SAFE (auto)            NEEDS APPROVAL      │                   │
    │              │                       │            │                   │
    │              ▼                       ▼            │                   │
    │     ┌──────────────┐    ┌────────────────┐       │                   │
    │     │ Auto-execute │    │ Write approval │       │                   │
    │     │ (send reply, │    │ file to vault/ │       │                   │
    │     │  post, etc.) │    │ Needs_Approval │       │                   │
    │     └──────┬───────┘    └────────┬───────┘       │                   │
    │            │                     │               │                   │
    │            ▼                     ▼               │                   │
    │     ┌──────────────┐    ┌────────────────┐       │                   │
    │     │ Move to      │    │ git commit     │       │                   │
    │     │ vault/Done/  │    │ git push       ├──────►│                   │
    │     └──────┬───────┘    └────────────────┘       │                   │
    │            │                                     │                   │
    │            ▼                                     │      git pull     │
    │     ┌──────────────┐                             ├──────────────────►│
    │     │ git commit   │                             │                   │
    │     │ git push     ├────────────────────────────►│                   │
    │     └──────────────┘                             │            ┌──────┴──────┐
    │                                                  │            │ Dashboard   │
    │                                                  │            │ shows new   │
    │                                                  │            │ approval    │
    │                                                  │            └──────┬──────┘
    │                                                  │                   │
    │                                                  │            ┌──────┴──────┐
    │                                                  │            │ Human       │
    │                                                  │            │ approves    │
    │                                                  │            └──────┬──────┘
    │                                                  │                   │
    │                                                  │            ┌──────┴──────┐
    │                                                  │◄───────────┤ git commit  │
    │                                                  │            │ git push    │
    │                          │                       │            └─────────────┘
    │                    ┌─────┴──────┐                │
    │                    │ git pull   │◄───────────────┤
    │                    │ sees       │                │
    │                    │ approval   │                │
    │                    └─────┬──────┘                │
    │                          │                       │
    │                    ┌─────┴──────┐                │
    │◄───────────────────┤ Execute    │                │
    │  (email sent,      │ approved   │                │
    │   tweet posted)    │ action     │                │
    │                    └─────┬──────┘                │
    │                          │                       │
    │                    ┌─────┴──────┐                │
    │                    │ Move to    │                │
    │                    │ Done/      │                │
    │                    │ git push   ├───────────────►│
    │                    └────────────┘                │
```

---

## 5. Approval File Flow

```
                    APPROVAL FILE LIFECYCLE
                    ══════════════════════

    Cloud creates:                  Local updates:
    ┌─────────────────────┐        ┌─────────────────────┐
    │ approve_abc123.md   │        │ approve_abc123.md   │
    │                     │        │                     │
    │ ---                 │        │ ---                 │
    │ id: abc123          │        │ id: abc123          │
    │ status: pending     │───────►│ status: approved    │
    │ type: content       │  sync  │ type: content       │
    │ risk: medium        │        │ risk: medium        │
    │ created: 2026-03-23 │        │ decided: 2026-03-23 │
    │ expires: 2026-03-24 │        │ decided_by: human   │
    │ ---                 │        │ ---                 │
    │                     │        │                     │
    │ ## Action           │        │ ## Action           │
    │ Post to Twitter:    │        │ Post to Twitter:    │
    │ "Exciting news..."  │        │ "Exciting news..."  │
    │                     │        │                     │
    │ ## Context          │        │ ## Decision         │
    │ Source: meta_agent   │        │ Approved. Go ahead. │
    │ Confidence: 0.85    │        │                     │
    └─────────────────────┘        └─────────────────────┘
           │                                │
           ▼                                ▼
    vault/Needs_Approval/           vault/Needs_Approval/
    (cloud commits + pushes)        (local commits + pushes)
                                           │
                                           ▼
                                    Cloud reads status=approved
                                    Executes action
                                    Moves to vault/Done/
```

---

## 6. MCP Server Communication

### Gold Tier (Current)

```
┌──────────────┐    stdio (JSON-RPC 2.0)    ┌──────────────┐
│  AIEmployee  │◄──────────────────────────►│  FastMCP     │
│  (parent)    │    stdin/stdout pipe        │  (child)     │
└──────────────┘                             └──────────────┘
```

### Platinum Tier (New)

```
┌──────────────┐    HTTPS (JSON-RPC 2.0)    ┌──────────────┐
│  Local       │───────────────────────────►│  Nginx :443  │
│  AIEmployee  │    X-API-Key header        │              │
└──────────────┘                             └──────┬───────┘
                                                    │
                                    ┌───────────────┼───────────────┐
                                    │               │               │
                                    ▼               ▼               ▼
                             ┌──────────┐   ┌──────────┐   ┌──────────┐
                             │ Meta MCP │   │ Twit MCP │   │ Odoo MCP │
                             │ :9002    │   │ :9003    │   │ :9004    │
                             └──────────┘   └──────────┘   └──────────┘


┌──────────────┐    localhost (JSON-RPC 2.0) ┌──────────────┐
│  Cloud       │───────────────────────────►│  MCP :900x   │
│  Orchestrator│    no TLS needed           │  (same host) │
└──────────────┘                             └──────────────┘
```

---

## 7. Monitoring & Health Flow

```
┌─────────────────── CLOUD ───────────────────┐
│                                              │
│  ┌────────────────┐                          │
│  │ Health Monitor │──── checks every 60s ──► │
│  │                │                          │
│  │ Checks:        │   ┌──────────────────┐   │
│  │ ├ Orchestrator │──►│ Service Status   │   │
│  │ ├ MCP :9001    │   │                  │   │
│  │ ├ MCP :9002    │   │ Each service:    │   │
│  │ ├ MCP :9003    │   │  status: UP/DOWN │   │
│  │ ├ MCP :9004    │   │  latency: 12ms   │   │
│  │ ├ Odoo :8069   │   │  restarts: 0     │   │
│  │ ├ PostgreSQL   │   │  uptime: 99.9%   │   │
│  │ └ Disk/Memory  │   └────────┬─────────┘   │
│  └────────────────┘            │              │
│                                ▼              │
│                       ┌──────────────────┐    │
│                       │ /health endpoint │    │
│                       │ :9090            │    │
│                       │                  │    │
│                       │ {                │    │
│                       │  "healthy": true │    │
│                       │  "services": {   │    │
│                       │    "mcp": "ok",  │    │
│                       │    "odoo": "ok", │    │
│                       │    "orch": "ok"  │    │
│                       │  },              │    │
│                       │  "uptime": "72h" │    │
│                       │ }                │    │
│                       └────────┬─────────┘    │
│                                │              │
└────────────────────────────────┼──────────────┘
                                 │ HTTPS
                                 │
┌────────────────────────────────┼──────────────┐
│  LOCAL                         │              │
│                                ▼              │
│  ┌──────────────────────────────────────┐     │
│  │ Cloud Health Poll (every 60s)       │     │
│  │                                      │     │
│  │ GET https://cloud-vm/health          │     │
│  │                                      │     │
│  │ If unhealthy:                        │     │
│  │   → Dashboard shows warning banner   │     │
│  │   → Log to local audit trail         │     │
│  │   → Optionally notify (email/SMS)    │     │
│  │                                      │     │
│  │ If unreachable (3 retries):          │     │
│  │   → Switch to Local-Only mode        │     │
│  │   → Start local MCP subprocesses     │     │
│  │   → Alert operator                   │     │
│  └──────────────────────────────────────┘     │
│                                                │
└────────────────────────────────────────────────┘
```

---

## 8. Git Sync Timing Diagram

```
Time ──────────────────────────────────────────────────────────►

CLOUD:     pull   gmail   push   pull   meta   push   pull
            │      │       │      │      │      │      │
            ▼      ▼       ▼      ▼      ▼      ▼      ▼
GitHub:  ──●──────●───────●──────●──────●──────●──────●──────
            ▲      ▲       ▲      ▲      ▲      ▲      ▲
            │      │       │      │      │      │      │
LOCAL:     pull          pull   approve push   pull
            │              │      │      │      │
           30s            30s    user   immed  30s
          timer           timer  click   push  timer

Legend:
  ● = commit on main branch
  Cloud pushes after each phase completes
  Local pushes immediately after approval decision
  Both pull every 30 seconds (configurable)
```

---

## 9. Error Recovery Flow

```
    ┌──────────────────┐
    │ Phase Execution  │
    │ (e.g. phase_meta)│
    └────────┬─────────┘
             │
             │ Exception!
             ▼
    ┌──────────────────┐
    │ Error Handler    │
    │                  │
    │ Classify:        │
    │  Type: TRANSIENT │
    │  Severity: MEDIUM│
    │  Action: RETRY   │
    └────────┬─────────┘
             │
             ▼
    ┌──────────────────┐     Success
    │ Retry Manager    │────────────────► Continue pipeline
    │                  │
    │ Attempt 1: fail  │
    │ Wait 2s          │
    │ Attempt 2: fail  │
    │ Wait 4s          │
    │ Attempt 3: fail  │
    │                  │
    │ EXHAUSTED        │
    └────────┬─────────┘
             │
             ▼
    ┌──────────────────┐     Success
    │ Fallback System  │────────────────► Continue pipeline
    │                  │                  (with alt agent)
    │ meta_agent FAIL  │
    │ → twitter_agent  │─── try ──► fail
    │ → task_agent     │─── try ──► success!
    │                  │
    └────────┬─────────┘
             │ All fallbacks exhausted
             ▼
    ┌──────────────────┐
    │ Circuit Breaker  │
    │ OPEN for 60s     │
    │                  │
    │ Audit: logged    │
    │ Alert: sent      │
    │ Phase: skipped   │
    └──────────────────┘
```

---

## 10. Deployment Architecture

```
┌────────────────────────── GitHub Actions ──────────────────────────┐
│                                                                    │
│  ┌─────────────────────┐    ┌─────────────────────┐               │
│  │ deploy-cloud.yml    │    │ health-ping.yml     │               │
│  │                     │    │                     │               │
│  │ Trigger: push to    │    │ Trigger: cron       │               │
│  │   main branch       │    │   every 5 min       │               │
│  │                     │    │                     │               │
│  │ Steps:              │    │ Steps:              │               │
│  │ 1. SSH to cloud VM  │    │ 1. curl /health     │               │
│  │ 2. git pull         │    │ 2. Check response   │               │
│  │ 3. pip install      │    │ 3. If fail:         │               │
│  │ 4. systemctl        │    │    notify owner     │               │
│  │    restart services │    │                     │               │
│  │ 5. Verify health    │    │                     │               │
│  └─────────┬───────────┘    └─────────┬───────────┘               │
│            │                          │                            │
└────────────┼──────────────────────────┼────────────────────────────┘
             │ SSH                       │ HTTPS
             ▼                          ▼
    ┌────────────────────────────────────────────────────┐
    │                   CLOUD VM                         │
    │                                                    │
    │   systemd services:                                │
    │   ┌────────────────────────────────────────────┐   │
    │   │ ai-employee-orchestrator.service  (main)   │   │
    │   │ ai-employee-mcp@communication.service      │   │
    │   │ ai-employee-mcp@meta.service               │   │
    │   │ ai-employee-mcp@twitter.service            │   │
    │   │ ai-employee-mcp@odoo.service               │   │
    │   │ ai-employee-sync.service        (git sync) │   │
    │   │ ai-employee-health.service      (endpoint) │   │
    │   │ nginx.service                   (proxy)    │   │
    │   │ odoo.service                    (ERP)      │   │
    │   │ postgresql.service              (database) │   │
    │   └────────────────────────────────────────────┘   │
    │                                                    │
    │   All services: Restart=always, WantedBy=multi-user│
    └────────────────────────────────────────────────────┘
```

---

## 11. Pipeline Phase Ownership

```
                        ┌───────────────┐
                        │  run_cycle()  │
                        └───────┬───────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
   CLOUD PHASES            SHARED PHASES           LOCAL PHASES
   (always-on)             (both can run)          (on-demand)
        │                       │                       │
   ┌────┴────┐            ┌─────┴─────┐           ┌────┴────┐
   │ GMAIL   │            │ TRIAGE    │           │ APPROVE │
   │ LINKEDIN│            │ PLAN      │           │ RALPH   │
   │ META    │            │ EXECUTE   │           │ (ad-hoc │
   │ TWITTER │            │ AUDIT     │           │  tasks) │
   │ ODOO    │            │ STATS     │           │         │
   └─────────┘            └───────────┘           └─────────┘
        │                       │                       │
   Polls external          Processes vault          Human-driven
   APIs on schedule        files (git-synced)       intelligence
```

---

## 12. Secret Distribution

```
┌──────────────── CLOUD VM ────────────────┐
│                                          │
│  /etc/ai-employee/.env                   │
│  ┌────────────────────────────────────┐  │
│  │ ANTHROPIC_API_KEY=sk-ant-...      │  │
│  │ EMAIL_ADDRESS=...                 │  │
│  │ EMAIL_PASSWORD=...                │  │
│  │ GMAIL_CREDENTIALS_FILE=...        │  │
│  │ GMAIL_TOKEN_FILE=...              │  │
│  │ LINKEDIN_EMAIL=...                │  │
│  │ LINKEDIN_PASSWORD=...             │  │
│  │ ODOO_URL=http://localhost:8069    │  │
│  │ ODOO_DB=...                       │  │
│  │ ODOO_USERNAME=...                 │  │
│  │ ODOO_PASSWORD=...                 │  │
│  │ META_ACCESS_TOKEN=...             │  │
│  │ META_PAGE_ID=...                  │  │
│  │ META_IG_USER_ID=...               │  │
│  │ TWITTER_BEARER_TOKEN=...          │  │
│  │ TWITTER_API_KEY=...               │  │
│  │ TWITTER_API_SECRET=...            │  │
│  │ TWITTER_ACCESS_TOKEN=...          │  │
│  │ TWITTER_ACCESS_TOKEN_SECRET=...   │  │
│  │ MCP_API_KEY=<for nginx auth>      │  │
│  └────────────────────────────────────┘  │
│  chmod 600, owner: ai-employee           │
└──────────────────────────────────────────┘

┌──────────────── LOCAL ───────────────────┐
│                                          │
│  .env (minimal)                          │
│  ┌────────────────────────────────────┐  │
│  │ ANTHROPIC_API_KEY=sk-ant-...      │  │
│  │ CLOUD_HOST=ai-employee.example.com│  │
│  │ CLOUD_API_KEY=<nginx auth key>    │  │
│  │ CLOUD_SSH_KEY=~/.ssh/ai-employee  │  │
│  └────────────────────────────────────┘  │
│                                          │
│  NOT stored locally:                     │
│  ✗ Gmail OAuth tokens                   │
│  ✗ LinkedIn credentials                 │
│  ✗ Odoo passwords                       │
│  ✗ Meta/Twitter tokens                  │
└──────────────────────────────────────────┘

┌──────────────── GITHUB ──────────────────┐
│                                          │
│  Repository Secrets (Actions only)       │
│  ┌────────────────────────────────────┐  │
│  │ SSH_PRIVATE_KEY (deploy key)      │  │
│  │ CLOUD_HOST (VM IP/hostname)       │  │
│  │ HEALTH_URL (cloud /health URL)    │  │
│  └────────────────────────────────────┘  │
│                                          │
│  .gitignore enforces:                    │
│  ✗ .env                                 │
│  ✗ *.token                              │
│  ✗ credentials.json                     │
│  ✗ token.json                           │
└──────────────────────────────────────────┘
```
