"""ServiceNow Code Search API operations."""

from typing import Any

from servicenow_mcp._client_transport import ServiceNowRequestClient


class CodeSearchApiClient(ServiceNowRequestClient):
    """Implement code search operations."""

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
