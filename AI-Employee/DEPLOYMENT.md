# AI Employee — Oracle Cloud 24/7 Deployment Guide

> Deploy your AI Employee to run **24/7 for FREE** on Oracle Cloud Always Free Tier.

---

## Why Oracle Cloud Free Tier?

| Resource | Free Forever |
|----------|-------------|
| **Compute** | ARM Ampere A1: 4 OCPUs, 24GB RAM |
| **Storage** | 200GB block volume |
| **Network** | 10TB/month outbound |
| **Cost** | $0/month — forever |

This is more than enough to run the entire AI Employee stack (app + Odoo + PostgreSQL + Nginx).

---

## Step 1: Create Oracle Cloud Account

1. Go to [cloud.oracle.com](https://cloud.oracle.com)
2. Sign up for a **Free Tier** account
3. Complete identity verification (requires credit card but **will NOT be charged**)
4. Wait for account activation (5-30 minutes)

---

## Step 2: Create ARM VM Instance

1. Go to **Oracle Cloud Console** → **Compute** → **Instances** → **Create Instance**

2. Configure:

   | Setting | Value |
   |---------|-------|
   | **Name** | `ai-employee` |
   | **Image** | Ubuntu 22.04 (or 24.04) |
   | **Shape** | Ampere A1 Flex |
   | **OCPUs** | 4 (max free) |
   | **Memory** | 24 GB (max free) |
   | **Boot Volume** | 100 GB |

3. **Networking**: Use default VCN or create a new one

4. **SSH Key**: Upload your public key or generate one
   ```bash
   # Generate SSH key (on your local machine)
   ssh-keygen -t ed25519 -f ~/.ssh/oracle-ai-employee
   ```

5. Click **Create** and wait for the instance to be **Running**

6. Note the **Public IP Address** from the instance details

---

## Step 3: Configure Oracle Cloud Firewall

The VM has TWO firewalls — both must be configured.

### A. Oracle Cloud Security List (Console)

1. Go to **Networking** → **Virtual Cloud Networks** → click your VCN
2. Click **Security Lists** → **Default Security List**
3. Click **Add Ingress Rules** and add:

   | Source CIDR | Protocol | Dest Port | Description |
   |------------|----------|-----------|-------------|
   | `0.0.0.0/0` | TCP | 80 | HTTP (Nginx) |
   | `0.0.0.0/0` | TCP | 443 | HTTPS (Nginx) |
   | `0.0.0.0/0` | TCP | 8080 | AI Dashboard |
   | `0.0.0.0/0` | TCP | 8069 | Odoo ERP |

### B. OS Firewall (done automatically by deploy script)

The deploy script handles iptables rules automatically.

---

## Step 4: SSH into Your VM

```bash
ssh -i ~/.ssh/oracle-ai-employee ubuntu@YOUR_PUBLIC_IP
```

---

## Step 5: One-Command Deploy

### Option A: Automatic Setup (Recommended)

```bash
# Clone the repository
sudo git clone https://github.com/YOUR_USERNAME/AI-Employee.git /opt/ai-employee
cd /opt/ai-employee

# Run the deployment script
sudo chmod +x deploy/oracle-cloud-setup.sh
sudo ./deploy/oracle-cloud-setup.sh
```

This script automatically:
- Installs Docker + Docker Compose
- Configures OS firewall
- Creates vault directories
- Builds Docker images
- Starts all services
- Enables auto-start on boot

### Option B: Manual Setup

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# 2. Clone repository
sudo git clone https://github.com/YOUR_USERNAME/AI-Employee.git /opt/ai-employee
cd /opt/ai-employee

# 3. Configure environment
cp .env.example .env
nano .env   # Fill in your API keys

# 4. Open firewall ports
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 8080 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 8069 -j ACCEPT
sudo apt install -y iptables-persistent
sudo netfilter-persistent save

# 5. Build and start
docker compose -f docker-compose.production.yml up -d --build

# 6. Install systemd service
sudo cp deploy/ai-employee.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ai-employee
sudo systemctl start ai-employee
```

---

## Step 6: Configure Your API Keys

```bash
sudo nano /opt/ai-employee/.env
```

Set at minimum:

```env
# REQUIRED
ANTHROPIC_API_KEY=sk-ant-api03-...
EMAIL_ADDRESS=you@gmail.com
EMAIL_PASSWORD=your-app-password

# Cloud-specific settings (already set by deploy script)
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8080
ODOO_URL=http://odoo:8069

# OPTIONAL (enable more agents)
LINKEDIN_EMAIL=...
LINKEDIN_PASSWORD=...
ODOO_DB=mycompany
ODOO_USERNAME=admin
ODOO_PASSWORD=admin
META_ACCESS_TOKEN=...
TWITTER_BEARER_TOKEN=...
```

After editing, restart:

```bash
sudo systemctl restart ai-employee
```

---

## Step 7: Verify Deployment

### Check all services are running:

```bash
cd /opt/ai-employee
docker compose -f docker-compose.production.yml ps
```

Expected output:

```
NAME            STATUS          PORTS
ai-employee     Up (healthy)    0.0.0.0:8080->8080/tcp
odoo-server     Up              0.0.0.0:8069->8069/tcp
odoo-db         Up (healthy)    5432/tcp
ai-nginx        Up              0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
watchtower      Up
```

### Access in browser:

| Service | URL |
|---------|-----|
| **AI Dashboard** | `http://YOUR_PUBLIC_IP` |
| **AI Dashboard** | `http://YOUR_PUBLIC_IP:8080` |
| **Odoo ERP** | `http://YOUR_PUBLIC_IP:8069` |

### Check logs:

```bash
# AI Employee logs
docker compose -f docker-compose.production.yml logs -f ai-employee

# All services
docker compose -f docker-compose.production.yml logs -f

# Systemd service
sudo journalctl -u ai-employee -f
```

---

## Management Commands

### Daily Operations

```bash
cd /opt/ai-employee

# Check status
docker compose -f docker-compose.production.yml ps
sudo systemctl status ai-employee

# View live logs
docker compose -f docker-compose.production.yml logs -f ai-employee

# Restart everything
sudo systemctl restart ai-employee

# Stop everything
sudo systemctl stop ai-employee

# Start everything
sudo systemctl start ai-employee
```

### Update to Latest Code

```bash
cd /opt/ai-employee
git pull origin master
docker compose -f docker-compose.production.yml up -d --build
```

### View Resource Usage

```bash
docker stats --no-stream
htop
```

### Backup Vault Data

```bash
# Manual backup
tar -czf vault-backup-$(date +%Y%m%d).tar.gz vault/ AI_Employee_Vault/

# The system also auto-syncs to git (configurable interval)
```

---

## Architecture on Oracle Cloud

```
                    Internet
                       │
                       ▼
              ┌────────────────┐
              │   Oracle Cloud  │
              │   Free Tier VM  │
              │  ARM64 / 24GB   │
              └────────┬───────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
    ┌─────────┐  ┌──────────┐  ┌─────────┐
    │  Nginx  │  │ AI Emp.  │  │  Odoo   │
    │  :80    │──│  :8080   │  │  :8069  │
    │  Proxy  │  │  Python  │  │  ERP    │
    └─────────┘  └────┬─────┘  └────┬────┘
                      │             │
                      ▼             ▼
                ┌──────────┐  ┌─────────┐
                │  Vault   │  │ Postgres│
                │  (files) │  │  :5432  │
                └──────────┘  └─────────┘
```

---

## Troubleshooting

### Container won't start

```bash
# Check container logs
docker compose -f docker-compose.production.yml logs ai-employee

# Check if port is already in use
sudo ss -tlnp | grep -E '8080|8069|80'

# Rebuild from scratch
docker compose -f docker-compose.production.yml down -v
docker compose -f docker-compose.production.yml up -d --build
```

### Can't access dashboard from browser

1. Check Oracle Cloud Security List (Step 3A)
2. Check OS firewall: `sudo iptables -L -n | grep 8080`
3. Check container is healthy: `docker ps`
4. Check DASHBOARD_HOST is `0.0.0.0` (not `127.0.0.1`) in `.env`

### Out of memory

```bash
# Check memory usage
free -h
docker stats --no-stream

# Reduce Odoo memory in docker-compose.production.yml
# Change memory limit from 1G to 512M
```

### Odoo not connecting

```bash
# Check if PostgreSQL is healthy
docker compose -f docker-compose.production.yml exec odoo-db pg_isready -U odoo

# Check Odoo logs
docker compose -f docker-compose.production.yml logs odoo
```

---

## Security Checklist

- [ ] Changed default Odoo password (admin/admin)
- [ ] Set strong ODOO_DB_PASSWORD in .env
- [ ] Restricted SSH access (key-only, no password)
- [ ] .env file has proper permissions (`chmod 600 .env`)
- [ ] No secrets committed to git
- [ ] Oracle Cloud Security List restricts to needed ports only
- [ ] Enabled automatic security updates on VM

```bash
# Enable automatic security updates (Ubuntu)
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades

# Restrict .env permissions
chmod 600 /opt/ai-employee/.env
```

---

## Cost Summary

| Component | Monthly Cost |
|-----------|-------------|
| Oracle Cloud VM (4 OCPU, 24GB) | **FREE** |
| Block Storage (100GB) | **FREE** |
| Network (10TB) | **FREE** |
| Docker | **FREE** |
| Odoo Community | **FREE** |
| **Total** | **$0/month** |

---

Built for the Agent Factory Hackathon 0 | Platinum Tier
