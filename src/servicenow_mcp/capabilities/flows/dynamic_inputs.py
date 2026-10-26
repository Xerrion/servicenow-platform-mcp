"""Source-native dynamic input values, separate from provider execution.

Studio 27: provider fields 234138-234166, typed JSON 234329-234400;
choice hydration 207523-207550, template columns 158939-158968.
Evidence is server-held and supplied by the separate explicit resolution effect.
"""

import copy
import json
import math
import re
from typing import Any

from pydantic import JsonValue

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, declarations, tree, widgets
from servicenow_mcp.capabilities.mutations.models import fingerprint
from servicenow_mcp.validation import validate_identifier, validate_sys_id

MODES = {"dynamic_inputs", "dynamic_choice", "dynamic_template"}
SCALARS = {
    "string",
    "integer",
    "boolean",
    "choice",
    "reference",
    "datetime",
    "glide_date_time",
    "json",
    "decimal",
    "number",
    "float",
    "email",
    "html",
    "xml",
    "password2",
}
DEFINITION_FIELDS = (
    "label",
    "type",
    "order",
    "mandatory",
    "defaultValue",
    "defaultDisplayValue",
    "reference",
    "reference_display",
    "choices",
    "readonly",
    "ref_qual",
    "use_dependent",
    "dependent_on",
    "type_label",
)
TEXT = {"email", "html", "xml", "password2"}
NUMERIC = {"decimal", "number", "float"}


def _name(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value):
        raise CapabilityError(
            "Resolved schema fields require unique static lowercase names.",
            "UNAVAILABLE",
        )
    return value


def _metadata_static(value: Any) -> None:
    if isinstance(value, str) and ("{{" in value or "}}" in value):
        raise CapabilityError(
            "Resolved schema metadata and defaults must not contain data-pill expressions.",
            "UNAVAILABLE",
        )
    if isinstance(value, dict):
        for child in value.values():
            _metadata_static(child)
    elif isinstance(value, list):
        for child in value:
            _metadata_static(child)


