# AI Employee — Autonomous Digital Worker

> **Platinum Tier** | Agent Factory Hackathon 0
>
> Your business on autopilot. Local-first, agent-driven, human-in-the-loop.

```
 _____ _____   _____                _
|  _  |_   _| |   __|_____ ___| |___ _ _ ___ ___
|     | | |   |   __|     | . | | . | | | -_| -_|
|__|__| |_|   |_____|_|_|_|  _|_|___|_  |___|___|
                          |_|       |___|
```

A fully autonomous AI Employee that manages your business 24/7 — processing emails, handling LinkedIn messages, managing social media, running Odoo ERP accounting, generating CEO briefings, and executing tasks with human-in-the-loop approval gates.

---

## Architecture

The system follows a **Perception → Reasoning → Action** pipeline with a hybrid Local Brain + Cloud Muscle design:

```
╔══════════════════════════════════════════════════════════════════════╗
║                   PLATINUM TIER ARCHITECTURE                        ║
║                                                                     ║
║   LOCAL (Brain)            GITHUB (Sync)        CLOUD (Muscle)      ║
║  ┌──────────────┐       ┌──────────────┐     ┌─────────────────┐   ║
║  │ Claude Code   │◄─────►│              │◄───►│ Cloud Watchers  │   ║
║  │ Approvals     │  git  │  main branch │ git │ MCP Servers     │   ║
║  │ Dashboard     │  sync │  vault/      │ sync│ Odoo ERP        │   ║
║  │ Decision Eng  │       │  logs/       │     │ Health Monitor  │   ║
║  │ Ralph Loop    │       │              │     │ Git Sync        │   ║
║  └──────────────┘       └──────────────┘     └─────────────────┘   ║
║                                                                     ║
║  Human sits here          Audit trail           Runs 24/7           ║
╚══════════════════════════════════════════════════════════════════════╝
```

### Layers

| Layer | Components | Role |
|-------|-----------|------|
| **Perception** | 6 Cloud Watchers (Gmail, WhatsApp, LinkedIn, Twitter, Instagram, Odoo) | Monitor channels, create `.md` task files in `vault/Inbox/` |
| **Reasoning** | Decision Engine, Task Classifier, Priority Engine, Claude API | Classify, prioritize, and route tasks to agents |
| **Action** | 4 MCP Servers (Communication, Meta, Twitter, Odoo) + 9 Agents | Execute tasks via external APIs |
| **Approval** | Approval Queue, File-based workflow, Dashboard UI | Human-in-the-loop gate for sensitive actions |
| **Recovery** | Ralph Loop, Error Handler, Retry Manager, Fallback System | Autonomous error fixing with 7-phase cognitive cycle |
| **Monitoring** | Health Monitor, Alert System, Audit Logger, Auto-Restart | Continuous probes, circuit breakers, structured logging |

---

## Features

### 9 Specialized Agents

| Agent | Capability |
|-------|-----------|
| **Gmail Agent** | OAuth2 inbox processing — fetch, analyze, draft/send replies |
| **LinkedIn Agent** | Message processing, connection requests, AI-powered replies |
| **Odoo Agent** | Overdue invoice detection, P&L reports, balance sheets |
| **Meta Agent** | Facebook + Instagram metrics, content posting, approval workflows |
| **Twitter Agent** | Mentions tracking, tweet generation, weekly summaries |
| **Audit Agent** | Weekly CEO briefing reports with multi-source data aggregation |
| **Executive Brief Generator** | Comprehensive CEO-level reports (revenue, expenses, risks, AI decisions) |
| **Email Agent** | SMTP email drafting and sending |
| **Task Agent** | Generic task execution framework |

### 4 MCP Tool Servers

| Server | Tools | External APIs |
|--------|-------|---------------|
| `communication` | Email send, draft, reply | Gmail API, SMTP |
| `meta-social` | Post, metrics, schedule | Meta Graph API |
| `twitter-social` | Tweet, mentions, analytics | Twitter API v2 |
| `odoo-accounting` | Invoices, payments, P&L | Odoo XML-RPC |

