#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# auto_push.sh — Stage, sanitise, commit, and push vault changes
#
# Called by git_sync.sh every cycle.  Can also be run standalone:
#   SYNC_ROLE=cloud ./scripts/auto_push.sh
#
# What gets staged depends on the SYNC_ROLE:
#
#   cloud:
#     vault/Inbox/*           (cloud creates inbox items)
#     vault/Needs_Action/*    (cloud creates draft replies)
#     vault/Reports/*         (cloud creates reports)
#     vault/Done/*            (cloud moves completed work)
#     AI_Employee_Vault/*     (cloud writes approval requests)
#     ai_employee/logs/*      (cloud writes audit trail)
#     vault/*.json            (cloud updates queue state)
#
#   local:
#     vault/Needs_Approval/*          (local writes approval decisions)
#     AI_Employee_Vault/Needs_Approval/*  (local writes approval decisions)
#     vault/Done/*                    (local moves completed work)
#     ai_employee/                    (local pushes code changes)
#
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(dirname "$SCRIPT_DIR")}"
cd "$PROJECT_ROOT"

SYNC_ROLE="${SYNC_ROLE:-local}"
SYNC_REMOTE="${SYNC_REMOTE:-origin}"
SYNC_BRANCH="${SYNC_BRANCH:-main}"

LOG_DIR="$PROJECT_ROOT/ai_employee/logs"
SYNC_LOG="$LOG_DIR/sync.log"

ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log_info()  { echo "[$(ts)] [PUSH]  $*" | tee -a "$SYNC_LOG"; }
log_warn()  { echo "[$(ts)] [PUSH]  WARN: $*" | tee -a "$SYNC_LOG" >&2; }
log_error() { echo "[$(ts)] [PUSH]  ERROR: $*" | tee -a "$SYNC_LOG" >&2; }

# ── Pre-flight checks ──────────────────────────────────────────────────────
if [ ! -d ".git" ]; then
    log_error "Not a git repository. Run git_sync.sh first."
    exit 1
fi

