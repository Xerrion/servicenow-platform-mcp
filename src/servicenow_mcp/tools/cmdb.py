"""CMDB inspection through the dedicated Instance and Meta APIs."""

from typing import Any, Final

from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access, enforce_query_safety, mask_sensitive_fields
from servicenow_mcp.response import format_response
from servicenow_mcp.validation import validate_identifier, validate_sys_id


_ACTION_REGISTRY: Final[dict[str, dict[str, Any]]] = {
    "query": {
        "description": "List CIs in a class. Returns CI names and sys_ids; count is the page size, not a total.",
        "params": {"class_name": "required", "encoded_query": "optional", "limit": "default 20", "offset": "default 0"},
    },
    "get": {
        "description": "Read a CI's attributes and inbound/outbound relationships.",
        "params": {"class_name": "required", "sys_id": "required"},
    },
    "meta": {"description": "Read CMDB class metadata (requires the ITIL role).", "params": {"class_name": "required"}},
    "describe": {"description": "List action contracts without platform calls.", "params": {}},
}


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the read-only CMDB tool."""
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

    @mcp.tool()
    @tool_handler
    async def cmdb(
        action: str,
        class_name: str | None = None,
        sys_id: str | None = None,
        encoded_query: str | None = None,
        limit: int | None = 20,
        offset: int | None = 0,
    ) -> str:
        """Inspect configuration items, relationships, or CMDB class metadata.

        Omit unused optional arguments; null uses their defaults.

        Args:
            action: 'query', 'get', 'meta', or 'describe'.
            class_name: CMDB table, such as 'cmdb_ci_server'. Required except for describe.
            sys_id: CI sys_id, required for get.
            encoded_query: Optional ServiceNow encoded filter for query.
            limit: Maximum CIs per query page (default 20, capped by MAX_ROW_LIMIT).
            offset: Starting query row (default 0). Advance by limit to fetch the next page.
        """
        action = action.strip().lower()
        if action not in _ACTION_REGISTRY:
            raise ValueError(f"Unknown action {action!r}. Available: {sorted(_ACTION_REGISTRY)}")
        if action == "describe":
            return format_response(data={"actions": _ACTION_REGISTRY})
        if not class_name:
            raise ValueError("'class_name' is required for this action.")
        validate_identifier(class_name)
        check_table_access(class_name)
        limit = 20 if limit is None else limit
        offset = 0 if offset is None else offset
        sys_id = "" if sys_id is None else sys_id
        pagination = None
        if action == "get":
            if not sys_id:
                raise ValueError("'sys_id' is required for action='get'.")
            validate_sys_id(sys_id)
        if action == "query":
            if limit <= 0 or offset < 0:
                raise ValueError("limit must be greater than 0 and offset must be non-negative.")
            limit = enforce_query_safety(class_name, encoded_query or "", limit, settings)["limit"]
            pagination = {"limit": limit, "offset": offset}

        async with client_factory() as client:
            if action == "query":
                result = await client.cmdb_query(class_name, encoded_query, limit=limit, offset=offset)
            elif action == "get":
                result = await client.cmdb_get_instance(class_name, sys_id)
            else:
                result = await client.cmdb_get_meta(class_name)
        return format_response(data=mask_sensitive_fields(result), pagination=pagination)
