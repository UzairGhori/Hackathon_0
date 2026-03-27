#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# git_sync.sh — Platinum Tier vault sync orchestrator
#
# Initialises the git repo (if needed), then runs a pull→push cycle every
# SYNC_INTERVAL seconds.  Designed for both Cloud (creates Inbox/Drafts/Reports)
# and Local (approves Payments/Emails/Posts) roles.
#
# Usage:
#   SYNC_ROLE=cloud  ./scripts/git_sync.sh          # on Cloud VM
#   SYNC_ROLE=local  ./scripts/git_sync.sh          # on Local machine
#   SYNC_ROLE=cloud  ./scripts/git_sync.sh --once   # single cycle, then exit
#
# Environment variables (all optional — sensible defaults provided):
#   SYNC_ROLE           cloud | local                (default: local)
#   SYNC_INTERVAL       seconds between cycles       (default: 300 = 5 min)
#   SYNC_REMOTE         git remote name              (default: origin)
#   SYNC_BRANCH         git branch name              (default: main)
#   SYNC_REPO_URL       remote repo URL for init     (default: "")
#   PROJECT_ROOT        path to AI-Employee root     (auto-detected)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Resolve project root ────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(dirname "$SCRIPT_DIR")}"
cd "$PROJECT_ROOT"

# ── Configuration ───────────────────────────────────────────────────────────
SYNC_ROLE="${SYNC_ROLE:-local}"
SYNC_INTERVAL="${SYNC_INTERVAL:-300}"
SYNC_REMOTE="${SYNC_REMOTE:-origin}"
SYNC_BRANCH="${SYNC_BRANCH:-main}"
SYNC_REPO_URL="${SYNC_REPO_URL:-}"

LOG_DIR="$PROJECT_ROOT/ai_employee/logs"
SYNC_LOG="$LOG_DIR/sync.log"
LOCK_FILE="$PROJECT_ROOT/.sync.lock"

# ── Helpers ─────────────────────────────────────────────────────────────────
ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

log_info()  { echo "[$(ts)] [INFO]  $*" | tee -a "$SYNC_LOG"; }
log_warn()  { echo "[$(ts)] [WARN]  $*" | tee -a "$SYNC_LOG" >&2; }
log_error() { echo "[$(ts)] [ERROR] $*" | tee -a "$SYNC_LOG" >&2; }

cleanup() {
    rm -f "$LOCK_FILE"
    log_info "Sync stopped (PID $$)"
}
trap cleanup EXIT INT TERM

# Prevent duplicate instances
acquire_lock() {
    if [ -f "$LOCK_FILE" ]; then
        local old_pid
        old_pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        # Check if that PID is still running
        if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
            log_error "Another sync is running (PID $old_pid). Exiting."
            exit 1
        fi
        log_warn "Stale lock file found (PID $old_pid gone). Removing."
        rm -f "$LOCK_FILE"
    fi
    echo $$ > "$LOCK_FILE"
}

# ── Ensure git repo exists ──────────────────────────────────────────────────
init_repo() {
    mkdir -p "$LOG_DIR"

    if [ -d ".git" ]; then
        log_info "Git repo already initialised"
        return 0
    fi

    log_info "Initialising git repository ..."
    git init -b "$SYNC_BRANCH"

    # Configure for vault sync
    git config user.name  "AI-Employee ($SYNC_ROLE)"
    git config user.email "ai-employee-${SYNC_ROLE}@noreply.local"

    # Prevent interactive editors
    git config core.editor true

    # Add remote if URL provided
    if [ -n "$SYNC_REPO_URL" ]; then
        git remote add "$SYNC_REMOTE" "$SYNC_REPO_URL" 2>/dev/null || \
            git remote set-url "$SYNC_REMOTE" "$SYNC_REPO_URL"
        log_info "Remote '$SYNC_REMOTE' set to $SYNC_REPO_URL"
    fi

    # Ensure .gitignore is in place before first commit
    ensure_gitignore

    # Seed commit
    git add .gitignore
    git commit -m "init: seed repo ($SYNC_ROLE)" --allow-empty || true

    log_info "Git repo initialised on branch '$SYNC_BRANCH'"
}

