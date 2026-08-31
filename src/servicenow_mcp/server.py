"""MCP server entry point with stdio and SSE transport."""

import importlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from mcp.server import MCPServer

from servicenow_mcp.auth import create_auth
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClientFactory
from servicenow_mcp.config import Settings
from servicenow_mcp.mcp_state import attach_servicenow_state
from servicenow_mcp.packages import _TOOL_GROUP_MODULES, get_package, list_packages
from servicenow_mcp.sentry import capture_exception as sentry_capture
from servicenow_mcp.sentry import set_sentry_context, setup_sentry, shutdown_sentry
from servicenow_mcp.telemetry import HttpTelemetry, TelemetryAsyncClient
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.utils import serialize


logger = logging.getLogger(__name__)


def create_mcp_server() -> MCPServer:
    """Create and configure the MCP server with tools based on the active package."""
    settings = Settings()
    auth_provider = create_auth(settings)
    setup_sentry(settings)
    set_sentry_context(
        "server",
        {
            "instance_url": urlparse(settings.servicenow_instance_url).hostname or "unknown",
            "environment": settings.servicenow_env,
            "is_production": settings.is_production,
            "tool_package": settings.mcp_tool_package,
        },
    )

    telemetry = HttpTelemetry()
    http_client = TelemetryAsyncClient(
        telemetry=telemetry,
        is_shared_pool=True,
        timeout=settings.httpx_timeout_seconds,
    )
    client_factory = ServiceNowClientFactory(settings, auth_provider, http_client)

    @asynccontextmanager
    async def lifespan(mcp_server: MCPServer) -> AsyncIterator[None]:
        del mcp_server
        try:
            yield
        finally:
            if not http_client.is_closed:
                await http_client.aclose()

    mcp = MCPServer("servicenow-platform-mcp", lifespan=lifespan)

    choices = ChoiceRegistry(settings, auth_provider, client_factory, telemetry)
    dictionary = DictionaryRegistry(settings, auth_provider, client_factory, telemetry)
    attach_servicenow_state(mcp, settings, auth_provider, choices, dictionary, client_factory, telemetry)

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
                    # All tool modules accept the ChoiceRegistry so unified
                    # tools can resolve display labels, and the
                    # DictionaryRegistry so script-field detection is shared
                    # across the surface. Modules that don't need either
                    # accept ``None`` and ignore it.
                    module.register_tools(
                        mcp,
                        settings,
                        auth_provider,
                        choices=choices,
                        dictionary=dictionary,
                        client_factory=client_factory,
                    )
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
