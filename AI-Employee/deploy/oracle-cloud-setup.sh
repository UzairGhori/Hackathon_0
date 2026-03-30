#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  AI Employee — Oracle Cloud Free Tier Deployment Script
#
#  One-command setup for Oracle Cloud ARM64 VM (Always Free):
#    - Installs Docker + Docker Compose
#    - Configures firewall (iptables + Oracle Cloud)
#    - Clones repository
#    - Sets up systemd service
#    - Starts the full production stack
#
#  Prerequisites:
#    - Oracle Cloud ARM VM (Ampere A1: 4 OCPU, 24GB RAM)
#    - Ubuntu 22.04+ (or Oracle Linux 9)
#    - SSH access
#
#  Usage:
#    # On your Oracle Cloud VM:
#    curl -sSL https://raw.githubusercontent.com/YOUR_USERNAME/AI-Employee/master/deploy/oracle-cloud-setup.sh | bash
#
#    # Or clone first, then run:
#    git clone https://github.com/YOUR_USERNAME/AI-Employee.git
#    cd AI-Employee
#    chmod +x deploy/oracle-cloud-setup.sh
#    sudo ./deploy/oracle-cloud-setup.sh
# ══════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${CYAN}${BOLD}━━━ $* ━━━${NC}"; }

# ── Check root ──────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    log_error "Please run as root: sudo $0"
    exit 1
fi

DEPLOY_DIR="/opt/ai-employee"
REPO_URL="${REPO_URL:-https://github.com/YOUR_USERNAME/AI-Employee.git}"
BRANCH="${BRANCH:-master}"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  AI Employee — Oracle Cloud Deployment"
echo "  Platinum Tier | 24/7 Autonomous Digital Worker"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Target directory : ${DEPLOY_DIR}"
echo "  Repository       : ${REPO_URL}"
echo "  Branch           : ${BRANCH}"
echo ""

# ════════════════════════════════════════════════════════════════════
#  PHASE 1: System Update + Dependencies
# ════════════════════════════════════════════════════════════════════

log_step "PHASE 1: System Update"

apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
    curl \
    git \
    ca-certificates \
    gnupg \
    lsb-release \
    htop \
    unzip \
    jq

log_info "System packages updated"

# ════════════════════════════════════════════════════════════════════
#  PHASE 2: Install Docker
# ════════════════════════════════════════════════════════════════════

log_step "PHASE 2: Docker Installation"

if command -v docker &>/dev/null; then
    DOCKER_VERSION=$(docker --version | awk '{print $3}' | tr -d ',')
    log_info "Docker already installed (${DOCKER_VERSION})"
else
    # Add Docker's official GPG key
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    # Add Docker repository
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
        $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Enable and start Docker
    systemctl enable docker
    systemctl start docker

    log_info "Docker installed successfully"
fi

# Add current user to docker group (if not root session)
SUDO_USER_NAME="${SUDO_USER:-ubuntu}"
if id "${SUDO_USER_NAME}" &>/dev/null; then
    usermod -aG docker "${SUDO_USER_NAME}"
    log_info "User '${SUDO_USER_NAME}' added to docker group"
fi

# ════════════════════════════════════════════════════════════════════
#  PHASE 3: Firewall Configuration
# ════════════════════════════════════════════════════════════════════

log_step "PHASE 3: Firewall Configuration"

# Open required ports in iptables (Oracle Linux firewall)
echo "Opening ports: 80 (HTTP), 443 (HTTPS), 8080 (Dashboard), 8069 (Odoo)"

# Check if iptables rules exist, add if not
for PORT in 80 443 8080 8069; do
    if ! iptables -C INPUT -p tcp --dport ${PORT} -j ACCEPT 2>/dev/null; then
        iptables -I INPUT -p tcp --dport ${PORT} -j ACCEPT
        log_info "Opened port ${PORT}"
    else
        log_info "Port ${PORT} already open"
    fi
done

# Save iptables rules (persist across reboots)
if command -v netfilter-persistent &>/dev/null; then
    netfilter-persistent save
elif [ -f /etc/iptables/rules.v4 ]; then
    iptables-save > /etc/iptables/rules.v4
else
    # Install iptables-persistent for Ubuntu
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables-persistent
    netfilter-persistent save
fi

log_info "Firewall configured"

echo ""
echo "  ╔════════════════════════════════════════════════════════╗"
echo "  ║  IMPORTANT: Oracle Cloud Security List                ║"
echo "  ║                                                       ║"
echo "  ║  You must ALSO open these ports in Oracle Cloud       ║"
echo "  ║  Console → Networking → Virtual Cloud Network →       ║"
echo "  ║  Security Lists → Ingress Rules:                      ║"
echo "  ║                                                       ║"
echo "  ║    Port 80   (HTTP)     — 0.0.0.0/0  TCP             ║"
echo "  ║    Port 443  (HTTPS)    — 0.0.0.0/0  TCP             ║"
echo "  ║    Port 8080 (Dashboard)— 0.0.0.0/0  TCP             ║"
echo "  ║    Port 8069 (Odoo)     — 0.0.0.0/0  TCP             ║"
echo "  ╚════════════════════════════════════════════════════════╝"
echo ""

# ════════════════════════════════════════════════════════════════════
#  PHASE 4: Clone Repository
# ════════════════════════════════════════════════════════════════════

log_step "PHASE 4: Clone Repository"

if [ -d "${DEPLOY_DIR}/.git" ]; then
    log_info "Repository exists — pulling latest changes"
    cd "${DEPLOY_DIR}"
    git pull origin "${BRANCH}" || log_warn "Git pull failed — using existing code"
