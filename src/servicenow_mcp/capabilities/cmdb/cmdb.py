"""CMDB inspection through the dedicated Instance and Meta APIs."""

from typing import Any, Final

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.contracts import result as operation_result
from servicenow_mcp.config import Settings
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.policy import check_table_access, enforce_query_safety, mask_sensitive_fields
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


class CmdbCapability:
    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        client_factory: ServiceNowClientProvider | None = None,
    ) -> None:
        """Bind the operation's platform and metadata dependencies."""
        client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

        self.auth_provider = auth_provider
        self.client_factory = client_factory
        self.settings = settings

    async def cmdb(
        self,
        action: str,
        class_name: str | None = None,
        sys_id: str | None = None,
        encoded_query: str | None = None,
        limit: int | None = 20,
        offset: int | None = 0,
    ) -> CapabilityResult:
        """Execute the cmdb capability with native typed inputs."""
        action = action.strip().lower()
        if action not in _ACTION_REGISTRY:
            raise ValueError(f"Unknown action {action!r}. Available: {sorted(_ACTION_REGISTRY)}")
        if action == "describe":
            return operation_result(data={"actions": _ACTION_REGISTRY})
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
            limit = enforce_query_safety(class_name, encoded_query or "", limit, self.settings)["limit"]
            pagination = {"limit": limit, "offset": offset}

        async with self.client_factory() as client:
            if action == "query":
                result = await client.cmdb_query(class_name, encoded_query, limit=limit, offset=offset)
            elif action == "get":
                result = await client.cmdb_get_instance(class_name, sys_id)
            else:
                result = await client.cmdb_get_meta(class_name)
        return operation_result(data=mask_sensitive_fields(result), pagination=pagination)
