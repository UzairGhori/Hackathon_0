"""
AI Employee — Dynamic MCP Tool Call Router

Routes tool call requests to the correct MCP server via JSON-RPC 2.0
over stdio. Supports 3-tier routing (exact → category → fuzzy search),
automatic server start, retry-on-failure, and parallel batch routing.

Usage:
    from ai_employee.integrations.mcp_router import MCPRouter
    from ai_employee.integrations.tool_registry import ToolRegistry, ToolCallRequest
    from ai_employee.integrations.server_manager import MCPServerManager

    registry = ToolRegistry()
    registry.discover_all()
    manager = MCPServerManager()
    manager.start_all()

    router = MCPRouter(registry, manager)
    result = router.route_call(ToolCallRequest(
        tool_name="send_email",
        arguments={"to": "x@example.com", "subject": "Hi", "body": "Hello"},
    ))
    print(result.to_dict())
"""

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from ai_employee.integrations.tool_registry import (
    ToolCallRequest,
    ToolCallResult,
    ToolCategory,
    ToolRegistry,
)
from ai_employee.integrations.server_manager import MCPServerManager

log = logging.getLogger("ai_employee.mcp_router")


# ══════════════════════════════════════════════════════════════════════
#  CATEGORY → SERVER MAPPING (for fallback routing)
# ══════════════════════════════════════════════════════════════════════

_CATEGORY_TO_SERVER: dict[ToolCategory, str] = {
    ToolCategory.SOCIAL: "meta-social",
    ToolCategory.ACCOUNTING: "odoo-accounting",
    ToolCategory.COMMUNICATION: "communication",
}


# ══════════════════════════════════════════════════════════════════════
#  MCP ROUTER
# ══════════════════════════════════════════════════════════════════════


