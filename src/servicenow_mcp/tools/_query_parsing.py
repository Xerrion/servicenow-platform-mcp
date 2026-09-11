"""Parse raw unified-query arguments into trusted request values."""

import re
from dataclasses import dataclass, field
from typing import Final

from servicenow_mcp.validation import validate_identifier


_VALID_AGGREGATE_OPS: Final[frozenset[str]] = frozenset({"count", "avg", "sum", "min", "max"})
_JOIN_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^(?:NQ|OR|EQ)")
_ORDER_PREFIXES: Final[tuple[str, ...]] = ("ORDERBYDESC", "ORDERBY")
_FIELD_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*")
_COMPACT_RECORD_FIELDS: Final[tuple[str, ...]] = ("sys_id", "sys_updated_on")


@dataclass(frozen=True)
class AggregatePlan:
    """Parsed aggregation request for the ServiceNow Stats API."""

    count: bool = False
    avg_fields: list[str] = field(default_factory=list)
    sum_fields: list[str] = field(default_factory=list)
    min_fields: list[str] = field(default_factory=list)
    max_fields: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Return whether the plan contains no aggregate operation."""
        return not (self.count or self.avg_fields or self.sum_fields or self.min_fields or self.max_fields)


@dataclass(frozen=True)
class LabelPair:
    """One parsed ``field=label`` choice-resolution directive."""

    field: str
    label: str


@dataclass(frozen=True)
class Projection:
    """A parsed ServiceNow field projection and its response metadata."""

    fields: list[str] | None
    selection: dict[str, object]


def parse_aggregate(spec: str) -> AggregatePlan | str:
    """Parse an aggregate specification, returning an error message on failure."""
    plan_count = False
    buckets: dict[str, list[str]] = {"avg": [], "sum": [], "min": [], "max": []}

    for raw_token in spec.split(","):
        token = raw_token.strip()
        if not token:
            continue
        if token == "count":
            plan_count = True
            continue
        op, separator, target = token.partition(":")
        op = op.strip()
        target = target.strip()
        if not separator or not target:
            return (
                f"Invalid aggregate token {token!r}. "
                f"Expected 'count' or 'op:field' where op is one of {sorted(_VALID_AGGREGATE_OPS - {'count'})}."
            )
        if op not in buckets:
            return f"Unknown aggregate operation {op!r}. Valid operations: {sorted(_VALID_AGGREGATE_OPS)}."
        try:
            validate_identifier(target)
        except ValueError as exc:
            return f"Invalid field name in aggregate token {token!r}: {exc}"
        buckets[op].append(target)

    return AggregatePlan(
        count=plan_count,
        avg_fields=buckets["avg"],
        sum_fields=buckets["sum"],
        min_fields=buckets["min"],
        max_fields=buckets["max"],
    )


def parse_label_pairs(spec: str) -> list[LabelPair] | str:
    """Parse choice labels, returning an error message on malformed input."""
    pairs: list[LabelPair] = []
    for raw_chunk in spec.split(","):
        chunk = raw_chunk.strip()
        if not chunk:
            continue
        field_name, separator, label = chunk.partition("=")
        field_name = field_name.strip()
        label = label.strip()
        if not separator or not field_name or not label:
            return f"Invalid resolve_labels token {chunk!r}. Expected 'field=label' (e.g. 'state=open')."
        try:
            validate_identifier(field_name)
        except ValueError as exc:
            return f"Invalid field name in resolve_labels token {chunk!r}: {exc}"
        pairs.append(LabelPair(field=field_name, label=label))
    return pairs


def parse_projection(fields: str, *, compact_default: bool) -> Projection | str:
    """Parse a field projection and describe its response contract."""
    if fields.strip() == "*":
        return Projection(
            fields=None,
            selection={
                "mode": "all",
                "requested_fields": "*",
                "returned_fields": "all fields returned by ServiceNow",
                "sys_id_added": False,
            },
        )

    requested = [item.strip() for item in fields.split(",") if item.strip()]
    if "*" in requested:
        return "fields='*' must be used alone."
    if not requested:
        if not compact_default:
            return "fields is required for list mode. Use a comma-separated projection or fields='*' for all fields."
        requested = list(_COMPACT_RECORD_FIELDS)
        mode = "compact"
    else:
        mode = "explicit"

    for name in requested:
        validate_identifier(name)

    sys_id_added = "sys_id" not in requested
    projected = list(dict.fromkeys(["sys_id", *requested]))
    return Projection(
        fields=projected,
        selection={
            "mode": mode,
            "requested_fields": None if mode == "compact" else requested,
            "returned_fields": projected,
            "sys_id_added": sys_id_added,
        },
    )


def parse_group_fields(group_by: str) -> list[str]:
    """Parse and validate the aggregate grouping field list."""
    group_fields = [item.strip() for item in group_by.split(",")] if group_by else []
    for group_field in group_fields:
        validate_identifier(group_field)
    return group_fields


def extract_query_fields(encoded_query: str) -> list[str]:
    """Extract unique root field names from supported encoded-query clauses."""
    roots: list[str] = []
    seen: set[str] = set()
    for raw_clause in encoded_query.split("^"):
        if not raw_clause:
            continue
        token = _extract_clause_field(raw_clause)
        if not token:
            continue
        root = token.split(".", 1)[0]
        if root not in seen:
            seen.add(root)
            roots.append(root)
    return roots


def join_query(existing: str, fragment: str) -> str:
    """Append an encoded-query fragment with the required separator."""
    if not existing:
        return fragment
    if not fragment:
        return existing
    return f"{existing}^{fragment}"


def _extract_clause_field(clause: str) -> str:
    for prefix in _ORDER_PREFIXES:
        if clause.startswith(prefix):
            match = _FIELD_TOKEN_RE.match(clause[len(prefix) :])
            return match.group(0) if match else ""

    condition = _JOIN_PREFIX_RE.sub("", clause, count=1)
    match = _FIELD_TOKEN_RE.match(condition)
    if not match or match.end() == len(condition):
        return ""
    return match.group(0)
