"""Flow Designer introspection and migration analysis tools.

This package replaces the former monolithic ``flow_designer.py`` module.
The tools were split by domain into private sub-modules to keep each file
focused and within reasonable size:

- ``_definition``: flow definition introspection (flow_list, flow_get, flow_map, flow_snapshot_list)
- ``_action``: flow action instance + type definition + steps (flow_action_detail)
- ``_execution``: flow execution contexts and logs (flow_execution_list, flow_execution_detail)
- ``_migration``: legacy workflow to Flow Designer migration analysis (workflow_migration_analysis)

The package import path remains ``servicenow_mcp.tools.flow_designer`` so
``packages.py`` and the ``importlib``-based loader in ``server.py`` continue
to work unchanged. ``register_tools`` is the single public entry point.

``_process_neighbor`` and ``TOOL_NAMES`` are re-exported because they are
referenced by tests and internal callers.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.tools.flow_designer import _action, _definition, _execution, _migration
from servicenow_mcp.tools.flow_designer._migration import _process_neighbor


TOOL_NAMES: list[str] = [
    "flow_list",
    "flow_get",
    "flow_map",
    "flow_action_detail",
    "flow_execution_list",
    "flow_execution_detail",
    "flow_snapshot_list",
    "workflow_migration_analysis",
]


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register Flow Designer introspection and migration tools on the MCP server.

    Tool registration order matches ``TOOL_NAMES``:
    flow_list, flow_get, flow_map -> flow_action_detail ->
    flow_execution_list, flow_execution_detail -> flow_snapshot_list ->
    workflow_migration_analysis.
    """
    _definition.register_core(mcp, settings, auth_provider)
    _action.register(mcp, settings, auth_provider)
    _execution.register(mcp, settings, auth_provider)
    _definition.register_snapshot_list(mcp, settings, auth_provider)
    _migration.register(mcp, settings, auth_provider)


__all__ = ["TOOL_NAMES", "_process_neighbor", "register_tools"]
