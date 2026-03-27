# Platinum Tier — Sync Strategy

> How the Cloud VM and Local Machine stay in sync via GitHub,
> with conflict resolution, approval handoff, and failure recovery.

---

## 1. Core Design

```
Cloud VM ◄────► GitHub (main) ◄────► Local Machine
              git push/pull          git push/pull
```

**Single branch:** `main`. No feature branches for vault sync — simplicity over ceremony.

**Single source of truth:** The vault directory. Both sides read and write to it.
Git is the transport and audit trail, not a collaboration tool.

**Conflict resolution:** Deterministic rules per directory. No manual merge needed.

---

## 2. What Gets Synced

| Path | Direction | Owner | Content |
|------|-----------|-------|---------|
| `vault/Inbox/` | Cloud → Local | Cloud writes, Local reads | New tasks from email/social |
| `vault/Needs_Action/` | Both | Cloud writes triage output, Local reads | Triaged tasks + plans |
| `vault/Needs_Approval/` | Both | Cloud writes requests, Local writes decisions | Approval lifecycle |
| `vault/Done/` | Both | Both write completed tasks | Finished work |
| `vault/Reports/` | Cloud → Local | Cloud writes briefings | CEO reports |
| `logs/audit_log.json` | Cloud → Local | Cloud writes | NDJSON audit trail |
| `ai_employee/` | Local → Cloud | Local writes code | Application code |
| `.env` | **NEVER SYNCED** | Each side has own copy | Credentials |
| `credentials.json` | **NEVER SYNCED** | Cloud only | Gmail OAuth |
| `token.json` | **NEVER SYNCED** | Cloud only | Gmail refresh token |

---

## 3. Sync Agent Implementation

### 3.1 SyncAgent Class

```python
# ai_employee/integrations/sync_agent.py

class SyncAgent:
    """Git-based sync between cloud and local machines."""

    def __init__(self, repo_path: Path, remote: str = "origin",
                 branch: str = "main", interval_seconds: int = 30):
        self.repo_path = repo_path
        self.remote = remote
        self.branch = branch
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_push = None
        self._last_pull = None
        self._conflict_log: list[dict] = []

    def start(self):
        """Start background sync daemon."""
        thread = threading.Thread(target=self._sync_loop, daemon=True)
        thread.start()

    def stop(self):
        self._stop.set()

    def _sync_loop(self):
        while not self._stop.is_set():
            self.pull()
            self._stop.wait(self.interval)

    def pull(self) -> SyncResult:
        """Pull latest from remote, auto-resolve conflicts."""
        ...

    def push(self, message: str) -> SyncResult:
        """Commit vault changes and push to remote."""
        ...

    def commit_and_push(self, message: str) -> SyncResult:
        """Stage vault/, commit, push — atomic operation."""
        ...
```

### 3.2 Sync Cycle

**Cloud Sync (after each pipeline phase):**
```
1. git add vault/ logs/
2. git commit -m "cloud: phase_{name} cycle #{n}"
3. git pull --rebase origin main
4. If conflict → auto-resolve (see §4)
5. git push origin main
```

**Local Sync (background daemon, every 30s):**
```
1. git pull --rebase origin main
2. If conflict → auto-resolve (see §4)
3. If local has uncommitted changes:
   a. git stash
   b. git pull --rebase
   c. git stash pop
   d. If stash conflict → auto-resolve
```

**Local Sync (on approval action):**
```
1. git add vault/Needs_Approval/{id}.md
2. git commit -m "local: approved {id}" (or "rejected")
3. git pull --rebase origin main
4. git push origin main
```

---

## 4. Conflict Resolution

### 4.1 Rules

Conflicts happen when cloud and local edit the same file between syncs.
Each vault directory has a deterministic winner:

