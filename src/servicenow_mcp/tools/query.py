"""Unified ``query`` tool: read records, aggregates, or a single record.

This module collapses the read-side surface (``table_query``, ``table_aggregate``,
``record_get``) into one tool that dispatches on its arguments. Three mutually
exclusive modes, in precedence order:

1. ``sys_id`` set    -> single-record fetch (no scan, no pagination).
2. ``aggregate`` set -> Stats API call, optionally grouped (no row masking).
3. otherwise         -> paginated record query with field masking.

Old tools stay registered alongside this one until Phase 3b retires them.
"""

from dataclasses import dataclass, field
from typing import Final

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    check_table_access,
    enforce_query_safety,
    mask_record,
)
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.utils import format_response, validate_identifier, validate_sys_id


TOOL_NAMES: list[str] = ["query"]

_VALID_AGGREGATE_OPS: Final[frozenset[str]] = frozenset({"count", "avg", "sum", "min", "max"})


# ---------------------------------------------------------------------------
# Parsers (parse-don't-validate: turn raw strings into trusted typed structs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AggregatePlan:
    """Parsed aggregation request, ready to hand to ``client.aggregate``."""

    count: bool = False
    avg_fields: list[str] = field(default_factory=list)
    sum_fields: list[str] = field(default_factory=list)
    min_fields: list[str] = field(default_factory=list)
    max_fields: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.count or self.avg_fields or self.sum_fields or self.min_fields or self.max_fields)


@dataclass(frozen=True)
class _LabelPair:
    """One ``field=label`` directive pulled from ``resolve_labels``."""

    field: str
    label: str


def _parse_aggregate(spec: str) -> _AggregatePlan | str:
    """Parse ``"count,avg:priority,sum:duration"`` into an ``_AggregatePlan``.

    Returns the plan on success, or an error message string on failure.
    Field names inside ``op:field`` tokens are validated as identifiers.
    """
    plan_count = False
    avg_fields: list[str] = []
    sum_fields: list[str] = []
    min_fields: list[str] = []
    max_fields: list[str] = []

    bucket_by_op: dict[str, list[str]] = {
        "avg": avg_fields,
        "sum": sum_fields,
        "min": min_fields,
        "max": max_fields,
    }

    for raw_token in spec.split(","):
        token = raw_token.strip()
        if not token:
            continue
        if token == "count":
            plan_count = True
            continue
        op, sep, target = token.partition(":")
        op = op.strip()
        target = target.strip()
        if not sep or not target:
            return (
                f"Invalid aggregate token {token!r}. "
                f"Expected 'count' or 'op:field' where op is one of {sorted(_VALID_AGGREGATE_OPS - {'count'})}."
            )
        if op not in bucket_by_op:
            return f"Unknown aggregate operation {op!r}. Valid operations: {sorted(_VALID_AGGREGATE_OPS)}."
        try:
            validate_identifier(target)
        except ValueError as exc:
            return f"Invalid field name in aggregate token {token!r}: {exc}"
        bucket_by_op[op].append(target)

    return _AggregatePlan(
        count=plan_count,
        avg_fields=avg_fields,
        sum_fields=sum_fields,
        min_fields=min_fields,
        max_fields=max_fields,
    )


def _parse_label_pairs(spec: str) -> list[_LabelPair] | str:
    """Parse ``"state=open,priority=high"`` into ``[_LabelPair(...), ...]``.

    Returns the list on success, or an error message string on failure.
    Field names are validated; labels are kept verbatim for ChoiceRegistry.
    """
    pairs: list[_LabelPair] = []
    for raw_chunk in spec.split(","):
        chunk = raw_chunk.strip()
        if not chunk:
            continue
        field_name, sep, label = chunk.partition("=")
        field_name = field_name.strip()
        label = label.strip()
        if not sep or not field_name or not label:
            return f"Invalid resolve_labels token {chunk!r}. Expected 'field=label' (e.g. 'state=open')."
        try:
            validate_identifier(field_name)
        except ValueError as exc:
            return f"Invalid field name in resolve_labels token {chunk!r}: {exc}"
        pairs.append(_LabelPair(field=field_name, label=label))
    return pairs


