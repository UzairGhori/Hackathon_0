"""
AI Employee — Central Tool Registry

Discovers, catalogs, and provides O(1) lookup for all tools across the
three MCP servers (Meta Social, Odoo Accounting, Communication).

Usage:
    from ai_employee.integrations.tool_registry import ToolRegistry

    registry = ToolRegistry()
    registry.discover_all()
    print(registry.summary())
    tool = registry.get_tool("send_email")
    server = registry.get_server_for_tool("post_facebook")
"""

import logging
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger("ai_employee.tool_registry")


# ══════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ══════════════════════════════════════════════════════════════════════


class ToolCategory(Enum):
    """High-level grouping for MCP tools."""
    SOCIAL = "social"
    ACCOUNTING = "accounting"
    COMMUNICATION = "communication"


class ServerStatus(Enum):
    """Lifecycle state of an MCP server."""
    UNKNOWN = "unknown"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class ToolParameter:
    """Describes a single parameter of an MCP tool."""
    name: str
    type: str
    description: str = ""
    required: bool = True
    default: str | None = None


@dataclass
class ToolEntry:
    """A registered MCP tool with full metadata."""
    name: str
    description: str
    server_name: str
    category: ToolCategory
    parameters: list[ToolParameter] = field(default_factory=list)
    module_path: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "server_name": self.server_name,
            "category": self.category.value,
            "parameters": [
                {"name": p.name, "type": p.type, "description": p.description,
                 "required": p.required, "default": p.default}
                for p in self.parameters
            ],
        }


@dataclass
class ToolCallRequest:
    """A request to call an MCP tool."""
    tool_name: str
    arguments: dict = field(default_factory=dict)
    call_id: str = ""

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "call_id": self.call_id,
        }


@dataclass
class ToolCallResult:
    """The result of an MCP tool call."""
    tool_name: str
    success: bool
    result: str = ""
    error: str = ""
    server_name: str = ""
    call_id: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "server_name": self.server_name,
            "call_id": self.call_id,
            "duration_ms": self.duration_ms,
        }


@dataclass
class ServerInfo:
    """Metadata about a registered MCP server."""
    name: str
    module_path: str
    category: ToolCategory
    status: ServerStatus = ServerStatus.UNKNOWN
    tool_count: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "module_path": self.module_path,
            "category": self.category.value,
            "status": self.status.value,
            "tool_count": self.tool_count,
        }


# ══════════════════════════════════════════════════════════════════════
#  SERVER → CATEGORY MAPPING
# ══════════════════════════════════════════════════════════════════════

_SERVER_CONFIGS: list[dict] = [
    {
        "name": "meta-social",
        "module": "ai_employee.integrations.mcp_meta_server",
        "category": ToolCategory.SOCIAL,
    },
    {
        "name": "odoo-accounting",
        "module": "ai_employee.integrations.odoo_mcp_server",
        "category": ToolCategory.ACCOUNTING,
    },
    {
        "name": "communication",
        "module": "ai_employee.integrations.mcp_communication_server",
        "category": ToolCategory.COMMUNICATION,
    },
]


# ══════════════════════════════════════════════════════════════════════
#  TOOL REGISTRY
# ══════════════════════════════════════════════════════════════════════


