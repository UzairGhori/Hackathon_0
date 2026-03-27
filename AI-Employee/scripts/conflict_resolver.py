"""
conflict_resolver.py — Deterministic conflict resolution for Platinum Tier sync.

Resolves git merge/rebase conflicts using directory-based ownership rules:

    Cloud owns (cloud always wins):
        vault/Inbox/*            — Cloud creates inbox items
        vault/Needs_Action/*     — Cloud creates draft replies
        vault/Reports/*          — Cloud generates reports
        ai_employee/logs/*       — Cloud writes audit trail

    Local owns (local always wins):
        vault/Needs_Approval/*               — Local approves payments
        AI_Employee_Vault/Needs_Approval/*   — Local approves emails/posts
        ai_employee/**/*.py                  — Local pushes code

    Shared (special merge logic):
        vault/Done/*             — Latest timestamp wins
        vault/approval_queue.json — Union-merge (combine all entries)
        vault/task_queue.json    — Latest-status wins per task_id

Usage:
    python scripts/conflict_resolver.py --file <path> --role <cloud|local>
    python scripts/conflict_resolver.py --file <path> --role local --mode rebase
    python scripts/conflict_resolver.py --scan --role local  (resolve all conflicts)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Setup ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "ai_employee" / "logs"
CONFLICT_LOG = LOG_DIR / "sync_conflicts.json"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [RESOLVE] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("conflict_resolver")


# ── Ownership rules ─────────────────────────────────────────────────────────

# Maps a path prefix to the role that owns it.
# Order matters — first match wins.
OWNERSHIP_RULES: list[tuple[str, str]] = [
    # Local-owned (approvals)
    ("vault/Needs_Approval/",               "local"),
    ("AI_Employee_Vault/Needs_Approval/",   "local"),

    # Cloud-owned (automation output)
    ("vault/Inbox/",                        "cloud"),
    ("vault/Needs_Action/",                 "cloud"),
    ("vault/Reports/",                      "cloud"),
    ("ai_employee/logs/",                   "cloud"),

    # Shared — special merge handlers
    ("vault/Done/",                         "shared_done"),
    ("vault/approval_queue.json",           "shared_approval_json"),
    ("vault/task_queue.json",               "shared_task_json"),
    ("vault/gmail_processed_ids.json",      "shared_processed_ids"),
    ("vault/linkedin_processed_ids.json",   "shared_processed_ids"),

    # Code — local authority
    ("ai_employee/",                        "local"),
    ("scripts/",                            "local"),
]


def get_owner(filepath: str) -> str:
    """Determine which role owns a file based on its path."""
    # Normalise slashes
    fp = filepath.replace("\\", "/")
    for prefix, owner in OWNERSHIP_RULES:
        if fp.startswith(prefix) or fp == prefix.rstrip("/"):
            return owner
    # Default: cloud wins (prefer automation continuity)
    return "cloud"


# ── Conflict marker parsing ─────────────────────────────────────────────────

CONFLICT_START  = re.compile(r"^<<<<<<<")
CONFLICT_MIDDLE = re.compile(r"^=======$")
CONFLICT_END    = re.compile(r"^>>>>>>>")


def parse_conflict_regions(content: str) -> list[dict]:
    """
    Parse git conflict markers from file content.

    Returns a list of regions:
      {"type": "clean",    "lines": [...]}
      {"type": "conflict", "ours": [...], "theirs": [...]}
    """
    regions: list[dict] = []
    current_clean: list[str] = []
    in_conflict = False
    in_ours = False
    ours_lines: list[str] = []
    theirs_lines: list[str] = []

    for line in content.splitlines(keepends=True):
        if CONFLICT_START.match(line):
            # Flush clean region
            if current_clean:
                regions.append({"type": "clean", "lines": current_clean})
                current_clean = []
            in_conflict = True
            in_ours = True
            ours_lines = []
            theirs_lines = []
            continue

        if in_conflict and CONFLICT_MIDDLE.match(line):
            in_ours = False
            continue

        if in_conflict and CONFLICT_END.match(line):
            regions.append({
                "type": "conflict",
                "ours": ours_lines,
                "theirs": theirs_lines,
            })
            in_conflict = False
            in_ours = False
            ours_lines = []
            theirs_lines = []
            continue

        if in_conflict:
            if in_ours:
                ours_lines.append(line)
            else:
                theirs_lines.append(line)
        else:
            current_clean.append(line)

    if current_clean:
        regions.append({"type": "clean", "lines": current_clean})

    return regions


def has_conflict_markers(content: str) -> bool:
    """Check whether the file content contains git conflict markers."""
    return bool(re.search(r"^<<<<<<<", content, re.MULTILINE))


# ── Resolution strategies ───────────────────────────────────────────────────

def resolve_by_owner(regions: list[dict], role: str, owner: str) -> str:
    """
    Resolve conflicts where one side always wins.

    If role == owner  → keep "ours"   (we are the authority)
    If role != owner  → keep "theirs" (the other side is the authority)
    """
    pick_ours = (role == owner)
    result: list[str] = []

    for region in regions:
        if region["type"] == "clean":
            result.extend(region["lines"])
        else:
            if pick_ours:
                result.extend(region["ours"])
            else:
                result.extend(region["theirs"])

    return "".join(result)


def resolve_done_by_timestamp(regions: list[dict], full_ours: str, full_theirs: str) -> str:
    """
    For vault/Done/ files — keep the version with the latest timestamp.

    Scans for ISO-8601 timestamps and picks the file whose latest timestamp
    is more recent.
    """
    ts_pattern = re.compile(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    )

    def latest_ts(text: str) -> str:
        matches = ts_pattern.findall(text)
        return max(matches) if matches else ""

    ours_latest = latest_ts(full_ours)
    theirs_latest = latest_ts(full_theirs)

    if ours_latest >= theirs_latest:
        # Ours is newer — strip conflict markers keeping ours
        return resolve_by_owner(regions, role="local", owner="local")
    else:
        return resolve_by_owner(regions, role="local", owner="cloud")


def resolve_approval_queue_json(ours_text: str, theirs_text: str) -> str:
    """
    Merge vault/approval_queue.json — union by request_id.

    Both sides can add new requests.  For the same request_id:
      - If statuses differ, the more-decisive status wins:
        approved/rejected > expired > pending
      - Latest decision_at timestamp breaks ties
    """
    STATUS_RANK = {"approved": 3, "rejected": 3, "expired": 2, "pending": 1}

    def load_safe(text: str) -> list[dict]:
        try:
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    ours_items = load_safe(ours_text)
    theirs_items = load_safe(theirs_text)

    # Index by request_id
    merged: dict[str, dict] = {}

    for item in theirs_items:
        rid = item.get("request_id", "")
        if rid:
            merged[rid] = item

    for item in ours_items:
        rid = item.get("request_id", "")
        if not rid:
            continue

        if rid not in merged:
            merged[rid] = item
            continue

        # Both sides have this request — pick the more decisive one
        existing = merged[rid]
        our_rank = STATUS_RANK.get(item.get("status", "pending"), 1)
        their_rank = STATUS_RANK.get(existing.get("status", "pending"), 1)

        if our_rank > their_rank:
            merged[rid] = item
        elif our_rank == their_rank:
            # Same rank — latest decision_at wins
            our_dt = item.get("decision_at", "")
            their_dt = existing.get("decision_at", "")
            if our_dt > their_dt:
                merged[rid] = item

    # Reconstruct list, sorted by created_at
    result = sorted(merged.values(), key=lambda x: x.get("created_at", ""))
    return json.dumps(result, indent=2, default=str, ensure_ascii=False) + "\n"


def resolve_task_queue_json(ours_text: str, theirs_text: str) -> str:
    """
    Merge vault/task_queue.json — union by task_id, latest status wins.
    """
    STATUS_RANK = {
        "completed": 4, "failed": 3, "awaiting_approval": 2, "pending": 1,
    }

    def load_safe(text: str) -> list[dict]:
        try:
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    ours_items = load_safe(ours_text)
    theirs_items = load_safe(theirs_text)

    merged: dict[str, dict] = {}

    for item in theirs_items:
        tid = item.get("task_id", "")
        if tid:
            merged[tid] = item

    for item in ours_items:
        tid = item.get("task_id", "")
        if not tid:
            continue
        if tid not in merged:
            merged[tid] = item
            continue

        existing = merged[tid]
        our_rank = STATUS_RANK.get(item.get("status", "pending"), 1)
        their_rank = STATUS_RANK.get(existing.get("status", "pending"), 1)

        if our_rank > their_rank:
            merged[tid] = item
        elif our_rank == their_rank:
            our_dt = item.get("completed_at", item.get("started_at", ""))
            their_dt = existing.get("completed_at", existing.get("started_at", ""))
            if our_dt > their_dt:
                merged[tid] = item

    result = sorted(merged.values(), key=lambda x: x.get("created_at", ""))
    return json.dumps(result, indent=2, default=str, ensure_ascii=False) + "\n"


def resolve_processed_ids_json(ours_text: str, theirs_text: str) -> str:
    """
    Merge processed ID trackers — union of all IDs, keep latest count/timestamp.
    """
    def load_safe(text: str) -> dict:
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    ours = load_safe(ours_text)
    theirs = load_safe(theirs_text)

    ours_ids = set(ours.get("processed_ids", []))
    theirs_ids = set(theirs.get("processed_ids", []))
    all_ids = sorted(ours_ids | theirs_ids)

    result = {
        "processed_ids": all_ids,
        "count": len(all_ids),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(result, indent=2, ensure_ascii=False) + "\n"


# ── Extract ours/theirs full content (pre-conflict) ─────────────────────────

def git_show_version(filepath: str, ref: str) -> str:
    """Get the file content from a specific git ref."""
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{filepath}"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        return result.stdout if result.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def get_ours_theirs_content(filepath: str, content_with_markers: str) -> tuple[str, str]:
    """
    Reconstruct full ours/theirs content from conflict markers.
    """
    regions = parse_conflict_regions(content_with_markers)

    ours_parts: list[str] = []
    theirs_parts: list[str] = []

    for region in regions:
        if region["type"] == "clean":
            ours_parts.extend(region["lines"])
            theirs_parts.extend(region["lines"])
        else:
            ours_parts.extend(region["ours"])
            theirs_parts.extend(region["theirs"])

    return "".join(ours_parts), "".join(theirs_parts)


# ── Conflict logging ────────────────────────────────────────────────────────

def log_conflict(filepath: str, role: str, owner: str,
                 resolution: str, reason: str,
                 ours_hash: str, theirs_hash: str) -> None:
    """Append a structured conflict record to sync_conflicts.json."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "file": filepath,
        "role": role,
        "owner": owner,
        "resolution": resolution,
        "reason": reason,
        "ours_hash": ours_hash[:8],
        "theirs_hash": theirs_hash[:8],
    }

    # Append as NDJSON
    try:
        with open(CONFLICT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError as exc:
        log.warning("Could not write conflict log: %s", exc)

    log.info(
        "Resolved: %s → %s (%s) [ours=%s theirs=%s]",
        filepath, resolution, reason, ours_hash[:8], theirs_hash[:8],
    )


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ── Main resolver ───────────────────────────────────────────────────────────

def resolve_file(filepath: str, role: str, mode: str = "rebase") -> bool:
    """
    Resolve a single conflicted file in-place.

    Args:
        filepath: Path relative to project root (e.g. "vault/Inbox/task.md")
        role: "cloud" or "local"
        mode: "rebase", "merge", or "stash"

    Returns:
        True if resolved successfully, False on error.
    """
    abs_path = PROJECT_ROOT / filepath

    if not abs_path.exists():
        log.warning("File does not exist: %s", abs_path)
        return False

    try:
        raw_content = abs_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.error("Cannot read %s: %s", filepath, exc)
        return False

    # If no conflict markers, nothing to do
    if not has_conflict_markers(raw_content):
        log.info("No conflict markers in %s — skipping", filepath)
        return True

    owner = get_owner(filepath)
    ours_content, theirs_content = get_ours_theirs_content(filepath, raw_content)
    ours_h = content_hash(ours_content)
    theirs_h = content_hash(theirs_content)

    resolved_content: str

    # ── Dispatch to the right strategy ──────────────────────────────────
    if owner in ("cloud", "local"):
        # Simple ownership: one side always wins
        resolved_content = resolve_by_owner(
            parse_conflict_regions(raw_content), role, owner,
        )
        resolution = f"{owner}_wins"
        reason = f"{owner}_owned_directory"

    elif owner == "shared_done":
        # vault/Done/ — latest timestamp wins
        resolved_content = resolve_done_by_timestamp(
            parse_conflict_regions(raw_content),
            ours_content, theirs_content,
        )
        resolution = "latest_timestamp"
        reason = "done_directory_timestamp_rule"

    elif owner == "shared_approval_json":
        # vault/approval_queue.json — union merge
        resolved_content = resolve_approval_queue_json(
            ours_content, theirs_content,
        )
        resolution = "union_merge"
        reason = "approval_queue_union"

    elif owner == "shared_task_json":
        # vault/task_queue.json — status-rank merge
        resolved_content = resolve_task_queue_json(
            ours_content, theirs_content,
        )
        resolution = "status_merge"
        reason = "task_queue_status_rank"

    elif owner == "shared_processed_ids":
        # Processed ID trackers — union of IDs
        resolved_content = resolve_processed_ids_json(
            ours_content, theirs_content,
        )
        resolution = "union_ids"
        reason = "processed_ids_union"

    else:
        # Unknown owner — fall back to cloud wins
        resolved_content = resolve_by_owner(
            parse_conflict_regions(raw_content), role, "cloud",
        )
        resolution = "cloud_wins_default"
        reason = "unknown_owner_fallback"

    # ── Write resolved content ──────────────────────────────────────────
    try:
        abs_path.write_text(resolved_content, encoding="utf-8")
    except OSError as exc:
        log.error("Cannot write resolved content to %s: %s", filepath, exc)
        return False

    # ── Log the resolution ──────────────────────────────────────────────
    log_conflict(
        filepath=filepath,
        role=role,
        owner=owner,
        resolution=resolution,
        reason=reason,
        ours_hash=ours_h,
        theirs_hash=theirs_h,
    )

    return True


def scan_and_resolve_all(role: str, mode: str = "merge") -> dict:
    """
    Find all conflicted files and resolve them.

    Returns summary dict.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        conflicted = [
            f.strip() for f in result.stdout.splitlines() if f.strip()
        ]
    except (subprocess.SubprocessError, OSError):
        conflicted = []

    if not conflicted:
        log.info("No conflicted files found")
        return {"resolved": 0, "failed": 0, "files": []}

    log.info("Found %d conflicted file(s)", len(conflicted))

    resolved = 0
    failed = 0
    files: list[dict] = []

    for filepath in conflicted:
        ok = resolve_file(filepath, role, mode)
        status = "resolved" if ok else "failed"
        files.append({"file": filepath, "status": status})
        if ok:
            resolved += 1
        else:
            failed += 1

    return {"resolved": resolved, "failed": failed, "files": files}


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve git conflicts using directory-based ownership rules.",
    )
    parser.add_argument(
        "--file",
        help="Path to a single conflicted file (relative to project root)",
    )
    parser.add_argument(
        "--role",
        choices=["cloud", "local"],
        default="local",
        help="Which side we are: cloud or local (default: local)",
    )
    parser.add_argument(
        "--mode",
        choices=["rebase", "merge", "stash"],
        default="rebase",
        help="Conflict context: rebase, merge, or stash (default: rebase)",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan and resolve ALL conflicted files (instead of a single --file)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be resolved without writing files",
    )

    args = parser.parse_args()

    if args.scan:
        summary = scan_and_resolve_all(args.role, args.mode)
        print(json.dumps(summary, indent=2))
        return 0 if summary["failed"] == 0 else 1

    if not args.file:
        parser.error("Either --file or --scan is required")

    # Normalise path separators
    filepath = args.file.replace("\\", "/")

    if args.dry_run:
        owner = get_owner(filepath)
        print(json.dumps({
            "file": filepath,
            "role": args.role,
            "owner": owner,
            "would_resolve": (
                f"{owner}_wins" if owner in ("cloud", "local")
                else f"{owner}_strategy"
            ),
        }, indent=2))
        return 0

    ok = resolve_file(filepath, args.role, args.mode)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
