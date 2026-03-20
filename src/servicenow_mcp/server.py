"""MCP server entry point with stdio and SSE transport."""

import importlib
import logging

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import create_auth
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.config import Settings
from servicenow_mcp.mcp_state import attach_servicenow_state
from servicenow_mcp.packages import _TOOL_GROUP_MODULES, get_package, list_packages
from servicenow_mcp.sentry import capture_exception as sentry_capture
from servicenow_mcp.sentry import set_sentry_context, setup_sentry, shutdown_sentry
from servicenow_mcp.state import QueryTokenStore
from servicenow_mcp.utils import serialize


logger = logging.getLogger(__name__)


def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server with tools based on the active package."""
    settings = Settings()
    auth_provider = create_auth(settings)
    setup_sentry(settings)
    set_sentry_context(
        "server",
        {
            "instance_url": settings.servicenow_instance_url.split("/")[2],  # hostname only
            "environment": settings.servicenow_env,
            "is_production": settings.is_production,
            "tool_package": settings.mcp_tool_package,
        },
    )

    mcp = FastMCP("servicenow-dev-debug")

    query_store = QueryTokenStore()
    choices = ChoiceRegistry(settings, auth_provider)
    attach_servicenow_state(mcp, settings, auth_provider, query_store, choices)

    # Always register the list_tool_packages tool
    @mcp.tool()
    def list_tool_packages() -> str:
        """List all available tool packages and their tool groups."""
        return serialize(list_packages())

    # Load tools based on active package
    package_name = settings.mcp_tool_package
    tool_groups = get_package(package_name)

    for group_name in tool_groups:
        module_path = _TOOL_GROUP_MODULES.get(group_name)
        if module_path:
            try:
                module = importlib.import_module(module_path)
                if hasattr(module, "register_tools"):
                    if group_name.startswith("domain_"):
                        module.register_tools(mcp, settings, auth_provider, choices=choices)
                    else:
                        module.register_tools(mcp, settings, auth_provider)
                    logger.info("Loaded tool group: %s", group_name)
            except ImportError as e:
                logger.warning("Could not load tool group '%s': %s", group_name, e)
                sentry_capture(e)

    return mcp


def main() -> None:
    """Run the MCP server with stdio transport."""
    mcp = create_mcp_server()
    try:
        mcp.run(transport="stdio")
    finally:
        shutdown_sentry()


if __name__ == "__main__":
    main()
