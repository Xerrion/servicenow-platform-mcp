"""Read-only Service Catalog operations."""

from typing import Literal

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError, CapabilityResult
from servicenow_mcp.config import Settings
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.policy import mask_sensitive_fields
from servicenow_mcp.validation import validate_sys_id


CatalogReadAction = Literal[
    "catalogs_list",
    "catalog_get",
    "categories_list",
    "category_get",
    "items_list",
    "item_get",
    "item_variables",
    "cart_get",
]


class ServiceCatalogCapability:
    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        client_factory: ServiceNowClientProvider | None = None,
    ) -> None:
        self.clients = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

    async def service_catalog(
        self,
        action: CatalogReadAction,
        sys_id: str | None = None,
        catalog_sys_id: str | None = None,
        catalog: str | None = None,
        category: str | None = None,
        text: str | None = None,
        limit: int | None = 20,
        offset: int | None = 0,
        top_level_only: bool | None = False,
    ) -> CapabilityResult:
        limit = 20 if limit is None else limit
        offset = 0 if offset is None else offset
        if limit <= 0 or offset < 0:
            raise CapabilityError("limit must be positive and offset must be non-negative.")
        if action in {"catalog_get", "category_get", "item_get", "item_variables"}:
            if not sys_id:
                raise CapabilityError("This action requires sys_id.")
            validate_sys_id(sys_id)
        if action == "categories_list":
            if not catalog_sys_id:
                raise CapabilityError("categories_list requires catalog_sys_id.")
            validate_sys_id(catalog_sys_id)
        async with self.clients() as client:
            match action:
                case "catalogs_list":
                    data = await client.sc_get_catalogs(limit=limit, text=text)
                case "catalog_get":
                    data = await client.sc_get_catalog(sys_id or "")
                case "categories_list":
                    data = await client.sc_get_catalog_categories(
                        catalog_sys_id or "", limit, offset, top_level_only or False
                    )
                case "category_get":
                    data = await client.sc_get_category(sys_id or "")
                case "items_list":
                    data = await client.sc_get_items(limit, offset, text, catalog, category)
                case "item_get":
                    data = await client.sc_get_item(sys_id or "")
                case "item_variables":
                    data = await client.sc_get_item_variables(sys_id or "")
                case "cart_get":
                    data = await client.sc_get_cart()
                case _:
                    raise CapabilityError("Unsupported catalog read action.")
        return CapabilityResult(data=mask_sensitive_fields({"value": data})["value"])
