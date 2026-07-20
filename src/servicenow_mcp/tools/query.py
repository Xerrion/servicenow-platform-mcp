"""Unified ``query`` tool: read records, aggregates, or a single record.

This module collapses the read-side surface (``table_query``, ``table_aggregate``,
``record_get``) into one tool that dispatches on its arguments. Three mutually
exclusive modes, in precedence order:

1. ``sys_id`` set    -> single-record fetch (no scan, no pagination).
2. ``aggregate`` set -> Stats API call, optionally grouped (no row masking).
3. otherwise         -> paginated record query with field masking.

Old tools stay registered alongside this one until Phase 3b retires them.
"""

import logging
import re
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


logger = logging.getLogger(__name__)

TOOL_NAMES: list[str] = ["query"]

_VALID_AGGREGATE_OPS: Final[frozenset[str]] = frozenset({"count", "avg", "sum", "min", "max"})

# Universal system fields present on every table. Never warn on these even if a
# dictionary fetch comes back incomplete.
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

# Clause-join keyword that prefixes a condition after a '^' split (e.g. the
# 'OR' in 'a=1^ORb=2', or 'NQ' for a new query). Stripped before field parsing.
_JOIN_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^(?:NQ|OR|EQ)")

# Order directives carry a bare field name and no operator/value.
_ORDER_PREFIXES: Final[tuple[str, ...]] = ("ORDERBYDESC", "ORDERBY")

# Leading field token of a condition: a lowercase element name, optionally
# dot-walked. Element names are always lowercase, so this naturally stops at
# uppercase textual operators (LIKE, STARTSWITH, ISEMPTY, ...) and at symbolic
# operators (=, !=, >, <).
_FIELD_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*")


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
# Encoded-query field validation (advisory)
# ---------------------------------------------------------------------------


def _order_field(clause: str) -> str | None:
    """Return the field of an ORDERBY clause, or ``None`` when not one.

    Returns the empty string for a malformed order directive (no field token),
    which the caller treats as "nothing to validate".
    """
    for prefix in _ORDER_PREFIXES:
        if clause.startswith(prefix):
            match = _FIELD_TOKEN_RE.match(clause[len(prefix) :])
            return match.group(0) if match else ""
    return None


def _condition_field(clause: str) -> str:
    """Return the leading field token of a ``field<op>value`` clause.

    Empty string when the clause has no recognizable leading field or no
    trailing operator (a bare lowercase word is not a trustworthy condition).
    """
    match = _FIELD_TOKEN_RE.match(clause)
    if not match:
        return ""
    if match.end() == len(clause):
        return ""
    return match.group(0)


def _extract_query_fields(encoded_query: str) -> list[str]:
    """Extract the root field names an encoded query filters on.

    Best-effort parse: split on ``^``, strip clause-join keywords, handle
    ORDERBY directives, and read the leading lowercase element token of each
    condition. Dot-walked references contribute their root segment only -
    traversing reference fields is out of scope. Clauses whose leading token is
    not a recognizable field (exotic operators, subqueries, uppercase keywords)
    are skipped rather than guessed at, which keeps false positives out of the
    warning path. Order is preserved and duplicates removed.
    """
    roots: list[str] = []
    seen: set[str] = set()
    for raw_clause in encoded_query.split("^"):
        if not raw_clause:
            continue
        order_field = _order_field(raw_clause)
        if order_field is not None:
            token = order_field
        else:
            token = _condition_field(_JOIN_PREFIX_RE.sub("", raw_clause, count=1))
        if not token:
            continue
        root = token.split(".", 1)[0]
        if root not in seen:
            seen.add(root)
            roots.append(root)
    return roots


async def _validate_query_fields(
    table: str,
    encoded_query: str,
    dictionary: DictionaryRegistry,
) -> list[str]:
    """Warn when an encoded query references fields absent from the table.

    ServiceNow silently ignores conditions on non-existent columns, so a typo
    like ``name=Foo`` on a table without a ``name`` column returns the *entire*
    table instead of an error. This surfaces that footgun as a warning without
    blocking the query.

    The check is advisory and must never break a working query: when the
    dictionary cannot be loaded (network, auth, or an unknown table) it is
    skipped. An empty field set is also treated as "could not introspect" and
    skipped, so a table we failed to read never produces spurious warnings.
    """
    candidates = _extract_query_fields(encoded_query)
    if not candidates:
        return []

    try:
        known = await dictionary.get_all_fields(table)
    except Exception:  # advisory only; a lookup failure must not fail the query
        logger.warning("field validation skipped for table=%s: dictionary lookup failed", table, exc_info=True)
        return []

    known_names = {entry.name for entry in known}
    if not known_names:
        return []

    unknown = [name for name in candidates if name not in known_names and name not in _UNIVERSAL_FIELDS]
    if not unknown:
        return []

    field_list = ", ".join(unknown)
    return [
        f"Query references field(s) not found on table '{table}': {field_list}. "
        "ServiceNow silently ignores conditions on unknown fields, so the result is "
        "NOT filtered by them. Verify the field names against the table dictionary."
    ]


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
# Mode-conflict + per-mode validation helpers for the ``query`` closure
# ---------------------------------------------------------------------------


