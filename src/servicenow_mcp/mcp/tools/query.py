"""Native query adapter."""

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.records._query_parsing import (
    AggregateOperation,
    LabelPair,
)
from servicenow_mcp.capabilities.records.query import QueryCapability
from servicenow_mcp.mcp.handlers import tool_handler
from servicenow_mcp.mcp.inputs import describe_tool_parameters

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings
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
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    @describe_tool_parameters(
        {
            "table": "ServiceNow table name, such as incident.",
            "sys_id": "Fetch one record by sys_id; cannot combine with aggregate.",
            "encoded_query": "ServiceNow encoded query used to filter matching records.",
            "fields": "List-mode projection array; required for lists, or use * for all fields.",
            "limit": "Maximum rows per list page; default is 20.",
            "offset": "Zero-based list page offset.",
            "order_by": "Field to sort by; prefix with - for descending.",
            "display_values": "Return display values for reference and choice fields.",
            "aggregate": "Operations array: count omits field; avg/sum/min/max require field.",
            "group_by": "Field-name array grouping aggregate results.",
            "resolve_labels": "Array of {field,label}; resolves labels and adds stored-value filters.",
        }
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
        """Query records with the Table API or use aggregate for counts, averages, sums, minima, and maxima with the Stats API. Large tables require a recognized date constraint."""
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
