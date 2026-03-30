"""
AI Employee — Production Runtime Entry Point

Hardened production-grade launcher for the AI Employee system.
Uses SystemManager for the full 7-phase startup sequence:

    1. Cloud Watchers   — 6 always-on channel watchers
    2. MCP Servers      — 4 tool-server subprocesses
    3. Odoo Connection  — XML-RPC connectivity warm-up
    4. Ralph Loop       — Autonomous error-fixing (standby)
    5. Health Monitor   — Continuous probes + auto-restart
    6. Git Sync         — Periodic vault ↔ git backup
    7. Dashboard        — FastAPI web UI + CEO analytics

Features:
    - PID file management (prevents duplicate instances)
    - Signal handling (SIGTERM, SIGINT → graceful shutdown)
    - Structured logging (console + file)
    - Startup health gate (fail-fast on critical errors)
    - Production status endpoint via dashboard
    - Configurable cycle interval and git sync interval

Usage:
    python production_main.py                       # Full production run
    python production_main.py --interval 2          # 2-minute cycles
    python production_main.py --git-interval 600    # 10-min git sync
    python production_main.py --status              # Print system status
    python production_main.py --health              # Health check only
    python production_main.py --once                # Single cycle, then exit
    python production_main.py --no-git              # Disable git sync
"""

import argparse
import atexit
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure the project root is on sys.path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai_employee.config.settings import Settings
from ai_employee.system_manager import SystemManager


# ── Constants ────────────────────────────────────────────────────────────

BANNER = r"""
 _____ _____   _____                _
|  _  |_   _| |   __|_____ ___| |___ _ _ ___ ___
|     | | |   |   __|     | . | | . | | | -_| -_|
|__|__| |_|   |_____|_|_|_|  _|_|___|_  |___|___|
                          |_|       |___|

    PLATINUM TIER — Production Runtime System
    Agent Factory Hackathon
"""

PID_FILENAME = "ai_employee.pid"


# ── Logging ──────────────────────────────────────────────────────────────

def setup_production_logging(
    log_dir: Path, verbose: bool = False,
) -> logging.Logger:
    """Configure production-grade logging with rotation-friendly file output."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"production_{datetime.now().strftime('%Y%m%d')}.log"

    logger = logging.getLogger("ai_employee")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ── PID file management ─────────────────────────────────────────────────

def write_pid_file(pid_path: Path) -> None:
    """Write the current PID to a file for process management."""
    pid_path.write_text(str(os.getpid()), encoding="utf-8")


def read_pid_file(pid_path: Path) -> int | None:
    """Read a PID from file. Returns None if file doesn't exist."""
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text("utf-8").strip())
    except (ValueError, OSError):
        return None


def is_pid_running(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)  # Signal 0 = existence check
        return True
    except (OSError, ProcessLookupError):
        return False


def remove_pid_file(pid_path: Path) -> None:
    """Remove the PID file."""
    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        pass


def check_existing_instance(pid_path: Path, log: logging.Logger) -> None:
    """Exit if another instance is already running."""
    existing_pid = read_pid_file(pid_path)
    if existing_pid is not None and is_pid_running(existing_pid):
        log.error(
            "Another instance is already running (PID %d). "
            "Stop it first or remove %s",
            existing_pid, pid_path,
        )
        sys.exit(1)


# ── Signal handling ──────────────────────────────────────────────────────

_manager: SystemManager | None = None


def _signal_handler(signum: int, frame) -> None:
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    if _manager:
        _manager.log.info("")
        _manager.log.info("  SIGNAL  | %s received — initiating graceful shutdown", sig_name)
        _manager.stop()


# ── CLI ──────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AI Employee — Production Runtime System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The production runtime executes a 7-phase startup sequence,\n"
            "then runs continuous AI Employee cycles until stopped.\n\n"
            "Environment variables are loaded from .env in the project root.\n"
            "See .env.example for all available configuration options."
        ),
    )

    # Run modes
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single pipeline cycle and exit",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print production system status and exit",
    )
    parser.add_argument(
        "--health", action="store_true",
        help="Run health checks and exit",
    )

    # Configuration
    parser.add_argument(
        "-i", "--interval", type=int, default=None,
        help="Cycle interval in minutes (default: from .env or 5)",
    )
    parser.add_argument(
        "--git-interval", type=int, default=300,
        help="Git sync interval in seconds (default: 300)",
    )
    parser.add_argument(
        "--no-git", action="store_true",
        help="Disable automatic git sync",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug-level logging",
    )

    return parser


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    global _manager

    parser = build_parser()
    args = parser.parse_args()

    # ── Load configuration ───────────────────────────────────────────
    settings = Settings.load()
    log = setup_production_logging(settings.log_dir, args.verbose)

    interval = args.interval if args.interval is not None else settings.cycle_interval_minutes
    git_interval = 0 if args.no_git else args.git_interval

    print(BANNER)
    log.info("  Project : %s", settings.project_root)
    log.info("  Vault   : %s", settings.vault_dir)
    log.info("  PID     : %d", os.getpid())
    log.info("")

    # ── PID file ─────────────────────────────────────────────────────
    pid_path = settings.project_root / PID_FILENAME
    check_existing_instance(pid_path, log)
    write_pid_file(pid_path)
    atexit.register(remove_pid_file, pid_path)

    # ── Build system manager ─────────────────────────────────────────
    _manager = SystemManager(
        settings=settings,
        log=log,
        git_sync_interval=git_interval,
    )

    # ── Signal handlers ──────────────────────────────────────────────
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # ── Health-only mode ─────────────────────────────────────────────
    if args.health:
        from ai_employee.monitoring.health_check import HealthCheck
        health_check = HealthCheck(settings)
        report = health_check.run()
        print(health_check.render_report(report))
        remove_pid_file(pid_path)
        return

    # ── Startup ──────────────────────────────────────────────────────
    startup_result = _manager.startup()

    if not startup_result.success:
        log.error("  ABORT   | Startup failed — critical errors detected")
        for err in startup_result.errors:
            log.error("  ABORT   |   %s", err)
        _manager.shutdown()
        remove_pid_file(pid_path)
        sys.exit(1)

    # ── Status-only mode ─────────────────────────────────────────────
    if args.status:
        _manager.print_status()
        _manager.shutdown()
        remove_pid_file(pid_path)
        return

    # ── Single cycle mode ────────────────────────────────────────────
    if args.once:
        log.info("  MODE    | Single production cycle")
        log.info("")
        _manager.employee.run_cycle(1)
        _manager.shutdown()
        remove_pid_file(pid_path)
        log.info("  EXIT    | Done.")
        return

    # ── Continuous production mode ────────────────────────────────────
    _manager.run(interval_minutes=interval)
    remove_pid_file(pid_path)


if __name__ == "__main__":
    main()
