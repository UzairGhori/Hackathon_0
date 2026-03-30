#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  AI Employee — Production Startup Script
#
#  Validates the environment, checks dependencies, manages the PID file,
#  and launches the production runtime with proper process management.
#
#  Usage:
#      ./startup.sh                  # Start production system
#      ./startup.sh --once           # Single cycle
#      ./startup.sh --status         # Print system status
#      ./startup.sh --health         # Health check only
#      ./startup.sh stop             # Graceful stop
#      ./startup.sh restart          # Stop + start
#      ./startup.sh logs             # Tail production logs
#
#  Environment:
#      CYCLE_INTERVAL=5              minutes between cycles
#      GIT_SYNC_INTERVAL=300         seconds between git syncs
#      PRODUCTION_NO_GIT=0           set to 1 to disable git sync
#      PYTHON=python                 Python interpreter path
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}"
PID_FILE="${PROJECT_ROOT}/ai_employee.pid"
LOG_DIR="${PROJECT_ROOT}/ai_employee/logs"
PYTHON="${PYTHON:-python}"

CYCLE_INTERVAL="${CYCLE_INTERVAL:-5}"
GIT_SYNC_INTERVAL="${GIT_SYNC_INTERVAL:-300}"
PRODUCTION_NO_GIT="${PRODUCTION_NO_GIT:-0}"

# ── Colors ───────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'  # No Color

# ── Helper functions ─────────────────────────────────────────────────

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${CYAN}[STEP]${NC}  $*"; }

# ── Pre-flight checks ───────────────────────────────────────────────

