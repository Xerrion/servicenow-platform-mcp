"""Service Catalog API operations."""

from typing import Any

from servicenow_mcp._client_transport import ServiceNowRequestClient


class ServiceCatalogApiClient(ServiceNowRequestClient):
    """Implement Service Catalog browsing, cart, and order operations."""

    def _sc_url(self, *segments: str) -> str:
        """Build a Service Catalog API URL."""
        return f"{self._settings.servicenow_instance_url}/api/sn_sc/servicecatalog/{'/'.join(segments)}"

    async def sc_get_catalogs(self, limit: int | None = None, text: str | None = None) -> Any:
        """Retrieve catalogs available to the current user."""
        params: dict[str, str] = {}
        if text:
            params["sysparm_text"] = text
        if limit is not None:
            params["sysparm_limit"] = str(limit)
        return await self._sc_get("catalogs", params=params)

    async def sc_get_catalog(self, sys_id: str) -> Any:
        """Retrieve a specific catalog."""
        return await self._sc_get("catalogs", sys_id)

    async def sc_get_catalog_categories(
        self,
        catalog_sys_id: str,
        limit: int | None = None,
        offset: int | None = None,
        top_level_only: bool = False,
    ) -> Any:
        """Retrieve categories for a catalog."""
        params: dict[str, str] = {}
        if limit is not None:
            params["sysparm_limit"] = str(limit)
        if offset is not None:
            params["sysparm_offset"] = str(offset)
        if top_level_only:
            params["sysparm_top_level_only"] = "true"
        return await self._sc_get("catalogs", catalog_sys_id, "categories", params=params)

    async def sc_get_category(self, sys_id: str) -> Any:
        """Retrieve a specific category."""
        return await self._sc_get("categories", sys_id)

    async def sc_get_items(
        self,
        limit: int | None = None,
        offset: int | None = None,
        text: str | None = None,
        catalog: str | None = None,
        category: str | None = None,
    ) -> Any:
        """Retrieve catalog items."""
        params: dict[str, str] = {}
        if text:
            params["sysparm_text"] = text
        if limit is not None:
            params["sysparm_limit"] = str(limit)
        if offset is not None:
            params["sysparm_offset"] = str(offset)
        if catalog:
            params["sysparm_catalog"] = catalog
        if category:
            params["sysparm_category"] = category
        return await self._sc_get("items", params=params)

    async def sc_get_item(self, sys_id: str) -> Any:
        """Retrieve a specific catalog item."""
        return await self._sc_get("items", sys_id)

    async def sc_get_item_variables(self, sys_id: str) -> Any:
        """Retrieve variables for a catalog item."""
        return await self._sc_get("items", sys_id, "variables")

    async def sc_order_now(self, item_sys_id: str, variables: dict[str, Any] | None = None) -> Any:
        """Order a catalog item without using the cart."""
        return await self._sc_post("items", item_sys_id, "order_now", body=self._sc_item_body(variables))

    async def sc_add_to_cart(self, item_sys_id: str, variables: dict[str, Any] | None = None) -> Any:
        """Add a catalog item to the cart."""
        return await self._sc_post("items", item_sys_id, "add_to_cart", body=self._sc_item_body(variables))

    async def sc_get_cart(self) -> Any:
        """Retrieve the current user's cart."""
        return await self._sc_get("cart")

    async def sc_submit_order(self) -> Any:
        """Submit the current cart as an order."""
        return await self._sc_post("cart", "submit_order", body={})

    async def sc_checkout(self) -> Any:
        """Check out the current cart."""
        return await self._sc_post("cart", "checkout", body={})

    @staticmethod
    def _sc_item_body(variables: dict[str, Any] | None) -> dict[str, Any]:
        if not variables:
            return {}
        return {"sysparm_quantity": "1", "variables": variables}

    async def _sc_get(self, *segments: str, params: dict[str, str] | None = None) -> Any:
        response = await self._ensure_client().get(
            self._sc_url(*segments),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def _sc_post(self, *segments: str, body: dict[str, Any]) -> Any:
        response = await self._ensure_client().post(
            self._sc_url(*segments),
            headers=await self._headers(),
            json=body,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())