def _field(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CapabilityError(
            "The resolved schema field is not an object.", "UNAVAILABLE"
        )
    row = copy.deepcopy(raw)
    _name(row.get("name"))
    if not isinstance(row.get("label"), str) or not row["label"].strip():
        raise CapabilityError(
            "Resolved schema fields require readable labels.", "UNAVAILABLE"
        )
    _metadata_static(row)
    row.setdefault("type", "string")
    if (
        not isinstance(row["type"], str)
        or row["type"] not in SCALARS
        or row.get("children")
        or row.get("dynamic")
    ):
        raise CapabilityError(
            "This resolved field type has no native dynamic-input serializer.",
            "UNAVAILABLE",
        )
    if any(row.get(key) for key in ("data_structure", "use_dependent", "dependent_on")):
        raise CapabilityError(
            "Nested unresolved provider dependencies require their own schema resolution.",
            "UNAVAILABLE",
        )
    for key in ("mandatory", "readonly", "read_only", "use_dependent"):
        if key in row and type(row[key]) is not bool:
            raise CapabilityError(
                "Resolved field flags require native booleans.", "UNAVAILABLE"
            )
    if "order" in row and type(row["order"]) is not int:
        raise CapabilityError(
            "Resolved schema order requires native integers.", "UNAVAILABLE"
        )
    if row["type"] == "reference":
        table = row.get("reference")
        if not isinstance(table, str):
            raise CapabilityError(
                "Resolved reference fields require a static table.", "UNAVAILABLE"
            )
        validate_identifier(table)
        if "." in table:
            raise CapabilityError("Resolved reference fields require one table name.")
    elif row.get("reference"):
        raise CapabilityError(
            "Non-reference resolved fields cannot declare a reference table."
        )
    if row["type"] == "choice":
        declarations.choices(row)
    if row.get("ref_qual") and "javascript:" in str(row["ref_qual"]).lower():
        raise CapabilityError(
            "Scripted dynamic reference qualifiers are unavailable.", "UNAVAILABLE"
        )
    if "defaultValue" in row and row["defaultValue"] not in (None, ""):
        _scalar(row, row["defaultValue"], wire=True)
    return row


def validate_schema(mode: str, output: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse the producer's exact output.data grammar; never infer a type."""
    if mode not in MODES or not isinstance(output, dict):
        raise CapabilityError(
            "This dynamic input mode has no source-native schema adapter.",
            "UNAVAILABLE",
        )
    raw = output.get("data")
    maximum = 5000 if mode == "dynamic_choice" else 40
    if not isinstance(raw, list) or not 1 <= len(raw) <= maximum:
        raise CapabilityError(
            "The provider returned an empty, malformed, or oversized schema.",
            "UNAVAILABLE",
        )
    if mode == "dynamic_choice":
        rows = []
        for raw_row in raw:
            if not isinstance(raw_row, dict):
                raise CapabilityError(
                    "Dynamic choices require named labeled objects.", "UNAVAILABLE"
                )
            row = copy.deepcopy(raw_row)
            _name(row.get("name"))
            if not isinstance(row.get("label"), str) or not row["label"].strip():
                raise CapabilityError(
                    "Dynamic choices require readable labels.", "UNAVAILABLE"
                )
            _metadata_static(row)
            rows.append(row)
    else:
        rows = [_field(row) for row in raw]
    if len({row["name"] for row in rows}) != len(rows):
        raise CapabilityError(
            "Resolved schema field names are ambiguous.", "UNAVAILABLE"
        )
    return rows


def _scalar(schema: dict[str, Any], value: Any, *, wire: bool = False) -> Any:
    """Validate typed values; integer widgets persist numeric strings (55732)."""
    kind = schema["type"]
    probe_schema = {
        key: copy.deepcopy(value)
        for key, value in schema.items()
        if key not in {"readonly", "readOnly", "read_only"}
    }
    if kind in TEXT:
        probe_schema["type"] = "string"
    if kind in NUMERIC and value not in (None, "") and not data.whole_pill(value):
        if wire and isinstance(value, str):
            try:
                number = float(value)
            except ValueError:
                raise CapabilityError(
                    "Resolved numeric fields require finite numeric values."
                ) from None
        else:
            number = value
        if type(number) not in {int, float} or not math.isfinite(number):
            raise CapabilityError(
                "Resolved numeric fields require finite numeric values."
            )
        return str(value) if not wire else value
    if (
        wire
        and kind == "integer"
        and isinstance(value, str)
        and re.fullmatch(r"-?\d+", value)
    ):
        value = int(value)
    probe = {"name": schema["name"], "parameter": probe_schema}
    data.set_value(probe, value)
    if kind == "integer" and type(value) is int:
        return str(value)
    return value


def _definition(schema: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: copy.deepcopy(schema[key]) for key in DEFINITION_FIELDS if key in schema
    }
    result.setdefault("mandatory", False)
    result.setdefault("readonly", False)
    result.setdefault("use_dependent", False)
    result.setdefault("type_label", schema["type"][0].upper() + schema["type"][1:])
    return result


def _parse_inputs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, str):
        raise CapabilityError("Dynamic input wire values require native JSON text.")
    try:
        parsed = json.loads(value)
    except ValueError:
        raise CapabilityError(
            "Dynamic input values require valid native JSON text."
        ) from None
    if not isinstance(parsed, dict) or not parsed or len(parsed) > 40:
        raise CapabilityError(
            "Dynamic input values require a complete bounded field map."
        )
    rows = []
    for name, entry in parsed.items():
        _name(name)
        if (
            not isinstance(entry, dict)
            or set(entry) != {"value", "display_value", "definition"}
            or not isinstance(entry["definition"], dict)
        ):
            raise CapabilityError(
                "Dynamic input entries require value, display_value, and definition."
            )
        definition = entry["definition"]
        if set(definition) - set(DEFINITION_FIELDS):
            raise CapabilityError(
                "Dynamic input definitions contain unsupported metadata."
            )
        schema = _field({"name": name, **definition})
        if _definition(schema) != definition:
            raise CapabilityError(
                "Dynamic input definitions are incomplete or contradictory."
            )
        _scalar(schema, entry["value"], wire=True)
        if not isinstance(entry["display_value"], str):
            raise CapabilityError("Dynamic input display values require strings.")
        if "{{" in entry["display_value"] or "}}" in entry["display_value"]:
            raise CapabilityError(
                "Dynamic display metadata cannot contain data-pill expressions."
            )
        rows.append(
            {**schema, "value": entry["value"], "display_value": entry["display_value"]}
        )
    return rows


def serialize_inputs(schema: list[dict[str, Any]], value: Any) -> str:
    """Write formatDataForBackend without accepting a caller-supplied schema."""
    if isinstance(value, str):
        rows = _parse_inputs(value)
        actual = {row["name"]: _definition(row) for row in rows}
        expected = {row["name"]: _definition(row) for row in schema}
        if actual != expected:
            raise CapabilityError(
                "Saved dynamic input schema differs from the resolved provider schema.",
                "STALE_PREVIEW",
            )
        for row in rows:
            if not row.get("readonly"):
                continue
            default = row.get("defaultValue", False if row["type"] == "boolean" else "")
            if default is None:
                default = False if row["type"] == "boolean" else ""
            if row["value"] != _scalar(row, default):
                raise CapabilityError(
                    "Read-only resolved values must retain their declared defaults."
                )
        return value
    if not isinstance(value, dict) or set(value) - {row["name"] for row in schema}:
        raise CapabilityError(
            "Dynamic inputs require a native object containing only resolved fields."
        )
    result = {}
    for row in schema:
        name = row["name"]
        if row.get("readonly") and name in value:
            raise CapabilityError(
                "Read-only resolved fields cannot be supplied by callers."
            )
        chosen = value.get(
            name, row.get("defaultValue", False if row["type"] == "boolean" else "")
        )
        if chosen is None:
            chosen = False if row["type"] == "boolean" else ""
        chosen = _scalar(row, chosen)
        result[name] = {
            "value": chosen,
            "display_value": "",
            "definition": _definition(row),
        }
    return json.dumps(result, separators=(",", ":"), ensure_ascii=False)


def template_columns(schema: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mirror getTemplateDefinition, keeping schema separate from encoded values."""
    result = []
    for row in schema:
        column = {
            "base_type": row.get("base_type") or "string",
            "column_type": "element",
            "default": "",
            "filterable": True,
            "hint": "",
            "label": row["label"],
            "mandatory": row.get("mandatory", False),
            "max_length": 100,
            "name": row["name"],
            "read_only": row.get("read_only", False),
            "security": {"can_read": True, "can_write": True},
            "can_read": True,
            "can_write": True,
            "type": row["type"],
            "value": row["name"],
            "canDrop": True,
            "isFromDynamicInput": False,
        }
        for key in ("defaultValue", "order", "reference", "choices"):
            if key in row:
                column[key] = copy.deepcopy(row[key])
        result.append(column)
    return result


def _template_bindings(item: dict[str, Any]) -> list[dict[str, Any]]:
    from servicenow_mcp.capabilities.flows import template_modes

    template_modes.validate_markers(item)
    parameter = data.spec(item)
    columns = item.get("columns", parameter.get("columns"))
    if not isinstance(columns, list) or not columns:
        raise CapabilityError(
            "Dynamic templates require independently resolved column schemas.",
            "UNAVAILABLE",
        )
    schemas = {
        _name(column.get("name")): _field(column)
        for column in columns
        if isinstance(column, dict)
    }
    if len(schemas) != len(columns):
        raise CapabilityError("Dynamic template columns are ambiguous or malformed.")
    result = []
    for name, value in widgets.template_fields(item.get("value")):
        if name == "coalesce_fields":
            continue
        if name not in schemas:
            raise CapabilityError(
                "Dynamic template values reference an unresolved field."
            )
        schema = schemas[name]
        if schema.get("readonly") or schema.get("read_only"):
            raise CapabilityError(
                "Read-only dynamic template fields cannot be assigned."
            )
        if value == "fd-scripted":
            # validate_markers independently requires the exact active native
            # parent/column script map; this is not a scalar runtime value.
            continue
        if schema["type"] == "boolean" and value in {"true", "false"}:
            value = value == "true"
        if schema["type"] == "reference" and value and not data.whole_pill(value):
            if not isinstance(value, str):
                raise CapabilityError(
                    "Dynamic template references require native reference JSON."
                )
            try:
                reference = json.loads(value)
            except ValueError:
                raise CapabilityError(
                    "Dynamic template references require native reference JSON."
                ) from None
            if (
                not isinstance(reference, dict)
                or reference.get("table") != schema["reference"]
                or not isinstance(reference.get("value"), str)
                or reference.get("value") != reference.get("sys_id")
            ):
                raise CapabilityError(
                    "Dynamic template reference identity or table is inconsistent."
                )
            value = reference["value"]
            validate_sys_id(value)
        _scalar(schema, value, wire=True)
        result.append({"name": name, "parameter": schema, "value": value})
    return result


def bindings(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose typed serialized dynamic leaves to the ordinary pill resolver."""
    kind = data.spec(item).get("type")
    if kind == "dynamic_inputs":
        rows = _parse_inputs(item.get("value"))
        result = []
        for row in rows:
            schema = {
                key: copy.deepcopy(value)
                for key, value in row.items()
                if key not in {"value", "display_value"}
            }
            value = row["value"]
            if (
                schema["type"] == "integer"
                and isinstance(value, str)
                and re.fullmatch(r"-?\d+", value)
            ):
                value = int(value)
            result.append({"name": row["name"], "parameter": schema, "value": value})
        return result
    if kind == "dynamic_template":
        return _template_bindings(item)
    if kind == "dynamic_choice":
        parameter = copy.deepcopy(data.spec(item))
        parameter.update(
            type="choice",
            choices=copy.deepcopy(item.get("choices", parameter.get("choices"))),
        )
        for key in ("dynamic", "dependent_on", "data_structure", "table", "columnName"):
            parameter.pop(key, None)
        declarations.choices(parameter)
        return [
            {"name": item["name"], "parameter": parameter, "value": item.get("value")}
        ]
    raise CapabilityError("The input is not a supported resolved dynamic mode.")


def set_value(item: dict[str, Any], value: Any) -> None:
    """Validate native already-resolved values; authoring adoption binds evidence."""
    probe = {**copy.deepcopy(item), "value": value}
    for binding in bindings(probe):
        _scalar(data.spec(binding), binding["value"], wire=True)
    item.update(value=value, displayValue="", children=[])


def adopt(
    definition: dict[str, Any],
    inputs: dict[str, Any],
    evidence_list: list[dict[str, Any]],
    *,
    connection_context: dict[str, str] | None = None,
    binding_state: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind effective input metadata and wire values to server-held provider evidence."""
    from servicenow_mcp.capabilities.flows import dynamic_schemas

    parameters = definition.get("inputs")
    if not isinstance(parameters, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("name"), str)
        for row in parameters
    ):
        raise CapabilityError("The call input schema is incomplete.", "UNAVAILABLE")

    effective = copy.deepcopy(definition)
    values = copy.deepcopy(inputs)
    for parameter in effective["inputs"]:
        if not parameter.get("dynamic"):
            if parameter.get("type") in MODES:
                raise CapabilityError(
                    "Dynamic inputs require their authoritative provider descriptor.",
                    "UNAVAILABLE",
                )
            continue
        kind = parameter.get("type")
        name = parameter.get("name")
        owner_id = definition.get("id")
        if not isinstance(name, str) or not isinstance(owner_id, str):
            raise CapabilityError(
                "The dynamic schema owner identity is incomplete.", "UNAVAILABLE"
            )
        if kind not in MODES:
            raise CapabilityError(
                "This dynamic owner has no native input-schema adapter.", "UNAVAILABLE"
            )
        aliases: dict[str, JsonValue] = {
            source: target for source, target in (connection_context or {}).items()
        }
        resolution: dict[str, JsonValue] = {
            "action_type_sys_id": owner_id,
            "input_name": name,
            "connection_overrides": aliases,
        }
        resolution_outcome: dict[str, JsonValue] = {
            "provider_executed": False,
            "schema_resolution": resolution,
        }
        evidence = [
            dynamic_schemas.validate_evidence(row)
            for row in evidence_list
            if row.get("action_type_sys_id") == definition.get("id")
            and row.get("input_name") == name
        ]
        if len(evidence) != 1:
            raise CapabilityError(
                "Resolve each dynamic input explicitly and supply its single-use schema token.",
                "UNAVAILABLE",
                outcome=resolution_outcome,
            )
        evidence = evidence[0]
        if (
            evidence["owner_fingerprint"] != fingerprint(definition)
            or evidence["mode"] != kind
        ):
            raise CapabilityError(
                "The resolved schema belongs to a different action definition.",
                "STALE_PREVIEW",
            )
        if (connection_context or {}) != evidence["connection_context"][
            "effective_overrides"
        ]:
            raise CapabilityError(
                "The dynamic schema connection context changed. Resolve the schema with the selected connections again.",
                "STALE_PREVIEW",
                outcome=resolution_outcome,
            )
        for sibling in evidence["sibling_inputs"]:
            binding = (binding_state or {}).get(sibling, {})
            if binding.get("scriptActive") or any(
                pill.startswith("fd_transform:")
                for pill in tree.pills(binding.get("value"))
            ):
                raise CapabilityError(
                    "Static schema evidence cannot authorize a scripted or transformed provider dependency.",
                    "STALE_PREVIEW",
                    outcome=resolution_outcome,
                )
        context = {**evidence["plan"]["inputs"], **evidence["sibling_inputs"]}
        for sibling, expected in context.items():
            declared = data.named(definition["inputs"], sibling)
            default = data.default_value(declared)
            if declared.get("type") == "boolean":
                default = default in ("true", "1", True)
            current = values.get(sibling, default)
            if current != expected:
                raise CapabilityError(
                    "The dynamic schema dependencies changed. Resolve the schema again.",
                    "STALE_PREVIEW",
                )
        schema = validate_schema(kind, {"data": evidence["schema"]})
        chosen = values.get(name, data.default_value(parameter))
        if kind == "dynamic_inputs":
            values[name] = serialize_inputs(schema, {} if chosen == "" else chosen)
        elif kind == "dynamic_choice":
            choices = [
                {"label": row["label"], "value": row["name"], "order": index}
                for index, row in enumerate(schema)
            ]
            if parameter.get("choiceOption") == "1":
                original = parameter.get("choices") or []
                none = (
                    copy.deepcopy(original[0])
                    if original and original[0].get("value") == ""
                    else {"label": "-- None --", "value": "", "order": 0}
                )
                choices = [
                    none,
                    *[{**row, "order": index + 1} for index, row in enumerate(choices)],
                ]
            parameter["choices"] = choices
            set_value({**parameter, "parameter": parameter}, chosen)
            values[name] = chosen
        else:
            parameter["columns"] = template_columns(schema)
            binding = copy.deepcopy((binding_state or {}).get(name, {}))
            binding.update(parameter=parameter, columns=parameter["columns"], name=name)
            set_value(binding, chosen)
            values[name] = chosen
    return effective, values


def adopt_bindings(
    raw: dict[str, Any],
    definition: dict[str, Any],
    updates: dict[str, Any],
    evidence_list: list[dict[str, Any]],
    *,
    connection_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Keep immutable action metadata separate from its resolved native bindings."""
    existing = raw.get("inputs")
    if (
        not isinstance(existing, list)
        or any(
            not isinstance(row, dict) or not isinstance(row.get("name"), str)
            for row in existing
        )
        or len({row["name"] for row in existing}) != len(existing)
    ):
        raise CapabilityError(
            "Saved dynamic input bindings are incomplete or ambiguous.", "UNAVAILABLE"
        )
    values = {row["name"]: row.get("value") for row in existing}
    values.update(updates)
    effective, values = adopt(
        definition,
        values,
        evidence_list,
        connection_context=connection_context,
        binding_state={row["name"]: row for row in existing},
    )
    result = copy.deepcopy(updates)
    for parameter in effective["inputs"]:
        if not parameter.get("dynamic"):
            continue
        binding = data.named(existing, parameter["name"])
        if data.spec(binding).get("dynamic") != parameter["dynamic"]:
            raise CapabilityError(
                "Saved dynamic metadata differs from the published owner.",
                "STALE_PREVIEW",
            )
        # choices/columns are Studio-owned effective metadata; preserve opaque
        # saved binding fields and the independent immutable action definition.
        binding["parameter"] = copy.deepcopy(parameter)
        for key in ("choices", "columns"):
            if key in parameter:
                binding[key] = copy.deepcopy(parameter[key])
        result[parameter["name"]] = values[parameter["name"]]
    return result
