# Odoo Community — Cloud VM Deployment Guide (Platinum Tier)

> **Version:** Odoo 18.0 Community Edition
> **Target:** Ubuntu 24.04 LTS (Cloud VM)
> **Architecture:** Same VM as AI Employee watchers, MCP servers, and git sync

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Option A — Native Installation (Recommended)](#3-option-a--native-installation-recommended)
4. [Option B — Docker Installation](#4-option-b--docker-installation)
5. [Odoo Configuration](#5-odoo-configuration)
6. [Module Installation](#6-module-installation)
7. [Database Setup](#7-database-setup)
8. [Nginx Reverse Proxy](#8-nginx-reverse-proxy)
9. [SSL / TLS](#9-ssl--tls)
10. [Firewall Rules](#10-firewall-rules)
11. [Systemd Service](#11-systemd-service)
12. [Backup Strategy](#12-backup-strategy)
13. [Health Monitoring](#13-health-monitoring)
14. [Performance Tuning](#14-performance-tuning)
15. [Troubleshooting](#15-troubleshooting)
16. [Post-Installation Checklist](#16-post-installation-checklist)

---

## 1. Architecture Overview

```
┌─────────────────────────────── Cloud VM ──────────────────────────────────┐
│                                                                           │
│  ┌─────────────┐     ┌──────────────┐     ┌────────────────────────┐     │
│  │ PostgreSQL   │────▶│  Odoo 18.0   │────▶│  Odoo MCP Server       │     │
│  │ 16           │     │  :8069       │     │  (FastMCP / stdio)     │     │
│  │ :5432        │     │  (web + API) │     │  :9004 (via supervisor)│     │
│  └─────────────┘     └──────┬───────┘     └────────────┬───────────┘     │
│                              │                          │                 │
│                              │ JSON-RPC                 │ MCP Protocol    │
│                              ▼                          ▼                 │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │  Nginx Reverse Proxy (:443)                                      │     │
│  │    /           → Odoo Web UI     (:8069)                         │     │
│  │    /jsonrpc    → Odoo JSON-RPC   (:8069)                         │     │
│  │    /mcp/odoo   → MCP Server      (:9004)                         │     │
│  │    /health     → Cloud Watchers  (:9090)                         │     │
│  └──────────────────────────────────────────────────────────────────┘     │
│                                                                           │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────────┐      │
│  │ Cloud Watchers  │  │ Git Sync Agent │  │ Other MCP Servers      │      │
│  │ (6 watchers)    │  │ (5-min cycle)  │  │ (comm, meta, twitter)  │      │
│  └────────────────┘  └────────────────┘  └────────────────────────┘      │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

**Key design decisions:**

| Decision                  | Rationale                                                   |
|---------------------------|-------------------------------------------------------------|
| Native install over Docker | Lower overhead, simpler debugging, direct PostgreSQL access |
| Same VM as AI Employee    | Odoo accessed via `localhost:8069` — no network latency     |
| Community Edition         | Free, sufficient for accounting/invoicing/expenses          |
| Nginx fronting Odoo       | SSL termination, rate limiting, unified entry point         |
| PostgreSQL 16             | Odoo 18 requires PostgreSQL 12+; v16 is Ubuntu 24.04 LTS default |

---

## 2. Prerequisites

### Hardware Requirements

| Resource | Minimum | Recommended | Notes                              |
|----------|---------|-------------|------------------------------------|
| CPU      | 2 cores | 4 cores     | Shared with AI Employee services   |
| RAM      | 4 GB    | 8 GB        | PostgreSQL + Odoo + Python workers |
| Disk     | 40 GB   | 80 GB SSD   | Database growth + attachments      |
| Network  | 100 Mbps| 1 Gbps      | API calls + git sync               |

### Software Requirements

| Software        | Version  | Purpose                          |
|-----------------|----------|----------------------------------|
| Ubuntu          | 24.04 LTS| Operating system                 |
| PostgreSQL      | 16.x     | Odoo database backend            |
| Python          | 3.12+    | Odoo runtime                     |
| Node.js         | 20.x LTS | Odoo asset compilation           |
| wkhtmltopdf     | 0.12.6+  | PDF report generation            |
| Nginx           | 1.24+    | Reverse proxy + SSL              |
| Certbot         | Latest   | Let's Encrypt SSL certificates   |
| Git             | 2.40+    | Odoo source + vault sync         |

### DNS

Point your domain to the Cloud VM's public IP:

```
A     odoo.yourdomain.com    → <CLOUD_VM_IP>
CNAME ai.yourdomain.com      → <CLOUD_VM_IP>
```

---

## 3. Option A — Native Installation (Recommended)

### 3.1 System Preparation

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install system dependencies
sudo apt install -y \
    python3-dev python3-pip python3-venv python3-wheel \
    build-essential libxml2-dev libxslt1-dev libevent-dev \
    libsasl2-dev libldap2-dev libpq-dev libjpeg-dev \
    zlib1g-dev libfreetype6-dev liblcms2-dev libwebp-dev \
    libopenjp2-7-dev libtiff5-dev libffi-dev \
    node-less npm git curl wget
```

### 3.2 Install wkhtmltopdf

```bash
# Odoo requires a patched version of wkhtmltopdf for PDF generation
wget https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.jammy_amd64.deb
sudo apt install -y ./wkhtmltox_0.12.6.1-3.jammy_amd64.deb
rm wkhtmltox_0.12.6.1-3.jammy_amd64.deb

# Verify
wkhtmltopdf --version
```

### 3.3 Install PostgreSQL 16

```bash
sudo apt install -y postgresql postgresql-client

# Verify version
psql --version

# Create Odoo database user
sudo -u postgres createuser --createdb --no-createrole --no-superuser odoo

# Set password
sudo -u postgres psql -c "ALTER USER odoo WITH PASSWORD 'CHANGE_THIS_PASSWORD';"
```

### 3.4 Create Odoo System User

```bash
sudo adduser --system --home /opt/odoo --group odoo
sudo mkdir -p /opt/odoo /var/log/odoo /etc/odoo
sudo chown odoo:odoo /opt/odoo /var/log/odoo /etc/odoo
```

### 3.5 Install Odoo 18

```bash
# Clone Odoo source
sudo -u odoo git clone --depth 1 --branch 18.0 \
    https://github.com/odoo/odoo.git /opt/odoo/odoo-server

# Create Python virtual environment
sudo -u odoo python3 -m venv /opt/odoo/venv

# Install Python dependencies
sudo -u odoo /opt/odoo/venv/bin/pip install --upgrade pip setuptools wheel
sudo -u odoo /opt/odoo/venv/bin/pip install -r /opt/odoo/odoo-server/requirements.txt

# Install additional dependencies for AI Employee integration
sudo -u odoo /opt/odoo/venv/bin/pip install psycopg2-binary
```

### 3.6 Create Odoo Configuration

```bash
sudo tee /etc/odoo/odoo.conf > /dev/null << 'EOF'
[options]
; ── Connection ──────────────────────────────────────────────────
admin_passwd = CHANGE_THIS_MASTER_PASSWORD
db_host = localhost
db_port = 5432
db_user = odoo
db_password = CHANGE_THIS_PASSWORD
db_name = ai_employee_accounting
db_maxconn = 64

; ── Paths ───────────────────────────────────────────────────────
data_dir = /opt/odoo/data
logfile = /var/log/odoo/odoo-server.log
addons_path = /opt/odoo/odoo-server/addons

; ── Network ─────────────────────────────────────────────────────
http_port = 8069
http_interface = 127.0.0.1
proxy_mode = True
xmlrpc = True
xmlrpc_port = 8069
longpolling_port = 8072

; ── Workers (production mode) ───────────────────────────────────
; Rule of thumb: workers = (2 * CPU cores) + 1
; With 4 cores: 9 workers
; Memory: ~150MB per worker
workers = 4
max_cron_threads = 1
limit_memory_hard = 2684354560
limit_memory_soft = 2147483648
limit_time_cpu = 600
limit_time_real = 1200
limit_time_real_cron = -1
limit_request = 8192

; ── Logging ─────────────────────────────────────────────────────
log_level = info
log_handler = :INFO
log_db = False

; ── Security ────────────────────────────────────────────────────
list_db = False
server_wide_modules = base,web

; ── Performance ─────────────────────────────────────────────────
osv_memory_count_limit = 0
osv_memory_age_limit = 1.0
unaccent = True
EOF

sudo chown odoo:odoo /etc/odoo/odoo.conf
sudo chmod 640 /etc/odoo/odoo.conf
```

### 3.7 Initialize the Database

```bash
# Create the database and install base modules
sudo -u odoo /opt/odoo/venv/bin/python /opt/odoo/odoo-server/odoo-bin \
    -c /etc/odoo/odoo.conf \
    -d ai_employee_accounting \
    -i base \
    --stop-after-init \
    --without-demo=all

echo "Database initialized successfully"
```

---

## 4. Option B — Docker Installation

For teams that prefer containerization.

### 4.1 Docker Compose

```bash
sudo mkdir -p /opt/odoo-docker && cd /opt/odoo-docker
```

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:16-alpine
    container_name: odoo-db
    restart: unless-stopped
    environment:
      POSTGRES_DB: postgres
      POSTGRES_USER: odoo
      POSTGRES_PASSWORD: ${ODOO_DB_PASSWORD:-CHANGE_THIS_PASSWORD}
      PGDATA: /var/lib/postgresql/data/pgdata
    volumes:
      - odoo-db-data:/var/lib/postgresql/data/pgdata
    ports:
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U odoo"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - odoo-net

  odoo:
    image: odoo:18.0
    container_name: odoo-web
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      HOST: postgres
      PORT: 5432
      USER: odoo
      PASSWORD: ${ODOO_DB_PASSWORD:-CHANGE_THIS_PASSWORD}
    volumes:
      - odoo-web-data:/var/lib/odoo
      - ./config:/etc/odoo
      - ./addons:/mnt/extra-addons
    ports:
      - "127.0.0.1:8069:8069"
      - "127.0.0.1:8072:8072"
    networks:
      - odoo-net

volumes:
  odoo-db-data:
  odoo-web-data:

networks:
  odoo-net:
    driver: bridge
```

### 4.2 Docker Config File

Create `config/odoo.conf`:

```ini
[options]
admin_passwd = CHANGE_THIS_MASTER_PASSWORD
db_host = postgres
db_port = 5432
db_user = odoo
db_password = CHANGE_THIS_PASSWORD
db_name = ai_employee_accounting

addons_path = /mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons
data_dir = /var/lib/odoo

http_port = 8069
proxy_mode = True
list_db = False
without_demo = all

workers = 4
max_cron_threads = 1
limit_memory_hard = 2684354560
limit_memory_soft = 2147483648
limit_time_cpu = 600
limit_time_real = 1200
```

### 4.3 Launch

```bash
# Create .env with credentials
echo 'ODOO_DB_PASSWORD=CHANGE_THIS_PASSWORD' > .env

# Start
docker compose up -d

# Check status
docker compose ps
docker compose logs -f odoo
```

---

## 5. Odoo Configuration

### 5.1 AI Employee Environment Variables

Add to `/etc/ai-employee/.env`:

```bash
# ── Odoo Connection (used by odoo_client.py) ──────────────────
ODOO_URL=http://127.0.0.1:8069
ODOO_DB=ai_employee_accounting
ODOO_USERNAME=admin
ODOO_PASSWORD=CHANGE_THIS_PASSWORD

# ── Odoo Watcher Interval (seconds) ──────────────────────────
WATCHER_ODOO_INTERVAL=600
```

### 5.2 Verify Connectivity

```bash
# Test JSON-RPC endpoint from the cloud VM
curl -s -X POST http://127.0.0.1:8069/jsonrpc \
    -H "Content-Type: application/json" \
    -d '{
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": "common",
            "method": "version",
            "args": []
        },
        "id": 1
    }' | python3 -m json.tool
```

Expected output:

```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "server_version": "18.0",
        "server_version_info": [18, 0, 0, "final", 0, ""],
        "server_serie": "18.0",
        "protocol_version": 1
    }
}
```

### 5.3 Test Authentication

```bash
curl -s -X POST http://127.0.0.1:8069/jsonrpc \
    -H "Content-Type: application/json" \
    -d '{
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": "common",
            "method": "authenticate",
            "args": ["ai_employee_accounting", "admin", "CHANGE_THIS_PASSWORD", {}]
        },
        "id": 2
    }' | python3 -m json.tool
```

A successful response returns a numeric UID (e.g., `"result": 2`).

---

## 6. Module Installation

### 6.1 Required Modules

The AI Employee integration requires these Odoo modules:

| Module            | Technical Name      | Purpose                         |
|-------------------|---------------------|---------------------------------|
| Accounting        | `account`           | Chart of accounts, journals     |
| Invoicing         | `account`           | Customer/vendor invoices        |
| Payment           | `account_payment`   | Payment registration            |
| Expenses          | `hr_expense`        | Employee expense tracking       |
| Contacts          | `contacts`          | Partner/customer management     |
| HR                | `hr`                | Employee records (for expenses) |

### 6.2 Install via CLI

```bash
# Install accounting + invoicing + expenses
sudo -u odoo /opt/odoo/venv/bin/python /opt/odoo/odoo-server/odoo-bin \
    -c /etc/odoo/odoo.conf \
    -d ai_employee_accounting \
    -i account,hr_expense,contacts,hr \
    --stop-after-init

echo "Modules installed"
```

### 6.3 Install via Web UI

1. Navigate to `http://VM_IP:8069`
2. Log in with admin credentials
3. Go to **Apps** menu
4. Search and install:
   - **Invoicing** (installs `account` automatically)
   - **Expenses**
   - **Contacts**

### 6.4 Configure Chart of Accounts

After installing the Accounting module:

1. Go to **Accounting > Configuration > Settings**
2. Select your **Fiscal Localization Package** (e.g., "Generic Chart of Accounts" or your country)
3. Click **Install** to generate the chart of accounts
4. Set the **Fiscal Year** start month
5. Configure **Default Journals**:
   - Bank Journal (for payments)
   - Cash Journal
   - Sales Journal
   - Purchase Journal
   - Miscellaneous Journal (for manual entries)

### 6.5 Create an API User (Recommended)

Instead of using the admin account, create a dedicated user for the AI Employee:

1. Go to **Settings > Users & Companies > Users**
2. Click **New**
3. Configure:
   - **Name:** `AI Employee Bot`
   - **Email:** `ai-bot@yourdomain.com`
   - **Access Rights:**
     - Accounting: **Billing** (read invoices, create drafts)
     - Expenses: **Team Approver** (read expenses)
     - Contacts: **User** (read/write partners)
   - **Technical Settings:**
     - API key enabled (under **Preferences** tab)
4. Save and note the user's credentials

Update `/etc/ai-employee/.env`:

```bash
ODOO_USERNAME=ai-bot@yourdomain.com
ODOO_PASSWORD=<api-user-password>
```

---

## 7. Database Setup

### 7.1 PostgreSQL Configuration

Edit `/etc/postgresql/16/main/postgresql.conf`:

```ini
# ── Connection ────────────────────────────────────
listen_addresses = 'localhost'
port = 5432
max_connections = 100

# ── Memory ────────────────────────────────────────
shared_buffers = 1GB              # 25% of RAM
effective_cache_size = 3GB        # 75% of RAM
work_mem = 16MB
maintenance_work_mem = 256MB

# ── WAL / Checkpoint ─────────────────────────────
wal_buffers = 16MB
checkpoint_completion_target = 0.9
max_wal_size = 2GB

# ── Planner ──────────────────────────────────────
random_page_cost = 1.1            # SSD
effective_io_concurrency = 200    # SSD

# ── Logging ──────────────────────────────────────
log_min_duration_statement = 1000  # Log queries > 1s
log_statement = 'none'
```

Apply changes:

```bash
sudo systemctl restart postgresql
```

### 7.2 PostgreSQL Authentication

Edit `/etc/postgresql/16/main/pg_hba.conf`:

```
# TYPE  DATABASE        USER    ADDRESS         METHOD
local   all             odoo                    md5
host    all             odoo    127.0.0.1/32    md5
```

```bash
sudo systemctl reload postgresql
```

### 7.3 Create the Database (If Not Done via Odoo CLI)

```bash
sudo -u postgres createdb -O odoo ai_employee_accounting

# Verify
sudo -u postgres psql -l | grep ai_employee
```

---

## 8. Nginx Reverse Proxy

### 8.1 Install Nginx

```bash
sudo apt install -y nginx
```

### 8.2 Odoo Site Configuration

```bash
sudo tee /etc/nginx/sites-available/odoo > /dev/null << 'NGINX_EOF'
# ── Odoo 18 + AI Employee MCP — Nginx Configuration ─────────────────────

# Upstream definitions
upstream odoo-web {
    server 127.0.0.1:8069;
}

upstream odoo-longpoll {
    server 127.0.0.1:8072;
}

upstream mcp-odoo {
    server 127.0.0.1:9004;
}

upstream ai-health {
    server 127.0.0.1:9090;
}

# HTTP → HTTPS redirect
server {
    listen 80;
    server_name odoo.yourdomain.com;
    return 301 https://$server_name$request_uri;
}

# Main HTTPS server
server {
    listen 443 ssl http2;
    server_name odoo.yourdomain.com;

    # ── SSL (managed by Certbot — see Section 9) ────────────────
    ssl_certificate     /etc/letsencrypt/live/odoo.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/odoo.yourdomain.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers on;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    # ── Security headers ────────────────────────────────────────
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-XSS-Protection "1; mode=block";

    # ── Logging ─────────────────────────────────────────────────
    access_log /var/log/nginx/odoo-access.log;
    error_log  /var/log/nginx/odoo-error.log;

    # ── Proxy settings ──────────────────────────────────────────
    proxy_read_timeout    720s;
    proxy_connect_timeout 720s;
    proxy_send_timeout    720s;
    proxy_set_header      Host $host;
    proxy_set_header      X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header      X-Forwarded-Proto $scheme;
    proxy_set_header      X-Real-IP $remote_addr;

    # ── Upload size (for attachments) ───────────────────────────
    client_max_body_size 50m;

    # ── JSON-RPC API (used by OdooClient) ───────────────────────
    location /jsonrpc {
        proxy_pass http://odoo-web;

        # Rate limit: 30 requests/sec per IP (burst 50)
        limit_req zone=odoo_api burst=50 nodelay;
    }

    # ── Longpolling (real-time notifications) ───────────────────
    location /longpolling {
        proxy_pass http://odoo-longpoll;
    }

    # ── Odoo Web UI ─────────────────────────────────────────────
    location / {
        proxy_pass http://odoo-web;
        proxy_redirect off;
    }

    # ── Static files (cache 30 days) ───────────────────────────
    location ~* /web/static/ {
        proxy_pass http://odoo-web;
        proxy_cache_valid 200 30d;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # ── MCP Odoo Server (internal API) ──────────────────────────
    location /mcp/odoo/ {
        proxy_pass http://mcp-odoo/;

        # Restrict to local + AI Employee API key
        # allow 127.0.0.1;
        # allow <LOCAL_MACHINE_IP>;
        # deny all;
    }

    # ── Health endpoint ─────────────────────────────────────────
    location /health {
        proxy_pass http://ai-health;
    }

    # ── Block dangerous paths ───────────────────────────────────
    location ~ ^/(web/database|web/proxy) {
        deny all;
        return 404;
    }
}
NGINX_EOF
```

### 8.3 Rate Limiting Zone

Add to `/etc/nginx/nginx.conf` inside the `http {}` block:

```nginx
# Rate limiting for Odoo JSON-RPC API
limit_req_zone $binary_remote_addr zone=odoo_api:10m rate=30r/s;
```

### 8.4 Enable and Test

```bash
# Enable site
sudo ln -sf /etc/nginx/sites-available/odoo /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Test configuration
sudo nginx -t

# Reload
sudo systemctl reload nginx
```

---

## 9. SSL / TLS

### 9.1 Install Certbot

```bash
sudo apt install -y certbot python3-certbot-nginx
```

### 9.2 Obtain Certificate

```bash
# Temporarily comment out the ssl_certificate lines in nginx config
# or use HTTP-only config first

sudo certbot --nginx -d odoo.yourdomain.com \
    --non-interactive --agree-tos \
    --email admin@yourdomain.com
```

### 9.3 Auto-Renewal

Certbot installs a systemd timer automatically. Verify:

```bash
sudo systemctl status certbot.timer
sudo certbot renew --dry-run
```

---

## 10. Firewall Rules

### 10.1 UFW Configuration

```bash
# Allow SSH
sudo ufw allow 22/tcp

# Allow HTTPS (Nginx)
sudo ufw allow 443/tcp

# Allow HTTP (redirect to HTTPS)
sudo ufw allow 80/tcp

# Block direct Odoo access from outside
# (Odoo listens on 127.0.0.1 only, but be explicit)
sudo ufw deny 8069
sudo ufw deny 8072

# Block direct PostgreSQL access
sudo ufw deny 5432

# Block direct MCP access
sudo ufw deny 9004

# Enable
sudo ufw enable
sudo ufw status verbose
```

### 10.2 Expected Status

```
Status: active

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW       Anywhere
443/tcp                    ALLOW       Anywhere
80/tcp                     ALLOW       Anywhere
8069                       DENY        Anywhere
8072                       DENY        Anywhere
5432                       DENY        Anywhere
9004                       DENY        Anywhere
```

---

## 11. Systemd Service

### 11.1 Odoo Service Unit

```bash
sudo tee /etc/systemd/system/odoo.service > /dev/null << 'EOF'
[Unit]
Description=Odoo 18.0 Community Edition
Documentation=https://www.odoo.com/documentation/18.0/
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=odoo
Group=odoo
WorkingDirectory=/opt/odoo

ExecStart=/opt/odoo/venv/bin/python /opt/odoo/odoo-server/odoo-bin \
    -c /etc/odoo/odoo.conf

# Restart policy
Restart=on-failure
RestartSec=10
StartLimitBurst=5
StartLimitIntervalSec=300

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/odoo/data /var/log/odoo
PrivateTmp=true

# Resource limits
LimitNOFILE=65535
TimeoutStartSec=120
TimeoutStopSec=60

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=odoo

[Install]
WantedBy=multi-user.target
EOF
```

### 11.2 Enable and Start

```bash
sudo systemctl daemon-reload
sudo systemctl enable odoo
sudo systemctl start odoo

# Verify
sudo systemctl status odoo
journalctl -u odoo -f --no-pager -n 50
```

### 11.3 Service Dependency Chain

```
postgresql.service
    └── odoo.service
            ├── ai-employee-mcp@odoo_mcp_server.service
            │       └── ai-employee-watchers.service (OdooWatcher)
            └── nginx.service (reverse proxy)
```

All AI Employee services should declare `After=odoo.service` to ensure Odoo is ready:

```ini
# In ai-employee-watchers.service
[Unit]
After=network.target postgresql.service odoo.service
```

---

## 12. Backup Strategy

### 12.1 Automated Database Backup

```bash
sudo mkdir -p /opt/odoo/backups
sudo chown odoo:odoo /opt/odoo/backups
```

Create `/opt/odoo/scripts/backup_odoo.sh`:

```bash
#!/usr/bin/env bash
# ── Odoo Database Backup Script ─────────────────────────────────
# Runs daily via cron. Keeps 14 days of backups.

set -euo pipefail

DB_NAME="ai_employee_accounting"
BACKUP_DIR="/opt/odoo/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}_${TIMESTAMP}.sql.gz"
RETENTION_DAYS=14

# Create compressed backup
sudo -u postgres pg_dump "$DB_NAME" | gzip > "$BACKUP_FILE"

# Also backup Odoo filestore (attachments, etc.)
FILESTORE_DIR="/opt/odoo/data/filestore/${DB_NAME}"
if [ -d "$FILESTORE_DIR" ]; then
    tar -czf "${BACKUP_DIR}/${DB_NAME}_filestore_${TIMESTAMP}.tar.gz" \
        -C "$FILESTORE_DIR" .
fi

# Delete backups older than retention period
find "$BACKUP_DIR" -name "${DB_NAME}_*.gz" -mtime +${RETENTION_DAYS} -delete

# Log
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Backup created: ${BACKUP_FILE}" \
    >> "${BACKUP_DIR}/backup.log"
```

```bash
sudo chmod +x /opt/odoo/scripts/backup_odoo.sh
```

### 12.2 Cron Schedule

```bash
# Run daily at 02:00 UTC
sudo crontab -u odoo -e
```

Add:

```
0 2 * * * /opt/odoo/scripts/backup_odoo.sh >> /var/log/odoo/backup.log 2>&1
```

### 12.3 Restore Procedure

```bash
# Stop Odoo
sudo systemctl stop odoo

# Drop and recreate database
sudo -u postgres dropdb ai_employee_accounting
sudo -u postgres createdb -O odoo ai_employee_accounting

# Restore
gunzip -c /opt/odoo/backups/ai_employee_accounting_YYYYMMDD_HHMMSS.sql.gz \
    | sudo -u postgres psql ai_employee_accounting

# Restore filestore
sudo -u odoo tar -xzf /opt/odoo/backups/ai_employee_accounting_filestore_YYYYMMDD_HHMMSS.tar.gz \
    -C /opt/odoo/data/filestore/ai_employee_accounting/

# Start Odoo
sudo systemctl start odoo
```

---

## 13. Health Monitoring

### 13.1 Odoo Health Check Script

Create `/opt/ai-employee/scripts/check_odoo_health.sh`:

```bash
#!/usr/bin/env bash
# ── Odoo Health Check ────────────────────────────────────────────
# Returns 0 if Odoo is healthy, 1 if not.

set -euo pipefail

ODOO_URL="${ODOO_URL:-http://127.0.0.1:8069}"
TIMEOUT=10

# 1. Check JSON-RPC endpoint
VERSION=$(curl -sf --max-time "$TIMEOUT" \
    -X POST "$ODOO_URL/jsonrpc" \
    -H "Content-Type: application/json" \
    -d '{
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": "common",
            "method": "version",
            "args": []
        },
        "id": 1
    }' | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('result', {}).get('server_version', 'unknown'))
" 2>/dev/null)

if [ -z "$VERSION" ] || [ "$VERSION" = "unknown" ]; then
    echo "UNHEALTHY: Odoo JSON-RPC not responding"
    exit 1
fi

# 2. Check database connectivity
AUTH=$(curl -sf --max-time "$TIMEOUT" \
    -X POST "$ODOO_URL/jsonrpc" \
    -H "Content-Type: application/json" \
    -d "{
        \"jsonrpc\": \"2.0\",
        \"method\": \"call\",
        \"params\": {
            \"service\": \"common\",
            \"method\": \"authenticate\",
            \"args\": [\"${ODOO_DB:-ai_employee_accounting}\", \"${ODOO_USERNAME:-admin}\", \"${ODOO_PASSWORD}\", {}]
        },
        \"id\": 2
    }" | python3 -c "
import sys, json
data = json.load(sys.stdin)
result = data.get('result')
print('ok' if isinstance(result, int) and result > 0 else 'fail')
" 2>/dev/null)

if [ "$AUTH" != "ok" ]; then
    echo "UNHEALTHY: Odoo authentication failed"
    exit 1
fi

# 3. Check PostgreSQL
if ! sudo -u postgres pg_isready -q; then
    echo "UNHEALTHY: PostgreSQL not ready"
    exit 1
fi

echo "HEALTHY: Odoo $VERSION — DB authenticated — PostgreSQL ready"
exit 0
```

```bash
sudo chmod +x /opt/ai-employee/scripts/check_odoo_health.sh
```

### 13.2 Integration with AI Employee Health Monitor

The `OdooWatcher` in `cloud_watchers.py` already monitors Odoo via its circuit breaker. The health endpoint at `:9090/health` includes Odoo status:

```json
{
  "status": "healthy",
  "watchers": {
    "odoo": {
      "enabled": true,
      "running": true,
      "circuit_state": "closed",
      "last_success": "2026-03-23T09:45:00Z",
      "items_processed": 5,
      "consecutive_failures": 0
    }
  }
}
```

### 13.3 Systemd Watchdog (Optional)

Add to the `[Service]` section of `odoo.service`:

```ini
WatchdogSec=120
```

This tells systemd to expect a health signal every 120s. Odoo's `--workers` mode uses `sd_notify` when compiled with systemd support.

---

## 14. Performance Tuning

### 14.1 Odoo Workers

| Workers | RAM Needed | Concurrent Users | Use Case         |
|---------|-----------|-------------------|------------------|
| 0       | ~300 MB   | 1 (dev mode)      | Development only |
| 2       | ~600 MB   | 5-10              | Light API usage  |
| 4       | ~1.2 GB   | 10-20             | **AI Employee**  |
| 8       | ~2.4 GB   | 20-50             | Heavy usage      |

For the AI Employee (primarily API calls, no concurrent human users):

```ini
workers = 4
max_cron_threads = 1
```

### 14.2 PostgreSQL Tuning for Odoo

Key parameters in `postgresql.conf`:

```ini
# Connection pooling
max_connections = 100

# Memory
shared_buffers = 1GB
effective_cache_size = 3GB
work_mem = 16MB

# Write performance
synchronous_commit = off       # OK for non-financial-critical reads
wal_level = replica
```

### 14.3 Odoo JSON-RPC Call Optimization

The `OdooClient` in `odoo_client.py` uses these optimizations:

- **Field selection:** Only requests needed fields (not `SELECT *`)
- **Domain filtering:** Server-side filtering reduces payload
- **Limit clauses:** Caps result sets (default 50)
- **Connection reuse:** Single `requests.Session` with 30s timeout
- **Cached UID:** Authenticates once per session

---

## 15. Troubleshooting

### 15.1 Common Issues

| Issue | Symptom | Fix |
|-------|---------|-----|
| Odoo won't start | `Port 8069 already in use` | `sudo lsof -i :8069` then kill stale process |
| Database error | `FATAL: role "odoo" does not exist` | `sudo -u postgres createuser --createdb odoo` |
| Module not found | `Module account not found` | Re-run with `-i account --stop-after-init` |
| Permission denied | `PermissionError: /opt/odoo/data` | `sudo chown -R odoo:odoo /opt/odoo/data` |
| PDF generation fails | `wkhtmltopdf not found` | Install the patched version (Section 3.2) |
| Slow queries | High CPU, timeouts | Check `limit_time_cpu`, tune PostgreSQL |
| Memory crash | `MemoryError` in logs | Lower `workers`, raise `limit_memory_hard` |
| JSON-RPC timeout | `OdooClient: Connection timed out` | Check Odoo is on `127.0.0.1:8069`, not `0.0.0.0` |
| Auth fails | `authenticate returns False` | Verify db name, username, password in .env |
| Nginx 502 | `Bad Gateway` | Check Odoo is running: `systemctl status odoo` |

### 15.2 Log Locations

| Log | Path |
|-----|------|
| Odoo server | `/var/log/odoo/odoo-server.log` |
| PostgreSQL | `/var/log/postgresql/postgresql-16-main.log` |
| Nginx access | `/var/log/nginx/odoo-access.log` |
| Nginx error | `/var/log/nginx/odoo-error.log` |
| MCP Odoo server | `/opt/ai-employee/ai_employee/logs/mcp_odoo.log` |
| Odoo Watcher | `/opt/ai-employee/ai_employee/logs/cloud_watchers.log` |
| Database backups | `/opt/odoo/backups/backup.log` |
| Systemd journal | `journalctl -u odoo -f` |

### 15.3 Diagnostic Commands

```bash
# Check Odoo is running
sudo systemctl status odoo
curl -s http://127.0.0.1:8069/web/webclient/version_info | python3 -m json.tool

# Check PostgreSQL
sudo -u postgres pg_isready
sudo -u postgres psql -d ai_employee_accounting -c "SELECT count(*) FROM res_users;"

# Check Nginx proxy
curl -I https://odoo.yourdomain.com

# Check MCP server
curl -s http://127.0.0.1:9004/health 2>/dev/null || echo "MCP server uses stdio, not HTTP"

# Check disk usage
df -h /opt/odoo
sudo -u postgres psql -c "SELECT pg_size_pretty(pg_database_size('ai_employee_accounting'));"

# Check Odoo worker processes
ps aux | grep odoo-bin

# Tail live logs
sudo tail -f /var/log/odoo/odoo-server.log
```

---

## 16. Post-Installation Checklist

Run through this checklist after deployment:

```
[ ] PostgreSQL 16 installed and running
[ ] Odoo system user created (/opt/odoo)
[ ] Odoo 18.0 source cloned and dependencies installed
[ ] /etc/odoo/odoo.conf configured with correct passwords
[ ] Database ai_employee_accounting created
[ ] Odoo systemd service enabled and started
[ ] Accounting module installed (account)
[ ] Expenses module installed (hr_expense)
[ ] Contacts module installed (contacts)
[ ] Chart of accounts configured
[ ] Dedicated API user created (recommended)
[ ] Nginx reverse proxy configured
[ ] SSL certificate obtained via Certbot
[ ] Firewall rules applied (UFW)
[ ] Direct ports blocked (8069, 8072, 5432, 9004)
[ ] JSON-RPC connectivity verified (curl test)
[ ] Authentication verified (curl test)
[ ] /etc/ai-employee/.env updated with Odoo credentials
[ ] AI Employee health check passes
[ ] Backup script installed and cron scheduled
[ ] All log directories exist with correct permissions
```

**Verification command:**

```bash
# Run the full health check
/opt/ai-employee/scripts/check_odoo_health.sh

# Test AI Employee's OdooClient
source /etc/ai-employee/.env
python3 -c "
from ai_employee.integrations.odoo_client import OdooClient
client = OdooClient(
    url='$ODOO_URL', db='$ODOO_DB',
    username='$ODOO_USERNAME', password='$ODOO_PASSWORD'
)
print('Authenticated as UID:', client._uid or client._ensure_authenticated())
invoices = client.get_invoices(limit=5)
print(f'Invoices found: {len(invoices)}')
report = client.get_profit_loss_summary()
print(f'P&L report: {\"OK\" if report else \"empty\"}')
"
```

---

**Next step:** See [odoo_mcp_setup.md](odoo_mcp_setup.md) for integrating the Odoo MCP server with this deployment.
