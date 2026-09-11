"""Resolve ServiceNow table inheritance chains."""

import logging
from typing import Any, Final

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.query_builder import ServiceNowQuery


logger = logging.getLogger(__name__)
_MAX_CHAIN_DEPTH: Final[int] = 8


async def resolve_chain(client: ServiceNowClient, table: str) -> list[str]:
    """Resolve a bounded, cycle-safe super-class chain child first."""
    chain: list[str] = []
    visited: set[str] = set()
    current = table
    for _ in range(_MAX_CHAIN_DEPTH):
        if current in visited:
            logger.warning("super_class cycle detected at table=%s; truncating chain at %s", current, chain)
            break
        visited.add(current)
        chain.append(current)
        parent = await lookup_super_class(client, current)
        if not parent:
            break
        current = parent
    else:
        logger.warning(
            "super_class chain for table=%s exceeded depth %d; truncated at %s",
            table,
            _MAX_CHAIN_DEPTH,
            chain,
        )
    return chain


async def lookup_super_class(client: ServiceNowClient, table: str) -> str:
    """Return the display name of a table's parent, or an empty string."""
    result = await client.query_records(
        table="sys_db_object",
        query=ServiceNowQuery().equals("name", table).build(),
        fields=["super_class.name"],
        limit=1,
    )
    records = result.get("records", [])
    if not records:
        return ""
    super_class: Any = records[0].get("super_class.name") or records[0].get("super_class") or ""
    if isinstance(super_class, dict):
        super_class = super_class.get("display_value") or super_class.get("value") or ""
    return str(super_class).strip()