# ── Ensure .gitignore covers secrets ────────────────────────────────────────
ensure_gitignore() {
    local gi="$PROJECT_ROOT/.gitignore"
    local needs_update=0

    # Required entries that must be present
    local -a required=(
        ".env"
        "credentials.json"
        "token.json"
        "*.token"
        "*.key"
        ".sync.lock"
    )

    for entry in "${required[@]}"; do
        if ! grep -qxF "$entry" "$gi" 2>/dev/null; then
            needs_update=1
            break
        fi
    done

    if [ "$needs_update" -eq 1 ]; then
        {
            echo ""
            echo "# ── Sync safety (auto-added by git_sync.sh) ──"
            for entry in "${required[@]}"; do
                grep -qxF "$entry" "$gi" 2>/dev/null || echo "$entry"
            done
        } >> "$gi"
        log_info ".gitignore updated with sync-safety entries"
    fi
}

# ── Ensure vault directories exist ──────────────────────────────────────────
ensure_dirs() {
    local -a dirs=(
        "vault/Inbox"
        "vault/Needs_Action"
        "vault/Needs_Approval"
        "vault/Done"
        "vault/Reports"
        "AI_Employee_Vault/Needs_Approval"
        "AI_Employee_Vault/Done"
        "ai_employee/logs"
    )
    for d in "${dirs[@]}"; do
        mkdir -p "$PROJECT_ROOT/$d"
        # Git won't track empty dirs — add .gitkeep
        if [ ! -f "$PROJECT_ROOT/$d/.gitkeep" ] && [ -z "$(ls -A "$PROJECT_ROOT/$d" 2>/dev/null)" ]; then
            touch "$PROJECT_ROOT/$d/.gitkeep"
        fi
    done
}

# ── Single sync cycle ───────────────────────────────────────────────────────
run_cycle() {
    local cycle_num="$1"
    local pull_ok=0
    local push_ok=0

    log_info "━━━ Cycle #$cycle_num ($SYNC_ROLE) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Step 1: Pull remote changes
    log_info "Pulling ..."
    if bash "$SCRIPT_DIR/auto_pull.sh"; then
        pull_ok=1
        log_info "Pull succeeded"
    else
        log_warn "Pull failed (exit $?) — will retry next cycle"
    fi

    # Step 2: Push local changes
    log_info "Pushing ..."
    if bash "$SCRIPT_DIR/auto_push.sh"; then
        push_ok=1
        log_info "Push succeeded"
    else
        log_warn "Push failed (exit $?) — changes buffered locally"
    fi

    # Summary
    if [ "$pull_ok" -eq 1 ] && [ "$push_ok" -eq 1 ]; then
        log_info "Cycle #$cycle_num complete ✓"
    else
        log_warn "Cycle #$cycle_num partial (pull=$pull_ok push=$push_ok)"
    fi
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    local once=0
    for arg in "$@"; do
        case "$arg" in
            --once) once=1 ;;
            --cloud) SYNC_ROLE="cloud" ;;
            --local) SYNC_ROLE="local" ;;
            *) log_error "Unknown argument: $arg"; exit 1 ;;
        esac
    done

    # Export role for child scripts
    export SYNC_ROLE
    export SYNC_REMOTE
    export SYNC_BRANCH
    export PROJECT_ROOT

    acquire_lock
    init_repo
    ensure_dirs

    log_info "═══════════════════════════════════════════════════════"
    log_info "  AI Employee — Platinum Tier Git Sync"
    log_info "  Role:     $SYNC_ROLE"
    log_info "  Interval: ${SYNC_INTERVAL}s ($(( SYNC_INTERVAL / 60 ))m)"
    log_info "  Remote:   $SYNC_REMOTE / $SYNC_BRANCH"
    log_info "  PID:      $$"
    log_info "═══════════════════════════════════════════════════════"

    if [ "$once" -eq 1 ]; then
        run_cycle 1
        exit 0
    fi

    local cycle=0
    while true; do
        cycle=$((cycle + 1))
        run_cycle "$cycle"

        log_info "Sleeping ${SYNC_INTERVAL}s until next cycle ..."
        sleep "$SYNC_INTERVAL"
    done
}

main "$@"
