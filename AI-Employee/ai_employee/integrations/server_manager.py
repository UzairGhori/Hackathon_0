"""
AI Employee — MCP Server Lifecycle Manager

Manages the lifecycle of MCP server subprocesses: start, stop, restart,
health checks, and auto-restart via a background monitor thread.

All servers run as subprocesses communicating over stdio (stdin/stdout).
Windows-compatible (uses proc.terminate() instead of SIGTERM).

Usage:
    from ai_employee.integrations.server_manager import MCPServerManager

    manager = MCPServerManager()
    manager.start_all()
    print(manager.health_check_all())
    manager.stop_all()
"""

import logging
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ai_employee.integrations.tool_registry import ServerStatus

log = logging.getLogger("ai_employee.server_manager")


# ══════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ══════════════════════════════════════════════════════════════════════


@dataclass
class ServerProcess:
    """Tracks a managed MCP server subprocess."""
    name: str
    module_path: str
    process: subprocess.Popen | None = None
    status: ServerStatus = ServerStatus.STOPPED
    start_count: int = 0
    crash_count: int = 0
    last_started: str = ""
    last_error: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "module_path": self.module_path,
            "pid": self.process.pid if self.process else None,
            "status": self.status.value,
            "start_count": self.start_count,
            "crash_count": self.crash_count,
            "last_started": self.last_started,
            "last_error": self.last_error,
        }


# ══════════════════════════════════════════════════════════════════════
#  DEFAULT SERVER REGISTRY
# ══════════════════════════════════════════════════════════════════════

_DEFAULT_SERVERS = {
    "meta-social": "ai_employee.integrations.mcp_meta_server",
    "twitter-social": "ai_employee.integrations.mcp_twitter_server",
    "odoo-accounting": "ai_employee.integrations.odoo_mcp_server",
    "communication": "ai_employee.integrations.mcp_communication_server",
}


# ══════════════════════════════════════════════════════════════════════
#  MCP SERVER MANAGER
# ══════════════════════════════════════════════════════════════════════