class ToolRegistry:
    """
    Central registry that discovers and catalogs all MCP tools.

    Provides O(1) lookup by tool name, filtered listing by category
    or server, and substring search.
    """

    def __init__(self) -> None:
        # tool_name → ToolEntry
        self._tools: dict[str, ToolEntry] = {}
        # server_name → ServerInfo
        self._servers: dict[str, ServerInfo] = {}
        # tool_name → server_name (O(1) routing)
        self._tool_to_server: dict[str, str] = {}

    # ── Discovery ─────────────────────────────────────────────────────

    def discover_all(self) -> int:
        """
        Import all 3 MCP server modules and inspect their FastMCP
        instances to catalog every registered tool.

        Returns:
            Total number of tools discovered.
        """
        total = 0
        for cfg in _SERVER_CONFIGS:
            try:
                count = self._discover_server(
                    name=cfg["name"],
                    module_path=cfg["module"],
                    category=cfg["category"],
                )
                total += count
                log.info(
                    "Discovered %d tools from %s", count, cfg["name"],
                )
            except Exception as exc:
                log.error(
                    "Failed to discover tools from %s: %s",
                    cfg["name"], exc,
                )
                self._servers[cfg["name"]] = ServerInfo(
                    name=cfg["name"],
                    module_path=cfg["module"],
                    category=cfg["category"],
                    status=ServerStatus.ERROR,
                )

        log.info("Tool registry: %d tools across %d servers",
                 total, len(self._servers))
        return total

    def _discover_server(self, name: str, module_path: str,
                         category: ToolCategory) -> int:
        """Import a single MCP server module and extract its tools."""
        import importlib

        mod = importlib.import_module(module_path)
        mcp_instance = getattr(mod, "mcp", None)
        if mcp_instance is None:
            raise ValueError(f"No 'mcp' FastMCP instance in {module_path}")

        # FastMCP stores tools in _tool_manager._tools (dict of name→Tool)
        tool_manager = getattr(mcp_instance, "_tool_manager", None)
        if tool_manager is None:
            raise ValueError(f"No _tool_manager on FastMCP in {module_path}")

        raw_tools = getattr(tool_manager, "_tools", {})
        count = 0

        for tool_name, tool_obj in raw_tools.items():
            params = self._extract_parameters(tool_obj)
            description = getattr(tool_obj, "description", "") or ""

            entry = ToolEntry(
                name=tool_name,
                description=description,
                server_name=name,
                category=category,
                parameters=params,
                module_path=module_path,
            )
            self._tools[tool_name] = entry
            self._tool_to_server[tool_name] = name
            count += 1

        self._servers[name] = ServerInfo(
            name=name,
            module_path=module_path,
            category=category,
            status=ServerStatus.STOPPED,
            tool_count=count,
        )
        return count

    @staticmethod
    def _extract_parameters(tool_obj) -> list[ToolParameter]:
        """Extract parameter metadata from a FastMCP Tool object."""
        params = []
        # FastMCP Tool objects have a .parameters dict or inputSchema
        schema = getattr(tool_obj, "parameters", None)
        if schema is None:
            schema = getattr(tool_obj, "inputSchema", None)

        if isinstance(schema, dict):
            properties = schema.get("properties", {})
            required_set = set(schema.get("required", []))
            for pname, pinfo in properties.items():
                params.append(ToolParameter(
                    name=pname,
                    type=pinfo.get("type", "string"),
                    description=pinfo.get("description", ""),
                    required=pname in required_set,
                    default=str(pinfo.get("default")) if "default" in pinfo else None,
                ))

        return params

    # ── Lookup ────────────────────────────────────────────────────────

    def get_tool(self, name: str) -> ToolEntry | None:
        """O(1) lookup of a tool by exact name."""
        return self._tools.get(name)

    def get_server_for_tool(self, name: str) -> str | None:
        """O(1) lookup: tool name → server name."""
        return self._tool_to_server.get(name)

    def get_server_info(self, server_name: str) -> ServerInfo | None:
        """Get metadata for a registered server."""
        return self._servers.get(server_name)

    # ── Listing ───────────────────────────────────────────────────────

    def list_tools(
        self,
        category: ToolCategory | None = None,
        server: str | None = None,
    ) -> list[ToolEntry]:
        """
        List tools, optionally filtered by category or server name.

        Args:
            category: Filter to a specific ToolCategory.
            server: Filter to tools from a specific server name.

        Returns:
            List of matching ToolEntry objects.
        """
        tools = list(self._tools.values())
        if category is not None:
            tools = [t for t in tools if t.category == category]
        if server is not None:
            tools = [t for t in tools if t.server_name == server]
        return sorted(tools, key=lambda t: t.name)

    def list_servers(self) -> list[ServerInfo]:
        """List all registered servers."""
        return sorted(self._servers.values(), key=lambda s: s.name)

    # ── Search ────────────────────────────────────────────────────────

    def search_tools(self, pattern: str) -> list[ToolEntry]:
        """
        Search tools by substring match on name or description.

        Args:
            pattern: Substring to search for (case-insensitive).

        Returns:
            List of matching ToolEntry objects.
        """
        pattern_lower = pattern.lower()
        return [
            t for t in self._tools.values()
            if pattern_lower in t.name.lower()
            or pattern_lower in t.description.lower()
        ]

    # ── Summary ───────────────────────────────────────────────────────

    def summary(self) -> dict:
        """
        Generate a summary of the registry.

        Returns:
            Dict with total_tools, by_server, by_category counts.
        """
        by_server: dict[str, int] = {}
        by_category: dict[str, int] = {}

        for tool in self._tools.values():
            by_server[tool.server_name] = by_server.get(tool.server_name, 0) + 1
            cat_key = tool.category.value
            by_category[cat_key] = by_category.get(cat_key, 0) + 1

        return {
            "total_tools": len(self._tools),
            "total_servers": len(self._servers),
            "by_server": by_server,
            "by_category": by_category,
            "servers": [s.to_dict() for s in self.list_servers()],
        }

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def server_count(self) -> int:
        return len(self._servers)
