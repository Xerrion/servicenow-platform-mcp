"""Native service_catalog adapter."""

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.catalog.contracts import CatalogReadAction, CatalogView
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
        action: CatalogReadAction,
        sys_id: str | None = None,
        catalog_sys_id: str | None = None,
        catalog: str | None = None,
        category: str | None = None,
        text: str | None = None,
        limit: int | None = 20,
        offset: int | None = 0,
        top_level_only: bool | None = False,
        view: CatalogView | None = None,
        item_type: str | None = None,
        user_sys_id: str | None = None,
        item_sys_id: str | None = None,
        cart_item_id: str | None = None,
        also_request_for: list[str] | None = None,
        value: str | None = None,
        api_version: str | None = None,
    ) -> CapabilityResult:
        """Read catalogs, categories, items, variables, and the current cart without mutation.

        Omit unused arguments. Null uses defaults.

        Args:
            action: catalogs_list, catalog_get, categories_list, category_get, items_list,
                item_get, item_variables, cart_get, delivery_address, delegation, wishlist_get,
                wishlist_item_get, get_invalid_delegated_users, or variable_display_value.
            sys_id: Required for catalog_get, category_get, item_get, item_variables, and variable_display_value.
            catalog_sys_id: Required for categories_list.
            catalog: Catalog sys_id filter for items_list.
            category: Category sys_id filter for items_list.
            text: Search text for catalogs_list or items_list.
            limit: Positive page size, default 20.
            offset: Non-negative row offset, default 0.
            top_level_only: Return top-level categories only, default false.
            view: sysparm_view for catalog, category, item, and variable GET endpoints.
            item_type: sysparm_type for items_list. Passed unchanged to ServiceNow.
            user_sys_id: User for delivery_address or delegation.
            item_sys_id: Item for delegation or get_invalid_delegated_users.
            cart_item_id: Required for wishlist_item_get.
            also_request_for: Non-empty user sys_id array for get_invalid_delegated_users.
            value: Record sys_id sent as sysparm_value for variable_display_value. Requires sys_id.
            api_version: Optional endpoint version, such as v1. Omit for the default URL.

        Delegation validation and display-value lookup use documented read-only POST endpoints.
        Personal address and display-value results are masked. Use service_catalog_preview for mutations."""
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
            view=view,
            item_type=item_type,
            user_sys_id=user_sys_id,
            item_sys_id=item_sys_id,
            cart_item_id=cart_item_id,
            also_request_for=also_request_for,
            value=value,
            api_version=api_version,
        )
