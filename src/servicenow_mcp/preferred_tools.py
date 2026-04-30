"""Static map of ServiceNow tables to preferred specialized tools.

Used by ``table_query`` to inject a steering warning when an agent calls the
generic query tool on a table that has a more capable specialized tool. The
goal is to nudge agents toward tools that resolve choice labels, mask
sensitive fields, or return richer payloads, without blocking the generic
path entirely.
"""

from __future__ import annotations

from typing import Required, TypedDict

from servicenow_mcp.tools.metadata import ARTIFACT_TABLES


class PreferredTool(TypedDict, total=False):
    """Preferred specialized tools for a given ServiceNow table.

    At least one of ``list`` or ``get`` must be set on every entry; both are
    individually optional because some tables only support one access pattern
    (e.g. ``cmdb_rel_ci`` has only a get-style tool, ``sys_audit`` only a
    list-style tool). ``value_add`` is required: a short human-readable
    phrase explaining why the specialized tool is preferred, interpolated
    into the steering warning.
    """

    list: str
    get: str
    value_add: Required[str]


# Single source of truth for all metadata-artifact tables. The keys of
# ``ARTIFACT_TABLES`` (business_rule, script_include, etc.) map to the
# ServiceNow table names we steer here.
_META_PREFERRED: PreferredTool = {
    "list": "meta_list_artifacts",
    "get": "meta_get_artifact",
    "value_add": "abstracts artifact-type to table mapping and includes the script body",
}


# Map of ServiceNow table name -> preferred tool metadata.
# Keys must be exact table names. CMDB subclasses (anything starting with
# ``cmdb_ci``) fall back to the ``cmdb_ci`` entry via ``preferred_tool_for``.
TABLE_TO_PREFERRED_TOOL: dict[str, PreferredTool] = {
    "incident": {
        "list": "incident_list",
        "get": "incident_get",
        "value_add": 'resolves choice labels (e.g. state="open"), applies sensitivity masking, returns display values, and uses sensible field defaults',
    },
    "change_request": {
        "list": "change_list",
        "get": "change_get",
        "value_add": "resolves choice labels, applies sensitivity masking, and uses sensible field defaults",
    },
    "change_task": {
        "list": "change_tasks",
        "value_add": "scopes results to a parent change request and uses sensible field defaults",
    },
    "problem": {
        "list": "problem_list",
        "get": "problem_get",
        "value_add": "resolves choice labels, applies sensitivity masking, and uses sensible field defaults",
    },
    "sc_request": {
        "list": "request_list",
        "get": "request_get",
        "value_add": "resolves choice labels and uses sensible field defaults",
    },
    "sc_req_item": {
        "list": "request_items",
        "get": "request_item_get",
        "value_add": "resolves choice labels and uses sensible field defaults",
    },
    "kb_knowledge": {
        "list": "knowledge_search",
        "get": "knowledge_get",
        "value_add": "supports fuzzy text search across short_description and text, and defaults to published articles",
    },
    "cmdb_ci": {
        "list": "cmdb_list",
        "get": "cmdb_get",
        "value_add": "supports name-or-sys_id lookup and ci_class scoping",
    },
    "cmdb_rel_ci": {
        "get": "cmdb_relationships",
        "value_add": "renders parent/child/both relationships with name resolution",
    },
    "sys_attachment": {
        "list": "attachment_list",
        "get": "attachment_get",
        "value_add": "supports source-record and filename filters and applies attachment-specific policy",
    },
    "sys_audit": {
        "list": "changes_last_touched",
        "value_add": 'answers "who changed what" / audit / history / timeline questions by correlating sys_audit, sys_journal_field, and syslog into a merged timeline',
    },
    "sc_catalog": {
        "list": "sc_catalogs_list",
        "get": "sc_catalog_get",
        "value_add": "resolves catalog metadata and active flags",
    },
    "sc_category": {
        "list": "sc_categories_list",
        "get": "sc_category_get",
        "value_add": "scopes to a specific catalog and resolves category hierarchy",
    },
    "sc_cat_item": {
        "list": "sc_items_list",
        "get": "sc_item_get",
        "value_add": "includes pricing, availability, and variable definitions",
    },
    "sys_hub_flow": {
        "list": "flow_list",
        "get": "flow_get",
        "value_add": "decodes Flow Designer flow definitions and includes activity metadata",
    },
    "wf_context": {
        "list": "workflow_contexts",
        "get": "workflow_status",
        "value_add": "decodes workflow execution state with activity-by-activity status",
    },
    # All metadata-artifact tables share the same steering target. Keyed off
    # ARTIFACT_TABLES so adding a new artifact type in tools/metadata.py
    # automatically extends the steering map.
    **dict.fromkeys(ARTIFACT_TABLES.values(), _META_PREFERRED),
}


def preferred_tool_for(table: str) -> PreferredTool | None:
    """Return preferred-tool metadata for ``table`` or ``None`` if no preference is registered.

    Performs an exact-match lookup, then falls back to the ``cmdb_ci`` entry
    for any table whose name starts with ``cmdb_ci`` (covering CMDB subclasses
    such as ``cmdb_ci_server``, ``cmdb_ci_database``, etc.).
    """
    direct = TABLE_TO_PREFERRED_TOOL.get(table)
    if direct is not None:
        return direct
    if table.startswith("cmdb_ci"):
        return TABLE_TO_PREFERRED_TOOL.get("cmdb_ci")
    return None


def format_preference_warning(table: str, preferred: PreferredTool) -> str:
    """Format the steering warning string for ``table`` given its preferred-tool metadata."""
    tools: list[str] = []
    if "list" in preferred:
        tools.append(f"`{preferred['list']}`")
    if "get" in preferred:
        tools.append(f"`{preferred['get']}`")
    if not tools:
        raise ValueError(f"Preferred tool entry for table {table!r} has neither 'list' nor 'get'")
    tool_phrase = " / ".join(tools)
    return (
        f"Prefer {tool_phrase} over `table_query` for table `{table}` - {preferred['value_add']}. "
        "`table_query` remains valid for ad-hoc filtering, but the specialized tool is usually a better fit."
    )
