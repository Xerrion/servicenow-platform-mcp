"""Typed Studio bindings and producer visibility. No platform writes or display-order references."""

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import declarations, tree, widgets
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class ValueType:
    kind: str
    reference: str = ""

    @property
    def many(self) -> bool:
        return self.kind.startswith("array.") or self.kind in {
            "array",
            "records",
            "glide_list",
        }


def spec(item: dict[str, Any]) -> dict[str, Any]:
    parameter = item.get("parameter")
    return parameter if isinstance(parameter, dict) else item


def default_value(parameter: dict[str, Any]) -> Any:
    """Native getValueOrDefault:235798-235805 preserves every present value."""
    return (
        parameter["value"]
        if "value" in parameter
        else parameter.get("defaultValue", "")
    )


def catalog_schema(item: dict[str, Any]) -> dict[str, Any]:
    """Read a catalog producer's native schema view without changing its wire DTO."""
    from servicenow_mcp.capabilities.flows.catalog_schema import view

    return view(item)


def value_type(item: dict[str, Any]) -> ValueType:
    parameter = spec(item)
    kind = parameter.get("type") or item.get("type") or item.get("internalType")
    if not isinstance(kind, str) or not kind:
        raise CapabilityError(
            "Input or output type metadata is unavailable.", "UNAVAILABLE"
        )
    reference = (
        declarations.reference_table(parameter)
        if kind == "array.reference"
        else str(parameter.get("reference") or "")
    )
    return ValueType(kind, reference)


def named(items: Any, name: str) -> dict[str, Any]:
    if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
        raise CapabilityError("Producer fields are incomplete.", "UNAVAILABLE")
    matches = [i for i in items if i.get("name") == name]
    if len(matches) != 1:
        raise CapabilityError("The referenced producer field is absent or ambiguous.")
    return matches[0]


def children(item: dict[str, Any]) -> list[dict[str, Any]]:
    parameter = spec(item)
    kind = parameter.get("type")
    if (
        isinstance(kind, str)
        and kind.startswith("array.")
        and kind not in declarations.ARRAYS
    ):
        raise CapabilityError(
            "This Array item type requires another schema adapter.", "UNAVAILABLE"
        )
    if kind in declarations.ARRAYS and any(
        "child_name" in parameter.get(key, {})
        for key in ("attributes", "fieldFacetMap")
    ):
        result = declarations.unpack(parameter, recursive=False)["children"]
    else:
        result = parameter.get("children", [])
    if not isinstance(result, list) or any(not isinstance(i, dict) for i in result):
        raise CapabilityError("The complex field schema is incomplete.", "UNAVAILABLE")
    if kind in declarations.ARRAYS and (
        len(result) != 1
        or not isinstance(result[0].get("name"), str)
        or not result[0]["name"]
        or value_type(result[0]).kind != declarations.ARRAYS[kind]
        or kind != "array.object"
        and result[0].get("children")
    ):
        raise CapabilityError(
            "Array schemas require exactly one matching native item declaration.",
            "UNAVAILABLE",
        )
    if kind == "array.choice":
        declarations.choices(parameter)
        if any(
            key in spec(result[0])
            and key in parameter
            and spec(result[0])[key] != parameter[key]
            for key in declarations.CHOICE_FIELDS
        ):
            raise CapabilityError(
                "Array.Choice item metadata contradicts the parent schema."
            )
        result = copy.deepcopy(result)
        for key in declarations.CHOICE_FIELDS:
            if key in parameter:
                spec(result[0])[key] = copy.deepcopy(parameter[key])
    if kind == "array.reference":
        table = declarations.reference_table(parameter)
        if any(
            parameter.get(key) for key in ("dynamic", "data_structure", "dependent_on")
        ):
            raise CapabilityError(
                "Dynamic Array.Reference schemas require another adapter.",
                "UNAVAILABLE",
            )
        if (
            len(result) != 1
            or not isinstance(result[0].get("name"), str)
            or not result[0]["name"]
            or value_type(result[0]).kind != "reference"
            or value_type(result[0]).reference not in ("", table)
            or ("children" in spec(result[0]) and spec(result[0])["children"] != [])
        ):
            raise CapabilityError(
                "Array.Reference requires one scalar item schema with its parent reference table.",
                "UNAVAILABLE",
            )
        result = copy.deepcopy(result)
        spec(result[0])["reference"] = table
    return result


def reference_array_item(value: Any) -> None:
    if whole_pill(value):
        return
    if not isinstance(value, str):
        raise CapabilityError("Array.Reference items require scalar sys_id strings.")
    validate_sys_id(value)


