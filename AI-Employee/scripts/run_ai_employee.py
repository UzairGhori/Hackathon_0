"""
AI Employee — Unified Scheduler (Silver Tier)

Runs the full AI Employee pipeline on a loop:
  1. Scan vault/Inbox/ for new .md files
  2. Triage each file (summarize + route to Needs_Action)
  3. Run the task planner (create execution plans)
  4. Log every cycle to vault/logs/

Can run in two modes:
  --once       Single pass, then exit (for Task Scheduler / cron)
  --loop       Continuous loop every N minutes (default: 5)

Usage:
    python scripts/run_ai_employee.py --once
    python scripts/run_ai_employee.py --loop --interval 5
"""

import argparse
import os
import sys
import time
import logging
from datetime import datetime

# ---------------------------------------------------------------------------
# Paths — everything relative to project root
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

# Add project root to sys.path so we can import watcher and planner
sys.path.insert(0, PROJECT_ROOT)

VAULT_DIR = os.path.join(PROJECT_ROOT, "vault")
INBOX_DIR = os.path.join(VAULT_DIR, "Inbox")
NEEDS_ACTION_DIR = os.path.join(VAULT_DIR, "Needs_Action")
DONE_DIR = os.path.join(VAULT_DIR, "Done")
LOG_DIR = os.path.join(VAULT_DIR, "logs")

# ---------------------------------------------------------------------------
# Logging setup — writes to both console and a daily log file
# ---------------------------------------------------------------------------
def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f"employee_{datetime.now().strftime('%Y%m%d')}.log")

    logger = logging.getLogger("ai_employee")
    logger.setLevel(logging.INFO)

    # Avoid adding duplicate handlers on repeated calls
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler (daily rotation by filename)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ---------------------------------------------------------------------------
# Phase 1 — Triage: summarize new Inbox files into Needs_Action
# ---------------------------------------------------------------------------
def run_triage(log: logging.Logger) -> int:
    """Import and run the watcher's process_file on every un-triaged Inbox file."""
    try:
        from watcher import process_file
    except ImportError:
        log.error("Cannot import watcher.py — make sure it exists in project root.")
        return 0

    os.makedirs(INBOX_DIR, exist_ok=True)
    os.makedirs(NEEDS_ACTION_DIR, exist_ok=True)

    inbox_files = sorted(f for f in os.listdir(INBOX_DIR) if f.endswith(".md"))
    if not inbox_files:
        log.info("TRIAGE  | Inbox is empty.")
        return 0

    existing = set(os.listdir(NEEDS_ACTION_DIR))
    processed = 0

    for filename in inbox_files:
        response_name = f"Response_{filename}"
        if response_name in existing:
            continue  # already triaged

        filepath = os.path.join(INBOX_DIR, filename)
        log.info(f"TRIAGE  | Processing: {filename}")

        try:
            process_file(filepath)
            processed += 1
        except Exception as exc:
            log.error(f"TRIAGE  | Failed on {filename}: {exc}")

    log.info(f"TRIAGE  | Done. Files triaged: {processed}")
    return processed


# ---------------------------------------------------------------------------
# Phase 2 — Plan: create execution plans for triaged files
# ---------------------------------------------------------------------------
def run_planner(log: logging.Logger) -> int:
    """Import and run the planner's create_plan on every un-planned Inbox file."""
    try:
        from planner import create_plan
    except ImportError:
        log.error("Cannot import planner.py — make sure it exists in project root.")
        return 0

    os.makedirs(INBOX_DIR, exist_ok=True)
    os.makedirs(NEEDS_ACTION_DIR, exist_ok=True)

    inbox_files = sorted(f for f in os.listdir(INBOX_DIR) if f.endswith(".md"))
    if not inbox_files:
        log.info("PLANNER | Inbox is empty.")
        return 0

    existing_plans = set(os.listdir(NEEDS_ACTION_DIR))
    planned = 0

    for filename in inbox_files:
        already_planned = any(
            p.endswith(f"_{filename}") and p.startswith("Plan_")
            for p in existing_plans
        )
        if already_planned:
            continue

        filepath = os.path.join(INBOX_DIR, filename)
        log.info(f"PLANNER | Planning: {filename}")

        try:
            result = create_plan(filepath)
            if result:
                log.info(f"PLANNER | Created: {os.path.basename(result)}")
                planned += 1
        except Exception as exc:
            log.error(f"PLANNER | Failed on {filename}: {exc}")

    log.info(f"PLANNER | Done. Plans created: {planned}")
    return planned


# ---------------------------------------------------------------------------
# Phase 3 — Stats: report current vault state
# ---------------------------------------------------------------------------
def report_stats(log: logging.Logger) -> None:
    """Log a quick count of files in each vault stage."""
    stages = {
        "Inbox": INBOX_DIR,
        "Needs_Action": NEEDS_ACTION_DIR,
        "Done": DONE_DIR,
    }
    counts = []
    for name, path in stages.items():
        if os.path.isdir(path):
            n = len([f for f in os.listdir(path) if f.endswith(".md")])
        else:
            n = 0
        counts.append(f"{name}: {n}")

    log.info(f"STATS   | {' | '.join(counts)}")


# ---------------------------------------------------------------------------
# Single cycle — runs triage + planner + stats once
# ---------------------------------------------------------------------------
def run_cycle(log: logging.Logger, cycle_num: int) -> None:
    log.info("=" * 55)
    log.info(f"CYCLE {cycle_num} | Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 55)

    triaged = run_triage(log)
    planned = run_planner(log)
    report_stats(log)

    if triaged == 0 and planned == 0:
        log.info("CYCLE   | No new work. Inbox is up to date.")
    else:
        log.info(f"CYCLE   | Completed. Triaged: {triaged}, Planned: {planned}")

    log.info("")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="AI Employee — Unified Scheduler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  Single pass (for Task Scheduler / cron):
    python scripts/run_ai_employee.py --once

  Continuous loop every 5 minutes:
    python scripts/run_ai_employee.py --loop --interval 5

  Continuous loop every 1 minute:
    python scripts/run_ai_employee.py --loop --interval 1
""",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single cycle and exit.",
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Run continuously on a timer.",
    )
    parser.add_argument(
        "--interval", type=int, default=5,
        help="Minutes between cycles in --loop mode (default: 5).",
    )
    args = parser.parse_args()

    # Default to --once if neither flag is given
    if not args.once and not args.loop:
        args.once = True

    log = setup_logging()

    log.info("##################################################")
    log.info("  AI Employee — Scheduler (Silver Tier)")
    log.info(f"  Mode: {'LOOP (every ' + str(args.interval) + ' min)' if args.loop else 'SINGLE RUN'}")
    log.info(f"  Vault: {VAULT_DIR}")
    log.info(f"  Logs:  {LOG_DIR}")
    log.info("##################################################")
    log.info("")

    # Ensure all vault folders exist
    for d in [INBOX_DIR, NEEDS_ACTION_DIR, DONE_DIR, LOG_DIR]:
        os.makedirs(d, exist_ok=True)

    if args.once:
        run_cycle(log, 1)
    else:
        cycle = 0
        try:
            while True:
                cycle += 1
                run_cycle(log, cycle)
                log.info(f"SLEEP   | Next cycle in {args.interval} minute(s)... (Ctrl+C to stop)")
                time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            log.info("")
            log.info("STOP    | Scheduler stopped by user.")


if __name__ == "__main__":
    main()