### Production Runtime

| Component | Purpose |
|-----------|---------|
| **SystemManager** | 7-phase startup sequence with health aggregation |
| **CloudWatcherManager** | 6 always-on channel watchers with circuit breakers |
| **GitSyncWorker** | Periodic vault ↔ git synchronization |
| **HealthMonitor** | Continuous probes: watchers, MCP, Odoo, disk, internet, API limits |
| **AutoRestartManager** | Automatic service recovery with backoff |
| **AlertSystem** | Rule-based multi-channel alerting |

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **Node.js v24+** (for MCP servers)
- **Git**
- **Anthropic API key** ([console.anthropic.com](https://console.anthropic.com/settings/keys))

### 1. Clone & Install

```bash
git clone https://github.com/YOUR_USERNAME/AI-Employee.git
cd AI-Employee

# Create virtual environment
python -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

**Required credentials:**

| Variable | Purpose | How to get |
|----------|---------|-----------|
| `ANTHROPIC_API_KEY` | Claude AI reasoning | [Anthropic Console](https://console.anthropic.com/settings/keys) |
| `EMAIL_ADDRESS` | Gmail SMTP fallback | Your Gmail address |
| `EMAIL_PASSWORD` | Gmail App Password | [Google App Passwords](https://myaccount.google.com/apppasswords) |

**Optional (enable more agents):**

| Variable | Purpose |
|----------|---------|
| `GMAIL_CREDENTIALS_FILE` | OAuth2 Gmail (advanced) |
| `LINKEDIN_EMAIL/PASSWORD` | LinkedIn agent |
| `ODOO_URL/DB/USERNAME/PASSWORD` | Odoo ERP accounting |
| `META_ACCESS_TOKEN/PAGE_ID/IG_USER_ID` | Facebook + Instagram |
| `TWITTER_BEARER_TOKEN` + keys | Twitter/X |
| `WHATSAPP_TOKEN/PHONE_NUMBER_ID` | WhatsApp Business |

### 3. Setup Odoo (Optional)

```bash
docker-compose up -d   # Starts Odoo 18 + PostgreSQL
# Access Odoo at http://localhost:8069
```

### 4. Create Vault Directories

```bash
mkdir -p vault/Inbox vault/Needs_Action vault/Done vault/Reports
mkdir -p AI_Employee_Vault/Needs_Approval
```

### 5. Run

```bash
# Full production mode (7-phase startup)
python production_main.py

# Or use the startup script (Linux/Mac)
chmod +x startup.sh
./startup.sh

# Single cycle (test)
python production_main.py --once

# Health check
python production_main.py --health

# Original Gold Tier mode
python -m ai_employee.main
```

---

## Usage

### Production Runtime (`production_main.py`)

```bash
python production_main.py                    # Full production (continuous)
python production_main.py --once             # Single cycle, then exit
python production_main.py --health           # Health report
python production_main.py --status           # System status
python production_main.py --interval 2       # 2-minute cycles
python production_main.py --git-interval 600 # 10-min git sync
python production_main.py --no-git           # Disable git sync
python production_main.py --verbose          # Debug logging
```

### Startup Script (`startup.sh`)

```bash
./startup.sh                   # Start production system
./startup.sh stop              # Graceful shutdown
./startup.sh restart           # Stop + start
./startup.sh status            # System status
./startup.sh health            # Health check
./startup.sh logs              # Tail production logs
```

### Gold Tier CLI (`python -m ai_employee.main`)

```bash
python -m ai_employee.main              # Continuous operation
python -m ai_employee.main --once       # Single cycle
python -m ai_employee.main --health     # Health report
python -m ai_employee.main --gmail      # Process Gmail once
python -m ai_employee.main --linkedin   # Process LinkedIn once
python -m ai_employee.main --odoo       # Process Odoo once
python -m ai_employee.main --meta       # Process Meta once
python -m ai_employee.main --twitter    # Process Twitter once
python -m ai_employee.main --audit      # Generate CEO briefing
python -m ai_employee.main --brief      # Generate Executive Brief
python -m ai_employee.main --ralph TASK # Autonomous loop on a task
python -m ai_employee.main --mcp-status # MCP server status
python -m ai_employee.main --dashboard  # Dashboard only
python -m ai_employee.main --watch      # Inbox watcher only
python -m ai_employee.main --monitor    # Health monitor only
```

### Cloud Watchers (`python -m ai_employee.cloud_watchers`)

```bash
python -m ai_employee.cloud_watchers          # Start all watchers
python -m ai_employee.cloud_watchers --gmail   # Gmail only
python -m ai_employee.cloud_watchers --odoo    # Odoo only
python -m ai_employee.cloud_watchers --health  # Health status
```

---

## Task Flow

```
1. NEW TASK ARRIVES
   └── Watcher detects event → creates .md file in vault/Inbox/

2. TRIAGE
   └── Task moved to vault/Needs_Action/ with metadata

3. PLAN
   └── Decision Engine: classify → prioritize → route to agent
   └── Task enqueued in TaskQueue

4. EXECUTE
   └── Scheduler dispatches to assigned agent
   └── Agent executes via MCP server or direct API

5. APPROVAL (if required)
   └── File placed in AI_Employee_Vault/Needs_Approval/
   └── Human reviews on Dashboard → approve/reject
   └── Approved tasks re-enter execution pipeline

6. DONE
   └── Results logged → task moved to vault/Done/
   └── Audit trail recorded → CEO briefing updated
```

---

## Project Structure

```
AI-Employee/
├── production_main.py          # Platinum: Production entry point
├── startup.sh                  # Platinum: Launch script
├── requirements.txt            # Python dependencies
├── docker-compose.yml          # Odoo 18 + PostgreSQL
├── .env.example                # Environment template
│
├── ai_employee/                # Core application package
│   ├── main.py                 # Gold Tier orchestrator (AIEmployee class)
│   ├── system_manager.py       # Platinum: 7-phase startup manager
│   ├── cloud_watchers.py       # Platinum: 6 channel watchers
│   │
│   ├── agents/                 # 9 specialized agents
│   │   ├── gmail_agent.py
│   │   ├── linkedin_agent.py
│   │   ├── odoo_agent.py
│   │   ├── meta_agent.py
│   │   ├── twitter_agent.py
│   │   ├── audit_agent.py
│   │   ├── executive_brief_generator.py
│   │   ├── email_agent.py
│   │   └── task_agent.py
│   │
│   ├── brain/                  # Task intelligence & orchestration
│   │   ├── decision_engine.py  # AI classification + routing
│   │   ├── memory.py           # SQLite persistent memory
│   │   ├── planner.py          # Task lifecycle orchestration
│   │   ├── ralph_loop.py       # 7-phase autonomous loop
│   │   ├── loop_controller.py  # Platinum loop with recovery
│   │   ├── task_queue.py       # In-memory task queue
│   │   ├── scheduler.py        # Task execution scheduler
│   │   ├── approval_queue.py   # Manager approval workflow
│   │   ├── approval_manager.py # Approval decisions
│   │   ├── role_manager.py     # Role-based access (CEO/cloud)
│   │   ├── secrets_manager.py  # Credential management
│   │   └── security_layer.py   # Unified security enforcement
│   │
│   ├── integrations/           # External service connectors
│   │   ├── gmail_reader.py     # OAuth2 Gmail inbox
│   │   ├── gmail_sender.py     # OAuth2 Gmail sending
│   │   ├── linkedin_client.py  # LinkedIn API
│   │   ├── odoo_client.py      # Odoo XML-RPC
│   │   ├── meta_client.py      # Meta Graph API
│   │   ├── twitter_client.py   # Twitter API v2
│   │   ├── server_manager.py   # MCP server lifecycle
│   │   ├── tool_registry.py    # MCP tool discovery
│   │   ├── mcp_router.py       # 3-tier tool routing
│   │   └── mcp_*_server.py     # 4 MCP server implementations
│   │
│   ├── monitoring/             # Health & resilience (Platinum)
│   │   ├── health_monitor.py   # Continuous health probes
│   │   ├── health_check.py     # System health validation
│   │   ├── alert_system.py     # Rule-based alerting
│   │   ├── auto_restart.py     # Service recovery
│   │   ├── error_handler.py    # Error classification
│   │   ├── retry_manager.py    # Exponential backoff retry
│   │   ├── fallback_system.py  # Agent fallback routing
│   │   ├── audit_logger.py     # Enterprise audit trail
│   │   └── watcher.py          # Inbox file watcher
│   │
│   ├── dashboard/              # FastAPI web UI
│   │   ├── web_app.py          # Routes + templates
│   │   ├── dashboard_server.py # Uvicorn server
│   │   ├── analytics.py        # Task statistics
│   │   ├── analytics_engine.py # CEO KPIs
│   │   └── ceo_dashboard.py    # Executive dashboard
│   │
│   └── config/
│       └── settings.py         # Centralized configuration
│
├── vault/                      # Shared state (git-synced)
│   ├── Inbox/                  # New incoming tasks
│   ├── Needs_Action/           # Triaged tasks
│   ├── Done/                   # Completed tasks
│   └── Reports/                # CEO briefings
│
├── AI_Employee_Vault/          # Approval workflow
│   └── Needs_Approval/         # Pending human review
│
├── docs/                       # Architecture documentation
│   ├── hybrid_architecture.md  # Platinum design principles
│   ├── system_diagram.md       # ASCII architecture diagrams
│   ├── sync_strategy.md        # Git sync protocol
│   └── odoo_mcp_setup.md      # Odoo integration guide
│
├── scripts/                    # Automation scripts
│   ├── git_sync.sh             # Git sync daemon
│   ├── auto_push.sh            # Auto-commit vault
│   ├── auto_pull.sh            # Auto-pull state
│   ├── conflict_resolver.py    # Git merge resolution
│   ├── setup_scheduler.sh      # Cron setup (Unix)
│   └── setup_scheduler.bat     # Task Scheduler (Windows)
│
└── skills/                     # Claude Code skills
    ├── file-triage/            # File classification
    └── task-planner/           # Plan generation
```

---

## Tier Declaration

This project implements **Platinum Tier** (60+ hours):

| Tier | Features | Status |
|------|----------|--------|
| **Bronze** | Vault setup, 1 watcher, manual folders | Included |
| **Silver** | Multiple watchers, LinkedIn, MCP servers, cron | Included |
| **Gold** | Odoo ERP, multi-social media, CEO briefings, error recovery | Included |
| **Platinum** | Cloud 24/7, vault sync, work-zone specialization, production security | Included |

---

## Security

See [SECURITY.md](SECURITY.md) for detailed security disclosure including:
- Credential storage and handling
- OAuth2 flow security
- Approval system architecture
- Audit logging and compliance
- Permission boundaries

---

## Detailed Documentation

| Document | Description |
|----------|-------------|
| [Hybrid Architecture](docs/hybrid_architecture.md) | Complete Platinum Tier design: local brain + cloud muscle |
| [System Diagrams](docs/system_diagram.md) | 12 ASCII diagrams covering all components |
| [Sync Strategy](docs/sync_strategy.md) | Git sync protocol, conflict resolution, failure scenarios |
| [Odoo MCP Setup](docs/odoo_mcp_setup.md) | Odoo integration with 18 MCP tools |
| [Odoo Deployment](docs/odoo_deployment_guide.md) | Cloud VM deployment guide |

---

## License

Built for the Agent Factory Hackathon 0 by Panaversity.
