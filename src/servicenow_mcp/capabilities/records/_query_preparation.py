"""Prepare unified-query requests and apply read-side policy."""

import logging
from typing import ClassVar, Final, Literal, NoReturn

import httpx2
from pydantic import BaseModel, ConfigDict

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.records._query_parsing import (
    AggregateOperation,
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
from servicenow_mcp.config import Settings
from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry
from servicenow_mcp.platform.metadata.choices import ChoiceRegistry
from servicenow_mcp.policy import check_table_access, enforce_query_safety
from servicenow_mcp.telemetry import current_tool_trace, timeout_phase
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


class AggregateRequest(BaseModel):
    """Validated aggregate request values."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    plan: AggregatePlan
    group_fields: list[str]


class ListRequest(BaseModel):
    """Validated list request values."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    projection: Projection
    limit: int
    order_by: str


def error_response(message: str) -> NoReturn:
    raise CapabilityError(message)


def check_mode_conflicts(
    sys_id: str | None, aggregate: list[AggregateOperation] | None, group_by: list[str] | None
) -> None:
    """Reject mutually exclusive query modes before platform requests."""
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


def prepare_single_record(sys_id: str, fields: list[str] | Literal["*"] | None) -> Projection:
    """Validate a single-record request and parse its projection."""
    validate_sys_id(sys_id)
    return parse_projection(fields, compact_default=True)


def prepare_list_projection(fields: list[str] | Literal["*"] | None) -> Projection:
    """Parse the required list projection before optional metadata I/O."""
    return parse_projection(fields, compact_default=False)


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
    aggregate: list[AggregateOperation],
    group_by: list[str],
    settings: Settings,
) -> AggregateRequest:
    """Parse aggregate arguments and enforce aggregate query safety."""
    plan = parse_aggregate(aggregate)
    if plan.is_empty:
        return error_response("aggregate must contain at least one operation.")
    group_fields = parse_group_fields(group_by)
    enforce_query_safety(table, encoded_query, None, settings)
    return AggregateRequest(plan=plan, group_fields=group_fields)


async def resolve_labels(
    table: str,
    encoded_query: str,
    resolve_labels_spec: list[LabelPair],
    choices: ChoiceRegistry | None,
) -> tuple[str, list[str]]:
    """Resolve choice labels and append their field filters to a query."""
    pairs = parse_label_pairs(resolve_labels_spec)
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
    """Report unresolved schema paths without interpreting hidden metadata as invalid."""
    candidates = [path for path in extract_query_fields(encoded_query) if path not in _UNIVERSAL_FIELDS]
    if not candidates:
        return []
    try:
        resolutions = await dictionary.get_query_field_paths(table, candidates)
    except Exception as exc:
        reason = (
            f"HTTP timeout ({timeout_phase(exc)})"
            if isinstance(exc, httpx2.TimeoutException)
            else "metadata lookup failed"
        )
        trace = current_tool_trace()
        trace_note = f" trace_id={trace.trace_id}" if trace else ""
        logger.warning("Query field validation skipped:%s reason=%s", trace_note, reason)
        return [
            (
                f"Could not validate query fields for table '{table}': {reason}.{trace_note} "
                "ServiceNow may ignore unknown fields and "
                "broaden the query; verify field names before trusting the result."
            )
        ]

    unverified = [entry for entry in resolutions if entry.status == "unverified"]
    if not unverified:
        return []
    details = ". ".join(f"{entry.path}: {entry.reason or 'metadata did not resolve this path'}" for entry in unverified)
    trace = current_tool_trace()
    trace_note = f" trace_id={trace.trace_id}" if trace else ""
    logger.warning("Query field validation incomplete:%s unverified_paths=%d", trace_note, len(unverified))
    return [
        (
            f"Query field path(s) remain unverified for table '{table}': {details}.{trace_note} "
            "Missing metadata does not prove a field is invalid. ServiceNow may ignore invalid conditions "
            "and broaden the query. Verify these paths before trusting the result."
        )
    ]


def _append_label_pairs(encoded_query: str, pairs: list[LabelPair]) -> str:
    augmented = encoded_query
    for pair in pairs:
        augmented = join_query(augmented, f"{pair.field}={pair.label}")
    return augmented
