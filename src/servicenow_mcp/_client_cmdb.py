"""Read operations for the CMDB Instance and Meta APIs."""

from typing import Any

import httpx

from servicenow_mcp._client_transport import ServiceNowRequestClient
from servicenow_mcp.errors import ServerError, ServiceNowMCPError
from servicenow_mcp.validation import validate_identifier, validate_sys_id


class CmdbApiClient(ServiceNowRequestClient):
    """Read configuration items, relationships, and class metadata."""

    def _cmdb_object(self, response: httpx.Response) -> dict[str, Any]:
        """Validate a CMDB object, including errors returned inside HTTP 200."""
        self._raise_for_status(response)
        result = self._extract_json_result(response)
        if not isinstance(result, dict):
            raise ServerError("Unexpected CMDB API result: expected an object.")
        if result.get("error"):
            raise ServiceNowMCPError("CMDB API reported an error. Verify the class, CI, and read access.")
        return result

    def _cmdb_instance_url(self, class_name: str, sys_id: str | None = None) -> str:
        validate_identifier(class_name)
        base = f"{self._settings.servicenow_instance_url}/api/now/cmdb/instance/{class_name}"
        if sys_id is not None:
            validate_sys_id(sys_id)
            return f"{base}/{sys_id}"
        return base

    async def cmdb_query(
        self,
        class_name: str,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List CIs in a class with an encoded filter and bounded pagination."""
        params = {"sysparm_limit": str(limit), "sysparm_offset": str(offset)}
        if query:
            params["sysparm_query"] = query
        response = await self._ensure_client().get(
            self._cmdb_instance_url(class_name), headers=await self._headers(), params=params
        )
        self._raise_for_status(response)
        records = self._extract_json_result(response)
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise ServerError("Unexpected CMDB query result: expected a list of records.")
        # The CMDB API does not promise X-Total-Count. Do not invent a total.
        return {"records": records, "count": len(records)}

    async def cmdb_get_instance(self, class_name: str, sys_id: str) -> dict[str, Any]:
        """Read a CI's attributes and inbound/outbound relationships."""
        response = await self._ensure_client().get(
            self._cmdb_instance_url(class_name, sys_id), headers=await self._headers()
        )
        return self._cmdb_object(response)

    async def cmdb_get_meta(self, class_name: str) -> dict[str, Any]:
        """Read class metadata using the CMDB Meta API."""
        validate_identifier(class_name)
        response = await self._ensure_client().get(
            f"{self._settings.servicenow_instance_url}/api/now/cmdb/meta/{class_name}",
            headers=await self._headers(),
        )
        return self._cmdb_object(response)