def _err(correlation_id: str, message: str) -> str:
    """Return a serialized error envelope with the given message."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


def _check_mode_conflicts(sys_id: str, aggregate: str, group_by: str, correlation_id: str) -> str | None:
    """Validate that the requested mode combination is legal.

    Three modes are mutually exclusive: sys_id-fetch, aggregate, and default
    query. ``group_by`` is only meaningful in aggregate mode. Returns an error
    envelope on conflict, otherwise ``None``. Runs BEFORE table validation so
    a bad combination surfaces as a mode error, not a table error.
    """
    if sys_id and aggregate:
        return _err(correlation_id, "Cannot combine sys_id with aggregate; sys_id mode fetches a single record.")
    if sys_id and group_by:
        return _err(correlation_id, "Cannot combine sys_id with group_by; sys_id mode fetches a single record.")
    if group_by and not aggregate:
        return _err(correlation_id, "group_by requires aggregate to be set (aggregate mode only).")
    return None


async def _apply_resolve_labels_block(
    table: str,
    encoded_query: str,
    resolve_labels: str,
    choices: ChoiceRegistry | None,
    correlation_id: str,
) -> tuple[str, list[str]] | str:
    """Parse ``resolve_labels`` and fold the resolved pairs into ``encoded_query``.

    Returns ``(augmented_query, warnings)`` on success or an error envelope
    string on parse failure. When ``choices`` is ``None`` (e.g. in tests),
    each label is treated as a literal value with a single passthrough
    warning, preserving the historic degraded-mode behaviour.
    """
    pairs_or_error = _parse_label_pairs(resolve_labels)
    if isinstance(pairs_or_error, str):
        return _err(correlation_id, pairs_or_error)

    if choices is None:
        warnings = [
            "resolve_labels supplied but ChoiceRegistry is unavailable; treating each label as a literal value."
        ]
        augmented = encoded_query
        for pair in pairs_or_error:
            augmented = _join_query(augmented, f"{pair.field}={pair.label}")
        return augmented, warnings

    augmented, label_warnings = await _apply_resolve_labels(
        table=table,
        encoded_query=encoded_query,
        pairs=pairs_or_error,
        choices=choices,
    )
    return augmented, label_warnings


def _validate_aggregate_block(aggregate: str, correlation_id: str) -> _AggregatePlan | str:
    """Parse and validate the ``aggregate`` spec.

    Returns the parsed ``_AggregatePlan`` on success or an error envelope
    string on either parse failure or an empty operation set.
    """
    plan_or_error = _parse_aggregate(aggregate)
    if isinstance(plan_or_error, str):
        return _err(correlation_id, plan_or_error)
    if plan_or_error.is_empty:
        return _err(
            correlation_id,
            "aggregate must contain at least one operation (count, avg:<f>, sum:<f>, min:<f>, max:<f>).",
        )
    return plan_or_error


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
    it is, ``resolve_labels`` degrades to passthrough with a warning. The
    ``dictionary`` registry, when supplied, drives advisory validation of the
    fields referenced in ``encoded_query``.
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
        # --- Mode conflict guards (early-exit, before table validation) ---
        conflict = _check_mode_conflicts(sys_id, aggregate, group_by, correlation_id)
        if conflict:
            return conflict

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
            result = await _apply_resolve_labels_block(table, augmented_query, resolve_labels, choices, correlation_id)
            if isinstance(result, str):
                return result
            augmented_query, label_warnings = result
            warnings.extend(label_warnings)

        # --- advisory field validation (catches silently-dropped filters) -
        if dictionary is not None:
            warnings.extend(await _validate_query_fields(table, augmented_query, dictionary))

        # --- aggregate mode -----------------------------------------------
        if aggregate:
            plan = _validate_aggregate_block(aggregate, correlation_id)
            if isinstance(plan, str):
                return plan
            return await _run_aggregate_mode(
                table=table,
                encoded_query=augmented_query,
                plan=plan,
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