def _parse_csv(spec: str) -> list[str]:
    """Split a comma-separated list, trimming whitespace and dropping empties."""
    return [item.strip() for item in spec.split(",") if item.strip()]


def _join_query(existing: str, fragment: str) -> str:
    """Append ``fragment`` to an existing encoded query with the ``^`` separator."""
    if not existing:
        return fragment
    if not fragment:
        return existing
    return f"{existing}^{fragment}"


# ---------------------------------------------------------------------------
# resolve_labels application
# ---------------------------------------------------------------------------


async def _apply_resolve_labels(
    table: str,
    encoded_query: str,
    pairs: list[_LabelPair],
    choices: ChoiceRegistry,
) -> tuple[str, list[str]]:
    """Resolve each label via the registry and AND it into the encoded query.

    Emits a warning per pair where the registry returned the label unchanged
    *and* the label is non-numeric (suggesting it failed to resolve, rather
    than being a value the user passed directly).
    """
    warnings: list[str] = []
    augmented = encoded_query
    for pair in pairs:
        resolved = await choices.resolve(table, pair.field, pair.label)
        if resolved == pair.label and not pair.label.isdigit():
            warnings.append(
                f"resolve_labels: '{pair.field}={pair.label}' did not resolve via ChoiceRegistry; "
                f"using label verbatim as the filter value."
            )
        augmented = _join_query(augmented, f"{pair.field}={resolved}")
    return augmented, warnings


# ---------------------------------------------------------------------------
# Mode bodies
# ---------------------------------------------------------------------------


async def _run_sys_id_mode(
    table: str,
    sys_id: str,
    fields: str,
    display_values: bool,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    correlation_id: str,
) -> str:
    validate_sys_id(sys_id)

    field_list = _parse_csv(fields) or None
    if field_list:
        for name in field_list:
            validate_identifier(name)

    async with ServiceNowClient(settings, auth_provider) as client:
        record = await client.get_record(table, sys_id, fields=field_list, display_values=display_values)

    return format_response(data=mask_record(table, record), correlation_id=correlation_id)


async def _run_aggregate_mode(
    table: str,
    encoded_query: str,
    plan: _AggregatePlan,
    group_by: str,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    correlation_id: str,
    warnings: list[str],
) -> str:
    if group_by:
        validate_identifier(group_by)
    enforce_query_safety(table, encoded_query, None, settings)

    async with ServiceNowClient(settings, auth_provider) as client:
        result = await client.aggregate(
            table,
            encoded_query,
            group_by=group_by or None,
            avg_fields=plan.avg_fields or None,
            sum_fields=plan.sum_fields or None,
            min_fields=plan.min_fields or None,
            max_fields=plan.max_fields or None,
        )

    return format_response(data=result, correlation_id=correlation_id, warnings=warnings or None)


