"""Async ServiceNow REST API client."""

import logging
import re
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.errors import (
    ACLError,
    AuthError,
    ForbiddenError,
    NotFoundError,
    ServerError,
    ServiceNowMCPError,
)
from servicenow_mcp.policy import INTERNAL_QUERY_LIMIT
from servicenow_mcp.sentry import set_sentry_context
from servicenow_mcp.utils import ServiceNowQuery, validate_identifier, validate_sys_id


logger = logging.getLogger(__name__)


_ATF_PLUGIN_ERROR = "ATF Cloud Runner plugin (sn_atf_tg) may not be installed"

# Word-boundary matching so unrelated words containing the substring "acl"
# (e.g. "oracle", "miracle", "barnacle") do not false-positive.
_ACL_INDICATOR_RE: re.Pattern[str] = re.compile(r"\b(?:acl|access control)\b")


class ServiceNowClient:
    """Async HTTP client for the ServiceNow REST API."""

    _settings: Settings
    _auth_provider: BasicAuthProvider

    def __init__(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        self._settings = settings
        self._auth_provider = auth_provider
        self._http_client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ServiceNowClient":
        self._http_client = httpx.AsyncClient(timeout=self._settings.httpx_timeout_seconds)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    # ── Client helpers ─────────────────────────────────────────────────

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

    def _attachment_url(self, sys_id: str | None = None) -> str:
        """Build the Attachment API URL."""
        base = f"{self._settings.servicenow_instance_url}/api/now/attachment"
        if sys_id is None:
            return base
        validate_sys_id(sys_id)
        return f"{base}/{sys_id}"

    def _attachment_file_url(self, sys_id: str | None = None) -> str:
        """Build the Attachment content/upload URL."""
        if sys_id is None:
            return f"{self._attachment_url()}/file"
        return f"{self._attachment_url(sys_id)}/file"

    def _attachment_file_by_name_url(self, table_sys_id: str, file_name: str) -> str:
        """Build the Attachment by-name download URL."""
        validate_sys_id(table_sys_id)
        return f"{self._attachment_url()}/{table_sys_id}/{quote(file_name, safe='')}/file"

    async def _headers(self) -> dict[str, str]:
        """Build request headers including auth and correlation ID."""
        headers = await self._auth_provider.get_headers()
        headers["X-Correlation-ID"] = str(uuid.uuid4())
        return headers

    @staticmethod
    def _parse_total_count(response: httpx.Response) -> int:
        """Parse X-Total-Count header, defaulting to 0 when absent or invalid."""
        try:
            return int(response.headers.get("X-Total-Count", "0"))
        except (TypeError, ValueError):
            return 0

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map HTTP status codes to custom exceptions."""
        if response.status_code < 400:
            return

        url = str(response.request.url)
        # Strip query parameters for privacy
        if "?" in url:
            url = url.split("?", 1)[0]

        set_sentry_context(
            "http",
            {
                "status_code": response.status_code,
                "method": response.request.method,
                "url": url,
            },
        )

        if response.status_code == 401:
            msg = self._extract_error_message(response, "Authentication failed")
            raise AuthError(msg)
        if response.status_code == 403:
            msg = self._extract_error_message(response, "Access forbidden")
            if self._is_acl_error_response(response):
                raise ACLError(msg)
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
    def _is_acl_error_response(response: httpx.Response) -> bool:
        """Return True when a ServiceNow 403 response explicitly reports an ACL denial."""
        try:
            payload = response.json()
        except Exception:
            logger.debug("Could not parse ServiceNow error body for ACL detection", exc_info=True)
            return False

        values: list[str] = []

        def collect_strings(value: Any) -> None:
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, dict):
                for nested in value.values():
                    collect_strings(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_strings(nested)

        collect_strings(payload)
        normalized = "\n".join(values).lower()
        return bool(_ACL_INDICATOR_RE.search(normalized))

    @staticmethod
    def _extract_error_message(response: httpx.Response, default: str) -> str:
        """Try to extract error message from ServiceNow JSON response."""
        try:
            body = response.json()
            if "error" in body and "message" in body["error"]:
                return body["error"]["message"]
        except Exception:
            logger.debug("Could not parse ServiceNow error body", exc_info=True)
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
        records = self._extract_result(response.json())
        return {"records": records, "count": self._parse_total_count(response)}

    async def list_attachments(
        self,
        query: str = "",
        limit: int = 100,
        offset: int = 0,
        order_by: str | None = None,
    ) -> dict[str, Any]:
        """List attachment metadata using the Attachment API."""
        http = self._ensure_client()
        params: dict[str, str] = {
            "sysparm_limit": str(limit),
        }
        effective_query = query
        if order_by:
            order_clause = ServiceNowQuery().order_by(order_by).build()
            effective_query = f"{query}^{order_clause}" if query else order_clause
        if effective_query:
            params["sysparm_query"] = effective_query
        if offset:
            params["sysparm_offset"] = str(offset)

        response = await http.get(
            self._attachment_url(),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        records = self._extract_result(response.json())
        return {"records": records, "count": self._parse_total_count(response)}

    async def get_attachment(self, sys_id: str) -> dict[str, Any]:
        """Fetch a single attachment metadata record."""
        http = self._ensure_client()
        response = await http.get(
            self._attachment_url(sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def upload_attachment(
        self,
        table_name: str,
        table_sys_id: str,
        file_name: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        encryption_context: str | None = None,
        creation_time: str | None = None,
    ) -> dict[str, Any]:
        """Upload binary content as an attachment."""
        http = self._ensure_client()
        validate_identifier(table_name)
        validate_sys_id(table_sys_id)
        params: dict[str, str] = {
            "table_name": table_name,
            "table_sys_id": table_sys_id,
            "file_name": file_name,
        }
        if encryption_context:
            params["encryption_context"] = encryption_context
        if creation_time:
            params["creation_time"] = creation_time

        headers = await self._headers()
        headers["Content-Type"] = content_type
        response = await http.post(
            self._attachment_file_url(),
            headers=headers,
            params=params,
            content=content,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def download_attachment(self, sys_id: str) -> bytes:
        """Download attachment content by attachment sys_id."""
        http = self._ensure_client()
        response = await http.get(
            self._attachment_file_url(sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return response.content

    async def download_attachment_by_name(self, table_sys_id: str, file_name: str) -> bytes:
        """Download attachment content by record sys_id and file name."""
        http = self._ensure_client()
        response = await http.get(
            self._attachment_file_by_name_url(table_sys_id, file_name),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return response.content

    async def delete_attachment(self, sys_id: str) -> bool:
        """Delete an attachment by sys_id."""
        http = self._ensure_client()
        response = await http.delete(
            self._attachment_url(sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return True

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
        records = self._extract_result(response.json())
        return {"records": records, "count": self._parse_total_count(response)}

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

    # ── Service Catalog API ──────────────────────────────────────────────

    def _sc_url(self, *segments: str) -> str:
        """Build the Service Catalog REST API URL.

        Examples:
            _sc_url("catalogs")            -> .../api/sn_sc/servicecatalog/catalogs
            _sc_url("items", sys_id)       -> .../api/sn_sc/servicecatalog/items/{sys_id}
        """
        path = "/".join(segments)
        return f"{self._settings.servicenow_instance_url}/api/sn_sc/servicecatalog/{path}"

    async def sc_get_catalogs(
        self,
        limit: int | None = None,
        text: str = "",
    ) -> Any:
        """Retrieve list of catalogs the user has access to."""
        http = self._ensure_client()
        params: dict[str, str] = {"sysparm_text": text}
        if limit is not None:
            params["sysparm_limit"] = str(limit)

        response = await http.get(
            self._sc_url("catalogs"),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def sc_get_catalog(self, sys_id: str) -> Any:
        """Retrieve details of a specific catalog."""
        http = self._ensure_client()
        response = await http.get(
            self._sc_url("catalogs", sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def sc_get_catalog_categories(
        self,
        catalog_sys_id: str,
        limit: int | None = None,
        offset: int | None = None,
        top_level_only: bool = False,
    ) -> Any:
        """Retrieve categories for a specific catalog."""
        http = self._ensure_client()
        params: dict[str, str] = {}
        if limit is not None:
            params["sysparm_limit"] = str(limit)
        if offset is not None:
            params["sysparm_offset"] = str(offset)
        if top_level_only:
            params["sysparm_top_level_only"] = "true"

        response = await http.get(
            self._sc_url("catalogs", catalog_sys_id, "categories"),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def sc_get_category(self, sys_id: str) -> Any:
        """Retrieve details of a specific category."""
        http = self._ensure_client()
        response = await http.get(
            self._sc_url("categories", sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def sc_get_items(
        self,
        limit: int | None = None,
        offset: int | None = None,
        text: str = "",
        catalog: str = "",
        category: str = "",
    ) -> Any:
        """Retrieve list of catalog items."""
        http = self._ensure_client()
        params: dict[str, str] = {"sysparm_text": text}
        if limit is not None:
            params["sysparm_limit"] = str(limit)
        if offset is not None:
            params["sysparm_offset"] = str(offset)
        if catalog:
            params["sysparm_catalog"] = catalog
        if category:
            params["sysparm_category"] = category

        response = await http.get(
            self._sc_url("items"),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def sc_get_item(self, sys_id: str) -> Any:
        """Retrieve details of a specific catalog item."""
        http = self._ensure_client()
        response = await http.get(
            self._sc_url("items", sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def sc_get_item_variables(self, sys_id: str) -> Any:
        """Retrieve variables for a specific catalog item."""
        http = self._ensure_client()
        response = await http.get(
            self._sc_url("items", sys_id, "variables"),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def sc_order_now(self, item_sys_id: str, variables: dict[str, Any] | None = None) -> Any:
        """Order a catalog item immediately (skip cart).

        Args:
            item_sys_id: The sys_id of the catalog item.
            variables: Variable name-value pairs for the item.
        """
        http = self._ensure_client()
        body: dict[str, Any] = {}
        if variables:
            body["sysparm_quantity"] = "1"
            body["variables"] = variables

        response = await http.post(
            self._sc_url("items", item_sys_id, "order_now"),
            headers=await self._headers(),
            json=body,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def sc_add_to_cart(self, item_sys_id: str, variables: dict[str, Any] | None = None) -> Any:
        """Add a catalog item to the cart.

        Args:
            item_sys_id: The sys_id of the catalog item.
            variables: Variable name-value pairs for the item.
        """
        http = self._ensure_client()
        body: dict[str, Any] = {}
        if variables:
            body["sysparm_quantity"] = "1"
            body["variables"] = variables

        response = await http.post(
            self._sc_url("items", item_sys_id, "add_to_cart"),
            headers=await self._headers(),
            json=body,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def sc_get_cart(self) -> Any:
        """Retrieve the current user's cart."""
        http = self._ensure_client()
        response = await http.get(
            self._sc_url("cart"),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def sc_submit_order(self) -> Any:
        """Submit the current cart as an order."""
        http = self._ensure_client()
        response = await http.post(
            self._sc_url("cart", "submit_order"),
            headers=await self._headers(),
            json={},
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def sc_checkout(self) -> Any:
        """Checkout the current cart (two-step ordering)."""
        http = self._ensure_client()
        response = await http.post(
            self._sc_url("cart", "checkout"),
            headers=await self._headers(),
            json={},
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
        except NotFoundError:
            raise NotFoundError(_ATF_PLUGIN_ERROR) from None

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
        except NotFoundError:
            raise NotFoundError(_ATF_PLUGIN_ERROR) from None

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
        except NotFoundError:
            raise NotFoundError(_ATF_PLUGIN_ERROR) from None

        response_data = response.json()
        try:
            return self._extract_result(response_data)
        except KeyError:
            return response_data