| Directory | Winner | Rationale |
|-----------|--------|-----------|
| `vault/Inbox/` | Cloud | Cloud creates these files — local should never edit |
| `vault/Needs_Action/` | Cloud | Cloud triages — local only reads |
| `vault/Needs_Approval/` | **Local** | Human decisions override cloud state |
| `vault/Done/` | **Latest timestamp** | Both can complete tasks — most recent wins |
| `vault/Reports/` | Cloud | Cloud generates reports |
| `logs/` | Cloud | Cloud is the primary log writer |
| `ai_employee/` | Local | Code changes only flow local → cloud |

### 4.2 Auto-Resolution Algorithm

```python
def resolve_conflict(file_path: Path, ours: str, theirs: str) -> str:
    """Deterministic conflict resolution based on directory rules."""

    dir_name = file_path.parent.name

    if dir_name == "Needs_Approval":
        # Local (human) always wins for approval decisions
        return ours if self.mode == "local" else theirs

    if dir_name in ("Inbox", "Needs_Action", "Reports"):
        # Cloud always wins for cloud-generated content
        return theirs if self.mode == "local" else ours

    if dir_name == "Done":
        # Most recent modification wins
        ours_time = _extract_timestamp(ours)
        theirs_time = _extract_timestamp(theirs)
        return ours if ours_time >= theirs_time else theirs

    # Default: cloud wins (prefer automation continuity)
    return theirs if self.mode == "local" else ours
```

### 4.3 Conflict Logging

Every auto-resolved conflict is logged:

```json
{
    "timestamp": "2026-03-23T10:30:00Z",
    "file": "vault/Needs_Approval/approve_abc123.md",
    "resolution": "local_wins",
    "reason": "approval_directory_rule",
    "ours_hash": "a1b2c3d",
    "theirs_hash": "e4f5g6h",
    "sync_mode": "local"
}
```

---

## 5. Approval File Protocol

### 5.1 File Format

Approval files use YAML frontmatter + markdown body:

```markdown
---
id: approve_abc123
status: pending
type: content
risk_level: medium
agent: meta_agent
created_at: "2026-03-23T10:00:00Z"
expires_at: "2026-03-24T10:00:00Z"
decided_at: null
decided_by: null
cloud_cycle: 42
---

## Proposed Action

Post to Facebook Page:

> Exciting news! Our Q1 results are in and we've exceeded targets
> across all divisions. Read the full report on our website.

## Context

- **Source:** audit_agent weekly briefing
- **Confidence:** 0.85
- **Safety flags:** none
- **Similar past posts:** 3 approved, 0 rejected

## Decision

<!-- LOCAL FILLS THIS IN -->
```

### 5.2 Status Lifecycle

```
    CLOUD creates                LOCAL decides              CLOUD executes
  ┌──────────────┐           ┌──────────────┐           ┌──────────────┐
  │   pending    │──── sync ──►│   pending    │           │              │
  │              │           │              │           │              │
  │              │           │  Human sees  │           │              │
  │              │           │  in dashboard│           │              │
  │              │           │              │           │              │
  │              │           │  ┌────────┐  │           │              │
  │              │           │  │Approve │  │           │              │
  │              │           │  └───┬────┘  │           │              │
  │              │           │      ▼       │           │              │
  │              │           │  approved    │──── sync ──►│  approved    │
  │              │           │              │           │      │       │
  │              │           └──────────────┘           │      ▼       │
  │              │                                     │  Execute     │
  │              │           ┌──────────────┐           │  action      │
  │              │           │  ┌────────┐  │           │      │       │
  │              │           │  │Reject  │  │           │      ▼       │
  │              │           │  └───┬────┘  │           │  executed    │
  │              │           │      ▼       │           │  (→ Done/)   │
  │              │           │  rejected    │──── sync ──►│  rejected    │
  │              │           └──────────────┘           │  (→ Done/)   │
  │              │                                     │              │
  │  (24h pass)  │                                     │              │
  │      ▼       │                                     │              │
  │   expired    │                                     │              │
  │  (→ Done/)   │                                     │              │
  └──────────────┘                                     └──────────────┘
```

### 5.3 Auto-Expiry Rules

