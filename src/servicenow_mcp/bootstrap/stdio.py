"""Compose the local ServiceNow server with explicit capability dependencies."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from mcp.types import ToolAnnotations

from servicenow_mcp.auth import create_auth
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.mutations.coordinator import MutationCoordinator
from servicenow_mcp.config import Settings
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.mcp.inputs import NativeMCPServer
from servicenow_mcp.mcp.tools import (
    analysis,
    attachment,
    audit,
    cmdb,
    code_search,
    describe,
    flow,
    investigate,
    mutations,
    query,
    record_read,
    resolve_choice,
    service_catalog,
)
from servicenow_mcp.packages import get_package, list_packages
from servicenow_mcp.platform.client import ServiceNowClientFactory
from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry
from servicenow_mcp.platform.metadata.choices import ChoiceRegistry
from servicenow_mcp.sentry import set_sentry_context, setup_sentry
from servicenow_mcp.telemetry import HttpTelemetry, TelemetryAsyncClient


logger = logging.getLogger(__name__)


def create_mcp_server(settings: Settings | None = None) -> NativeMCPServer:
    """Construct capabilities without authorizing or contacting ServiceNow."""
    settings = settings or Settings()
    auth = create_auth(settings)
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
    http = TelemetryAsyncClient(telemetry=telemetry, is_shared_pool=True, timeout=settings.httpx_timeout_seconds)
    clients = ServiceNowClientFactory(settings, auth, http)
    dictionary = DictionaryRegistry(settings, auth, clients, telemetry)
    choices = ChoiceRegistry(settings, auth, clients, telemetry)
    groups = get_package(settings.mcp_tool_package)
    mutation_groups = {"record_write": "records", "attachment_write": "attachments", "service_catalog_write": "catalog"}
    coordinator = MutationCoordinator(
        settings, auth, clients, dictionary, frozenset(mutation_groups[g] for g in groups if g in mutation_groups)
    )

    @asynccontextmanager
    async def lifespan(_server: NativeMCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await coordinator.store.clear()
            if not http.is_closed:
                await http.aclose()

    server = NativeMCPServer("servicenow-platform-mcp", lifespan=lifespan)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
        )
    )
    @tool_handler
    async def list_tool_packages() -> CapabilityResult:
        """List named packages and their tool groups without contacting ServiceNow."""
        return CapabilityResult(data=list_packages())

    registrars = {
        "query": lambda: query.register_tools(server, settings, auth, choices, dictionary, clients),
        "record_read": lambda: record_read.register_tools(server, settings, auth, dictionary, clients),
        "describe": lambda: describe.register_tools(server, settings, auth, dictionary, clients),
        "resolve_choice": lambda: resolve_choice.register_tools(server, choices),
        "attachment": lambda: attachment.register_tools(server, settings, auth, clients),
        "analysis": lambda: analysis.register_tools(server, settings, auth, dictionary, clients),
        "audit": lambda: audit.register_tools(server, settings, auth, dictionary, clients, telemetry),
        "flow": lambda: flow.register_tools(server, settings, auth, clients),
        "investigate": lambda: investigate.register_tools(server, settings, auth, clients),
        "cmdb": lambda: cmdb.register_tools(server, settings, auth, clients),
        "code_search": lambda: code_search.register_tools(server, settings, auth, clients),
        "service_catalog": lambda: service_catalog.register_tools(server, settings, auth, clients),
    }
    for group in groups:
        if group not in mutation_groups:
            registrars[group]()
    if coordinator.families:
        mutations.register_tools(server, coordinator)
    return server
