"""Fetch choice and documentation enrichment for describe responses."""

import collections
import logging
from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.policy import INTERNAL_QUERY_LIMIT
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.tools._dictionary import DictionaryField


logger = logging.getLogger(__name__)


async def fetch_inherited_choice_counts(
    client: ServiceNowClient,
    fields: list[DictionaryField],
    table: str,
    warnings: list[str],
) -> dict[str, int]:
    """Fetch choice counts from each field's declaring table."""
    names = [field.name for field in fields]
    counts = await _fetch_choice_counts(client, table, names, warnings)
    fields_by_table: dict[str, list[str]] = collections.defaultdict(list)
    for field in fields:
        if field.inherited_from and not counts.get(field.name):
            fields_by_table[field.inherited_from].append(field.name)

    for source_table, inherited_names in fields_by_table.items():
        source_counts = await _fetch_choice_counts(client, source_table, inherited_names, warnings)
        for name, count in source_counts.items():
            counts.setdefault(name, count)
    return counts


async def fetch_inherited_documentation(
    client: ServiceNowClient,
    fields: list[DictionaryField],
    table: str,
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    """Fetch documentation from each field's declaring table."""
    names = [field.name for field in fields]
    documentation = await _fetch_documentation(client, table, names, warnings)
    fields_by_table: dict[str, list[str]] = collections.defaultdict(list)
    for field in fields:
        if field.inherited_from and field.name not in documentation:
            fields_by_table[field.inherited_from].append(field.name)

    for source_table, inherited_names in fields_by_table.items():
        source_docs = await _fetch_documentation(client, source_table, inherited_names, warnings)
        for name, document in source_docs.items():
            documentation.setdefault(name, document)
    return documentation


async def _fetch_choice_counts(
    client: ServiceNowClient,
    table: str,
    fields: list[str],
    warnings: list[str],
) -> dict[str, int]:
    if not fields:
        return {}
    try:
        response = await client.query_records(
            "sys_choice",
            ServiceNowQuery().equals("name", table).in_list("element", fields).build(),
            fields=["element"],
            limit=INTERNAL_QUERY_LIMIT,
        )
    except Exception:
        logger.warning("sys_choice fetch failed for table %s; choice_count will be 0", table)
        warnings.append("Could not fetch sys_choice; choice_count is 0 for all fields")
        return {}

    records = response.get("records", [])
    if len(records) >= INTERNAL_QUERY_LIMIT:
        warnings.append(f"sys_choice records may be truncated at {INTERNAL_QUERY_LIMIT} entries")
    return dict(collections.Counter(record.get("element", "") for record in records if record.get("element")))


async def _fetch_documentation(
    client: ServiceNowClient,
    table: str,
    fields: list[str],
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    if not fields:
        return {}
    result = await client.query_records(
        "sys_documentation",
        ServiceNowQuery().equals("name", table).in_list("element", fields).build(),
        fields=["element", "label", "help", "hint", "url"],
        limit=500,
    )
    records = result.get("records", [])
    if len(records) >= 500:
        warnings.append("Documentation records may be truncated at 500 entries")
    return {record["element"]: record for record in records if record.get("element")}