class MCPRouter:
    """
    Routes MCP tool call requests to the correct server subprocess.

    Routing algorithm (3-tier):
      1. Exact tool name lookup in registry → server_name
      2. Category-based fallback (if caller provides a category hint)
      3. Fuzzy search (substring match) with suggestions

    Communication with server subprocesses uses JSON-RPC 2.0 over stdio.
    """

    CALL_TIMEOUT = 30  # seconds
    MAX_BATCH_WORKERS = 3

    def __init__(
        self,
        registry: ToolRegistry,
        manager: MCPServerManager,
    ) -> None:
        self._registry = registry
        self._manager = manager
        self._call_log: list[dict] = []
        self._lock = threading.Lock()

    # ── Main routing entry point ──────────────────────────────────────

    def route_call(
        self,
        request: ToolCallRequest,
        category_hint: ToolCategory | None = None,
        retry: bool = True,
    ) -> ToolCallResult:
        """
        Route a tool call request to the correct MCP server.

        Uses a 3-tier routing algorithm:
          1. Exact tool name lookup
          2. Category-based fallback
          3. Fuzzy search with suggestions

        Args:
            request: The tool call request.
            category_hint: Optional category for fallback routing.
            retry: If True, retry once after restarting the server.

        Returns:
            ToolCallResult with the server's response or error.
        """
        if not request.call_id:
            request.call_id = uuid.uuid4().hex[:12]

        start = time.time()

        # Tier 1: Exact lookup
        server_name = self._registry.get_server_for_tool(request.tool_name)

        # Tier 2: Category fallback
        if server_name is None and category_hint is not None:
            server_name = _CATEGORY_TO_SERVER.get(category_hint)
            if server_name:
                log.info(
                    "Tier 2 routing: %s → %s (category %s)",
                    request.tool_name, server_name, category_hint.value,
                )

        # Tier 3: Fuzzy search
        if server_name is None:
            matches = self._registry.search_tools(request.tool_name)
            if matches:
                suggestions = [m.name for m in matches[:5]]
                elapsed = (time.time() - start) * 1000
                result = ToolCallResult(
                    tool_name=request.tool_name,
                    success=False,
                    error=f"Tool '{request.tool_name}' not found. "
                          f"Did you mean: {', '.join(suggestions)}?",
                    call_id=request.call_id,
                    duration_ms=elapsed,
                )
                self._log_call(request, result)
                return result
            else:
                elapsed = (time.time() - start) * 1000
                result = ToolCallResult(
                    tool_name=request.tool_name,
                    success=False,
                    error=f"Tool '{request.tool_name}' not found in any server.",
                    call_id=request.call_id,
                    duration_ms=elapsed,
                )
                self._log_call(request, result)
                return result

        # Ensure the server is running
        self._ensure_server_running(server_name)

        # Forward the call
        result = self._forward_to_server(server_name, request)
        result.duration_ms = (time.time() - start) * 1000

        # Retry once on failure (restart server, then re-send)
        if not result.success and retry:
            log.warning(
                "Call to %s/%s failed, retrying after restart...",
                server_name, request.tool_name,
            )
            self._manager.restart_server(server_name)
            result = self._forward_to_server(server_name, request)
            result.duration_ms = (time.time() - start) * 1000

        self._log_call(request, result)
        return result

    # ── Batch routing ─────────────────────────────────────────────────

    def batch_route(
        self,
        requests: list[ToolCallRequest],
    ) -> list[ToolCallResult]:
        """
        Route multiple tool calls in parallel using a thread pool.

        Calls targeting the same server are sequential; calls to different
        servers run in parallel.

        Args:
            requests: List of tool call requests.

        Returns:
            List of ToolCallResult in the same order as requests.
        """
        # Group by target server for parallel execution
        results: list[ToolCallResult | None] = [None] * len(requests)

        with ThreadPoolExecutor(
            max_workers=self.MAX_BATCH_WORKERS,
            thread_name_prefix="mcp-batch",
        ) as pool:
            futures = {}
            for idx, req in enumerate(requests):
                if not req.call_id:
                    req.call_id = uuid.uuid4().hex[:12]
                future = pool.submit(self.route_call, req, retry=True)
                futures[future] = idx

            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result(timeout=self.CALL_TIMEOUT)
                except Exception as exc:
                    results[idx] = ToolCallResult(
                        tool_name=requests[idx].tool_name,
                        success=False,
                        error=f"Batch call failed: {exc}",
                        call_id=requests[idx].call_id,
                    )

        return results  # type: ignore[return-value]

    # ── Server management helpers ─────────────────────────────────────

    def _ensure_server_running(self, server_name: str) -> None:
        """Auto-start a server if it's not currently running."""
        if not self._manager.is_server_running(server_name):
            log.info("Auto-starting server %s", server_name)
            self._manager.start_server(server_name)
            # Brief pause for the subprocess to initialize
            time.sleep(0.5)

    # ── JSON-RPC 2.0 over stdio ──────────────────────────────────────

    def _forward_to_server(
        self,
        server_name: str,
        request: ToolCallRequest,
    ) -> ToolCallResult:
        """
        Send a JSON-RPC 2.0 request to the server's stdin and read the
        response from stdout.

        Args:
            server_name: Target server name.
            request: The tool call request to forward.

        Returns:
            ToolCallResult with the response or error.
        """
        sp = self._manager.get_server_process(server_name)
        if sp is None or sp.process is None:
            return ToolCallResult(
                tool_name=request.tool_name,
                success=False,
                error=f"Server '{server_name}' not available",
                server_name=server_name,
                call_id=request.call_id,
            )

        proc = sp.process
        if proc.poll() is not None:
            return ToolCallResult(
                tool_name=request.tool_name,
                success=False,
                error=f"Server '{server_name}' process is not running",
                server_name=server_name,
                call_id=request.call_id,
            )

        # Build JSON-RPC 2.0 request
        rpc_request = {
            "jsonrpc": "2.0",
            "id": request.call_id,
            "method": "tools/call",
            "params": {
                "name": request.tool_name,
                "arguments": request.arguments,
            },
        }

        try:
            payload = json.dumps(rpc_request) + "\n"

            if proc.stdin is None:
                return ToolCallResult(
                    tool_name=request.tool_name,
                    success=False,
                    error=f"Server '{server_name}' stdin not available",
                    server_name=server_name,
                    call_id=request.call_id,
                )

            proc.stdin.write(payload.encode("utf-8"))
            proc.stdin.flush()

            # Read response line
            if proc.stdout is None:
                return ToolCallResult(
                    tool_name=request.tool_name,
                    success=False,
                    error=f"Server '{server_name}' stdout not available",
                    server_name=server_name,
                    call_id=request.call_id,
                )

            response_line = proc.stdout.readline()
            if not response_line:
                return ToolCallResult(
                    tool_name=request.tool_name,
                    success=False,
                    error=f"No response from server '{server_name}' (EOF)",
                    server_name=server_name,
                    call_id=request.call_id,
                )

            rpc_response = json.loads(response_line.decode("utf-8"))

            # Parse JSON-RPC 2.0 response
            if "error" in rpc_response:
                err = rpc_response["error"]
                error_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                return ToolCallResult(
                    tool_name=request.tool_name,
                    success=False,
                    error=error_msg,
                    server_name=server_name,
                    call_id=request.call_id,
                )

            result_data = rpc_response.get("result", {})
            # MCP tool results typically have content[0].text
            if isinstance(result_data, dict):
                content = result_data.get("content", [])
                if content and isinstance(content, list):
                    text = content[0].get("text", json.dumps(result_data))
                else:
                    text = json.dumps(result_data, indent=2)
            else:
                text = str(result_data)

            return ToolCallResult(
                tool_name=request.tool_name,
                success=True,
                result=text,
                server_name=server_name,
                call_id=request.call_id,
            )

        except json.JSONDecodeError as exc:
            return ToolCallResult(
                tool_name=request.tool_name,
                success=False,
                error=f"Invalid JSON response from '{server_name}': {exc}",
                server_name=server_name,
                call_id=request.call_id,
            )
        except OSError as exc:
            return ToolCallResult(
                tool_name=request.tool_name,
                success=False,
                error=f"IO error communicating with '{server_name}': {exc}",
                server_name=server_name,
                call_id=request.call_id,
            )
        except Exception as exc:
            return ToolCallResult(
                tool_name=request.tool_name,
                success=False,
                error=f"Unexpected error routing to '{server_name}': {exc}",
                server_name=server_name,
                call_id=request.call_id,
            )

    # ── Call logging ──────────────────────────────────────────────────

    def _log_call(self, request: ToolCallRequest, result: ToolCallResult) -> None:
        """Record a tool call for audit/dashboard purposes."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "call_id": request.call_id,
            "tool_name": request.tool_name,
            "server_name": result.server_name,
            "success": result.success,
            "duration_ms": round(result.duration_ms, 1),
            "error": result.error or None,
        }
        with self._lock:
            self._call_log.append(entry)
            # Keep last 500 entries
            if len(self._call_log) > 500:
                self._call_log = self._call_log[-500:]

        level = logging.INFO if result.success else logging.WARNING
        log.log(
            level,
            "MCP call %s → %s [%s] %.1fms%s",
            request.tool_name,
            result.server_name or "?",
            "OK" if result.success else "FAIL",
            result.duration_ms,
            f" ({result.error})" if result.error else "",
        )

    # ── Status / Inspection ───────────────────────────────────────────

    def get_call_log(self, limit: int = 50) -> list[dict]:
        """Get recent call log entries."""
        with self._lock:
            return list(reversed(self._call_log[-limit:]))

    def status(self) -> dict:
        """Get router status including registry and manager status."""
        with self._lock:
            total_calls = len(self._call_log)
            success_calls = sum(1 for c in self._call_log if c["success"])

        return {
            "registry": self._registry.summary(),
            "servers": self._manager.status(),
            "routing": {
                "total_calls": total_calls,
                "success_calls": success_calls,
                "failure_calls": total_calls - success_calls,
                "success_rate": (
                    f"{success_calls / total_calls * 100:.1f}%"
                    if total_calls > 0 else "N/A"
                ),
            },
        }
