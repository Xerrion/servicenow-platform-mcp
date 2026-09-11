"""Orchestrate metadata retrieval and describe response assembly."""

from typing import Any

from servicenow_mcp.client import ServiceNowClientProvider
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.tools._describe_enrichment import (
    fetch_inherited_choice_counts,
    fetch_inherited_documentation,
)
from servicenow_mcp.tools._describe_projection import (
    build_selection,
    filter_projected_fields,
    project_fields,
    select_dictionary_fields,
)
from servicenow_mcp.tools._describe_projection import (
    parse_fields_filter as _parse_fields_filter,
)
from servicenow_mcp.tools._dictionary import DictionaryRegistry


DEFAULT_DESCRIBE_FIELD_LIMIT = 25

__all__ = ["DEFAULT_DESCRIBE_FIELD_LIMIT", "_describe_impl", "_parse_fields_filter"]


async def _describe_impl(
    table: str,
    *,
    verbose: bool,
    include_docs: bool,
    requested_fields: list[str],
    field_offset: int,
    field_limit: int,
    client_factory: ServiceNowClientProvider,
    dictionary: DictionaryRegistry,
) -> tuple[dict[str, Any], list[str]]:
    """Retrieve metadata and assemble the describe data and warnings."""
    warnings: list[str] = []
    all_dictionary_fields = await dictionary.get_all_fields(table)
    total_field_count = len(all_dictionary_fields)
    selected_fields = select_dictionary_fields(
        all_dictionary_fields,
        requested_fields,
        field_offset,
        field_limit,
    )

    async with client_factory() as client:
        table_meta = await client.query_records(
            "sys_db_object",
            ServiceNowQuery().equals("name", table).build(),
            fields=["label", "super_class", "is_extendable", "number_ref", "sys_id"],
            limit=1,
        )
        table_info = table_meta.get("records", [{}])[0] if table_meta.get("records") else {}
        choice_counts = await fetch_inherited_choice_counts(client, selected_fields, table, warnings)
        documentation: dict[str, dict[str, Any]] = {}
        if include_docs:
            documentation = await fetch_inherited_documentation(client, selected_fields, table, warnings)

    projected_fields = project_fields(selected_fields, choice_counts, verbose=verbose)
    if requested_fields:
        projected_fields, unknown = filter_projected_fields(projected_fields, requested_fields)
        if unknown:
            warnings.append(f"Unknown field(s): {','.join(unknown)}")
    selection = build_selection(
        projected_fields,
        requested_fields,
        field_offset,
        field_limit,
        total_field_count,
    )
    data: dict[str, Any] = {
        "table": table_info,
        "fields": projected_fields,
        "field_count": len(projected_fields),
        "total_field_count": total_field_count,
        "selection": selection,
    }
    if include_docs:
        data["documentation"] = documentation
    return data, warnings
