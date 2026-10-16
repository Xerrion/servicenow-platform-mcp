"""Table metadata discovery and slim or verbose field descriptions.

Projection helpers live in ``servicenow_mcp.capabilities.metadata._describe_helpers``.
"""

import logging
from typing import TYPE_CHECKING, Literal

from servicenow_mcp.capabilities.contracts import CapabilityError, CapabilityResult
from servicenow_mcp.capabilities.contracts import result as operation_result
from servicenow_mcp.capabilities.metadata._describe_helpers import (
    DEFAULT_DESCRIBE_FIELD_LIMIT,
    _describe_impl,
    _parse_fields_filter,
)
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry, ScriptField
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.validation import validate_identifier

if TYPE_CHECKING:
    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings


logger = logging.getLogger(__name__)


_VALID_DESCRIBE_ACTIONS: frozenset[str] = frozenset(
    {"list_script_fields", "list_tables"}
)

# sys_db_object holds thousands of rows; cap list_tables and warn on truncation.
_LIST_TABLES_LIMIT: int = 500
_LIST_TABLES_FIELDS: list[str] = ["name", "label", "super_class", "sys_scope"]


def _coerce_display(value: object) -> str:
    """Flatten a field that may arrive as a display-value dict into a string."""
    if isinstance(value, dict):
        return str(value.get("display_value") or value.get("value") or "")
    return str(value or "")


def _script_field_summary(fields: list[ScriptField]) -> list[dict[str, object]]:
    """Project ``ScriptField`` instances into a JSON-friendly list."""
    return [
        {
            "name": f.name,
            "internal_type": f.internal_type,
            "inherited_from": f.inherited_from,
            "via_heuristic": f.via_heuristic,
        }
        for f in fields
    ]


async def _run_list_script_fields(
    table: str,
    dictionary: DictionaryRegistry,
) -> CapabilityResult:
    """Resolve script-bearing fields for ``table`` via ``DictionaryRegistry``.

    Returns the resolved super_class chain alongside the script fields so the
    caller can audit where each field is inherited from.
    """
    if not table:
        raise CapabilityError("table is required when action='list_script_fields'.")

    validate_identifier(table)
    check_table_access(table)

    chain = await dictionary.get_chain(table)
    script_fields = await dictionary.get_script_fields(table)

    return operation_result(
        data={
            "table": table,
            "chain": chain,
            "script_fields": _script_field_summary(script_fields),
            "count": len(script_fields),
        },
    )


async def _run_list_tables(
    name_filter: str,
    client_factory: ServiceNowClientProvider,
) -> CapabilityResult:
    """List tables from ``sys_db_object``, optionally filtered by name/label.

    ``name_filter`` is matched as a substring against both the table ``name``
    and its human-readable ``label``. Results are ordered by name, capped at
    ``_LIST_TABLES_LIMIT``, and accompanied by a truncation warning when the cap
    is reached so the caller knows to narrow the filter.
    """
    builder = ServiceNowQuery()
    if name_filter:
        builder.like("name", name_filter).or_condition("label", "LIKE", name_filter)
    query = builder.order_by("name").build()

    async with client_factory() as client:
        result = await client.query_records(
            table="sys_db_object",
            query=query,
            fields=_LIST_TABLES_FIELDS,
            limit=_LIST_TABLES_LIMIT,
            display_values=True,
        )

    records = result.get("records", [])
    tables = [
        {name: _coerce_display(row.get(name)) for name in _LIST_TABLES_FIELDS}
        for row in records
    ]

    warnings: list[str] = []
    if len(tables) >= _LIST_TABLES_LIMIT:
        warnings.append(
            f"Result truncated at {_LIST_TABLES_LIMIT} tables; narrow name_filter to see more."
        )

    return operation_result(
        data={"tables": tables, "count": len(tables)},
        warnings=warnings or None,
    )


class DescribeCapability:
    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        dictionary: DictionaryRegistry | None = None,
        client_factory: ServiceNowClientProvider | None = None,
    ) -> None:
        """Bind the operation's platform and metadata dependencies."""
        client_factory = client_factory or (
            lambda: ServiceNowClient(settings, auth_provider)
        )
        if dictionary is None:
            dictionary = DictionaryRegistry(settings, auth_provider, client_factory)
        dict_registry = dictionary

        self.auth_provider = auth_provider
        self.client_factory = client_factory
        self.dict_registry = dict_registry
        self.dictionary = dictionary
        self.settings = settings

    async def describe(
        self,
        table: str | None = None,
        fields: list[str] | Literal["*"] | None = None,
        verbose: bool | None = False,
        include_docs: bool | None = False,
        action: str | None = None,
        name_filter: str | None = None,
        field_offset: int | None = 0,
        field_limit: int | None = DEFAULT_DESCRIBE_FIELD_LIMIT,
    ) -> CapabilityResult:
        """Execute the describe capability with native typed inputs."""
        table = "" if table is None else table
        fields = [] if fields is None else fields
        verbose = False if verbose is None else verbose
        include_docs = False if include_docs is None else include_docs
        action = "" if action is None else action
        name_filter = "" if name_filter is None else name_filter
        field_offset = 0 if field_offset is None else field_offset
        field_limit = (
            DEFAULT_DESCRIBE_FIELD_LIMIT if field_limit is None else field_limit
        )

        if action:
            if action not in _VALID_DESCRIBE_ACTIONS:
                raise CapabilityError(
                    f"Unknown describe action {action!r}. Valid actions: {sorted(_VALID_DESCRIBE_ACTIONS)}.",
                )
            if action == "list_tables":
                return await _run_list_tables(name_filter, self.client_factory)
            return await _run_list_script_fields(table, self.dict_registry)

        if not table:
            raise CapabilityError("table is required when action is not set.")

        validate_identifier(table)
        check_table_access(table)

        if field_offset < 0:
            raise CapabilityError("field_offset must be zero or greater.")
        if not 1 <= field_limit <= 100:
            raise CapabilityError("field_limit must be between 1 and 100.")

        is_all_fields = fields == "*"
        requested_fields = [] if is_all_fields else _parse_fields_filter(fields)
        if "*" in requested_fields:
            raise CapabilityError("fields='*' must be used alone.")
        for name in requested_fields:
            validate_identifier(name)

        data, warnings = await _describe_impl(
            table,
            verbose=verbose,
            include_docs=include_docs,
            requested_fields=requested_fields,
            field_offset=0 if is_all_fields else field_offset,
            field_limit=1_000_000 if is_all_fields else field_limit,
            client_factory=self.client_factory,
            dictionary=self.dict_registry,
        )

        return operation_result(
            data=data,
            warnings=warnings or None,
            pagination=(
                {
                    "offset": field_offset,
                    "limit": field_limit,
                    "total": data["total_field_count"],
                }
                if not is_all_fields and not requested_fields
                else None
            ),
        )
