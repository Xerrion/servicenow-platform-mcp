"""CMDB, code search, and encoded-query translation operations."""

from typing import Any

from servicenow_mcp._client_transport import ServiceNowRequestClient
from servicenow_mcp.validation import validate_identifier


class CmdbApiClient(ServiceNowRequestClient):
    """Implement CMDB and code search operations."""

    def _code_search_url(self) -> str:
        """Build the Code Search API URL."""
        return f"{self._settings.servicenow_instance_url}/api/sn_codesearch/code_search/search"

    def _code_search_tables_url(self) -> str:
        """Build the Code Search Tables API URL."""
        return f"{self._settings.servicenow_instance_url}/api/sn_codesearch/code_search/tables"

    async def code_search(
        self,
        term: str,
        table: str | None = None,
        search_group: str | None = None,
        limit: int | None = None,
        *,
        extended_matching: bool | None = None,
    ) -> dict[str, Any]:
        """Search script tables; None keeps the search group's context default."""
        params = {"term": term, "search_group": search_group or "sn_codesearch.Default Search Group"}
        if table:
            params["table"] = table
        if limit is not None:
            params["limit"] = str(limit)
        if extended_matching is not None:
            params["extended_matching"] = str(extended_matching).lower()
        response = await self._ensure_client().get(
            self._code_search_url(),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def code_search_tables(self, search_group: str | None = None) -> dict[str, Any]:
        """Get tables searched by a code search group."""
        response = await self._ensure_client().get(
            self._code_search_tables_url(),
            headers=await self._headers(),
            params={"search_group": search_group or "sn_codesearch.Default Search Group"},
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    def _cmdb_instance_url(self, class_name: str, sys_id: str | None = None) -> str:
        """Build a validated CMDB Instance API URL."""
        validate_identifier(class_name)
        base = f"{self._settings.servicenow_instance_url}/api/now/cmdb/instance/{class_name}"
        return f"{base}/{sys_id}" if sys_id else base

    def _cmdb_meta_url(self, class_name: str) -> str:
        """Build a validated CMDB Meta API URL."""
        validate_identifier(class_name)
        return f"{self._settings.servicenow_instance_url}/api/now/cmdb/meta/{class_name}"

    def _encoded_query_url(self) -> str:
        """Build the Encoded Query Translator API URL."""
        return f"{self._settings.servicenow_instance_url}/api/now/cmdb_workspace_api/encodedquery"

    async def cmdb_query(
        self,
        class_name: str,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Query CMDB instances for a class."""
        params = {"sysparm_limit": str(limit), "sysparm_offset": str(offset)}
        if query:
            params["sysparm_query"] = query
        response = await self._ensure_client().get(
            self._cmdb_instance_url(class_name),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return {
            "records": self._extract_result(response.json()),
            "count": self._parse_total_count(response),
        }

    async def cmdb_get_instance(self, class_name: str, sys_id: str) -> dict[str, Any]:
        """Get a CMDB instance with its relationships."""
        response = await self._ensure_client().get(
            self._cmdb_instance_url(class_name, sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def cmdb_get_meta(self, class_name: str) -> dict[str, Any]:
        """Get metadata for a CMDB class."""
        response = await self._ensure_client().get(self._cmdb_meta_url(class_name), headers=await self._headers())
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def translate_encoded_query(self, table: str, query: str) -> dict[str, Any]:
        """Translate an encoded query to a human-readable display name."""
        response = await self._ensure_client().get(
            self._encoded_query_url(),
            headers=await self._headers(),
            params={"table": table, "query": query},
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())