def child(item: dict[str, Any], name: str) -> dict[str, Any]:
    rows = children(item)
    matches = [
        row
        for row in rows
        if isinstance(row.get("name"), str) and row["name"].split(".")[-1] == name
    ]
    if len(matches) != 1:
        raise CapabilityError("The nested producer field is absent or ambiguous.")
    return matches[0]


def whole_pill(value: Any) -> str | None:
    if isinstance(value, str) and (match := tree.PILL.fullmatch(value)):
        return str(match.group(1))
    return None


def output_assignment_value(row: dict[str, Any]) -> Any:
    """Read only the documented element-mapping fallback for output rows."""
    # Studio GET puts an output binding in displayValue with value blank
    # (bundle:150470-150479). False and zero remain actual values.
    if (
        row.get("type") in declarations.LABELS
        or spec(row).get("type") in declarations.LABELS
    ):
        return row.get("value")
    if row.get("value") == "" and "displayValue" in row:
        return row["displayValue"]
    return row.get("value")


def output_assignment_mirror(row: dict[str, Any]) -> Any:
    """Rebuild the pre-save UI data mirror from a serialized output row.

    fillInstanceFieldData runs before constructInstanceValuesForArrays clones
    the instances (bundle:207329-207331,150470-150479,23449). Array values in
    data therefore remain arrays when output wire parents become blank.
    """

    def ui_item(item: dict[str, Any]) -> dict[str, Any]:
        restored = copy.deepcopy(item)
        if "children" in item:
            restored["children"] = [ui_item(child) for child in item["children"]]
        if value_type(item).kind in declarations.ARRAYS:
            restored["value"] = output_assignment_mirror(item)
        return restored

    kind = value_type(row).kind
    if whole_pill(row.get("value")):
        return row["value"]
    if kind == "array.object":
        return [ui_item(child) for child in row.get("children", [])]
    if kind in declarations.ARRAYS:
        return [child["value"] for child in row.get("children", [])]
    return output_assignment_value(row)


