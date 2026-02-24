"""MCP server entry point with stdio and SSE transport."""

import importlib
import json
import logging

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import create_auth
from servicenow_mcp.config import Settings
from servicenow_mcp.packages import get_package, list_packages

logger = logging.getLogger(__name__)

# Map tool group names to their module paths
_TOOL_GROUP_MODULES: dict[str, str] = {
    "introspection": "servicenow_mcp.tools.introspection",
    "relationships": "servicenow_mcp.tools.relationships",
    "metadata": "servicenow_mcp.tools.metadata",
    "changes": "servicenow_mcp.tools.changes",
    "debug": "servicenow_mcp.tools.debug",
    "developer": "servicenow_mcp.tools.developer",
    "investigations": "servicenow_mcp.tools.investigations",
    "documentation": "servicenow_mcp.tools.documentation",
    "utility": "servicenow_mcp.tools.utility",
}


def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server with tools based on the active package."""
    settings = Settings()
    auth_provider = create_auth(settings)

    mcp = FastMCP("servicenow-dev-debug")

    # Store settings and client factory on the server for tools to access
    mcp._sn_settings = settings  # type: ignore[attr-defined]
    mcp._sn_auth = auth_provider  # type: ignore[attr-defined]

    # Always register the list_tool_packages tool
    @mcp.tool()
    def list_tool_packages() -> str:
        """List all available tool packages and their tool groups."""
        return json.dumps(list_packages(), indent=2)

    # Load tools based on active package
    package_name = settings.mcp_tool_package
    tool_groups = get_package(package_name)

    for group_name in tool_groups:
        module_path = _TOOL_GROUP_MODULES.get(group_name)
        if module_path:
            try:
                module = importlib.import_module(module_path)
                if hasattr(module, "register_tools"):
                    module.register_tools(mcp, settings, auth_provider)
                    logger.info(f"Loaded tool group: {group_name}")
            except ImportError as e:
                logger.warning(f"Could not load tool group '{group_name}': {e}")

    return mcp


def main() -> None:
    """Run the MCP server with stdio transport."""
    mcp = create_mcp_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