preflight() {
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  AI Employee — Pre-flight Checks"
    echo "═══════════════════════════════════════════════════════════"
    echo ""

    local errors=0

    # 1. Python version
    log_step "Checking Python..."
    if ! command -v "${PYTHON}" &>/dev/null; then
        log_error "Python not found at '${PYTHON}'"
        log_error "Set PYTHON=/path/to/python or install Python 3.11+"
        errors=$((errors + 1))
    else
        PY_VERSION=$("${PYTHON}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        PY_MAJOR=$("${PYTHON}" -c "import sys; print(sys.version_info.major)")
        PY_MINOR=$("${PYTHON}" -c "import sys; print(sys.version_info.minor)")

        if [ "${PY_MAJOR}" -lt 3 ] || { [ "${PY_MAJOR}" -eq 3 ] && [ "${PY_MINOR}" -lt 11 ]; }; then
            log_error "Python ${PY_VERSION} found, but 3.11+ required"
            errors=$((errors + 1))
        else
            log_info "Python ${PY_VERSION} OK"
        fi
    fi

    # 2. Project structure
    log_step "Checking project structure..."
    for dir in "ai_employee" "vault" "vault/Inbox" "vault/Needs_Action" "vault/Done" "vault/Reports"; do
        if [ ! -d "${PROJECT_ROOT}/${dir}" ]; then
            mkdir -p "${PROJECT_ROOT}/${dir}"
            log_warn "Created missing directory: ${dir}/"
        fi
    done
    log_info "Project structure OK"

    # 3. .env file
    log_step "Checking environment..."
    if [ ! -f "${PROJECT_ROOT}/.env" ]; then
        log_warn ".env file not found — using system environment variables"
    else
        log_info ".env file found"
    fi

    # 4. Key dependencies
    log_step "Checking Python dependencies..."
    local missing_deps=()
    for pkg in dotenv anthropic fastapi uvicorn watchdog; do
        if ! "${PYTHON}" -c "import ${pkg}" 2>/dev/null; then
            missing_deps+=("${pkg}")
        fi
    done

    if [ ${#missing_deps[@]} -gt 0 ]; then
        log_warn "Missing packages: ${missing_deps[*]}"
        log_warn "Run: pip install -r requirements.txt"
        errors=$((errors + 1))
    else
        log_info "Python dependencies OK"
    fi

    # 5. Git repo
    log_step "Checking git repository..."
    if [ -d "${PROJECT_ROOT}/.git" ]; then
        BRANCH=$(git -C "${PROJECT_ROOT}" branch --show-current 2>/dev/null || echo "unknown")
        log_info "Git repo OK (branch: ${BRANCH})"
    else
        log_warn "Not a git repository — git sync will be disabled"
    fi

    # 6. Log directory
    mkdir -p "${LOG_DIR}"
    log_info "Log directory: ${LOG_DIR}"

    echo ""
    if [ ${errors} -gt 0 ]; then
        log_error "${errors} pre-flight check(s) failed"
        echo ""
        return 1
    fi

    log_info "All pre-flight checks passed"
    echo ""
    return 0
}

# ── PID management ───────────────────────────────────────────────────

is_running() {
    if [ -f "${PID_FILE}" ]; then
        local pid
        pid=$(cat "${PID_FILE}" 2>/dev/null)
        if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

get_pid() {
    if [ -f "${PID_FILE}" ]; then
        cat "${PID_FILE}" 2>/dev/null
    fi
}

# ── Commands ─────────────────────────────────────────────────────────

cmd_start() {
    local extra_args=("$@")

    # Check if already running
    if is_running; then
        local pid
        pid=$(get_pid)
        log_error "AI Employee is already running (PID ${pid})"
        log_error "Use '$0 stop' to stop it first, or '$0 restart' to restart"
        exit 1
    fi

    # Pre-flight checks
    if ! preflight; then
        log_error "Pre-flight checks failed. Fix the issues above and retry."
        exit 1
    fi

    # Build the command
    local cmd=("${PYTHON}" "${PROJECT_ROOT}/production_main.py")
    cmd+=("--interval" "${CYCLE_INTERVAL}")

    if [ "${PRODUCTION_NO_GIT}" = "1" ]; then
        cmd+=("--no-git")
    else
        cmd+=("--git-interval" "${GIT_SYNC_INTERVAL}")
    fi

    # Pass through extra args (--once, --verbose, etc.)
    cmd+=("${extra_args[@]}")

    echo "═══════════════════════════════════════════════════════════"
    echo "  AI Employee — Production Launch"
    echo "═══════════════════════════════════════════════════════════"
    echo ""
    log_info "Command:  ${cmd[*]}"
    log_info "Interval: ${CYCLE_INTERVAL} min"
    log_info "Git sync: $([ "${PRODUCTION_NO_GIT}" = "1" ] && echo "disabled" || echo "${GIT_SYNC_INTERVAL}s")"
    log_info "PID file: ${PID_FILE}"
    echo ""

    # Execute
    exec "${cmd[@]}"
}

cmd_stop() {
    if ! is_running; then
        log_info "AI Employee is not running"
        rm -f "${PID_FILE}"
        return 0
    fi

    local pid
    pid=$(get_pid)
    log_info "Stopping AI Employee (PID ${pid})..."

    # Send SIGTERM for graceful shutdown
    kill -TERM "${pid}" 2>/dev/null

    # Wait up to 30 seconds for graceful shutdown
    local waited=0
    while kill -0 "${pid}" 2>/dev/null && [ ${waited} -lt 30 ]; do
        sleep 1
        waited=$((waited + 1))
        if [ $((waited % 5)) -eq 0 ]; then
            log_info "Waiting for shutdown... (${waited}s)"
        fi
    done

    if kill -0 "${pid}" 2>/dev/null; then
        log_warn "Process didn't stop gracefully — sending SIGKILL"
        kill -KILL "${pid}" 2>/dev/null
        sleep 1
    fi

    rm -f "${PID_FILE}"
    log_info "AI Employee stopped"
}

cmd_restart() {
    cmd_stop
    sleep 2
    cmd_start "$@"
}

cmd_status() {
    if is_running; then
        local pid
        pid=$(get_pid)
        log_info "AI Employee is running (PID ${pid})"
        echo ""
        "${PYTHON}" "${PROJECT_ROOT}/production_main.py" --status 2>/dev/null || true
    else
        log_info "AI Employee is not running"
    fi
}

cmd_health() {
    "${PYTHON}" "${PROJECT_ROOT}/production_main.py" --health
}

cmd_logs() {
    local log_file
    log_file="${LOG_DIR}/production_$(date +%Y%m%d).log"

    if [ ! -f "${log_file}" ]; then
        log_warn "No log file for today: ${log_file}"
        # Try the most recent log
        log_file=$(ls -t "${LOG_DIR}"/production_*.log 2>/dev/null | head -1)
        if [ -z "${log_file}" ]; then
            log_error "No production logs found"
            exit 1
        fi
        log_info "Showing most recent: ${log_file}"
    fi

    echo "═══════════════════════════════════════════════════════════"
    echo "  Tailing: ${log_file}"
    echo "  Press Ctrl+C to stop"
    echo "═══════════════════════════════════════════════════════════"
    echo ""
    tail -f "${log_file}"
}

# ── Usage ────────────────────────────────────────────────────────────

usage() {
    cat <<EOF

AI Employee — Production Startup Script

Usage:
    $0 [command] [options]

Commands:
    start [opts]    Start the production system (default)
    stop            Graceful shutdown
    restart [opts]  Stop + start
    status          Show system status
    health          Run health checks
    logs            Tail production logs

Options (passed to production_main.py):
    --once          Single cycle, then exit
    --verbose       Debug-level logging
    --no-git        Disable git sync

Environment:
    CYCLE_INTERVAL       Minutes between cycles (default: 5)
    GIT_SYNC_INTERVAL    Seconds between git syncs (default: 300)
    PRODUCTION_NO_GIT    Set to 1 to disable git sync
    PYTHON               Python interpreter (default: python)

Examples:
    $0                           # Start with defaults
    $0 start --once              # Single production cycle
    $0 start --verbose           # Debug logging
    $0 restart                   # Restart the system
    $0 logs                      # Watch logs live
    CYCLE_INTERVAL=2 $0 start    # 2-minute cycles

EOF
}

# ── Entry point ──────────────────────────────────────────────────────

cd "${PROJECT_ROOT}"

COMMAND="${1:-start}"

case "${COMMAND}" in
    start)
        shift 2>/dev/null || true
        cmd_start "$@"
        ;;
    stop)
        cmd_stop
        ;;
    restart)
        shift 2>/dev/null || true
        cmd_restart "$@"
        ;;
    status)
        cmd_status
        ;;
    health)
        cmd_health
        ;;
    logs)
        cmd_logs
        ;;
    --once|--verbose|--health|--status|-v|-i)
        # User passed flags directly — treat as start
        cmd_start "$@"
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        log_error "Unknown command: ${COMMAND}"
        usage
        exit 1
        ;;
esac
