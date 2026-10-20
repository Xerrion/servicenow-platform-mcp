"""Typed Studio bindings and producer visibility. No platform writes or display-order references."""

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import tree
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class ValueType:
    kind: str
    reference: str = ""

    @property
    def many(self) -> bool:
        return self.kind.startswith("array.") or self.kind in {"records", "glide_list"}


def spec(item: dict[str, Any]) -> dict[str, Any]:
    parameter = item.get("parameter")
    return parameter if isinstance(parameter, dict) else item


def value_type(item: dict[str, Any]) -> ValueType:
    parameter = spec(item)
    kind = parameter.get("type") or item.get("type") or item.get("internalType")
    if not isinstance(kind, str) or not kind:
        raise CapabilityError(
            "Input or output type metadata is unavailable.", "UNAVAILABLE"
        )
    return ValueType(kind, str(parameter.get("reference") or ""))


def named(items: Any, name: str) -> dict[str, Any]:
    if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
        raise CapabilityError("Producer fields are incomplete.", "UNAVAILABLE")
    matches = [i for i in items if i.get("name") == name]
    if len(matches) != 1:
        raise CapabilityError("The referenced producer field is absent or ambiguous.")
    return matches[0]


def children(item: dict[str, Any]) -> list[dict[str, Any]]:
    result = spec(item).get("children", [])
    if not isinstance(result, list) or any(not isinstance(i, dict) for i in result):
        raise CapabilityError("The complex field schema is incomplete.", "UNAVAILABLE")
    return result


def output_assignment_value(row: dict[str, Any]) -> Any:
    """Read only the documented element-mapping fallback for output rows."""
    # Studio GET puts an output binding in displayValue with value blank
    # (bundle:150470-150479). False and zero remain actual values.
    if row.get("value") == "" and "displayValue" in row:
        return row["displayValue"]
    return row.get("value")


def set_value(item: dict[str, Any], value: Any) -> None:
    """Serialize native complex values using Studio's observed children/value contract."""
    parameter = spec(item)
    if parameter.get("readonly") is True or parameter.get("readOnly") is True:
        raise CapabilityError("The input is read-only.")
    if item.get("scriptActive"):
        raise CapabilityError(
            "Scripted inputs cannot be replaced by this adapter.", "UNAVAILABLE"
        )
    declared = children(item)
    if isinstance(value, str) and ("{{" in value or "}}" in value):
        unmatched = tree.PILL.sub("", value)
        if "{{" in unmatched or "}}" in unmatched:
            raise CapabilityError("The input contains a malformed pill reference.")
    if isinstance(value, dict | list):
        kind = value_type(item).kind
        current_children = item.get("children", [])
        if not isinstance(current_children, list) or any(
            not isinstance(i, dict) for i in current_children
        ):
            raise CapabilityError(
                "Existing complex input fields are incomplete.", "UNAVAILABLE"
            )
        if isinstance(value, dict):
            if kind != "object":
                raise CapabilityError("An object value requires an Object input.")
            unknown = set(value) - {i.get("name") for i in declared}
            if unknown:
                raise CapabilityError("The object contains undeclared fields.")
            values = []
            for child in declared:
                child_value = value.get(child["name"], child.get("defaultValue", ""))
                existing = next(
                    (i for i in current_children if i.get("name") == child["name"]), {}
                )
                binding = {
                    **copy.deepcopy(child),
                    **copy.deepcopy(existing),
                    "parameter": copy.deepcopy(child),
                }
                set_value(binding, child_value)
                values.append(binding)
        else:
            if not kind.startswith("array.") or len(declared) != 1:
                raise CapabilityError(
                    "An array value requires one declared Array item schema.",
                    "UNAVAILABLE",
                )
            values = []
            for index, element in enumerate(value):
                child = declared[0]
                existing = (
                    current_children[index] if index < len(current_children) else {}
                )
                binding = {
                    **copy.deepcopy(child),
                    **copy.deepcopy(existing),
                    "parameter": copy.deepcopy(child),
                }
                set_value(binding, element)
                values.append(binding)
        item.update(value="", displayValue="", children=values)
        return
    maximum = parameter.get("maxsize") or item.get("maxsize")
    if isinstance(value, str) and maximum and len(value) > int(maximum):
        raise CapabilityError("The input exceeds its maximum length.")
    if parameter.get("mandatory") is True and value in ("", None):
        raise CapabilityError("A mandatory input is empty.")
    # Static values and references use the same type boundary.
    # Literal pill expressions are checked after topology edits.
    if not tree.pills(value) and value not in ("", None):
        kind = value_type(item).kind
        if kind not in {
            "boolean",
            "integer",
            "long",
            "string",
            "choice",
            "reference",
            "document_id",
            "records",
            "glide_list",
            "table_name",
            "json",
            "object",
        } and not kind.startswith("array."):
            raise CapabilityError(
                "This input type requires a family-specific value adapter.",
                "UNAVAILABLE",
            )
        if kind == "object" or kind.startswith("array."):
            raise CapabilityError(
                "Complex inputs require native objects or arrays, or one typed pill."
            )
        if kind == "boolean" and not isinstance(value, bool):
            raise CapabilityError("Boolean inputs require JSON booleans.")
        if kind in {"integer", "long"} and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            raise CapabilityError("Integer inputs require JSON integers.")
        if kind in {
            "string",
            "choice",
            "reference",
            "document_id",
            "records",
            "glide_list",
            "table_name",
            "json",
        } and not isinstance(value, str):
            raise CapabilityError("This input requires a string value.")
        if kind == "json":
            import json

            try:
                json.loads(value)
            except ValueError:
                raise CapabilityError("JSON inputs require valid JSON text.") from None
        if kind == "table_name":
            from servicenow_mcp.validation import validate_identifier

            validate_identifier(value)
        if kind in {"reference", "document_id"}:
            validate_sys_id(value)
        if kind in {"records", "glide_list"}:
            for identity in value.split(","):
                validate_sys_id(identity)
        choices = parameter.get("choices") or []
        if kind == "choice" and choices and value not in [i["value"] for i in choices]:
            raise CapabilityError("The value is not an allowed choice.")
    item["value"] = value
    item["displayValue"] = "" if tree.pills(value) else value
    if declared:
        item["children"] = []