async def _run_query_mode(
    table: str,
    encoded_query: str,
    fields: str,
    limit: int,
    offset: int,
    order_by: str,
    display_values: bool,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    correlation_id: str,
    warnings: list[str],
) -> str:
    field_list = _parse_csv(fields) or None
    if field_list:
        for name in field_list:
            validate_identifier(name)

    order_field = order_by.lstrip("-") if order_by else ""
    if order_field:
        validate_identifier(order_field)

    safety = enforce_query_safety(table, encoded_query, limit, settings)
    effective_limit = safety["limit"]
    if effective_limit < limit:
        warnings.append(f"Limit capped at {effective_limit}")

    async with ServiceNowClient(settings, auth_provider) as client:
        result = await client.query_records(
            table,
            encoded_query,
            fields=field_list,
            limit=effective_limit,
            offset=offset,
            order_by=order_by or None,
            display_values=display_values,
        )

    masked = [mask_record(table, record) for record in result["records"]]
    return format_response(
        data=masked,
        correlation_id=correlation_id,
        pagination={"offset": offset, "limit": effective_limit, "total": result["count"]},
        warnings=warnings or None,
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
) -> None:
    """Register the unified ``query`` tool on the MCP server.

    Mirrors the domain-tool registration signature so ``server.py`` can inject
    the shared ``ChoiceRegistry``. ``choices`` may be ``None`` in tests; when
    it is, ``resolve_labels`` degrades to passthrough with a warning.
    """

    @mcp.tool()
    @tool_handler
    async def query(
        table: str,
        sys_id: str = "",
        encoded_query: str = "",
        fields: str = "",
        limit: int = 20,
        offset: int = 0,
        order_by: str = "",
        display_values: bool = False,
        aggregate: str = "",
        group_by: str = "",
        resolve_labels: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Read records, aggregates, or a single record from any ServiceNow table.

        Args:
            table: ServiceNow table name (e.g. 'incident').
            sys_id: When set, fetch a single record by sys_id (other filter args ignored
                except `fields` and `display_values`).
            encoded_query: ServiceNow encoded query string (e.g. 'state=1^priority=2').
                Empty = no filter.
            fields: Comma-separated field list. Empty returns all (subject to masking).
            limit: Max rows (1-max_row_limit). Default 20.
            offset: Pagination offset.
            order_by: Field name; prefix with '-' for descending (e.g. '-sys_created_on').
            display_values: True returns display_value form for reference and choice fields.
            aggregate: Comma-separated aggregations: 'count', 'avg:<field>', 'sum:<field>',
                'min:<field>', 'max:<field>'. When set, returns aggregate result instead of rows.
            group_by: Field to group aggregate results by (aggregate mode only).
            resolve_labels: Comma-separated 'field=label' pairs (e.g. 'state=open,priority=high').
                Each label is resolved via ChoiceRegistry to its underlying value, then ANDed
                into encoded_query as 'field=value'.
        """
        # --- Mode conflict guards (early-exit) ----------------------------
        if sys_id and aggregate:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="Cannot combine sys_id with aggregate; sys_id mode fetches a single record.",
            )
        if sys_id and group_by:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="Cannot combine sys_id with group_by; sys_id mode fetches a single record.",
            )
        if group_by and not aggregate:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="group_by requires aggregate to be set (aggregate mode only).",
            )

        # --- Shared policy gates ------------------------------------------
        validate_identifier(table)
        check_table_access(table)

        # --- sys_id mode (no scan, no resolve_labels) ---------------------
        if sys_id:
            return await _run_sys_id_mode(
                table=table,
                sys_id=sys_id,
                fields=fields,
                display_values=display_values,
                settings=settings,
                auth_provider=auth_provider,
                correlation_id=correlation_id,
            )

        # --- resolve_labels: augment encoded_query before safety check ----
        warnings: list[str] = []
        augmented_query = encoded_query
        if resolve_labels:
            pairs_or_error = _parse_label_pairs(resolve_labels)
            if isinstance(pairs_or_error, str):
                return format_response(data=None, correlation_id=correlation_id, status="error", error=pairs_or_error)
            if choices is None:
                warnings.append(
                    "resolve_labels supplied but ChoiceRegistry is unavailable; treating each label as a literal value."
                )
                for pair in pairs_or_error:
                    augmented_query = _join_query(augmented_query, f"{pair.field}={pair.label}")
            else:
                augmented_query, label_warnings = await _apply_resolve_labels(
                    table=table,
                    encoded_query=augmented_query,
                    pairs=pairs_or_error,
                    choices=choices,
                )
                warnings.extend(label_warnings)

        # --- aggregate mode -----------------------------------------------
        if aggregate:
            plan_or_error = _parse_aggregate(aggregate)
            if isinstance(plan_or_error, str):
                return format_response(data=None, correlation_id=correlation_id, status="error", error=plan_or_error)
            if plan_or_error.is_empty:
                return format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error="aggregate must contain at least one operation (count, avg:<f>, sum:<f>, min:<f>, max:<f>).",
                )
            return await _run_aggregate_mode(
                table=table,
                encoded_query=augmented_query,
                plan=plan_or_error,
                group_by=group_by,
                settings=settings,
                auth_provider=auth_provider,
                correlation_id=correlation_id,
                warnings=warnings,
            )

        # --- query mode (default) -----------------------------------------
        return await _run_query_mode(
            table=table,
            encoded_query=augmented_query,
            fields=fields,
            limit=limit,
            offset=offset,
            order_by=order_by,
            display_values=display_values,
            settings=settings,
            auth_provider=auth_provider,
            correlation_id=correlation_id,
            warnings=warnings,
        )
