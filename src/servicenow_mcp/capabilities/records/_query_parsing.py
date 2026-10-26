"""Parse raw unified-query arguments into trusted request values."""

import re
from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.validation import validate_identifier

_VALID_AGGREGATE_OPS: Final[frozenset[str]] = frozenset(
    {"count", "avg", "sum", "min", "max"}
)
_JOIN_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^(?:NQ|OR|EQ)")
_ORDER_PREFIXES: Final[tuple[str, ...]] = ("ORDERBYDESC", "ORDERBY")
_FIELD_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*"
)
_COMPACT_RECORD_FIELDS: Final[tuple[str, ...]] = ("sys_id", "sys_updated_on")


class AggregatePlan(BaseModel):
    """Parsed aggregation request for the ServiceNow Stats API."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: bool = False
    avg_fields: list[str] = Field(default_factory=list)
    sum_fields: list[str] = Field(default_factory=list)
    min_fields: list[str] = Field(default_factory=list)
    max_fields: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Return whether the plan contains no aggregate operation."""
        return not (
            self.count
            or self.avg_fields
            or self.sum_fields
            or self.min_fields
            or self.max_fields
        )


class LabelPair(BaseModel):
    """One parsed ``field=label`` choice-resolution directive."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", hide_input_in_errors=True
    )

    field: str = Field(description="Choice field element name on the query table.")
    label: str = Field(description="Displayed choice label to resolve and filter by.")

    @model_validator(mode="after")
    def check_field(self) -> LabelPair:
        validate_identifier(self.field)
        if not self.label:
            raise ValueError("A choice label must not be empty.")
        return self


class AggregateOperation(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", hide_input_in_errors=True
    )
    operation: Literal["count", "avg", "sum", "min", "max"] = Field(
        description="Aggregate to calculate; count omits field, others require it."
    )
    field: str | None = Field(
        default=None,
        description="Numeric or comparable table field for avg, sum, min, or max.",
    )

    @model_validator(mode="after")
    def check_operation(self) -> AggregateOperation:
        if self.operation == "count":
            if self.field is not None:
                raise ValueError("count must not specify a field.")
        elif self.field is None:
            raise ValueError("The aggregate operation requires a field.")
        else:
            validate_identifier(self.field)
        return self


class Projection(BaseModel):
    """A parsed ServiceNow field projection."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    fields: list[str] | None


def parse_aggregate(spec: list[AggregateOperation]) -> AggregatePlan:
    """Build a Stats API plan from native aggregate operations."""
    plan_count = False
    buckets: dict[str, list[str]] = {"avg": [], "sum": [], "min": [], "max": []}

    for item in spec:
        if item.operation == "count":
            plan_count = True
            continue
        assert item.field is not None
        validate_identifier(item.field)
        buckets[item.operation].append(item.field)

    return AggregatePlan(
        count=plan_count,
        avg_fields=buckets["avg"],
        sum_fields=buckets["sum"],
        min_fields=buckets["min"],
        max_fields=buckets["max"],
    )


def parse_label_pairs(spec: list[LabelPair]) -> list[LabelPair]:
    """Check the native field/label predicates."""
    for pair in spec:
        validate_identifier(pair.field)
    return spec


def parse_projection(
    fields: list[str] | Literal["*"] | None, *, compact_default: bool
) -> Projection:
    """Parse a field projection, including sys_id for explicit field lists."""
    if fields == "*":
        return Projection(fields=None)

    requested = fields or []
    if "*" in requested:
        raise CapabilityError("fields='*' must be used alone.")
    if not requested:
        if not compact_default:
            raise CapabilityError(
                "fields is required for list mode. Use a native field array or '*' for all fields.",
                missing_arguments=["fields"],
            )
        requested = list(_COMPACT_RECORD_FIELDS)

    for name in requested:
        validate_identifier(name)

    projected = list(dict.fromkeys(["sys_id", *requested]))
    return Projection(fields=projected)


def parse_group_fields(group_by: list[str] | None) -> list[str]:
    """Parse and validate the aggregate grouping field list."""
    group_fields = group_by or []
    for group_field in group_fields:
        validate_identifier(group_field)
    return group_fields


def extract_query_fields(encoded_query: str) -> list[str]:
    """Extract unique complete field paths from supported encoded-query clauses."""
    fields: list[str] = []
    seen: set[str] = set()
    for raw_clause in encoded_query.split("^"):
        if not raw_clause:
            continue
        token = _extract_clause_field(raw_clause)
        if not token:
            continue
        if token not in seen:
            seen.add(token)
            fields.append(token)
    return fields


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