def bindings(items: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Visit values and nested serialized children without interpreting schema defaults as values."""
    for item in items:
        yield item
        nested = item.get("children", [])
        if not isinstance(nested, list) or any(not isinstance(i, dict) for i in nested):
            raise CapabilityError("Complex input values are malformed.")
        yield from bindings(nested)


class Scope:
    """Conservative dominance across conditional, looping, parallel, and error regions."""

    def __init__(self, roots: list[tree.Step]) -> None:
        self.steps = {step.uid: step for step in tree.walk(roots)}
        self.parents: dict[str, tuple[tree.Step, ...]] = {}
        self.positions = {
            step.uid: index for index, step in enumerate(tree.walk(roots))
        }

        def visit(siblings: list[tree.Step], ancestors: tuple[tree.Step, ...]) -> None:
            for step in siblings:
                self.parents[step.uid] = ancestors
                visit(step.children, (*ancestors, step))

        visit(roots, ())

    def visible(
        self, producer: tree.Step, consumer: tree.Step, path: list[str]
    ) -> None:
        producer_region = self.parents[producer.uid]
        consumer_region = self.parents[consumer.uid]
        if producer is consumer:
            raise CapabilityError("An input cannot reference its own step output.")
        if producer.raw.get("errorHandlingInstance", False) != consumer.raw.get(
            "errorHandlingInstance", False
        ):
            raise CapabilityError(
                "A reference cannot cross the top-level error-handler boundary."
            )
        if (
            producer.kind == "subflow"
            and producer.raw.get("waitForCompletion", {}).get("value") is not True
        ):
            raise CapabilityError(
                "Outputs are unavailable from a subflow that does not wait for completion."
            )
        # Loop item outputs exist inside the loop before any child, never outside it.
        if producer.logic_type == "FOREACH":
            if any(p.uid == producer.uid for p in consumer_region):
                return
            raise CapabilityError("A For Each item is visible only inside its loop.")
        if self.positions[producer.uid] >= self.positions[consumer.uid]:
            raise CapabilityError("The producer must precede the consumer.")
        if tuple(s.uid for s in producer_region) == tuple(
            s.uid for s in consumer_region[: len(producer_region)]
        ):
            return
        # Parallel branch outputs become available after the join, but never to another path.
        missing = [
            region
            for region in producer_region
            if region.uid not in {r.uid for r in consumer_region}
        ]
        if [region.logic_type for region in missing] == ["PARALLEL", "PARALLELBLOCK"]:
            # No conditional or loop can intervene before the join.
            return
        # A Catch can inspect action status from its paired Try, not values from an incomplete Try path.
        if (
            path[:1] == ["__action_status__"]
            and len(missing) == 1
            and missing[0].logic_type == "TRY"
        ):
            catches = [r for r in consumer_region if r.logic_type == "CATCH"]
            if catches and catches[0].raw.get("connectedTo") == missing[0].uid:
                return
        raise CapabilityError(
            "The producer does not dominate this input across branch, loop, parallel, or error regions.",
        )


def compatible(source: ValueType, target: ValueType, *, interpolated: bool) -> None:
    if interpolated:
        if (
            target.kind not in {"string", "json", "conditions"}
            or source.many
            or source.kind == "object"
        ):
            raise CapabilityError(
                "Only scalar values can be interpolated into a text input."
            )
        return
    if source.many != target.many:
        raise CapabilityError("The pill and input have different cardinality.")
    aliases = {"choice": "string", "document_id": "reference"}
    if aliases.get(source.kind, source.kind) != aliases.get(target.kind, target.kind):
        raise CapabilityError("The pill type is incompatible with the input type.")
    if target.reference and not source.reference:
        raise CapabilityError(
            "The producer's reference table is unavailable.", "UNAVAILABLE"
        )
