"""Service Catalog domain tools."""

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import write_gate
from servicenow_mcp.tools._payload import parse_payload_json
from servicenow_mcp.utils import format_response, validate_identifier


TOOL_NAMES: list[str] = [
    "sc_catalogs_list",
    "sc_catalog_get",
    "sc_categories_list",
    "sc_category_get",
    "sc_items_list",
    "sc_item_get",
    "sc_item_variables",
    "sc_order_now",
    "sc_add_to_cart",
    "sc_cart_get",
    "sc_cart_submit",
    "sc_cart_checkout",
]


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    """Register Service Catalog tools with MCP server.

    Args:
        mcp: FastMCP instance for tool registration
        settings: Server configuration settings
        auth_provider: Authentication provider for ServiceNow API
        choices: Optional choice registry for resolving field values
    """
    _ = choices  # Accepted for interface conformance with domain tool convention

    @mcp.tool()
    @tool_handler
    async def sc_catalogs_list(
        limit: int = 20,
        text: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """List service catalogs the user has access to.

        Args:
            limit: Maximum number of catalogs to return (default 20)
            text: Search text to filter catalogs
        """
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_get_catalogs(limit=limit, text=text)
            return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def sc_catalog_get(
        sys_id: str,
        *,
        correlation_id: str,
    ) -> str:
        """Fetch details of a specific service catalog.

        Args:
            sys_id: The sys_id of the catalog
        """
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_get_catalog(sys_id)
            return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def sc_categories_list(
        catalog_sys_id: str,
        limit: int = 20,
        offset: int = 0,
        top_level_only: bool = False,
        *,
        correlation_id: str,
    ) -> str:
        """List categories for a specific service catalog.

        Args:
            catalog_sys_id: The sys_id of the catalog
            limit: Maximum number of categories to return (default 20)
            offset: Number of records to skip for pagination
            top_level_only: If true, return only top-level categories
        """
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_get_catalog_categories(
                catalog_sys_id=catalog_sys_id,
                limit=limit,
                offset=offset,
                top_level_only=top_level_only,
            )
            return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def sc_category_get(
        sys_id: str,
        *,
        correlation_id: str,
    ) -> str:
        """Fetch details of a specific service catalog category.

        Args:
            sys_id: The sys_id of the category
        """
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_get_category(sys_id)
            return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def sc_items_list(
        limit: int = 20,
        offset: int = 0,
        text: str = "",
        catalog: str = "",
        category: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """List catalog items with optional filters.

        Args:
            limit: Maximum number of items to return (default 20)
            offset: Number of records to skip for pagination
            text: Search text to filter items
            catalog: Filter by catalog sys_id
            category: Filter by category sys_id
        """
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_get_items(
                limit=limit,
                offset=offset,
                text=text,
                catalog=catalog,
                category=category,
            )
            return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def sc_item_get(
        sys_id: str,
        *,
        correlation_id: str,
    ) -> str:
        """Fetch details of a specific catalog item.

        Args:
            sys_id: The sys_id of the catalog item
        """
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_get_item(sys_id)
            return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def sc_item_variables(
        sys_id: str,
        *,
        correlation_id: str,
    ) -> str:
        """Fetch variables (form fields) for a specific catalog item.

        Args:
            sys_id: The sys_id of the catalog item
        """
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_get_item_variables(sys_id)
            return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def sc_order_now(
        item_sys_id: str,
        variables: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Order a catalog item immediately, bypassing the cart.

        Args:
            item_sys_id: The sys_id of the catalog item to order
            variables: JSON string of variable name-value pairs (e.g. '{"urgency": "1"}')
        """
        blocked = write_gate("sc_req_item", settings, correlation_id)
        if blocked:
            return blocked
        validate_identifier(item_sys_id)

        parsed_vars: dict | None = None
        if variables:
            parsed = parse_payload_json(
                variables, field_name="variables", correlation_id=correlation_id, validate_keys=False
            )
            if isinstance(parsed, str):
                return parsed
            parsed_vars = parsed

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_order_now(item_sys_id, variables=parsed_vars)
            return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def sc_add_to_cart(
        item_sys_id: str,
        variables: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Add a catalog item to the shopping cart.

        Args:
            item_sys_id: The sys_id of the catalog item to add
            variables: JSON string of variable name-value pairs (e.g. '{"urgency": "1"}')
        """
        blocked = write_gate("sc_cart_item", settings, correlation_id)
        if blocked:
            return blocked
        validate_identifier(item_sys_id)

        parsed_vars: dict | None = None
        if variables:
            parsed = parse_payload_json(
                variables, field_name="variables", correlation_id=correlation_id, validate_keys=False
            )
            if isinstance(parsed, str):
                return parsed
            parsed_vars = parsed

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_add_to_cart(item_sys_id, variables=parsed_vars)
            return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def sc_cart_get(
        *,
        correlation_id: str,
    ) -> str:
        """Retrieve the current user's shopping cart contents."""
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_get_cart()
            return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def sc_cart_submit(
        *,
        correlation_id: str,
    ) -> str:
        """Submit the current shopping cart as an order request."""
        blocked = write_gate("sc_request", settings, correlation_id)
        if blocked:
            return blocked

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_submit_order()
            return format_response(data=result, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def sc_cart_checkout(
        *,
        correlation_id: str,
    ) -> str:
        """Checkout the current shopping cart (two-step ordering)."""
        blocked = write_gate("sc_request", settings, correlation_id)
        if blocked:
            return blocked

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.sc_checkout()
            return format_response(data=result, correlation_id=correlation_id)
