"""Register the unified ServiceNow record-query tool."""

from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools._query_modes import run_aggregate, run_list, run_single_record
from servicenow_mcp.tools._query_parsing import Projection
from servicenow_mcp.tools._query_preparation import (
    check_mode_conflicts,
    prepare_aggregate_request,
    prepare_list_projection,
    prepare_list_request,
    prepare_single_record,
    prepare_table,
    validate_query_fields,
)
from servicenow_mcp.tools._query_preparation import (
    resolve_labels as prepare_resolved_labels,
)


TOOL_NAMES: list[str] = ["query"]


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``query`` tool on the MCP server."""
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

    @mcp.tool()
    @tool_handler
    async def query(
        table: str,
        sys_id: str | None = None,
        encoded_query: str | None = None,
        fields: str | None = None,
        limit: int = 20,
        offset: int = 0,
        order_by: str | None = None,
        display_values: bool = False,
        aggregate: str | None = None,
        group_by: str | None = None,
        resolve_labels: str | None = None,
    ) -> str:
        """Read records, aggregates, or a single record from any ServiceNow table.

        Args:
            table: ServiceNow table name (e.g. 'incident').
            sys_id: When set, fetch a single record by sys_id (other filter args ignored
                except `fields` and `display_values`).
            encoded_query: ServiceNow encoded query string (e.g. 'state=1^priority=2').
                Omit or pass null for no filter; empty strings are also accepted.
            fields: Comma-separated field projection. List mode requires this argument.
                ``'*'`` explicitly requests all masked fields. Exact sys_id mode defaults
                to the compact ``sys_id,sys_updated_on`` projection.
            limit: Max rows (1-max_row_limit). Default 20.
            offset: Pagination offset.
            order_by: Field name; prefix with '-' for descending (e.g. '-sys_created_on').
            display_values: True returns display_value form for reference and choice fields.
            aggregate: Comma-separated aggregations: 'count', 'avg:<field>', 'sum:<field>',
                'min:<field>', 'max:<field>'. When set, returns aggregate result instead of rows.
            group_by: Comma-separated fields to group by, e.g. state,active (aggregate mode only).
            resolve_labels: Comma-separated 'field=label' pairs (e.g. 'state=open,priority=high').
                Each label is resolved via ChoiceRegistry to its underlying value, then ANDed
                into encoded_query as 'field=value'.
        """
        conflict = check_mode_conflicts(sys_id, aggregate, group_by)
        if conflict:
            return conflict
        prepare_table(table)

        if sys_id:
            projection = prepare_single_record(sys_id, fields or "")
            if isinstance(projection, str):
                return projection
            return await run_single_record(table, sys_id, projection, display_values, client_factory)

        list_projection = None
        if not aggregate:
            list_projection = prepare_list_projection(fields or "")
            if isinstance(list_projection, str):
                return list_projection

        warnings: list[str] = []
        prepared_query = encoded_query or ""
        if resolve_labels:
            resolved = await prepare_resolved_labels(table, prepared_query, resolve_labels, choices)
            if isinstance(resolved, str):
                return resolved
            prepared_query, label_warnings = resolved
            warnings.extend(label_warnings)

        if dictionary is not None:
            warnings.extend(await validate_query_fields(table, prepared_query, dictionary))

        if aggregate:
            aggregate_request = prepare_aggregate_request(table, prepared_query, aggregate, group_by or "", settings)
            if isinstance(aggregate_request, str):
                return aggregate_request
            return await run_aggregate(table, prepared_query, aggregate_request, client_factory, warnings)

        assert isinstance(list_projection, Projection)
        list_request = prepare_list_request(
            table,
            prepared_query,
            list_projection,
            limit,
            order_by or "",
            settings,
        )
        return await run_list(
            table,
            prepared_query,
            list_request,
            offset,
            display_values,
            client_factory,
            warnings,
        )
