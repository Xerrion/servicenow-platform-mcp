"""Prepare unified-query requests and apply read-side policy."""

import logging
from dataclasses import dataclass
from typing import Final

from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import check_table_access, enforce_query_safety
from servicenow_mcp.response import format_response
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools._query_parsing import (
    AggregatePlan,
    LabelPair,
    Projection,
    extract_query_fields,
    join_query,
    parse_aggregate,
    parse_group_fields,
    parse_label_pairs,
    parse_projection,
)
from servicenow_mcp.validation import validate_identifier, validate_sys_id


logger = logging.getLogger(__name__)

_UNIVERSAL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "sys_id",
        "sys_created_on",
        "sys_created_by",
        "sys_updated_on",
        "sys_updated_by",
        "sys_mod_count",
        "sys_tags",
    }
)


@dataclass(frozen=True)
class AggregateRequest:
    """Validated aggregate request values."""

    plan: AggregatePlan
    group_fields: list[str]


@dataclass(frozen=True)
class ListRequest:
    """Validated list request values."""

    projection: Projection
    limit: int
    order_by: str


def error_response(message: str) -> str:
    """Return a serialized error envelope."""
    return format_response(data=None, status="error", error=message)


def check_mode_conflicts(sys_id: str | None, aggregate: str | None, group_by: str | None) -> str | None:
    """Return an error envelope when mode arguments conflict."""
    if sys_id and aggregate:
        return error_response("Cannot combine sys_id with aggregate; sys_id mode fetches a single record.")
    if sys_id and group_by:
        return error_response("Cannot combine sys_id with group_by; sys_id mode fetches a single record.")
    if group_by and not aggregate:
        return error_response("group_by requires aggregate to be set (aggregate mode only).")
    return None


def prepare_table(table: str) -> None:
    """Validate the table identifier and enforce table access policy."""
    validate_identifier(table)
    check_table_access(table)


def prepare_single_record(sys_id: str, fields: str) -> Projection | str:
    """Validate a single-record request and parse its projection."""
    validate_sys_id(sys_id)
    projection = parse_projection(fields, compact_default=True)
    return error_response(projection) if isinstance(projection, str) else projection


def prepare_list_projection(fields: str) -> Projection | str:
    """Parse the required list projection before optional metadata I/O."""
    projection = parse_projection(fields, compact_default=False)
    return error_response(projection) if isinstance(projection, str) else projection


def prepare_list_request(
    table: str,
    encoded_query: str,
    projection: Projection,
    limit: int,
    order_by: str,
    settings: Settings,
) -> ListRequest:
    """Validate list ordering and apply query-safety limits."""
    order_field = order_by.lstrip("-") if order_by else ""
    if order_field:
        validate_identifier(order_field)
    safety = enforce_query_safety(table, encoded_query, limit, settings)
    return ListRequest(projection=projection, limit=safety["limit"], order_by=order_by)


def prepare_aggregate_request(
    table: str,
    encoded_query: str,
    aggregate: str,
    group_by: str,
    settings: Settings,
) -> AggregateRequest | str:
    """Parse aggregate arguments and enforce aggregate query safety."""
    plan = parse_aggregate(aggregate)
    if isinstance(plan, str):
        return error_response(plan)
    if plan.is_empty:
        return error_response(
            "aggregate must contain at least one operation (count, avg:<f>, sum:<f>, min:<f>, max:<f>)."
        )
    group_fields = parse_group_fields(group_by)
    enforce_query_safety(table, encoded_query, None, settings)
    return AggregateRequest(plan=plan, group_fields=group_fields)


async def resolve_labels(
    table: str,
    encoded_query: str,
    resolve_labels_spec: str,
    choices: ChoiceRegistry | None,
) -> tuple[str, list[str]] | str:
    """Resolve choice labels and append their field filters to a query."""
    pairs = parse_label_pairs(resolve_labels_spec)
    if isinstance(pairs, str):
        return error_response(pairs)
    if choices is None:
        warning = "resolve_labels supplied but ChoiceRegistry is unavailable; treating each label as a literal value."
        return _append_label_pairs(encoded_query, pairs), [warning]

    warnings: list[str] = []
    augmented = encoded_query
    for pair in pairs:
        resolved = await choices.resolve(table, pair.field, pair.label)
        if resolved == pair.label and not pair.label.isdigit():
            warnings.append(
                f"resolve_labels: '{pair.field}={pair.label}' did not resolve via ChoiceRegistry; "
                "using label verbatim as the filter value."
            )
        augmented = join_query(augmented, f"{pair.field}={resolved}")
    return augmented, warnings


async def validate_query_fields(
    table: str,
    encoded_query: str,
    dictionary: DictionaryRegistry,
) -> list[str]:
    """Return an advisory warning for query fields absent from the table."""
    candidates = extract_query_fields(encoded_query)
    if not candidates:
        return []
    try:
        known = await dictionary.get_all_fields(table)
    except Exception:
        logger.warning("field validation skipped for table=%s: dictionary lookup failed", table, exc_info=True)
        return []

    known_names = {entry.name for entry in known}
    if not known_names:
        return []
    unknown = [name for name in candidates if name not in known_names and name not in _UNIVERSAL_FIELDS]
    if not unknown:
        return []
    return [
        (
            f"Query references field(s) not found on table '{table}': {', '.join(unknown)}. "
            "ServiceNow silently ignores conditions on unknown fields, so the result is "
            "NOT filtered by them. Verify the field names against the table dictionary."
        )
    ]


def _append_label_pairs(encoded_query: str, pairs: list[LabelPair]) -> str:
    augmented = encoded_query
    for pair in pairs:
        augmented = join_query(augmented, f"{pair.field}={pair.label}")
    return augmented
