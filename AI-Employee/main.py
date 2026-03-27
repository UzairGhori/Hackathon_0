"""
AI Employee — Master Orchestrator (Silver Tier)

Single entry point that runs the full AI Employee pipeline:

  1. WATCH   — Monitor vault/Inbox/ for new .md files
  2. TRIAGE  — Summarize and classify each new file
  3. PLAN    — Generate execution plans with priority + approval flags
  4. APPROVE — Route sensitive tasks through human approval
  5. MOVE    — Advance completed tasks through the pipeline
  6. LOG     — Record every action to vault/logs/

Usage:
    python main.py                  # Run once and exit
    python main.py --loop           # Run every 5 minutes
    python main.py --loop -i 2      # Run every 2 minutes
    python main.py --watch          # Real-time file watcher mode

Requirements:
    pip install watchdog
"""

import argparse
import os
import sys
import time
import logging
import subprocess
from datetime import datetime

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

VAULT_DIR = os.path.join(PROJECT_ROOT, "vault")
INBOX_DIR = os.path.join(VAULT_DIR, "Inbox")
NEEDS_ACTION_DIR = os.path.join(VAULT_DIR, "Needs_Action")
DONE_DIR = os.path.join(VAULT_DIR, "Done")
LOG_DIR = os.path.join(VAULT_DIR, "logs")
APPROVAL_DIR = os.path.join(PROJECT_ROOT, "AI_Employee_Vault", "Needs_Approval")

# Skill script paths
SKILLS_DIR = os.path.join(PROJECT_ROOT, ".claude", "skills")
MOVE_SCRIPT = os.path.join(SKILLS_DIR, "vault-file-manager", "scripts", "move_task.py")
APPROVAL_SCRIPT = os.path.join(SKILLS_DIR, "human-approval", "scripts", "request_approval.py")
EMAIL_SCRIPT = os.path.join(SKILLS_DIR, "gmail-send", "scripts", "send_email.py")

BANNER = r"""
 _____ _____   _____                _
|  _  |_   _| |   __|_____ ___| |___ _ _ ___ ___
|     | | |   |   __|     | . | | . | | | -_| -_|
|__|__| |_|   |_____|_|_|_|  _|_|___|_  |___|___|
                          |_|       |___|

       Silver Tier — Agent Factory Hackathon
"""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(verbose: bool = False) -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f"employee_{datetime.now().strftime('%Y%m%d')}.log")

    logger = logging.getLogger("ai_employee")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if logger.handlers:
        return logger

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ---------------------------------------------------------------------------
# Phase 1 — Triage new inbox files
# ---------------------------------------------------------------------------
def phase_triage(log: logging.Logger) -> int:
    from watcher import process_file

    inbox_files = sorted(f for f in os.listdir(INBOX_DIR) if f.endswith(".md"))
    existing = set(os.listdir(NEEDS_ACTION_DIR))
    count = 0

    for filename in inbox_files:
        if f"Response_{filename}" in existing:
            continue
        filepath = os.path.join(INBOX_DIR, filename)
        log.info(f"  TRIAGE  | {filename}")
        try:
            process_file(filepath)
            count += 1
        except Exception as exc:
            log.error(f"  TRIAGE  | FAILED {filename}: {exc}")

    return count


# ---------------------------------------------------------------------------
# Phase 2 — Plan unplanned tasks
# ---------------------------------------------------------------------------
def phase_plan(log: logging.Logger) -> int:
    from planner import create_plan

    inbox_files = sorted(f for f in os.listdir(INBOX_DIR) if f.endswith(".md"))
    existing_plans = set(os.listdir(NEEDS_ACTION_DIR))
    count = 0

    for filename in inbox_files:
        already = any(p.endswith(f"_{filename}") and p.startswith("Plan_") for p in existing_plans)
        if already:
            continue
        filepath = os.path.join(INBOX_DIR, filename)
        log.info(f"  PLAN    | {filename}")
        try:
            result = create_plan(filepath)
            if result:
                log.info(f"  PLAN    | -> {os.path.basename(result)}")
                count += 1
        except Exception as exc:
            log.error(f"  PLAN    | FAILED {filename}: {exc}")

    return count


