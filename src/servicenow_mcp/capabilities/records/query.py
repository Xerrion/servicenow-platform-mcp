"""Register the unified ServiceNow record-query tool."""

from typing import Literal

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.records._query_modes import run_aggregate, run_list, run_single_record
from servicenow_mcp.capabilities.records._query_parsing import AggregateOperation, LabelPair, Projection
from servicenow_mcp.capabilities.records._query_preparation import (
    check_mode_conflicts,
    prepare_aggregate_request,
    prepare_list_projection,
    prepare_list_request,
    prepare_single_record,
    prepare_table,
    validate_query_fields,
)
from servicenow_mcp.capabilities.records._query_preparation import (
    resolve_labels as prepare_resolved_labels,
)
from servicenow_mcp.config import Settings
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry
from servicenow_mcp.platform.metadata.choices import ChoiceRegistry


class QueryCapability:
    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        choices: ChoiceRegistry | None = None,
        dictionary: DictionaryRegistry | None = None,
        client_factory: ServiceNowClientProvider | None = None,
    ) -> None:
        """Bind the operation's platform and metadata dependencies."""
        client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

        self.auth_provider = auth_provider
        self.choices = choices
        self.client_factory = client_factory
        self.dictionary = dictionary
        self.settings = settings

    async def query(
        self,
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
        """Execute the query capability with native typed inputs."""
        limit = 20 if limit is None else limit
        offset = 0 if offset is None else offset
        display_values = False if display_values is None else display_values

        check_mode_conflicts(sys_id, aggregate, group_by)
        prepare_table(table)

        if sys_id:
            projection = prepare_single_record(sys_id, fields)
            return await run_single_record(table, sys_id, projection, display_values, self.client_factory)

        list_projection = None
        if not aggregate:
            list_projection = prepare_list_projection(fields)

        warnings: list[str] = []
        prepared_query = encoded_query or ""
        if resolve_labels:
            resolved = await prepare_resolved_labels(table, prepared_query, resolve_labels, self.choices)
            prepared_query, label_warnings = resolved
            warnings.extend(label_warnings)

        if aggregate:
            aggregate_request = prepare_aggregate_request(
                table, prepared_query, aggregate, group_by or [], self.settings
            )
            if self.dictionary is not None:
                warnings.extend(await validate_query_fields(table, prepared_query, self.dictionary))
            return await run_aggregate(table, prepared_query, aggregate_request, self.client_factory, warnings)

        assert isinstance(list_projection, Projection)
        list_request = prepare_list_request(
            table,
            prepared_query,
            list_projection,
            limit,
            order_by or "",
            self.settings,
        )
        if self.dictionary is not None:
            warnings.extend(await validate_query_fields(table, prepared_query, self.dictionary))
        return await run_list(
            table,
            prepared_query,
            list_request,
            offset,
            display_values,
            self.client_factory,
            warnings,
        )