| Risk Level | Auto-Expire | Auto-Approve |
|------------|-------------|--------------|
| low | 24 hours | After 12 hours if safety check passes |
| medium | 48 hours | Never — requires human decision |
| high | 72 hours | Never — requires human decision |
| critical | Never (queue indefinitely) | Never |

```python
def check_expiry(approval_file: dict) -> str:
    """Check if an approval should auto-expire or auto-approve."""
    age = now() - approval_file["created_at"]
    risk = approval_file["risk_level"]

    if risk == "low" and age > timedelta(hours=12):
        if approval_file["safety_flags"] == []:
            return "auto_approved"

    if risk == "low" and age > timedelta(hours=24):
        return "expired"
    if risk == "medium" and age > timedelta(hours=48):
        return "expired"
    if risk == "high" and age > timedelta(hours=72):
        return "expired"

    return "pending"  # critical never expires
```

---

## 6. Git Configuration

### 6.1 .gitignore (Root)

```gitignore
# Secrets — NEVER sync
.env
*.token
*.key
credentials.json
token.json

# OS artifacts
__pycache__/
*.pyc
.DS_Store
Thumbs.db

# Virtual environments
venv/
.venv/

# IDE
.vscode/
.idea/

# Large binaries (use vault for text only)
*.zip
*.tar.gz
*.exe

# Rotated audit logs (keep only current)
logs/audit_log_*.json
```

### 6.2 .gitattributes

```gitattributes
# Vault files: always use LF (cross-platform consistency)
vault/**/*.md text eol=lf

# Logs: treat as binary to avoid merge attempts on NDJSON
logs/*.json binary

# Approval files: merge=ours on local, merge=theirs on cloud
# (handled by custom merge driver, see §6.3)
```

### 6.3 Custom Merge Driver

```ini
# .git/config (cloud machine)
[merge "cloud-wins"]
    name = Cloud wins for cloud-owned directories
    driver = python -m ai_employee.integrations.sync_agent merge-cloud %O %A %B

# .git/config (local machine)
[merge "local-wins"]
    name = Local wins for approval decisions
    driver = python -m ai_employee.integrations.sync_agent merge-local %O %A %B
```

---

## 7. Sync Timing & Bandwidth

### 7.1 Normal Operation

```
Event                      | Cloud Action        | Local Action
---------------------------|--------------------|-----------------
Pipeline phase completes   | commit + push      | —
30-second timer fires      | —                  | pull
Approval decision made     | —                  | commit + push
Audit log rotates          | commit + push      | —
Code deployed              | pull (via Actions) | push
```

### 7.2 Expected Bandwidth

| Data Type | Size per Sync | Frequency | Daily Volume |
|-----------|---------------|-----------|-------------|
| Approval files | ~1 KB each | 5-20/day | ~20 KB |
| Task files (Inbox) | ~2 KB each | 10-50/day | ~100 KB |
| Done files | ~3 KB each | 10-50/day | ~150 KB |
| Audit log | ~50 KB/day | Continuous | ~50 KB |
| Reports | ~10 KB each | 1/week | ~1.5 KB/day |
| Code changes | Variable | Manual | Variable |
| **Total** | | | **~320 KB/day** |

Git overhead: ~30% compression → **~420 KB/day actual transfer**.

### 7.3 Rate Limiting

```python
class SyncRateLimiter:
    """Prevent git storms during high-activity periods."""

    def __init__(self):
        self.min_push_interval = 10   # seconds between pushes
        self.max_pushes_per_hour = 60  # cap hourly pushes
        self.batch_threshold = 5       # batch this many changes before pushing

    def should_push(self, pending_changes: int) -> bool:
        if time_since_last_push() < self.min_push_interval:
            return False
        if self.pushes_this_hour >= self.max_pushes_per_hour:
            return False
        if pending_changes < self.batch_threshold:
            return False  # wait for more changes to batch
        return True
```

---

## 8. Failure Scenarios

### 8.1 GitHub Unreachable