# ---------------------------------------------------------------------------
# Phase 3 — Check for pending approvals
# ---------------------------------------------------------------------------
def phase_check_approvals(log: logging.Logger) -> dict:
    os.makedirs(APPROVAL_DIR, exist_ok=True)
    results = {"approved": 0, "rejected": 0, "pending": 0}

    for filename in os.listdir(APPROVAL_DIR):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(APPROVAL_DIR, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue

        upper = content.upper()
        if "<!-- DECISION BELOW THIS LINE -->" in content:
            decision_area = content.split("<!-- DECISION BELOW THIS LINE -->", 1)[1].strip().upper()
            if "YES" in decision_area or "APPROVED" in decision_area:
                results["approved"] += 1
                log.info(f"  APPROVE | {filename} -> APPROVED")
            elif "NO" in decision_area or "REJECTED" in decision_area:
                results["rejected"] += 1
                log.info(f"  APPROVE | {filename} -> REJECTED")
            else:
                results["pending"] += 1
        elif "PENDING" in upper:
            results["pending"] += 1

    return results


# ---------------------------------------------------------------------------
# Phase 4 — Stats
# ---------------------------------------------------------------------------
def phase_stats(log: logging.Logger) -> None:
    stages = [
        ("Inbox", INBOX_DIR),
        ("Needs_Action", NEEDS_ACTION_DIR),
        ("Done", DONE_DIR),
        ("Approvals", APPROVAL_DIR),
    ]
    parts = []
    for name, path in stages:
        if os.path.isdir(path):
            n = len([f for f in os.listdir(path) if f.endswith(".md")])
        else:
            n = 0
        parts.append(f"{name}: {n}")

    log.info(f"  STATS   | {' | '.join(parts)}")


# ---------------------------------------------------------------------------
# Run one full cycle
# ---------------------------------------------------------------------------
def run_cycle(log: logging.Logger, cycle_num: int) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"{'=' * 55}")
    log.info(f"  CYCLE {cycle_num} | {timestamp}")
    log.info(f"{'=' * 55}")

    triaged = phase_triage(log)
    planned = phase_plan(log)
    approvals = phase_check_approvals(log)
    phase_stats(log)

    total_work = triaged + planned
    if total_work == 0:
        log.info("  RESULT  | No new work. Inbox is up to date.")
    else:
        log.info(f"  RESULT  | Triaged: {triaged} | Planned: {planned}")

    if approvals["pending"] > 0:
        log.info(f"  RESULT  | Pending approvals: {approvals['pending']}")

    log.info("")


# ---------------------------------------------------------------------------
# Watch mode — real-time file monitoring
# ---------------------------------------------------------------------------
def run_watch_mode(log: logging.Logger) -> None:
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        log.error("watchdog not installed. Run: pip install watchdog")
        sys.exit(1)

    from watcher import process_file, read_with_retry
    from planner import create_plan

    class InboxOrchestrator(FileSystemEventHandler):
        def __init__(self):
            super().__init__()
            self._processed = set()

        def _handle(self, event):
            if event.is_directory or not event.src_path.endswith(".md"):
                return

            filepath = os.path.abspath(event.src_path)
            if filepath in self._processed:
                return
            self._processed.add(filepath)

            filename = os.path.basename(filepath)
            time.sleep(1)

            # Triage
            log.info(f"  TRIAGE  | {filename}")
            try:
                process_file(filepath)
            except Exception as exc:
                log.error(f"  TRIAGE  | FAILED: {exc}")

            # Plan
            existing_plans = set(os.listdir(NEEDS_ACTION_DIR))
            already = any(p.endswith(f"_{filename}") and p.startswith("Plan_") for p in existing_plans)
            if not already:
                log.info(f"  PLAN    | {filename}")
                try:
                    result = create_plan(filepath)
                    if result:
                        log.info(f"  PLAN    | -> {os.path.basename(result)}")
                except Exception as exc:
                    log.error(f"  PLAN    | FAILED: {exc}")

            phase_stats(log)
            log.info("")

        def on_created(self, event):
            self._handle(event)

        def on_modified(self, event):
            self._handle(event)

    handler = InboxOrchestrator()
    observer = Observer()
    observer.schedule(handler, path=INBOX_DIR, recursive=False)
    observer.start()

    log.info(f"  MODE    | Real-time watcher")
    log.info(f"  WATCH   | {INBOX_DIR}")
    log.info(f"  Drop .md files into Inbox/ to trigger the pipeline.")
    log.info(f"  Press Ctrl+C to stop.")
    log.info("")

    # Run one initial cycle to catch existing files
    run_cycle(log, 0)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("  STOP    | Shutting down watcher...")
        observer.stop()
    observer.join()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="AI Employee — Master Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", default=True,
                      help="Single cycle, then exit (default)")
    mode.add_argument("--loop", action="store_true",
                      help="Repeat every N minutes")
    mode.add_argument("--watch", action="store_true",
                      help="Real-time file watcher mode")
    parser.add_argument("-i", "--interval", type=int, default=5,
                        help="Loop interval in minutes (default: 5)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    # Ensure vault structure
    for d in [INBOX_DIR, NEEDS_ACTION_DIR, DONE_DIR, LOG_DIR, APPROVAL_DIR]:
        os.makedirs(d, exist_ok=True)

    log = setup_logging(args.verbose)

    print(BANNER)
    log.info(f"  Project : {PROJECT_ROOT}")
    log.info(f"  Vault   : {VAULT_DIR}")
    log.info(f"  Logs    : {LOG_DIR}")

    if args.watch:
        log.info("")
        run_watch_mode(log)
    elif args.loop:
        log.info(f"  MODE    | Loop every {args.interval} min")
        log.info("")
        cycle = 0
        try:
            while True:
                cycle += 1
                run_cycle(log, cycle)
                log.info(f"  SLEEP   | Next cycle in {args.interval} min (Ctrl+C to stop)")
                time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            log.info("")
            log.info("  STOP    | Scheduler stopped.")
    else:
        log.info(f"  MODE    | Single run")
        log.info("")
        run_cycle(log, 1)

    log.info("  EXIT    | AI Employee stopped.")


if __name__ == "__main__":
    main()
