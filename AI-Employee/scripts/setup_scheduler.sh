#!/usr/bin/env bash
# ============================================
# AI Employee — Linux/Mac Cron Setup
# ============================================
# Adds a cron job that runs the AI Employee
# every 5 minutes.
#
# Usage: bash scripts/setup_scheduler.sh
# ============================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_EXE="$(which python3 || which python)"
SCRIPT_PATH="$PROJECT_DIR/scripts/run_ai_employee.py"
CRON_MARKER="# AI_Employee_Scheduler"

echo ""
echo "================================================"
echo "  AI Employee — Cron Job Setup"
echo "================================================"
echo ""
echo "  Script   : $SCRIPT_PATH"
echo "  Python   : $PYTHON_EXE"
echo "  Interval : Every 5 minutes"
echo ""

if [ -z "$PYTHON_EXE" ]; then
    echo "  [ERROR] Python not found. Install Python 3.10+ first."
    exit 1
fi

# Build the cron line
CRON_LINE="*/5 * * * * cd \"$PROJECT_DIR\" && \"$PYTHON_EXE\" \"$SCRIPT_PATH\" --once >> \"$PROJECT_DIR/vault/logs/cron.log\" 2>&1 $CRON_MARKER"

# Remove old entry if it exists, then add the new one
(crontab -l 2>/dev/null | grep -v "$CRON_MARKER"; echo "$CRON_LINE") | crontab -

if [ $? -eq 0 ]; then
    echo "  [SUCCESS] Cron job installed."
    echo ""
    echo "  Useful commands:"
    echo "    View cron:    crontab -l"
    echo "    Remove:       crontab -l | grep -v '$CRON_MARKER' | crontab -"
    echo ""
else
    echo "  [ERROR] Failed to install cron job."
    exit 1
fi
