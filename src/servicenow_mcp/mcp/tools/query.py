"""Native query adapter."""

from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.records._query_parsing import AggregateOperation, LabelPair
from servicenow_mcp.capabilities.records.query import QueryCapability
from servicenow_mcp.config import Settings
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.platform.client import ServiceNowClientProvider
from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry
from servicenow_mcp.platform.metadata.choices import ChoiceRegistry


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    capability = QueryCapability(
        settings=settings,
        auth_provider=auth_provider,
        choices=choices,
        dictionary=dictionary,
        client_factory=client_factory,
    )

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        )
    )
    @tool_handler
    async def query(
        table: str,
        sys_id: str | None = None,
        encoded_query: str | None = None,
        fields: list[str] | Literal["*"] | None = None,
        limit: int | None = 20,
        offset: int | None = 0,
        order_by: str | None = None,
        display_values: bool | None = False,
        aggregate: list[AggregateOperation] | None = None,
        group_by: list[str] | None = None,
        resolve_labels: list[LabelPair] | None = None,
    ) -> CapabilityResult:
        """Read records, aggregates, or a single record from any ServiceNow table.

        Omit unused optional arguments. Explicit null uses the same defaults.

        Args:
            table: ServiceNow table name (e.g. 'incident').
            sys_id: When set, fetch a single record by sys_id (other filter args ignored
                except `fields` and `display_values`).
            encoded_query: ServiceNow encoded query string (e.g. 'state=1^priority=2').
                Omit or pass null for no filter; empty strings are also accepted.
                Large tables require a recognized date constraint. For sys_audit, prefer
                tablename plus documentkey when investigating one record.
                Supported field paths use inactive-aware inherited metadata and static reference checks.
                Unresolved metadata produces unverified warnings, not proof of invalid fields or ACL access.
            fields: Native array of field projection. List mode requires this argument.
                ``'*'`` explicitly requests all masked fields. Exact sys_id mode defaults
                to the compact ``sys_id,sys_updated_on`` projection.
            limit: Max returned rows (1-max_row_limit), not a database scan or count cap. Default 20.
            offset: Pagination offset.
            order_by: Field name; prefix with '-' for descending (e.g. '-sys_created_on').
            display_values: True returns display_value form for reference and choice fields.
            aggregate: Native array of aggregate objects: {operation: count} or
                {operation: avg|sum|min|max, field: field_name}. When set, returns aggregate result instead of rows.
            group_by: Native array of fields to group by, e.g. state,active (aggregate mode only).
            resolve_labels: Native array of {field, label} objects (for example {field: state, label: open}).
                Each label is resolved via ChoiceRegistry to its underlying value, then ANDed
                into encoded_query as 'field=value'."""
        return await capability.query(
            table=table,
            sys_id=sys_id,
            encoded_query=encoded_query,
            fields=fields,
            limit=limit,
            offset=offset,
            order_by=order_by,
            display_values=display_values,
            aggregate=aggregate,
            group_by=group_by,
            resolve_labels=resolve_labels,
        )
