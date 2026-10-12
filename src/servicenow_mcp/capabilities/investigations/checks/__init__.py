"""Pluggable investigation modules.

Each module exports:
    async def run(client, params) -> dict
    async def explain(client, element_id) -> dict

The INVESTIGATION_REGISTRY maps investigation names to their modules.
"""

import types

from servicenow_mcp.capabilities.investigations.checks import (
    acl_conflicts,
    deprecated_apis,
    error_analysis,
    performance_bottlenecks,
    slow_transactions,
    stale_automations,
    table_health,
)


INVESTIGATION_REGISTRY: dict[str, types.ModuleType] = {
    "stale_automations": stale_automations,
    "deprecated_apis": deprecated_apis,
    "table_health": table_health,
    "acl_conflicts": acl_conflicts,
    "error_analysis": error_analysis,
    "slow_transactions": slow_transactions,
    "performance_bottlenecks": performance_bottlenecks,
}