class MCPServerManager:
    """
    Manages MCP server subprocesses with lifecycle control and monitoring.

    Features:
    - Start/stop/restart individual servers or all at once
    - Health checks via process.poll()
    - Background monitor thread with auto-restart (max 3 retries)
    - Thread-safe via threading.Lock
    - Windows-compatible process management
    """

    MAX_RESTART_RETRIES = 3

    def __init__(
        self,
        servers: dict[str, str] | None = None,
        python_executable: str | None = None,
    ) -> None:
        """
        Args:
            servers: Dict of server_name → module_path. Uses defaults if None.
            python_executable: Path to Python interpreter. Uses sys.executable if None.
        """
        self._lock = threading.Lock()
        self._python = python_executable or sys.executable
        self._servers: dict[str, ServerProcess] = {}
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop = threading.Event()

        server_map = servers or _DEFAULT_SERVERS
        for name, module_path in server_map.items():
            self._servers[name] = ServerProcess(
                name=name,
                module_path=module_path,
            )

    # ── Start / Stop / Restart ────────────────────────────────────────

    def start_server(self, name: str) -> bool:
        """
        Start a single MCP server as a subprocess.

        Args:
            name: Server name (e.g. "meta-social").

        Returns:
            True if started successfully, False otherwise.
        """
        with self._lock:
            sp = self._servers.get(name)
            if sp is None:
                log.error("Unknown server: %s", name)
                return False

            if sp.process is not None and sp.process.poll() is None:
                log.warning("Server %s already running (PID %d)",
                            name, sp.process.pid)
                return True

            return self._start_process(sp)

    def _start_process(self, sp: ServerProcess) -> bool:
        """Start the subprocess for a ServerProcess (caller holds lock)."""
        try:
            sp.status = ServerStatus.STARTING
            sp.process = subprocess.Popen(
                [self._python, "-m", sp.module_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Don't create a console window on Windows
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            sp.status = ServerStatus.RUNNING
            sp.start_count += 1
            sp.last_started = datetime.now().isoformat()
            sp.last_error = ""

            log.info("Started %s (PID %d)", sp.name, sp.process.pid)
            return True

        except Exception as exc:
            sp.status = ServerStatus.ERROR
            sp.last_error = str(exc)
            log.error("Failed to start %s: %s", sp.name, exc)
            return False

    def stop_server(self, name: str) -> bool:
        """
        Stop a running MCP server subprocess.

        Args:
            name: Server name.

        Returns:
            True if stopped successfully.
        """
        with self._lock:
            sp = self._servers.get(name)
            if sp is None:
                log.error("Unknown server: %s", name)
                return False
            return self._stop_process(sp)

    def _stop_process(self, sp: ServerProcess) -> bool:
        """Terminate the subprocess for a ServerProcess (caller holds lock)."""
        if sp.process is None or sp.process.poll() is not None:
            sp.status = ServerStatus.STOPPED
            sp.process = None
            return True

        try:
            # Close stdin to signal EOF, then terminate
            if sp.process.stdin:
                try:
                    sp.process.stdin.close()
                except OSError:
                    pass

            sp.process.terminate()
            try:
                sp.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sp.process.kill()
                sp.process.wait(timeout=3)

            log.info("Stopped %s (PID %d)", sp.name, sp.process.pid)
            sp.status = ServerStatus.STOPPED
            sp.process = None
            return True

        except Exception as exc:
            sp.status = ServerStatus.ERROR
            sp.last_error = str(exc)
            log.error("Failed to stop %s: %s", sp.name, exc)
            return False

    def restart_server(self, name: str) -> bool:
        """Stop and re-start a server."""
        self.stop_server(name)
        return self.start_server(name)

    # ── Batch operations ──────────────────────────────────────────────

    def start_all(self) -> dict[str, bool]:
        """Start all registered servers. Returns {name: success}."""
        results = {}
        for name in self._servers:
            results[name] = self.start_server(name)
        return results

    def stop_all(self) -> dict[str, bool]:
        """Stop all running servers. Returns {name: success}."""
        self.stop_health_monitor()
        results = {}
        for name in self._servers:
            results[name] = self.stop_server(name)
        return results

    # ── Health checks ─────────────────────────────────────────────────

    def health_check(self, name: str) -> dict:
        """
        Check the health of a single server.

        Returns:
            Dict with name, status, pid, alive, and metadata.
        """
        with self._lock:
            sp = self._servers.get(name)
            if sp is None:
                return {"name": name, "status": "unknown", "error": "not registered"}

            alive = False
            if sp.process is not None:
                rc = sp.process.poll()
                if rc is None:
                    alive = True
                    sp.status = ServerStatus.RUNNING
                else:
                    sp.status = ServerStatus.STOPPED
                    sp.last_error = f"exited with code {rc}"
                    sp.crash_count += 1
                    sp.process = None

            return {
                "name": sp.name,
                "status": sp.status.value,
                "pid": sp.process.pid if sp.process else None,
                "alive": alive,
                "start_count": sp.start_count,
                "crash_count": sp.crash_count,
                "last_started": sp.last_started,
                "last_error": sp.last_error,
            }

    def health_check_all(self) -> dict[str, dict]:
        """Check health of all servers. Returns {name: health_dict}."""
        return {name: self.health_check(name) for name in self._servers}

    # ── Background health monitor ─────────────────────────────────────

    def start_health_monitor(self, interval: int = 30) -> None:
        """
        Start a background daemon thread that periodically checks server
        health and auto-restarts crashed servers (max 3 retries).

        Args:
            interval: Seconds between health checks.
        """
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            log.warning("Health monitor already running")
            return

        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True,
            name="mcp-health-monitor",
        )
        self._monitor_thread.start()
        log.info("MCP health monitor started (interval=%ds)", interval)

    def stop_health_monitor(self) -> None:
        """Stop the background health monitor thread."""
        if self._monitor_thread is None:
            return

        self._monitor_stop.set()
        self._monitor_thread.join(timeout=10)
        self._monitor_thread = None
        log.info("MCP health monitor stopped")

    def _monitor_loop(self, interval: int) -> None:
        """Background loop: check health, auto-restart crashed servers."""
        while not self._monitor_stop.is_set():
            for name in list(self._servers.keys()):
                with self._lock:
                    sp = self._servers.get(name)
                    if sp is None:
                        continue

                    # Only monitor servers that were previously started
                    if sp.start_count == 0:
                        continue

                    # Check if process crashed
                    if sp.process is not None:
                        rc = sp.process.poll()
                        if rc is not None:
                            sp.status = ServerStatus.STOPPED
                            sp.last_error = f"exited with code {rc}"
                            sp.crash_count += 1
                            sp.process = None
                            log.warning(
                                "Server %s crashed (exit code %d, crash #%d)",
                                name, rc, sp.crash_count,
                            )

                    # Auto-restart if under retry limit
                    if (sp.status == ServerStatus.STOPPED
                            and sp.start_count > 0
                            and sp.crash_count <= self.MAX_RESTART_RETRIES):
                        log.info("Auto-restarting %s (attempt %d/%d)",
                                 name, sp.crash_count, self.MAX_RESTART_RETRIES)
                        self._start_process(sp)
                    elif sp.crash_count > self.MAX_RESTART_RETRIES:
                        sp.status = ServerStatus.ERROR
                        log.error(
                            "Server %s exceeded max restarts (%d), giving up",
                            name, self.MAX_RESTART_RETRIES,
                        )

            self._monitor_stop.wait(timeout=interval)

    # ── Status ────────────────────────────────────────────────────────

    def status(self) -> dict:
        """
        Get a full status report of all managed servers.

        Returns:
            Dict with server details, counts, and monitor state.
        """
        health = self.health_check_all()
        running = sum(1 for h in health.values() if h["alive"])
        return {
            "total_servers": len(self._servers),
            "running": running,
            "stopped": len(self._servers) - running,
            "monitor_active": (
                self._monitor_thread is not None
                and self._monitor_thread.is_alive()
            ),
            "servers": health,
        }

    def get_server_process(self, name: str) -> ServerProcess | None:
        """Get the ServerProcess for direct stdio access (used by router)."""
        return self._servers.get(name)

    def is_server_running(self, name: str) -> bool:
        """Check if a server process is alive."""
        with self._lock:
            sp = self._servers.get(name)
            if sp is None or sp.process is None:
                return False
            return sp.process.poll() is None
