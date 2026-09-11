"""Table and aggregate API operations."""

from typing import Any

from servicenow_mcp._client_transport import ServiceNowRequestClient
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.validation import validate_identifier


class TableApiClient(ServiceNowRequestClient):
    """Implement Table API CRUD and Stats API aggregation."""

    def _stats_url(self, table: str) -> str:
        """Build the Stats API URL."""
        validate_identifier(table)
        return f"{self._settings.servicenow_instance_url}/api/now/stats/{table}"

    async def get_record(
        self,
        table: str,
        sys_id: str,
        fields: list[str] | None = None,
        display_values: bool = False,
    ) -> dict[str, Any]:
        """Fetch a single record by sys_id."""
        response = await self._ensure_client().get(
            self._table_url(table, sys_id),
            headers=await self._headers(),
            params={
                "sysparm_display_value": str(display_values).lower(),
                **({"sysparm_fields": ",".join(fields)} if fields else {}),
            },
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def query_records(
        self,
        table: str,
        query: str | None = None,
        fields: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str | None = None,
        display_values: bool = False,
    ) -> dict[str, Any]:
        """Query records with an encoded query string."""
        params: dict[str, str] = {
            "sysparm_limit": str(limit),
            "sysparm_offset": str(offset),
            "sysparm_display_value": str(display_values).lower(),
        }
        effective_query = query
        if order_by:
            is_descending = order_by.startswith("-")
            order_field = order_by[1:] if is_descending else order_by
            order_clause = ServiceNowQuery().order_by(order_field, descending=is_descending).build()
            effective_query = f"{query}^{order_clause}" if query else order_clause
        if effective_query:
            params["sysparm_query"] = effective_query
        if fields:
            params["sysparm_fields"] = ",".join(fields)

        response = await self._ensure_client().get(
            self._table_url(table),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return {
            "records": self._extract_result(response.json()),
            "count": self._parse_total_count(response),
        }

    async def aggregate(
        self,
        table: str,
        query: str | None = None,
        group_by: str | None = None,
        avg_fields: list[str] | None = None,
        min_fields: list[str] | None = None,
        max_fields: list[str] | None = None,
        sum_fields: list[str] | None = None,
        order_by: str | None = None,
        having: str | None = None,
        display_value: bool = False,
    ) -> dict[str, Any]:
        """Perform a field-aware aggregate query with the Stats API."""
        params: dict[str, str] = {
            "sysparm_count": "true",
            "sysparm_display_value": str(display_value).lower(),
        }
        optional_params = (
            ("sysparm_query", query),
            ("sysparm_group_by", group_by),
            ("sysparm_avg_fields", ",".join(avg_fields) if avg_fields else None),
            ("sysparm_min_fields", ",".join(min_fields) if min_fields else None),
            ("sysparm_max_fields", ",".join(max_fields) if max_fields else None),
            ("sysparm_sum_fields", ",".join(sum_fields) if sum_fields else None),
            ("sysparm_orderby", order_by),
            ("sysparm_having", having),
        )
        params.update((name, value) for name, value in optional_params if value)
        response = await self._ensure_client().get(
            self._stats_url(table),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def create_record(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create a record with the Table API."""
        response = await self._ensure_client().post(
            self._table_url(table),
            headers=await self._headers(),
            json=data,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def update_record(self, table: str, sys_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update a record with the Table API."""
        response = await self._ensure_client().patch(
            self._table_url(table, sys_id),
            headers=await self._headers(),
            json=data,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def delete_record(self, table: str, sys_id: str) -> bool:
        """Delete a record with the Table API."""
        response = await self._ensure_client().delete(
            self._table_url(table, sys_id),
            headers=await self._headers(),
        )
        self._raise_for_status(response)
        return True