else
    log_info "Cloning repository..."
    git clone --branch "${BRANCH}" --single-branch "${REPO_URL}" "${DEPLOY_DIR}"
    cd "${DEPLOY_DIR}"
    log_info "Repository cloned to ${DEPLOY_DIR}"
fi

# ════════════════════════════════════════════════════════════════════
#  PHASE 5: Configure Environment
# ════════════════════════════════════════════════════════════════════

log_step "PHASE 5: Environment Configuration"

cd "${DEPLOY_DIR}"

# Create vault directories
mkdir -p vault/Inbox vault/Needs_Action vault/Done vault/Reports vault/logs
mkdir -p AI_Employee_Vault/Needs_Approval
mkdir -p ai_employee/logs

if [ ! -f .env ]; then
    cp .env.example .env
    log_warn ".env created from template — EDIT IT with your real credentials!"
    echo ""
    echo "  ╔════════════════════════════════════════════════════════╗"
    echo "  ║  REQUIRED: Edit /opt/ai-employee/.env                 ║"
    echo "  ║                                                       ║"
    echo "  ║  nano /opt/ai-employee/.env                           ║"
    echo "  ║                                                       ║"
    echo "  ║  At minimum, set:                                     ║"
    echo "  ║    ANTHROPIC_API_KEY=sk-ant-...                       ║"
    echo "  ║    EMAIL_ADDRESS=you@gmail.com                        ║"
    echo "  ║    EMAIL_PASSWORD=your-app-password                   ║"
    echo "  ╚════════════════════════════════════════════════════════╝"
    echo ""
else
    log_info ".env file already exists"
fi

# Update dashboard host for cloud access
if grep -q "DASHBOARD_HOST=127.0.0.1" .env 2>/dev/null; then
    sed -i 's/DASHBOARD_HOST=127.0.0.1/DASHBOARD_HOST=0.0.0.0/' .env
    log_info "Updated DASHBOARD_HOST to 0.0.0.0 for cloud access"
fi

# ════════════════════════════════════════════════════════════════════
#  PHASE 6: Install Systemd Service
# ════════════════════════════════════════════════════════════════════

log_step "PHASE 6: Systemd Service Setup"

# Copy and configure the service file
cp deploy/ai-employee.service /etc/systemd/system/ai-employee.service

# Update the working directory in the service file
sed -i "s|WorkingDirectory=.*|WorkingDirectory=${DEPLOY_DIR}|" /etc/systemd/system/ai-employee.service

# Reload systemd
systemctl daemon-reload
systemctl enable ai-employee

log_info "Systemd service installed and enabled"

# ════════════════════════════════════════════════════════════════════
#  PHASE 7: Build & Start
# ════════════════════════════════════════════════════════════════════

log_step "PHASE 7: Build & Launch"

cd "${DEPLOY_DIR}"

# Build the Docker image
echo "Building Docker image (this may take 2-3 minutes)..."
docker compose -f docker-compose.production.yml build --no-cache

# Start all services
echo "Starting production stack..."
docker compose -f docker-compose.production.yml up -d

# Wait for services to stabilize
echo "Waiting for services to start..."
sleep 15

# ════════════════════════════════════════════════════════════════════
#  PHASE 8: Verify Deployment
# ════════════════════════════════════════════════════════════════════

log_step "PHASE 8: Verification"

echo ""
echo "Container Status:"
docker compose -f docker-compose.production.yml ps
echo ""

# Check each service
SERVICES_OK=0
SERVICES_TOTAL=0

for SVC in ai-employee odoo-server odoo-db ai-nginx; do
    SERVICES_TOTAL=$((SERVICES_TOTAL + 1))
    if docker ps --format '{{.Names}}' | grep -q "^${SVC}$"; then
        log_info "${SVC} is running"
        SERVICES_OK=$((SERVICES_OK + 1))
    else
        log_error "${SVC} is NOT running"
    fi
done

# Test dashboard endpoint
echo ""
if curl -sf http://localhost:8080/health > /dev/null 2>&1; then
    log_info "Dashboard health check: PASSED"
else
    log_warn "Dashboard health check: PENDING (may need more startup time)"
fi

# ════════════════════════════════════════════════════════════════════
#  DONE
# ════════════════════════════════════════════════════════════════════

# Get the public IP
PUBLIC_IP=$(curl -sf http://ifconfig.me 2>/dev/null || echo "YOUR_VM_IP")

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  AI Employee — Deployment Complete!"
echo ""
echo "  ${SERVICES_OK}/${SERVICES_TOTAL} services running"
echo ""
echo "  ┌──────────────────────────────────────────────────────────┐"
echo "  │  Dashboard   : http://${PUBLIC_IP}                       │"
echo "  │  Dashboard   : http://${PUBLIC_IP}:8080                  │"
echo "  │  Odoo ERP    : http://${PUBLIC_IP}:8069                  │"
echo "  │  Odoo (proxy): http://${PUBLIC_IP}/odoo/                 │"
echo "  └──────────────────────────────────────────────────────────┘"
echo ""
echo "  Management Commands:"
echo "    sudo systemctl status ai-employee   # Check status"
echo "    sudo systemctl restart ai-employee  # Restart"
echo "    sudo systemctl stop ai-employee     # Stop"
echo "    sudo journalctl -u ai-employee -f   # View logs"
echo ""
echo "    cd ${DEPLOY_DIR}"
echo "    docker compose -f docker-compose.production.yml logs -f ai-employee"
echo "    docker compose -f docker-compose.production.yml ps"
echo ""
echo "  IMPORTANT:"
echo "    1. Edit .env: nano ${DEPLOY_DIR}/.env"
echo "    2. Restart:   sudo systemctl restart ai-employee"
echo "    3. Open Oracle Cloud Security List ports: 80, 443, 8080, 8069"
echo ""
echo "═══════════════════════════════════════════════════════════════"
