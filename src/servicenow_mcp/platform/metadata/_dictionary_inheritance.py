"""Resolve ServiceNow table inheritance chains."""

import logging
from typing import TYPE_CHECKING, Final, Literal

from servicenow_mcp.platform.metadata._dictionary_models import TableInheritance
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.validation import validate_identifier

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient


logger = logging.getLogger(__name__)
_MAX_CHAIN_DEPTH: Final[int] = 8


async def resolve_chain(client: ServiceNowClient, table: str) -> TableInheritance:
    """Resolve a bounded, cycle-safe super-class chain child first."""
    chain: list[str] = []
    visited: set[str] = set()
    current = table
    for _ in range(_MAX_CHAIN_DEPTH):
        if current in visited:
            logger.warning(
                "super_class cycle detected at table=%s; truncating chain at %s",
                current,
                chain,
            )
            return TableInheritance(
                tables=chain, complete=False, reason="inheritance_cycle"
            )
        visited.add(current)
        chain.append(current)
        parent, reason = await lookup_super_class(client, current)
        if reason is not None:
            return TableInheritance(tables=chain, complete=False, reason=reason)
        if not parent:
            return TableInheritance(tables=chain, complete=True)
        current = parent
    logger.warning(
        "super_class chain for table=%s exceeded depth %d; truncated at %s",
        table,
        _MAX_CHAIN_DEPTH,
        chain,
    )
    return TableInheritance(
        tables=chain, complete=False, reason="inheritance_depth_limit"
    )


async def lookup_super_class(
    client: ServiceNowClient,
    table: str,
) -> tuple[str, Literal["table_not_visible", "parent_not_visible"] | None]:
    """Distinguish an observed root from missing table or parent metadata."""
    result = await client.query_records(
        table="sys_db_object",
        query=ServiceNowQuery().equals("name", table).build(),
        fields=["super_class", "super_class.name"],
        limit=1,
    )
    records = result.get("records", [])
    if not records:
        return "", "table_not_visible"
    row = records[0]
    parent = row.get("super_class.name")
    if isinstance(parent, dict):
        parent = parent.get("value") or parent.get("display_value")
    if isinstance(parent, str) and parent:
        try:
            validate_identifier(parent)
        except ValueError:
            return "", "parent_not_visible"
        return parent, None
    if "super_class" not in row:
        return "", "parent_not_visible"
    reference = row["super_class"]
    if isinstance(reference, dict):
        if "value" not in reference:
            return "", "parent_not_visible"
        reference = reference["value"]
    if reference in ("", None):
        return "", None
    return "", "parent_not_visible"