def set_value(item: dict[str, Any], value: Any) -> None:
    """Serialize native complex values using Studio's observed children/value contract."""
    parameter = spec(item)
    template_probe: dict[str, Any] | None = None
    if widgets.input_state(item)["readonly"]:
        raise CapabilityError("The input is read-only.")
    if parameter.get("type") in {"template_value", "dynamic_template"}:
        from servicenow_mcp.capabilities.flows import template_modes

        template_probe = template_modes.prepare_basic(item, value)
    elif item.get("scriptActive"):
        raise CapabilityError(
            "Scripted inputs require an explicit input_scripts mode switch before their basic value can be replaced.",
            "UNAVAILABLE",
        )
    if parameter.get("type") in {
        "dynamic_inputs",
        "dynamic_choice",
        "dynamic_template",
    }:
        from servicenow_mcp.capabilities.flows import dynamic_inputs

        if template_probe is None:
            dynamic_inputs.set_value(item, value)
        else:
            dynamic_inputs.set_value(template_probe, value)
            item.update(template_probe)
        return
    if value_type(item).kind in widgets.TYPES:
        children(item)
        state = widgets.input_state(item)
        if state["readonly"]:
            raise CapabilityError("The input is read-only.")
        effective = {**parameter, "mandatory": state["mandatory"]}
        if "maxsize" not in effective and "maxsize" in item:
            effective["maxsize"] = item["maxsize"]
        if "availableList" not in effective and "availableList" in item:
            effective["availableList"] = item["availableList"]
        widgets.validate(value_type(item).kind, value, effective)
        item.update(
            value=value,
            displayValue=""
            if tree.pills(value)
            and value_type(item).kind != "string"
            or value_type(item).kind == "schedule_date_time"
            and parameter.get("attributes", {}).get("ui_approval") in (True, "true")
            else value,
        )
        if template_probe is not None:
            for key in ("script", "scriptActive", "scriptAsJsonString"):
                if key in template_probe:
                    item[key] = template_probe[key]
        return
    declared = children(item)
    if (
        isinstance(value, str)
        and value_type(item).kind != "json"
        and ("{{" in value or "}}" in value)
    ):
        unmatched = tree.PILL.sub("", value)
        if "{{" in unmatched or "}}" in unmatched:
            raise CapabilityError("The input contains a malformed pill reference.")
    if value_type(item).kind == "array":
        if not whole_pill(value) or declared:
            raise CapabilityError(
                "Generic Array items require one typed array pill; native Studio serialization cannot preserve nested literal arrays.",
                "UNAVAILABLE",
            )
        item.update(value=value, displayValue="", children=[])
        return
    if value_type(item).kind in declarations.LABELS and whole_pill(value):
        # Studio skips complex value construction for a whole pill. The saved
        # representation has no child values (bundle:23453,23465,23559-23562).
        item.update(value=value, displayValue="", children=[])
        return
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
            if kind not in declarations.ARRAYS or len(declared) != 1:
                raise CapabilityError(
                    "An array value requires one declared Array item schema.",
                    "UNAVAILABLE",
                )
            values = []
            for index, element in enumerate(value):
                if kind == "array.reference":
                    reference_array_item(element)
                child = declared[0]
                existing = (
                    current_children[index] if index < len(current_children) else {}
                )
                if (
                    kind == "array.reference"
                    and existing
                    and (uid := declarations.identity(child))
                    and declarations.identity(existing) != uid
                ):
                    raise CapabilityError(
                        "The saved Array.Reference item identity differs from its declaration."
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
    native_reference_pill = whole_pill(value) and value_type(item).kind in {
        "reference",
        "document_id",
        "records",
        "glide_list",
        "table_name",
    }
    if (
        isinstance(value, str)
        and maximum
        and len(value) > int(maximum)
        and not native_reference_pill
    ):
        raise CapabilityError("The input exceeds its maximum length.")
    if parameter.get("mandatory") is True and value in ("", None):
        raise CapabilityError("A mandatory input is empty.")
    # Static values and references use the same type boundary.
    # Literal pill expressions are checked after topology edits.
    if not tree.pills(value) and value not in ("", None):
        kind = value_type(item).kind
        if kind not in {
            "boolean",
            "long",
            "choice",
            "field_name",
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
        if kind == "long" and type(value) is not int:
            raise CapabilityError("Integer inputs require JSON integers.")
        if kind in {
            "choice",
            "field_name",
            "json",
        } and not isinstance(value, str):
            raise CapabilityError("This input requires a string value.")
        if kind == "json":
            import json

            try:
                json.loads(value)
            except ValueError:
                raise CapabilityError("JSON inputs require valid JSON text.") from None
        if kind == "field_name":
            from servicenow_mcp.validation import validate_identifier

            validate_identifier(value)
            if "." in value:
                raise CapabilityError(
                    "Dependent field inputs require one static field name."
                )
        if kind == "choice" and value not in {
            choice["value"] for choice in declarations.choices(parameter)
        }:
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
        if producer.logic_type == "TOP_LEVEL_CATCH":
            if any(parent.uid == producer.uid for parent in consumer_region):
                return
            raise CapabilityError(
                "Error Status pills are visible only inside their handler."
            )
        producer_handler = producer.raw.get("errorHandlingInstance", False) or any(
            parent.logic_type == "TOP_LEVEL_CATCH"
            for parent in (*producer_region, producer)
        )
        consumer_handler = consumer.raw.get("errorHandlingInstance", False) or any(
            parent.logic_type == "TOP_LEVEL_CATCH" for parent in consumer_region
        )
        if producer_handler != consumer_handler:
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
        if producer.logic_type == "DECISIONBLOCK":
            if any(parent.uid == producer.uid for parent in consumer_region):
                return
            raise CapabilityError(
                "Decision branch outputs are visible only inside their branch."
            )
        # Studio's Until editor is the closing decorator, not the opening order.
        # Only direct body children with exportable outputs are available there
        # (designer-bundle.min.js:16008-16017); nested conditional/loop paths are not.
        if (
            consumer.logic_type == "DOUNTIL"
            and producer_region
            and producer_region[-1].uid == consumer.uid
            and producer.logic_type not in {"IF", "ELSEIF", "ELSE", "FOREACH"}
        ):
            definition = producer.raw.get(
                "actionType"
                if producer.kind == "action"
                else "subFlow"
                if producer.kind == "subflow"
                else "flowLogicDefinition",
                {},
            )
            if definition.get("attributes", {}).get(
                "pills_draggable_outside_block"
            ) not in {False, "false"}:
                return
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
            target.kind
            not in widgets.TEXT_TYPES
            | {"string", "json", "conditions", "xml", "password2"}
            or source.many
            or source.kind == "object"
        ):
            raise CapabilityError(
                "Only scalar values can be interpolated into a text input."
            )
        return
    if source.many != target.many:
        raise CapabilityError("The pill and input have different cardinality.")
    aliases = {
        "choice": "string",
        "document_id": "reference",
        "datetime": "glide_date_time",
        "email": "string",
        "html": "string",
        "xml": "string",
        "password2": "string",
        "string_full_utf8": "string",
        "text": "string",
        "longtext": "string",
        "multi_small": "string",
        "translated_html": "string",
        "url": "string",
        "glide_list": "records",
        "duration": "glide_duration",
    }
    if aliases.get(source.kind, source.kind) != aliases.get(target.kind, target.kind):
        raise CapabilityError("The pill type is incompatible with the input type.")
    if target.reference and not source.reference:
        raise CapabilityError(
            "The producer's reference table is unavailable.", "UNAVAILABLE"
        )
