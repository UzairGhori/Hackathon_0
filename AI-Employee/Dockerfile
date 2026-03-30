# ══════════════════════════════════════════════════════════════════
#  AI Employee — Production Dockerfile
#  Platinum Tier | Oracle Cloud Free ARM64 Optimized
# ══════════════════════════════════════════════════════════════════

FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r aiemployee && useradd -r -g aiemployee -m -s /bin/bash aiemployee

# Working directory
WORKDIR /app

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY ai_employee/ ai_employee/
COPY production_main.py .
COPY startup.sh .
COPY scripts/ scripts/

# Copy vault structure templates (will be overridden by volume mount)
RUN mkdir -p vault/Inbox vault/Needs_Action vault/Done vault/Reports vault/logs \
    && mkdir -p AI_Employee_Vault/Needs_Approval \
    && mkdir -p ai_employee/logs

# Copy config files if they exist
COPY .env.example .env.example
COPY credentials.json* ./

# Set ownership
RUN chown -R aiemployee:aiemployee /app

# Switch to non-root user
USER aiemployee

# Dashboard port
EXPOSE 8080

# Health check — hits the dashboard /health endpoint
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Default entrypoint: production runtime
ENTRYPOINT ["python", "production_main.py"]

# Default args (can be overridden)
CMD ["--interval", "5", "--git-interval", "300"]
