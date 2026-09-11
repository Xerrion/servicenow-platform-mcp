"""Execute the single-record, aggregate, and list unified-query modes."""

from servicenow_mcp.client import ServiceNowClientProvider
from servicenow_mcp.policy import mask_record
from servicenow_mcp.response import format_response
from servicenow_mcp.tools._query_parsing import Projection
from servicenow_mcp.tools._query_preparation import AggregateRequest, ListRequest


def _project_record(record: dict[str, object], fields: list[str] | None) -> dict[str, object]:
    if fields is None:
        return record
    return {name: record[name] for name in fields if name in record}


async def run_single_record(
    table: str,
    sys_id: str,
    projection: Projection,
    display_values: bool,
    client_factory: ServiceNowClientProvider,
) -> str:
    """Fetch, mask, and project one record."""
    async with client_factory() as client:
        record = await client.get_record(table, sys_id, fields=projection.fields, display_values=display_values)

    masked = mask_record(table, record)
    selection = projection.selection
    if projection.fields is None:
        selection = selection | {"returned_fields": list(masked)}
    return format_response(data=_project_record(masked, projection.fields), selection=selection)


async def run_aggregate(
    table: str,
    encoded_query: str,
    request: AggregateRequest,
    client_factory: ServiceNowClientProvider,
    warnings: list[str],
) -> str:
    """Execute a prepared Stats API request."""
    async with client_factory() as client:
        result = await client.aggregate(
            table,
            encoded_query,
            group_by=",".join(request.group_fields) or None,
            avg_fields=request.plan.avg_fields or None,
            sum_fields=request.plan.sum_fields or None,
            min_fields=request.plan.min_fields or None,
            max_fields=request.plan.max_fields or None,
        )
    return format_response(data=result, warnings=warnings or None)


async def run_list(
    table: str,
    encoded_query: str,
    request: ListRequest,
    offset: int,
    display_values: bool,
    client_factory: ServiceNowClientProvider,
    warnings: list[str],
) -> str:
    """Execute a prepared record-list request, then mask and project rows."""
    fields = request.projection.fields
    async with client_factory() as client:
        result = await client.query_records(
            table,
            encoded_query,
            fields=fields,
            limit=request.limit,
            offset=offset,
            order_by=request.order_by or None,
            display_values=display_values,
        )

    masked = [_project_record(mask_record(table, record), fields) for record in result["records"]]
    selection = request.projection.selection
    if fields is None:
        selection = selection | {"returned_fields": sorted({name for record in masked for name in record})}
    return format_response(
        data=masked,
        pagination={"offset": offset, "limit": request.limit, "total": result["count"]},
        warnings=warnings or None,
        selection=selection,
    )
