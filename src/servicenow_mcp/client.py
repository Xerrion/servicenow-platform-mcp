"""Caller-facing facade for ServiceNow REST API families."""

from collections.abc import Callable

import httpx

from servicenow_mcp._client_attachments import AttachmentApiClient
from servicenow_mcp._client_catalog import ServiceCatalogApiClient
from servicenow_mcp._client_cmdb import CmdbApiClient
from servicenow_mcp._client_flow import FlowDesignerApiClient
from servicenow_mcp._client_metadata import MetadataApiClient
from servicenow_mcp._client_table import TableApiClient
from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.config import Settings


class ServiceNowClient(
    TableApiClient,
    AttachmentApiClient,
    MetadataApiClient,
    CmdbApiClient,
    ServiceCatalogApiClient,
    FlowDesignerApiClient,
):
    """Expose ServiceNow API families through one compatible client facade."""


class ServiceNowClientFactory:
    """Create ServiceNow clients with consistent HTTP transport ownership."""

    _settings: Settings
    _auth_provider: OAuthPKCEProvider
    _http_client: httpx.AsyncClient | None

    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._auth_provider = auth_provider
        self._http_client = http_client

    def __call__(self) -> ServiceNowClient:
        """Return a ServiceNow client backed by the configured transport."""
        return ServiceNowClient(self._settings, self._auth_provider, self._http_client)


ServiceNowClientProvider = Callable[[], ServiceNowClient]
