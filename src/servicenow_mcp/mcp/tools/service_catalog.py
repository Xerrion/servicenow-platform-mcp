"""Native service_catalog adapter."""

from typing import TYPE_CHECKING

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.catalog.contracts import CatalogReadAction, CatalogView
from servicenow_mcp.capabilities.catalog.service_catalog import ServiceCatalogCapability
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.mcp.inputs import describe_tool_parameters

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings
    from servicenow_mcp.platform.client import ServiceNowClientProvider


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    capability = ServiceCatalogCapability(
        settings=settings, auth_provider=auth_provider, client_factory=client_factory
    )

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    @describe_tool_parameters(
        {
            "action": "Read action: browse/get catalogs, categories, items, variables, cart, address, delegation, wishlist, or display values.",
            "sys_id": "Catalog/category/item sys_id; required for get, item_variables, and variable_display_value.",
            "catalog_sys_id": "Catalog sys_id for categories_list.",
            "catalog": "Catalog sys_id filter for items_list.",
            "category": "Category sys_id filter for items_list.",
            "text": "Search phrase for catalogs_list or items_list.",
            "limit": "Maximum list rows per page; default is 20.",
            "offset": "Zero-based list offset.",
            "top_level_only": "Return only top-level categories.",
            "view": "Optional desktop, mobile, or both catalog view.",
            "item_type": "ServiceNow item type filter for items_list.",
            "user_sys_id": "User sys_id for delivery_address or delegation.",
            "item_sys_id": "Item sys_id for delegation or delegated-user validation.",
            "cart_item_id": "Cart-item sys_id for wishlist_item_get.",
            "also_request_for": "User sys_id array for delegated-user validation.",
            "value": "Record sys_id for variable_display_value.",
            "api_version": {
                "description": "Optional API version, such as v1.",
                "pattern": r"^v[1-9][0-9]*$",
            },
        }
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
        """Read Service Catalog items, variables, cart, and user-specific details. Use service_catalog_preview to prepare changes."""
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