```
Detection: git push/pull returns non-zero exit code
           or timeout after 10 seconds

Cloud behavior:
  1. Buffer commits locally (git commit continues working)
  2. Retry push every 60 seconds (exponential backoff to 5 min)
  3. After 10 failures: log warning to audit trail
  4. After 30 min: alert via health endpoint
  5. On reconnect: push all buffered commits

Local behavior:
  1. Dashboard shows "Sync: Offline" banner
  2. Continue processing local vault (stale data)
  3. Retry pull every 60 seconds
  4. On reconnect: pull all changes, auto-resolve conflicts

Data safety:
  - No data loss: git commits are local-first
  - Divergence: cloud and local may process same task independently
  - Resolution: on reconnect, Done/ uses latest-timestamp rule
```

### 8.2 Cloud VM Down

```
Detection: Local health poll returns timeout/error (3 retries)

Local behavior:
  1. Switch to Local-Only mode (Gold tier)
  2. Start local MCP servers as subprocesses
  3. Dashboard shows "Cloud: Offline — Running locally"
  4. Process vault with local AIEmployee
  5. All changes committed locally

On cloud recovery:
  1. Cloud starts, pulls latest from GitHub
  2. Cloud may re-process tasks local already handled
  3. Done/ latest-timestamp rule prevents duplicate execution
  4. Approval files: local decisions are preserved (local wins)
```

### 8.3 Local Machine Offline

```
Detection: Cloud doesn't see local pushes for >1 hour

Cloud behavior:
  1. Continue normal operation
  2. Approvals accumulate in Needs_Approval/
  3. Low-risk items: auto-approve after 12h (if safe)
  4. Medium/high-risk items: queue indefinitely
  5. Critical items: queue indefinitely, alert via cloud log

On local reconnect:
  1. Local pulls all accumulated changes
  2. Dashboard shows backlog of pending approvals
  3. Human processes approval queue
  4. Approved items pushed, cloud executes on next pull
```

### 8.4 Merge Conflict in Approval File

```
Scenario: Cloud updates approval status while local approves same item

Resolution:
  1. Detect conflict during git pull --rebase
  2. Apply rule: Needs_Approval/ → local wins
  3. Local's "approved" status overwrites cloud's update
  4. Log conflict to sync_conflicts.json
  5. Cloud reads approved status on next pull, executes action

Edge case: Cloud already executed based on stale state
  → Done/ file created by cloud with "executed" status
  → Local's approval becomes redundant (no harm, already done)
  → Audit trail shows both cloud-auto and local-approved events
```

---

## 9. Commit Message Convention

All sync commits follow a structured format for easy filtering:

```
<source>: <phase/action> [cycle #<n>]

Examples:
  cloud: phase_gmail cycle #42
  cloud: phase_triage cycle #42
  cloud: approval queued approve_abc123
  cloud: auto-expired approve_def456
  local: approved approve_abc123
  local: rejected approve_ghi789
  local: ralph completed "draft Q1 report"
  deploy: code update v1.2.3
```

**Filtering syncs in git log:**
```bash
git log --oneline --grep="^cloud:"   # All cloud commits
git log --oneline --grep="^local:"   # All local commits
git log --oneline --grep="approved"  # All approvals
git log --oneline --grep="cycle #"   # All pipeline cycles
```

---

## 10. Monitoring Sync Health

### 10.1 Sync Metrics

The sync agent exposes metrics to the health monitor:

```python
@dataclass
class SyncMetrics:
    last_push_time: datetime | None
    last_pull_time: datetime | None
    pushes_last_hour: int
    pulls_last_hour: int
    conflicts_last_hour: int
    consecutive_failures: int
    sync_lag_seconds: float      # time since last successful sync
    pending_local_commits: int   # committed but not pushed
    is_healthy: bool
```

### 10.2 Dashboard Integration

The local dashboard shows sync status:

```
┌─────────────────────────────────────────┐
│  Sync Status                            │
│  ─────────────────────────────────────  │
│  Status:     ● Connected                │
│  Last pull:  12 seconds ago             │
│  Last push:  45 seconds ago             │
│  Lag:        0s                         │
│  Pending:    0 local commits            │
│  Conflicts:  0 (last hour)              │
│  Cloud:      ● Healthy (uptime: 72h)    │
│                                         │
│  [Force Sync]  [View Conflict Log]      │
└─────────────────────────────────────────┘
```

