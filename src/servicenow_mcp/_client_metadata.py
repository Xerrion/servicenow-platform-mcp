"""Metadata, email, import set, and reporting API operations."""

from typing import Any

from servicenow_mcp._client_transport import ServiceNowRequestClient
from servicenow_mcp.policy import INTERNAL_QUERY_LIMIT
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.validation import validate_identifier


class MetadataApiClient(ServiceNowRequestClient):
    """Implement metadata and reporting operations."""

    def _email_url(self, email_id: str) -> str:
        """Build an Email API URL."""
        return f"{self._settings.servicenow_instance_url}/api/now/v1/email/{email_id}"

    async def get_metadata(self, table: str) -> list[dict[str, Any]]:
        """Fetch dictionary metadata for a table."""
        response = await self._ensure_client().get(
            self._table_url("sys_dictionary"),
            headers=await self._headers(),
            params={
                "sysparm_query": ServiceNowQuery().equals("name", table).build(),
                "sysparm_limit": str(INTERNAL_QUERY_LIMIT),
            },
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def get_email(self, email_id: str, fields: list[str] | None = None) -> dict[str, Any]:
        """Fetch an email record by ID."""
        response = await self._ensure_client().get(
            self._email_url(email_id),
            headers=await self._headers(),
            params={"sysparm_fields": ",".join(fields)} if fields else {},
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    def _import_set_url(self, staging_table: str, sys_id: str) -> str:
        """Build a validated Import Set API URL."""
        validate_identifier(staging_table)
        return f"{self._settings.servicenow_instance_url}/api/now/import/{staging_table}/{sys_id}"

    async def get_import_set_record(self, staging_table: str, sys_id: str) -> dict[str, Any]:
        """Retrieve an import set record from a staging table."""
        response = await self._ensure_client().get(
            self._import_set_url(staging_table, sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    def _table_description_url(self, table: str) -> str:
        """Build a validated Reporting Table Description API URL."""
        validate_identifier(table)
        return f"{self._settings.servicenow_instance_url}/api/now/reporting_table_description/{table}"

    def _field_descriptions_url(self, table: str) -> str:
        """Build a validated Reporting Field Description API URL."""
        validate_identifier(table)
        return f"{self._settings.servicenow_instance_url}/api/now/reporting_table_description/field_description/{table}"

    def _reporting_url(self) -> str:
        """Build the Reporting API URL."""
        return f"{self._settings.servicenow_instance_url}/api/now/reporting"

    async def list_reports(
        self,
        search: str | None = None,
        sort_by: str | None = None,
        sort_dir: str | None = None,
        page: int | None = None,
        per_page: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve reports with optional search, sorting, and pagination."""
        params: dict[str, str] = {}
        if search:
            params["sysparm_contains"] = search
        if sort_by:
            params["sysparm_sortby"] = sort_by
        if sort_dir:
            params["sysparm_sortdir"] = sort_dir
        if page is not None:
            params["sysparm_page"] = str(page)
        if per_page is not None:
            params["sysparm_per_page"] = str(per_page)
        response = await self._ensure_client().get(
            self._reporting_url(),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def get_table_description(self, table: str) -> dict[str, Any]:
        """Get a table description from the Reporting API."""
        response = await self._ensure_client().get(
            self._table_description_url(table),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def get_field_descriptions(self, table: str) -> list[dict[str, Any]]:
        """Get field descriptions for a table."""
        response = await self._ensure_client().get(
            self._field_descriptions_url(table),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())
