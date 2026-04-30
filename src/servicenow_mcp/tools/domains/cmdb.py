"""CMDB domain tools for ServiceNow MCP server."""

import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.utils import ServiceNowQuery, format_response


_SYS_ID_RE: re.Pattern[str] = re.compile(r"^[a-f0-9]{32}$")


def _is_sys_id(value: str) -> bool:
    """Check if a string looks like a ServiceNow sys_id (32-char hex)."""
    return bool(_SYS_ID_RE.match(value.lower()))


TOOL_NAMES: list[str] = [
    "cmdb_list",
    "cmdb_get",
    "cmdb_relationships",
    "cmdb_classes",
    "cmdb_health",
]


# ------------------------------------------------------------------
# Module-scope helpers (extracted from register_tools closure)
# ------------------------------------------------------------------


async def _resolve_ci_sys_id(
    client: ServiceNowClient,
    name_or_sys_id: str,
    ci_class: str,
    correlation_id: str,
) -> tuple[str, str | None]:
    """Resolve a CI name to its sys_id. Returns (sys_id, error_or_None).

    If the input is already a sys_id (32-char hex), returns it directly.
    Otherwise performs a name lookup against the given ci_class.
    """
    if _is_sys_id(name_or_sys_id):
        return name_or_sys_id, None

    lookup_result = await client.query_records(
        table=ci_class,
        query=ServiceNowQuery().equals("name", name_or_sys_id).build(),
        limit=1,
    )
    if not lookup_result["records"]:
        return "", format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"CI '{name_or_sys_id}' not found in {ci_class}.",
        )
    return lookup_result["records"][0]["sys_id"], None


def _build_relationship_query(ci_sys_id: str, direction: str) -> str | None:
    """Build the encoded query string for CMDB relationship lookup.

    Returns None if the direction is invalid.
    """
    if direction == "parent":
        return ServiceNowQuery().equals("child.sys_id", ci_sys_id).build()
    if direction == "child":
        return ServiceNowQuery().equals("parent.sys_id", ci_sys_id).build()
    if direction == "both":
        return ServiceNowQuery().equals("child.sys_id", ci_sys_id).or_equals("parent.sys_id", ci_sys_id).build()
    return None


# ------------------------------------------------------------------
# Tool registration
# ------------------------------------------------------------------


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    """Register CMDB domain tools.

    Args:
        mcp: FastMCP instance for tool registration
        settings: Server configuration settings
        auth_provider: Authentication provider for ServiceNow API
        choices: Optional choice registry for resolving field values
    """

    @mcp.tool()
    @tool_handler
    async def cmdb_list(
        ci_class: str = "cmdb_ci",
        operational_status: str = "",
        fields: str = "name,sys_class_name,operational_status,sys_id,sys_updated_on",
        limit: int = 20,
        *,
        correlation_id: str,
    ) -> str:
        """List Configuration Items from CMDB.

        Preferred over `table_query` for `cmdb_ci` and its subclasses - resolves operational_status labels, applies sensitivity masking, and uses CMDB-relevant default fields.

        Args:
            ci_class: CMDB table/class to query (default "cmdb_ci")
            operational_status: Filter by operational status (operational=1, non_operational=2, etc.)
            fields: Comma-separated list of fields to return (empty for all)
            limit: Maximum results to return (default 20)
        """
        check_table_access(ci_class)

        q = ServiceNowQuery()
        if operational_status:
            status_value = (
                await choices.resolve("cmdb_ci", "operational_status", operational_status.lower())
                if choices
                else operational_status
            )
            q = q.equals("operational_status", status_value)
        query = q.build()
        field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table=ci_class,
                query=query,
                fields=field_list,
                display_values=True,
                limit=limit,
            )
            masked = [mask_sensitive_fields(r) for r in result["records"]]
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def cmdb_get(
        name_or_sys_id: str,
        ci_class: str = "cmdb_ci",
        *,
        correlation_id: str,
    ) -> str:
        """Fetch a Configuration Item by name or sys_id.

        Preferred over `record_get` / `table_query` when looking up a CI - accepts both CI name and sys_id, and resolves the correct CMDB subclass.

        Args:
            name_or_sys_id: CI name or sys_id (32-char hex string)
            ci_class: CMDB table/class to query (default "cmdb_ci")
        """
        check_table_access(ci_class)

        # Detect if input is sys_id (32-char hex string)
        is_sys_id = _is_sys_id(name_or_sys_id)

        if is_sys_id:
            query = ServiceNowQuery().equals("sys_id", name_or_sys_id).build()
        else:
            query = ServiceNowQuery().equals("name", name_or_sys_id).build()

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table=ci_class,
                query=query,
                display_values=True,
                limit=1,
            )
            if not result["records"]:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"CI '{name_or_sys_id}' not found in {ci_class}.",
                )
            masked = mask_sensitive_fields(result["records"][0])
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def cmdb_relationships(
        name_or_sys_id: str,
        direction: str = "both",
        ci_class: str = "cmdb_ci",
        *,
        correlation_id: str,
    ) -> str:
        """Fetch CMDB relationships for a Configuration Item.

        Preferred over `table_query` on `cmdb_rel_ci` - decodes the parent/child reference graph for a CI in one call instead of requiring two reference walks.

        Args:
            name_or_sys_id: CI name or sys_id (32-char hex string)
            direction: Relationship direction - "parent", "child", or "both" (default "both")
            ci_class: CMDB table/class to query for name resolution (default "cmdb_ci")
        """
        check_table_access(ci_class)
        check_table_access("cmdb_rel_ci")

        async with ServiceNowClient(settings, auth_provider) as client:
            # Resolve name to sys_id if needed
            ci_sys_id, err = await _resolve_ci_sys_id(client, name_or_sys_id, ci_class, correlation_id)
            if err:
                return err

            # Build relationship query based on direction
            query = _build_relationship_query(ci_sys_id, direction)
            if query is None:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=f"Invalid direction: {direction}. Must be 'parent', 'child', or 'both'.",
                )

            result = await client.query_records(
                table="cmdb_rel_ci",
                query=query,
                display_values=True,
                limit=100,
            )
            masked = [mask_sensitive_fields(r) for r in result["records"]]
            return format_response(data=masked, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def cmdb_classes(
        limit: int = 50,
        *,
        correlation_id: str,
    ) -> str:
        """List unique CI classes in CMDB using aggregate API.

        Args:
            limit: Maximum number of classes to return (default 50)
        """
        check_table_access("cmdb_ci")
        effective_limit = min(limit, settings.max_row_limit)

        async with ServiceNowClient(settings, auth_provider) as client:
            aggregate_result: Any = await client.aggregate(
                table="cmdb_ci",
                query="",
                group_by="sys_class_name",
            )
            if isinstance(aggregate_result, list):
                return format_response(data=aggregate_result[:effective_limit], correlation_id=correlation_id)

            groups = aggregate_result.get("group_by", [])
            truncated = groups[:effective_limit]
            return format_response(data={**aggregate_result, "group_by": truncated}, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def cmdb_health(
        ci_class: str = "cmdb_ci",
        *,
        correlation_id: str,
    ) -> str:
        """Check CMDB health by aggregating operational status.

        Args:
            ci_class: CMDB table/class to check (default "cmdb_ci")
        """
        check_table_access(ci_class)

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.aggregate(
                table=ci_class,
                query="",
                group_by="operational_status",
            )
            return format_response(data=result, correlation_id=correlation_id)
