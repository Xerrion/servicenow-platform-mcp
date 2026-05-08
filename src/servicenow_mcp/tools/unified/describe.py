"""Unified ``describe`` tool: slim field metadata for any table.

Phase 3a relocation of ``tools/table.py:table_describe``. The behavior, return
shape, policy gates, and warning strategy are identical; only the tool name
(``describe``) and module location change. The legacy ``table_describe`` stays
registered until Phase 3b flips the package registry over.

Helpers for projecting sys_dictionary rows into the slim/verbose shapes live in
``servicenow_mcp.tools._describe_helpers`` so the legacy and unified tools share
a single source of truth.
"""

import collections
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    INTERNAL_QUERY_LIMIT,
    check_table_access,
)
from servicenow_mcp.tools._describe_helpers import (
    _build_slim_field_list,
    _build_verbose_field_list,
    _parse_fields_filter,
)
from servicenow_mcp.utils import ServiceNowQuery, format_response, validate_identifier


logger = logging.getLogger(__name__)

TOOL_NAMES: list[str] = ["describe"]


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    """Register the unified ``describe`` tool on the MCP server.

    Mirrors the unified-tool registration signature used by ``server.py`` for
    ``unified.*`` modules. ``choices`` is unused by ``describe`` but accepted
    for contract parity.
    """
    del choices  # unused; signature retained for loader parity

    @mcp.tool()
    @tool_handler
    async def describe(
        table: str,
        fields: str = "",
        verbose: bool = False,
        include_docs: bool = False,
        *,
        correlation_id: str = "",
    ) -> str:
        """Return slim field metadata for a table. Default is the 8-key slim shape per field.

        Args:
            table: ServiceNow table name.
            fields: Comma-separated list of fields to include. Empty = all fields.
            verbose: When True, return the full sys_dictionary row per field
                minus a fixed deny-list of high-noise keys. Default False.
            include_docs: When True, attach the matching sys_documentation entry
                (label/help/hint/url) per field. Default False.
        """
        validate_identifier(table)
        check_table_access(table)

        requested_fields = _parse_fields_filter(fields)
        for name in requested_fields:
            validate_identifier(name)

        warnings: list[str] = []

        async with ServiceNowClient(settings, auth_provider) as client:
            metadata = await client.get_metadata(table)

            # Fetch table-level metadata from sys_db_object
            table_meta = await client.query_records(
                "sys_db_object",
                ServiceNowQuery().equals("name", table).build(),
                fields=["label", "super_class", "is_extendable", "number_ref", "sys_id"],
                limit=1,
            )
            table_info = table_meta.get("records", [{}])[0] if table_meta.get("records") else {}

            # Batched sys_choice fetch keyed by field (element). One HTTP call per
            # describe; failure (e.g. ACL on sys_choice) is non-fatal so a slim
            # describe still works in restricted instances.
            choice_counts: dict[str, int] = {}
            try:
                choices_resp = await client.query_records(
                    "sys_choice",
                    ServiceNowQuery().equals("name", table).build(),
                    fields=["element"],
                    limit=INTERNAL_QUERY_LIMIT,
                )
                choice_records = choices_resp.get("records", [])
                choice_counts = dict(
                    collections.Counter(c.get("element", "") for c in choice_records if c.get("element"))
                )
                if len(choice_records) >= INTERNAL_QUERY_LIMIT:
                    warnings.append(f"sys_choice records may be truncated at {INTERNAL_QUERY_LIMIT} entries")
            except Exception:
                logger.warning("sys_choice fetch failed for table %s; choice_count will be 0", table)
                warnings.append("Could not fetch sys_choice; choice_count is 0 for all fields")
                choice_counts = {}

            # Optional sys_documentation fetch (off by default; help text is huge).
            docs: dict[str, dict[str, Any]] = {}
            if include_docs:
                docs_result = await client.query_records(
                    "sys_documentation",
                    ServiceNowQuery().equals("name", table).build(),
                    fields=["element", "label", "help", "hint", "url"],
                    limit=500,
                )
                docs = {d["element"]: d for d in docs_result.get("records", []) if d.get("element")}
                if len(docs_result.get("records", [])) >= 500:
                    warnings.append("Documentation records may be truncated at 500 entries")

        field_list = (
            _build_verbose_field_list(metadata, choice_counts)
            if verbose
            else _build_slim_field_list(metadata, choice_counts)
        )

        if requested_fields:
            wanted = set(requested_fields)
            present = {str(f.get("name") or f.get("element") or "") for f in field_list}
            unknown = [name for name in requested_fields if name not in present]
            field_list = [f for f in field_list if str(f.get("name") or f.get("element") or "") in wanted]
            if unknown:
                warnings.append(f"Unknown field(s): {','.join(unknown)}")

        data: dict[str, Any] = {
            "table": table_info,
            "fields": field_list,
            "field_count": len(field_list),
        }
        if include_docs:
            data["documentation"] = docs

        return format_response(
            data=data,
            correlation_id=correlation_id,
            warnings=warnings or None,
        )
