"""Async ServiceNow REST API client."""

import uuid
from typing import Any

import httpx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.errors import (
    AuthError,
    ForbiddenError,
    NotFoundError,
    ServerError,
    ServiceNowMCPError,
)
from servicenow_mcp.policy import INTERNAL_QUERY_LIMIT
from servicenow_mcp.utils import ServiceNowQuery, validate_identifier


class ServiceNowClient:
    """Async HTTP client for the ServiceNow REST API."""

    def __init__(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        self._settings = settings
        self._auth_provider = auth_provider
        self._http_client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ServiceNowClient":
        self._http_client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the HTTP client, raising RuntimeError if not initialized."""
        if self._http_client is None:
            raise RuntimeError("Client not initialized. Use 'async with ServiceNowClient(...)' as context manager.")
        return self._http_client

    def _extract_result(self, data: dict[str, Any]) -> Any:
        """Extract 'result' from API response data, raising ServerError if missing."""
        try:
            return data["result"]
        except KeyError:
            raise ServerError("Unexpected API response format: missing 'result' key") from None

    def _table_url(self, table: str, sys_id: str | None = None) -> str:
        """Build the REST API URL for a table resource."""
        validate_identifier(table)
        base = f"{self._settings.servicenow_instance_url}/api/now/table/{table}"
        if sys_id:
            base = f"{base}/{sys_id}"
        return base

    def _stats_url(self, table: str) -> str:
        """Build the stats/aggregate API URL."""
        validate_identifier(table)
        return f"{self._settings.servicenow_instance_url}/api/now/stats/{table}"

    async def _headers(self) -> dict[str, str]:
        """Build request headers including auth and correlation ID."""
        headers = await self._auth_provider.get_headers()
        headers["X-Correlation-ID"] = str(uuid.uuid4())
        return headers

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map HTTP status codes to custom exceptions."""
        if response.status_code == 401:
            msg = self._extract_error_message(response, "Authentication failed")
            raise AuthError(msg)
        if response.status_code == 403:
            msg = self._extract_error_message(response, "Access forbidden")
            raise ForbiddenError(msg)
        if response.status_code == 404:
            msg = self._extract_error_message(response, "Resource not found")
            raise NotFoundError(msg)
        if response.status_code >= 500:
            msg = self._extract_error_message(response, "ServiceNow server error")
            raise ServerError(msg, status_code=response.status_code)
        if response.status_code >= 400:
            msg = self._extract_error_message(response, "Request failed")
            raise ServiceNowMCPError(msg, status_code=response.status_code)

    @staticmethod
    def _extract_error_message(response: httpx.Response, default: str) -> str:
        """Try to extract error message from ServiceNow JSON response."""
        try:
            body = response.json()
            if "error" in body and "message" in body["error"]:
                return body["error"]["message"]
        except Exception:
            pass
        return default

    async def get_record(
        self,
        table: str,
        sys_id: str,
        fields: list[str] | None = None,
        display_values: bool = False,
    ) -> dict[str, Any]:
        """Fetch a single record by sys_id."""
        http = self._ensure_client()
        params: dict[str, str] = {}
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        if display_values:
            params["sysparm_display_value"] = "true"

        response = await http.get(
            self._table_url(table, sys_id),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def query_records(
        self,
        table: str,
        query: str,
        fields: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str | None = None,
        display_values: bool = False,
    ) -> dict[str, Any]:
        """Query records with encoded query string."""
        http = self._ensure_client()
        params: dict[str, str] = {
            "sysparm_query": query,
            "sysparm_limit": str(limit),
            "sysparm_offset": str(offset),
        }
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        if order_by:
            params["sysparm_orderby"] = order_by
        if display_values:
            params["sysparm_display_value"] = "true"

        response = await http.get(
            self._table_url(table),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)

        try:
            total_count = int(response.headers.get("X-Total-Count", "0"))
        except (ValueError, TypeError):
            total_count = 0
        records = self._extract_result(response.json())
        return {"records": records, "count": total_count}

    async def get_metadata(self, table: str) -> list[dict[str, Any]]:
        """Fetch dictionary metadata for a table from sys_dictionary."""
        http = self._ensure_client()
        params = {
            "sysparm_query": ServiceNowQuery().equals("name", table).build(),
            "sysparm_limit": str(INTERNAL_QUERY_LIMIT),
        }

        response = await http.get(
            self._table_url("sys_dictionary"),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def aggregate(
        self,
        table: str,
        query: str,
        group_by: str | None = None,
        avg_fields: list[str] | None = None,
        min_fields: list[str] | None = None,
        max_fields: list[str] | None = None,
        sum_fields: list[str] | None = None,
        order_by: str | None = None,
        having: str | None = None,
        display_value: bool = False,
    ) -> dict[str, Any]:
        """Perform an aggregate query using the Stats API.

        Supports field-specific statistical operations. For example,
        avg_fields=["priority"] sets sysparm_avg_fields=priority.
        """
        http = self._ensure_client()
        params: dict[str, str] = {
            "sysparm_query": query,
            "sysparm_count": "true",
        }
        if group_by:
            params["sysparm_group_by"] = group_by
        if avg_fields:
            params["sysparm_avg_fields"] = ",".join(avg_fields)
        if min_fields:
            params["sysparm_min_fields"] = ",".join(min_fields)
        if max_fields:
            params["sysparm_max_fields"] = ",".join(max_fields)
        if sum_fields:
            params["sysparm_sum_fields"] = ",".join(sum_fields)
        if order_by:
            params["sysparm_orderby"] = order_by
        if having:
            params["sysparm_having"] = having
        if display_value:
            params["sysparm_display_value"] = "true"

        response = await http.get(
            self._stats_url(table),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def create_record(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new record via POST."""
        http = self._ensure_client()
        response = await http.post(
            self._table_url(table),
            headers=await self._headers(),
            json=data,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def update_record(self, table: str, sys_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing record via PATCH."""
        http = self._ensure_client()
        response = await http.patch(
            self._table_url(table, sys_id),
            headers=await self._headers(),
            json=data,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def delete_record(self, table: str, sys_id: str) -> bool:
        """Delete a record via DELETE."""
        http = self._ensure_client()
        response = await http.delete(
            self._table_url(table, sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return True

    # ── Email API ──────────────────────────────────────────────────────

    def _email_url(self, email_id: str) -> str:
        """Build the Email API URL."""
        return f"{self._settings.servicenow_instance_url}/api/now/v1/email/{email_id}"

    async def get_email(
        self,
        email_id: str,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch an email record by ID."""
        http = self._ensure_client()
        params: dict[str, str] = {}
        if fields:
            params["sysparm_fields"] = ",".join(fields)

        response = await http.get(
            self._email_url(email_id),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    # ── Import Set API ─────────────────────────────────────────────────

    def _import_set_url(self, staging_table: str, sys_id: str) -> str:
        """Build the Import Set API URL."""
        validate_identifier(staging_table)
        return f"{self._settings.servicenow_instance_url}/api/now/import/{staging_table}/{sys_id}"

    async def get_import_set_record(
        self,
        staging_table: str,
        sys_id: str,
    ) -> dict[str, Any]:
        """Retrieve an import set record from a staging table."""
        http = self._ensure_client()
        response = await http.get(
            self._import_set_url(staging_table, sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    # ── Reporting APIs ─────────────────────────────────────────────────

    def _reporting_url(self) -> str:
        """Build the Reporting API URL."""
        return f"{self._settings.servicenow_instance_url}/api/now/reporting"

    def _table_description_url(self, table: str) -> str:
        """Build the Reporting Table Description API URL."""
        validate_identifier(table)
        return f"{self._settings.servicenow_instance_url}/api/now/reporting_table_description/{table}"

    def _field_descriptions_url(self, table: str) -> str:
        """Build the Reporting Field Description API URL."""
        validate_identifier(table)
        return f"{self._settings.servicenow_instance_url}/api/now/reporting_table_description/field_description/{table}"

    async def list_reports(
        self,
        search: str | None = None,
        sort_by: str | None = None,
        sort_dir: str | None = None,
        page: int | None = None,
        per_page: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve a list of reports."""
        http = self._ensure_client()
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

        response = await http.get(
            self._reporting_url(),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def get_table_description(self, table: str) -> dict[str, Any]:
        """Get a table's description from the Reporting Table Description API."""
        http = self._ensure_client()
        response = await http.get(
            self._table_description_url(table),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def get_field_descriptions(self, table: str) -> list[dict[str, Any]]:
        """Get field descriptions for a table."""
        http = self._ensure_client()
        response = await http.get(
            self._field_descriptions_url(table),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    # ── Code Search API ────────────────────────────────────────────────

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
    ) -> dict[str, Any]:
        """Search code across ServiceNow script tables."""
        http = self._ensure_client()
        params: dict[str, str] = {"term": term}
        if table:
            params["table"] = table
        if search_group:
            params["search_group"] = search_group
        if limit is not None:
            params["limit"] = str(limit)

        response = await http.get(
            self._code_search_url(),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def code_search_tables(
        self,
        search_group: str | None = None,
    ) -> dict[str, Any]:
        """Get the list of tables that would be searched for a given search group."""
        http = self._ensure_client()
        params: dict[str, str] = {}
        if search_group:
            params["search_group"] = search_group

        response = await http.get(
            self._code_search_tables_url(),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    # ── CMDB APIs ──────────────────────────────────────────────────────

    def _cmdb_instance_url(self, class_name: str, sys_id: str | None = None) -> str:
        """Build the CMDB Instance API URL."""
        validate_identifier(class_name)
        base = f"{self._settings.servicenow_instance_url}/api/now/cmdb/instance/{class_name}"
        if sys_id:
            base = f"{base}/{sys_id}"
        return base

    def _cmdb_meta_url(self, class_name: str) -> str:
        """Build the CMDB Meta API URL."""
        validate_identifier(class_name)
        return f"{self._settings.servicenow_instance_url}/api/now/cmdb/meta/{class_name}"

    async def cmdb_query(
        self,
        class_name: str,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Query CMDB instances for a given class."""
        http = self._ensure_client()
        params: dict[str, str] = {
            "sysparm_limit": str(limit),
            "sysparm_offset": str(offset),
        }
        if query:
            params["sysparm_query"] = query

        response = await http.get(
            self._cmdb_instance_url(class_name),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)

        try:
            total_count = int(response.headers.get("X-Total-Count", "0"))
        except (ValueError, TypeError):
            total_count = 0
        records = self._extract_result(response.json())
        return {"records": records, "count": total_count}

    async def cmdb_get_instance(
        self,
        class_name: str,
        sys_id: str,
    ) -> dict[str, Any]:
        """Get a specific CMDB CI with its relationships."""
        http = self._ensure_client()
        response = await http.get(
            self._cmdb_instance_url(class_name, sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def cmdb_get_meta(self, class_name: str) -> dict[str, Any]:
        """Get metadata for a CMDB class."""
        http = self._ensure_client()
        response = await http.get(
            self._cmdb_meta_url(class_name),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    # ── Encoded Query Translator ───────────────────────────────────────

    def _encoded_query_url(self) -> str:
        """Build the Encoded Query Translator API URL."""
        return f"{self._settings.servicenow_instance_url}/api/now/cmdb_workspace_api/encodedquery"

    async def translate_encoded_query(
        self,
        table: str,
        query: str,
    ) -> dict[str, Any]:
        """Translate an encoded query to a human-readable display name."""
        http = self._ensure_client()
        params: dict[str, str] = {
            "table": table,
            "query": query,
        }

        response = await http.get(
            self._encoded_query_url(),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    # ── ATF Cloud Runner API ───────────────────────────────────────────

    def _atf_cloud_runner_url(self, endpoint: str) -> str:
        """Build the ATF Cloud Runner API URL."""
        return f"{self._settings.servicenow_instance_url}/api/now/sn_atf_tg/{endpoint}"

    async def atf_run(self, test_or_suite_id: str, is_suite: bool = False) -> dict[str, Any]:
        """Run an ATF test or suite via Cloud Runner.

        Args:
            test_or_suite_id: The sys_id of the test or suite to run.
            is_suite: If True, treat test_or_suite_id as a suite ID; otherwise as a test ID.

        Returns:
            Response dict with at minimum a "snboqId" for tracking the execution.
        """
        http = self._ensure_client()
        data = {"suiteId" if is_suite else "testId": test_or_suite_id}

        try:
            response = await http.post(
                self._atf_cloud_runner_url("test_runner"),
                headers=await self._headers(),
                json=data,
            )
            self._raise_for_status(response)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise NotFoundError("ATF Cloud Runner plugin (sn_atf_tg) may not be installed") from None
            raise

        response_data = response.json()
        try:
            return self._extract_result(response_data)
        except KeyError:
            return response_data

    async def atf_progress(self, snboq_id: str) -> dict[str, Any]:
        """Get progress of an ATF Cloud Runner execution.

        Args:
            snboq_id: The execution ID returned by atf_run().

        Returns:
            Progress dict with fields like "progress" and "state".
        """
        http = self._ensure_client()
        params: dict[str, str] = {"snboqId": snboq_id}

        try:
            response = await http.get(
                self._atf_cloud_runner_url("test_runner_progress"),
                headers=await self._headers(),
                params=params,
            )
            self._raise_for_status(response)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise NotFoundError("ATF Cloud Runner plugin (sn_atf_tg) may not be installed") from None
            raise

        response_data = response.json()
        try:
            return self._extract_result(response_data)
        except KeyError:
            return response_data

    async def atf_cancel(self, snboq_id: str) -> dict[str, Any]:
        """Cancel an ATF Cloud Runner execution.

        Args:
            snboq_id: The execution ID returned by atf_run().

        Returns:
            Result dict confirming cancellation.
        """
        http = self._ensure_client()
        data = {"snboqId": snboq_id}

        try:
            response = await http.post(
                self._atf_cloud_runner_url("cancel_test_runner"),
                headers=await self._headers(),
                json=data,
            )
            self._raise_for_status(response)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise NotFoundError("ATF Cloud Runner plugin (sn_atf_tg) may not be installed") from None
            raise

        response_data = response.json()
        try:
            return self._extract_result(response_data)
        except KeyError:
            return response_data