### 10.3 Alerting Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| `sync_lag_seconds` | > 120s | > 300s |
| `consecutive_failures` | > 3 | > 10 |
| `conflicts_last_hour` | > 5 | > 15 |
| `pending_local_commits` | > 10 | > 50 |

---

## 11. Security Considerations

### 11.1 Git over SSH

```
# Cloud → GitHub: deploy key (read-write)
ssh-keygen -t ed25519 -C "ai-employee-cloud" -f ~/.ssh/ai_employee_deploy

# Local → GitHub: user's SSH key (read-write)
# Already configured via user's git setup

# Local → Cloud: SSH key for health/deploy
ssh-keygen -t ed25519 -C "ai-employee-admin" -f ~/.ssh/ai_employee_cloud
```

### 11.2 Vault File Sanitization

Before committing, the sync agent strips sensitive data:

```python
REDACT_PATTERNS = [
    r'password\s*[:=]\s*\S+',
    r'token\s*[:=]\s*\S+',
    r'api[_-]?key\s*[:=]\s*\S+',
    r'sk-[a-zA-Z0-9]{20,}',
    r'Bearer\s+[a-zA-Z0-9._-]+',
]

def sanitize_before_commit(content: str) -> str:
    for pattern in REDACT_PATTERNS:
        content = re.sub(pattern, '[REDACTED]', content, flags=re.IGNORECASE)
    return content
```

### 11.3 Audit Trail Integrity

```
The NDJSON audit log is append-only:
  - Cloud writes sequentially (monotonic sequence numbers)
  - Local never writes to cloud's audit log
  - Git history preserves all versions (tamper-evident)
  - Each entry has a SHA-256 hash of the previous entry (chain)
```

---

## 12. Implementation Checklist

### Phase 1: Git Foundation
- [ ] Initialize git repo with proper `.gitignore` and `.gitattributes`
- [ ] Create GitHub repository, configure SSH deploy key
- [ ] Set up vault/ directory structure in git
- [ ] Test basic push/pull from both cloud and local

### Phase 2: SyncAgent Class
- [ ] Implement `SyncAgent` with pull/push/commit_and_push
- [ ] Implement background sync daemon (pull every 30s)
- [ ] Implement `SyncRateLimiter` to prevent git storms
- [ ] Add sync metrics collection

### Phase 3: Conflict Resolution
- [ ] Implement `resolve_conflict()` with directory-based rules
- [ ] Add conflict logging to `sync_conflicts.json`
- [ ] Test: simultaneous edits to Needs_Approval/ (local wins)
- [ ] Test: simultaneous edits to Done/ (latest timestamp wins)
- [ ] Test: simultaneous edits to Inbox/ (cloud wins)

### Phase 4: Approval Protocol
- [ ] Define YAML frontmatter schema for approval files
- [ ] Implement `check_expiry()` with risk-based auto-expire
- [ ] Wire approval file creation into cloud orchestrator
- [ ] Wire approval file reading into local dashboard
- [ ] Test full cycle: cloud creates → local approves → cloud executes

### Phase 5: Integration
- [ ] Wire `SyncAgent` into `CloudOrchestrator` (push after each phase)
- [ ] Wire `SyncAgent` into local `AIEmployee` (pull daemon)
- [ ] Add sync status to dashboard (`/api/sync` endpoint)
- [ ] Add sync health to `HealthMonitor` checks
- [ ] Add `--sync` CLI flag for manual force-sync

### Phase 6: Hardening
- [ ] Implement sanitize_before_commit for vault files
- [ ] Set up GitHub Actions health-ping workflow
- [ ] Test failure scenarios (GitHub down, VM down, local offline)
- [ ] Test recovery scenarios (reconnect after partition)
- [ ] Load test with 100+ approval files / hour
