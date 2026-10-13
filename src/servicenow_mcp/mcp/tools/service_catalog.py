"""Native service_catalog adapter."""

from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.catalog.service_catalog import ServiceCatalogCapability
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.config import Settings
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.platform.client import ServiceNowClientProvider


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    capability = ServiceCatalogCapability(settings=settings, auth_provider=auth_provider, client_factory=client_factory)

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        )
    )
    @tool_handler
    async def service_catalog(
        action: Literal[
            "catalogs_list",
            "catalog_get",
            "categories_list",
            "category_get",
            "items_list",
            "item_get",
            "item_variables",
            "cart_get",
        ],
        sys_id: str | None = None,
        catalog_sys_id: str | None = None,
        catalog: str | None = None,
        category: str | None = None,
        text: str | None = None,
        limit: int | None = 20,
        offset: int | None = 0,
        top_level_only: bool | None = False,
    ) -> CapabilityResult:
        """Read catalogs, categories, items, variables, and the current cart without mutation.

        Omit unused arguments. Null uses defaults.

        Args:
            action: catalogs_list, catalog_get, categories_list, category_get, items_list,
                item_get, item_variables, or cart_get.
            sys_id: Required for catalog_get, category_get, item_get, and item_variables.
            catalog_sys_id: Required for categories_list.
            catalog: Catalog sys_id filter for items_list.
            category: Category sys_id filter for items_list.
            text: Search text for catalogs_list or items_list.
            limit: Positive page size, default 20.
            offset: Non-negative row offset, default 0.
            top_level_only: Return top-level categories only, default false.

        Use service_catalog_preview for catalog and cart mutations."""
        return await capability.service_catalog(
            action=action,
            sys_id=sys_id,
            catalog_sys_id=catalog_sys_id,
            catalog=catalog,
            category=category,
            text=text,
            limit=limit,
            offset=offset,
            top_level_only=top_level_only,
        )