# ── Sanitise staged files — strip secrets before commit ─────────────────────
sanitise_staged() {
    # Patterns that should never appear in committed files
    local -a patterns=(
        's/[Pp]assword\s*[:=]\s*\S\+/[REDACTED]/g'
        's/[Tt]oken\s*[:=]\s*\S\+/[REDACTED]/g'
        's/[Aa]pi[_-]\?[Kk]ey\s*[:=]\s*\S\+/[REDACTED]/g'
        's/sk-ant-[a-zA-Z0-9]\{20,\}/[REDACTED]/g'
        's/Bearer [a-zA-Z0-9._-]\{20,\}/Bearer [REDACTED]/g'
        's/xox[bpas]-[a-zA-Z0-9-]\{20,\}/[REDACTED]/g'
    )

    # Only sanitise vault text files (not JSON state files)
    local staged_files
    staged_files=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)

    while IFS= read -r filepath; do
        [ -z "$filepath" ] && continue

        # Only sanitise .md files in vault directories
        case "$filepath" in
            vault/*.md|AI_Employee_Vault/*.md)
                local dirty=0
                for pat in "${patterns[@]}"; do
                    if grep -qE '(password|token|api.?key|sk-ant-|Bearer |xox[bpas]-)' "$filepath" 2>/dev/null; then
                        sed -i "$pat" "$filepath" 2>/dev/null || true
                        dirty=1
                    fi
                done
                if [ "$dirty" -eq 1 ]; then
                    git add "$filepath"
                    log_warn "Sanitised secrets in $filepath"
                fi
                ;;
        esac
    done <<< "$staged_files"
}

# ── Stage files based on role ───────────────────────────────────────────────
stage_changes() {
    local staged_count=0

    if [ "$SYNC_ROLE" = "cloud" ]; then
        # ── CLOUD creates: Inbox, Drafts (Needs_Action), Reports ────────
        local -a cloud_paths=(
            "vault/Inbox/"
            "vault/Needs_Action/"
            "vault/Reports/"
            "vault/Done/"
            "vault/approval_queue.json"
            "vault/task_queue.json"
            "vault/gmail_processed_ids.json"
            "vault/linkedin_processed_ids.json"
            "AI_Employee_Vault/Needs_Approval/"
            "AI_Employee_Vault/Done/"
            "ai_employee/logs/"
        )

        for p in "${cloud_paths[@]}"; do
            if [ -e "$PROJECT_ROOT/$p" ]; then
                git add "$p" 2>/dev/null || true
            fi
        done

    elif [ "$SYNC_ROLE" = "local" ]; then
        # ── LOCAL approves: Payments, Emails, Posts ─────────────────────
        local -a local_paths=(
            "vault/Needs_Approval/"
            "AI_Employee_Vault/Needs_Approval/"
            "vault/Done/"
            "AI_Employee_Vault/Done/"
        )

        for p in "${local_paths[@]}"; do
            if [ -e "$PROJECT_ROOT/$p" ]; then
                git add "$p" 2>/dev/null || true
            fi
        done

        # Also stage code changes (local is the code authority)
        # Only add tracked+modified Python files — don't blindly add everything
        local modified_code
        modified_code=$(git diff --name-only -- 'ai_employee/' '*.py' 'scripts/' 2>/dev/null || true)
        if [ -n "$modified_code" ]; then
            while IFS= read -r f; do
                [ -z "$f" ] && continue
                git add "$f" 2>/dev/null || true
            done <<< "$modified_code"
        fi

        # Stage new untracked approval files
        local untracked
        untracked=$(git ls-files --others --exclude-standard \
            -- 'vault/Needs_Approval/' 'AI_Employee_Vault/Needs_Approval/' \
               'vault/Done/' 'AI_Employee_Vault/Done/' 2>/dev/null || true)
        if [ -n "$untracked" ]; then
            while IFS= read -r f; do
                [ -z "$f" ] && continue
                git add "$f" 2>/dev/null || true
            done <<< "$untracked"
        fi
    fi

    # Always stage .gitignore and .gitattributes if changed
    git add .gitignore .gitattributes 2>/dev/null || true

    # Count what's staged
    staged_count=$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
    echo "$staged_count"
}

# ── Build commit message ───────────────────────────────────────────────────
build_commit_message() {
    local staged_files
    staged_files=$(git diff --cached --name-only 2>/dev/null || true)

    # Count by directory
    local inbox=0 drafts=0 reports=0 done=0 approvals=0 logs=0 code=0 other=0

    while IFS= read -r f; do
        [ -z "$f" ] && continue
        case "$f" in
            vault/Inbox/*)            inbox=$((inbox + 1)) ;;
            vault/Needs_Action/*)     drafts=$((drafts + 1)) ;;
            vault/Reports/*)          reports=$((reports + 1)) ;;
            vault/Done/*)             done=$((done + 1)) ;;
            vault/Needs_Approval/*|AI_Employee_Vault/Needs_Approval/*)
                                      approvals=$((approvals + 1)) ;;
            ai_employee/logs/*)       logs=$((logs + 1)) ;;
            ai_employee/*|*.py)       code=$((code + 1)) ;;
            *)                        other=$((other + 1)) ;;
        esac
    done <<< "$staged_files"

    # Build summary parts
    local parts=()
    [ "$inbox"     -gt 0 ] && parts+=("${inbox} inbox")
    [ "$drafts"    -gt 0 ] && parts+=("${drafts} drafts")
    [ "$reports"   -gt 0 ] && parts+=("${reports} reports")
    [ "$approvals" -gt 0 ] && parts+=("${approvals} approvals")
    [ "$done"      -gt 0 ] && parts+=("${done} done")
    [ "$logs"      -gt 0 ] && parts+=("${logs} logs")
    [ "$code"      -gt 0 ] && parts+=("${code} code")
    [ "$other"     -gt 0 ] && parts+=("${other} other")

    local summary=""
    if [ ${#parts[@]} -gt 0 ]; then
        summary=$(IFS=", "; echo "${parts[*]}")
    else
        summary="sync"
    fi

    echo "$SYNC_ROLE: $summary"
}

# ── Push with retry ─────────────────────────────────────────────────────────
push_with_retry() {
    local max_attempts=3
    local attempt=1
    local delay=5

    # Check if remote exists
    if ! git remote get-url "$SYNC_REMOTE" &>/dev/null; then
        log_warn "Remote '$SYNC_REMOTE' not configured — commit saved locally"
        return 0
    fi

    while [ $attempt -le $max_attempts ]; do
        if git push "$SYNC_REMOTE" "$SYNC_BRANCH" --quiet 2>>"$SYNC_LOG"; then
            return 0
        fi

        log_warn "Push attempt $attempt/$max_attempts failed"

        if [ $attempt -lt $max_attempts ]; then
            # Might be behind remote — pull first, then retry
            log_info "Pulling before retry ..."
            bash "$SCRIPT_DIR/auto_pull.sh" 2>>"$SYNC_LOG" || true
            sleep "$delay"
            delay=$((delay * 2))
        fi

        attempt=$((attempt + 1))
    done

    log_error "Push failed after $max_attempts attempts — changes buffered locally"
    return 1
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    log_info "Staging ($SYNC_ROLE) ..."

    local staged_count
    staged_count=$(stage_changes)

    if [ "$staged_count" -eq 0 ]; then
        log_info "Nothing to push"
        exit 0
    fi

    log_info "Staged $staged_count file(s)"

    # Sanitise before committing
    sanitise_staged

    # Build structured commit message
    local msg
    msg=$(build_commit_message)

    # Commit
    git commit -m "$msg" --quiet 2>>"$SYNC_LOG"
    local commit_hash
    commit_hash=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
    log_info "Committed: $commit_hash — $msg"

    # Push
    push_with_retry
}

main "$@"
