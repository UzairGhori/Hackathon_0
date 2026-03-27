#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# auto_pull.sh — Pull remote vault changes, auto-resolve conflicts
#
# Called by git_sync.sh every cycle.  Can also be run standalone:
#   SYNC_ROLE=local ./scripts/auto_pull.sh
#
# Flow:
#   1. Stash any uncommitted local work
#   2. git pull --rebase
#   3. If conflicts → invoke conflict_resolver.py per file
#   4. Pop stash (if stashed), resolve stash conflicts the same way
#   5. Exit 0 on success, 1 on failure
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
RESOLVER="$SCRIPT_DIR/conflict_resolver.py"

ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log_info()  { echo "[$(ts)] [PULL]  $*" | tee -a "$SYNC_LOG"; }
log_warn()  { echo "[$(ts)] [PULL]  WARN: $*" | tee -a "$SYNC_LOG" >&2; }
log_error() { echo "[$(ts)] [PULL]  ERROR: $*" | tee -a "$SYNC_LOG" >&2; }

# ── Pre-flight checks ──────────────────────────────────────────────────────
if [ ! -d ".git" ]; then
    log_error "Not a git repository. Run git_sync.sh first."
    exit 1
fi

# Check if remote exists
if ! git remote get-url "$SYNC_REMOTE" &>/dev/null; then
    log_warn "Remote '$SYNC_REMOTE' not configured — skipping pull"
    exit 0
fi

# ── Check for connectivity ─────────────────────────────────────────────────
log_info "Fetching from $SYNC_REMOTE/$SYNC_BRANCH ..."
if ! git fetch "$SYNC_REMOTE" "$SYNC_BRANCH" --quiet 2>>"$SYNC_LOG"; then
    log_error "git fetch failed — remote unreachable"
    exit 1
fi

# ── Check if there's anything new ──────────────────────────────────────────
LOCAL_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "none")
REMOTE_HEAD=$(git rev-parse "$SYNC_REMOTE/$SYNC_BRANCH" 2>/dev/null || echo "none")

if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ]; then
    log_info "Already up to date ($LOCAL_HEAD)"
    exit 0
fi

# ── Stash uncommitted work ─────────────────────────────────────────────────
STASHED=0
if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
    log_info "Stashing uncommitted changes ..."
    git stash push -m "auto-pull-stash-$(date +%s)" --quiet
    STASHED=1
fi

# ── Pull with rebase ───────────────────────────────────────────────────────
log_info "Rebasing onto $SYNC_REMOTE/$SYNC_BRANCH ..."
PULL_OK=1
if ! git rebase "$SYNC_REMOTE/$SYNC_BRANCH" --quiet 2>>"$SYNC_LOG"; then
    PULL_OK=0
    log_warn "Rebase hit conflicts — resolving ..."

    # Resolve each conflicted file
    resolve_rebase_conflicts() {
        local conflict_files
        conflict_files=$(git diff --name-only --diff-filter=U 2>/dev/null || true)

        if [ -z "$conflict_files" ]; then
            return 0
        fi

        local resolved_count=0
        local failed_count=0

        while IFS= read -r filepath; do
            [ -z "$filepath" ] && continue

            log_info "  Resolving conflict: $filepath"

            if python "$RESOLVER" \
                --file "$filepath" \
                --role "$SYNC_ROLE" \
                --mode rebase 2>>"$SYNC_LOG"; then

                git add "$filepath"
                resolved_count=$((resolved_count + 1))
            else
                log_error "  Resolver failed for $filepath — accepting theirs"
                git checkout --theirs "$filepath" 2>/dev/null || true
                git add "$filepath"
                failed_count=$((failed_count + 1))
            fi
        done <<< "$conflict_files"

        log_info "  Resolved: $resolved_count, fallback: $failed_count"
        return 0
    }

    # Loop through rebase steps until clean
    MAX_REBASE_STEPS=20
    step=0
    while [ $step -lt $MAX_REBASE_STEPS ]; do
        step=$((step + 1))

        # Check for conflicts at this rebase step
        conflict_files=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
        if [ -z "$conflict_files" ]; then
            break
        fi

        resolve_rebase_conflicts

        # Continue the rebase
        if git rebase --continue --quiet 2>>"$SYNC_LOG"; then
            PULL_OK=1
            break
        fi
        # If rebase --continue itself produces new conflicts, loop again
    done

    # If still in rebase state, abort and fall back to merge
    if git status 2>/dev/null | grep -q "rebase in progress"; then
        log_warn "Rebase stuck after $step steps — aborting, falling back to merge"
        git rebase --abort 2>/dev/null || true

        if ! git merge "$SYNC_REMOTE/$SYNC_BRANCH" --no-edit 2>>"$SYNC_LOG"; then
            # Resolve merge conflicts
            merge_conflicts=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
            if [ -n "$merge_conflicts" ]; then
                while IFS= read -r filepath; do
                    [ -z "$filepath" ] && continue
                    log_info "  Resolving merge conflict: $filepath"
                    if python "$RESOLVER" \
                        --file "$filepath" \
                        --role "$SYNC_ROLE" \
                        --mode merge 2>>"$SYNC_LOG"; then
                        git add "$filepath"
                    else
                        git checkout --theirs "$filepath" 2>/dev/null || true
                        git add "$filepath"
                    fi
                done <<< "$merge_conflicts"

                git commit --no-edit -m "sync($SYNC_ROLE): auto-resolved merge conflicts" 2>>"$SYNC_LOG" || true
            fi
        fi
        PULL_OK=1
    else
        PULL_OK=1
    fi
fi

# ── Pop stash ───────────────────────────────────────────────────────────────
if [ "$STASHED" -eq 1 ]; then
    log_info "Popping stash ..."
    if ! git stash pop --quiet 2>>"$SYNC_LOG"; then
        log_warn "Stash pop conflict — resolving ..."

        stash_conflicts=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
        if [ -n "$stash_conflicts" ]; then
            while IFS= read -r filepath; do
                [ -z "$filepath" ] && continue
                log_info "  Resolving stash conflict: $filepath"

                # For stash conflicts: local (our uncommitted work) wins for
                # approval dirs, cloud wins for cloud-owned dirs
                if python "$RESOLVER" \
                    --file "$filepath" \
                    --role "$SYNC_ROLE" \
                    --mode stash 2>>"$SYNC_LOG"; then
                    git add "$filepath"
                else
                    # Keep ours (the stashed local work)
                    git checkout --ours "$filepath" 2>/dev/null || true
                    git add "$filepath"
                fi
            done <<< "$stash_conflicts"
        fi

        # Drop the stash since we've manually resolved it
        git stash drop --quiet 2>/dev/null || true
        log_info "Stash conflicts resolved"
    fi
fi

# ── Summary ─────────────────────────────────────────────────────────────────
NEW_HEAD=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
BEHIND=$(git rev-list --count HEAD.."$SYNC_REMOTE/$SYNC_BRANCH" 2>/dev/null || echo "?")
AHEAD=$(git rev-list --count "$SYNC_REMOTE/$SYNC_BRANCH"..HEAD 2>/dev/null || echo "?")

log_info "Pull complete — HEAD=$NEW_HEAD (ahead=$AHEAD behind=$BEHIND)"

if [ "$PULL_OK" -eq 1 ]; then
    exit 0
else
    exit 1
fi
