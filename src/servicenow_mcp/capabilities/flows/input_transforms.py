"""Static, source-native transform-function authoring; never execute a transform.

Studio 27: definition GET 1074,499757-499763; normalization 265688-265715;
parameter.value editing 329221-329267; constructors 226399-226460;
REST pharmacy serialization 61056-61116. Definition DTOs have no proven generic
input/output type slots. Signatures below are the individual function contracts
in local ServiceNowDocs/build-workflows/workflow-studio/*-transform-functions.md,
not guessed inputType/outputType fields or caller-supplied type assertions.
"""

from __future__ import annotations

import copy
import json
import math
import re
import uuid
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, logic_blocks, payloads, tree
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.capabilities.flows.editing import PillResolver
    from servicenow_mcp.platform.client import ServiceNowClient


_NUMBERS = frozenset({"number", "integer", "long", "decimal", "float"})
_NUMBER_ARRAYS = frozenset({"array.number", "array.integer", "array.decimal"})
_SORT_ARRAYS = frozenset(
    {
        "array.string",
        "array.integer",
        "array.boolean",
        "array.datetime",
        "array.glide_date_time",
    }
)
_DATES = frozenset(
    {
        "datetime",
        "glide_date_time",
        "glide_date",
        "date",
        "due_date",
        "schedule_date_time",
    }
)
# Each key is an exact server definition.label, not a fuzzy user selector.
_SIGNATURES: dict[str, tuple[frozenset[str] | None, str]] = {
    **{
        name: (frozenset({"string"}), "string")
        for name in (
            "First Character",
            "Last Character",
            "Replace String",
            "Substring",
            "To Lower Case",
            "To Proper Case",
            "To Upper Case",
            "Trim",
            "Sanitize Bash shell arguments",
            "Sanitize SQL Identifier",
            "Sanitize SQL Value",
        )
    },
    **{
        name: (frozenset({"string"}), "boolean")
        for name in (
            "Contains",
            "Does not Contain",
            "Ends With",
            "Starts With",
        )
    },
    "Convert String to Number": (frozenset({"string"}), "number"),
    "Size": (frozenset({"string"}), "integer"),
    "Split": (frozenset({"string"}), "array.string"),
    "String to Date": (frozenset({"string"}), "glide_date_time"),
    "Date to String": (_DATES, "string"),
    "Add Time": (_DATES | {"glide_duration", "duration"}, "glide_date_time"),
    "Subtract Time": (_DATES | {"glide_duration", "duration"}, "glide_date_time"),
    **{
        name: (frozenset({"datetime", "glide_date_time"}), "integer")
        for name in (
            "Day",
            "Hour",
            "Minute",
            "Second",
            "Month",
            "Week",
            "Year",
        )
    },
    "Date Difference": (_DATES, "glide_duration"),
    **{
        name: (_NUMBERS, "number")
        for name in (
            "Absolute Value",
            "Add",
            "Divide",
            "Multiply",
            "Power",
            "Round",
            "Square Root",
            "Subtract",
            "To Fixed",
        )
    },
    **{
        name: (_NUMBER_ARRAYS, "number")
        for name in (
            "Average",
            "Max",
            "Median",
            "Min",
            "Sum",
        )
    },
    "Count": (
        _NUMBER_ARRAYS | {"array.object", "array.string", "array.boolean"},
        "number",
    ),
    **{
        name: (frozenset({"array.object"}), "object")
        for name in (
            "Get First Item from Array",
            "Get Item from Array",
            "Get Last Item from Array",
        )
    },
    "Get Item from Name/Values": (
        frozenset({"name_values", "simple_name_values"}),
        "string",
    ),
    "Key Value Map": (frozenset({"string", "integer"}), "string"),
    **{
        name: (None, "boolean")
        for name in (
            "Is Blank",
            "Is False",
            "Is Not Blank",
            "Is Null",
            "Is True",
        )
    },
    "Sort": (_SORT_ARRAYS, "preserve"),
    "Unique": (_SORT_ARRAYS, "preserve"),
    "Join": (_SORT_ARRAYS, "string"),
    "To XML": (frozenset({"object"}), "xml"),
}
# Exact installed labels observed in the independently read native catalog;
# aliases link those definitions to the corresponding local documentation.
_NATIVE_LABELS = {
    "Contains?": "Contains",
    "Does not Contain?": "Does not Contain",
    "Ends With?": "Ends With",
    "Starts With?": "Starts With",
    "Is Blank?": "Is Blank",
    "Is False?": "Is False",
    "Is Not Blank?": "Is Not Blank",
    "Is Null?": "Is Null",
    "Is True?": "Is True",
    "Get item from Name/Values": "Get Item from Name/Values",
    "Sanitize Bash Shell Arguments": "Sanitize Bash shell arguments",
}
_PARAMETER_STATE = frozenset({"value", "order", "uiId"})


def _identity(value: Any) -> str:
    if not isinstance(value, str):
        raise CapabilityError(
            "Native transform identities require explicit sys_id strings."
        )
    validate_sys_id(value)
    return value


def _object(value: Any, allowed: set[str], message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed:
        raise CapabilityError(message)
    return value


def parse(value: Any) -> dict[str, dict[str, Any] | None]:
    """Parse named source/transform/composition/parameter options, never DTOs.

    Every declared composition is applied. Optional composition entries only
    override named parameter values; omitted values remain the metadata.value
    used by the native editor, not an invented defaultValue fallback.
    """
    if not isinstance(value, dict) or any(
        not isinstance(name, str) or not name for name in value
    ):
        raise CapabilityError("input_transforms requires a map of native input names.")
    result: dict[str, dict[str, Any] | None] = {}
    for name, raw in value.items():
        if raw is None:
            result[name] = None
            continue
        option = _object(
            raw,
            {"source", "transforms"},
            "Transform inputs accept only source and transforms.",
        )
        source_path = data.whole_pill(option.get("source"))
        if (
            source_path is None
            or source_path.startswith("fd_transform:")
            or "." not in source_path
        ):
            raise CapabilityError(
                "A transform source requires one ordinary producer pill."
            )
        transforms = option.get("transforms")
        if not isinstance(transforms, list) or not transforms:
            raise CapabilityError(
                "A transform input requires a nonempty transforms list; use null to clear it."
            )
        for raw_transform in transforms:
            transform = _object(
                raw_transform,
                {"transform_sys_id", "compositions"},
                "Transforms accept only transform_sys_id and optional compositions.",
            )
            _identity(transform.get("transform_sys_id", ""))
            compositions = transform.get("compositions", [])
            if not isinstance(compositions, list):
                raise CapabilityError(
                    "Transform compositions require a list of named composition options."
                )
            seen: set[str] = set()
            for raw_composition in compositions:
                composition = _object(
                    raw_composition,
                    {"composition_sys_id", "parameters"},
                    "Composition options accept only composition_sys_id and parameters.",
                )
                identity = _identity(composition.get("composition_sys_id", ""))
                if identity in seen:
                    raise CapabilityError(
                        "A transform composition was supplied more than once."
                    )
                seen.add(identity)
                parameters = composition.get("parameters")
                if not isinstance(parameters, dict) or any(
                    not isinstance(key, str) or not key for key in parameters
                ):
                    raise CapabilityError(
                        "Composition parameters require a map of native parameter names to values."
                    )
        result[name] = copy.deepcopy(option)
    return result


def _pharmacy(flow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pharmacy = flow.get("pharmacy")
    if pharmacy is None:
        return {}
    if not isinstance(pharmacy, dict):
        raise CapabilityError(
            "The saved pharmacy metadata is malformed.", "UNAVAILABLE"
        )
    rows = pharmacy.get("pharmacyCompound")
    if rows is None:
        return {}
    if not isinstance(rows, dict) or any(
        not isinstance(row, dict) for row in rows.values()
    ):
        raise CapabilityError(
            "The saved pharmacy compounds are malformed.", "UNAVAILABLE"
        )
    return rows


def _binding(flow: dict[str, Any], key: str, row: dict[str, Any]) -> None:
    if row.get("uiId") != key:
        raise CapabilityError(
            "A saved transform compound has a different UI identity.", "UNAVAILABLE"
        )
    # Native GQL slots:472619-472629. Independent dictionary/record reads of
    # sys_hub_pill_compound establish attached_to -> flow header and sys_scope.
    # Unsaved UI rows omit these fields; never invent defaults on new rows.
    for field, expected in (
        ("attachedTo", flow.get("id")),
        ("scopeId", flow.get("scope")),
    ):
        if field in row and row[field] != expected:
            raise CapabilityError(
                "A saved transform belongs to another flow or scope.", "UNAVAILABLE"
            )
    if row.get("id") not in (None, ""):
        _identity(row["id"])


def source(flow: dict[str, Any], pill: str) -> str:
    """Return a saved root's ordinary path, validating declared record bindings."""
    if not pill.startswith("fd_transform:"):
        return pill
    identity = pill.removeprefix("fd_transform:")
    root = _pharmacy(flow).get(identity)
    if root is None:
        raise CapabilityError("The saved transform root is missing.", "UNAVAILABLE")
    _binding(flow, identity, root)
    if (
        type(root.get("order")) is not int
        or root["order"] != 0
        or root.get("sourceUiId") not in (None, "")
        or root.get("transformDefinitionId") not in (None, "")
        or root.get("compositions") not in (None, [])
    ):
        raise CapabilityError(
            "The transform pill does not identify an ordinary source root.",
            "UNAVAILABLE",
        )
    path = data.whole_pill(root.get("prescription"))
    if path is None or path.startswith("fd_transform:") or "." not in path:
        raise CapabilityError(
            "The transform prescription requires one ordinary typed pill.",
            "UNAVAILABLE",
        )
    return path


async def evidence(
    flow: dict[str, Any],
    client: ServiceNowClient,
    *,
    transform_sys_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Independently read the full native catalog and bind its selected identities.

    No authored parameter values, scripts, credentials, or execution results are
    added to this evidence. Callers retain it server-side for revision hashing.
    """
    selected = set(transform_sys_ids or [])
    for key, row in _pharmacy(flow).items():
        _binding(flow, key, row)
        if row.get("transformDefinitionId"):
            if (
                not isinstance(row.get("sourceUiId"), str)
                or type(row.get("order")) is not int
                or row["order"] < 1
            ):
                raise CapabilityError(
                    "A saved transform has no valid root link or order.", "UNAVAILABLE"
                )
            source(flow, "fd_transform:" + row["sourceUiId"])
            selected.add(_identity(row["transformDefinitionId"]))
    for identity in selected:
        _identity(identity)
    definitions = await client.studio_transform_definitions()
    payloads.validate_definition(
        {"definitions": definitions}, field_name="transform definitions"
    )
    catalog: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        identity = _identity(definition.get("id", ""))
        if identity in catalog:
            raise CapabilityError(
                "Native transform definition IDs are ambiguous.", "UNAVAILABLE"
            )
        catalog[identity] = definition
    for identity in selected:
        _definition(catalog, identity)
    return {
        "flow_sys_id": flow.get("id"),
        "scope": flow.get("scope"),
        "transform_sys_ids": sorted(selected),
        "definitions": copy.deepcopy(definitions),
    }


async def _metadata(resolver: PillResolver) -> dict[str, Any]:
    current = getattr(resolver, "input_transform_metadata", None)
    if current is None:
        current = await evidence(resolver.flow, resolver.client)
        resolver.input_transform_metadata = current
    if current["flow_sys_id"] != resolver.flow.get("id") or current[
        "scope"
    ] != resolver.flow.get("scope"):
        raise CapabilityError(
            "The transform metadata belongs to another flow or scope.", "UNAVAILABLE"
        )
    return current


def _definition(catalog: dict[str, dict[str, Any]], identity: str) -> dict[str, Any]:
    definition = catalog.get(identity)
    if definition is None:
        raise CapabilityError(
            "The selected native transform definition is unavailable.", "UNAVAILABLE"
        )
    label = definition.get("label")
    if label == "End of Month":
        raise CapabilityError(
            "End of Month has conflicting published output contracts (Integer versus a Date example); its final type cannot be established statically.",
            "UNAVAILABLE",
        )
    if (
        not isinstance(label, str)
        or _NATIVE_LABELS.get(label, label) not in _SIGNATURES
    ):
        raise CapabilityError(
            "This native transform has no documented static input/output contract in the local source.",
            "UNAVAILABLE",
        )
    compositions = definition.get("transformCompositions")
    if not isinstance(compositions, list) or any(
        not isinstance(row, dict) for row in compositions
    ):
        raise CapabilityError(
            "The native transform composition metadata is incomplete.", "UNAVAILABLE"
        )
    identities: set[str] = set()
    for composition in compositions:
        composition_id = _identity(composition.get("id", ""))
        if composition_id in identities:
            raise CapabilityError(
                "Native transform composition IDs are ambiguous.", "UNAVAILABLE"
            )
        identities.add(composition_id)
        if (
            "transformDefinition" in composition
            and composition["transformDefinition"] != identity
        ):
            raise CapabilityError(
                "A native transform composition belongs to another definition.",
                "UNAVAILABLE",
            )
        if "transformType" in composition:
            _identity(composition["transformType"])
        parameters = composition.get("parameters")
        if not isinstance(parameters, list) or any(
            not isinstance(row, dict) for row in parameters
        ):
            raise CapabilityError(
                "The native transform parameter metadata is incomplete.", "UNAVAILABLE"
            )
        names = [row.get("name") for row in parameters]
        if any(not isinstance(name, str) or not name for name in names) or len(
            set(names)
        ) != len(names):
            raise CapabilityError(
                "Native transform parameter names are absent or ambiguous.",
                "UNAVAILABLE",
            )
        for parameter in parameters:
            if parameter.get("id") not in (None, ""):
                _identity(parameter["id"])
            data.value_type(parameter)
            if "attributes" in parameter and not isinstance(
                parameter["attributes"], dict
            ):
                raise CapabilityError(
                    "Native transform parameter attributes are malformed.",
                    "UNAVAILABLE",
                )
            for flag in ("mandatory", "readonly", "readOnly", "read_only"):
                if flag in parameter and type(parameter[flag]) is not bool:
                    raise CapabilityError(
                        "Native transform parameter flags require booleans.",
                        "UNAVAILABLE",
                    )
    return definition


def _value(parameter: dict[str, Any], value: Any, *, wire: bool) -> Any:
    """Use shared validation, then retain only parameter.value as the UI does."""
    schema = {
        key: copy.deepcopy(val)
        for key, val in parameter.items()
        if key not in {"readonly", "readOnly", "read_only"}
    }
    if any(
        schema.get(key)
        for key in (
            "script",
            "scriptActive",
            "dynamic",
            "dependent_on",
            "use_dependent",
        )
    ):
        raise CapabilityError(
            "Dynamic, dependent, or scripted transform parameters have no static value contract.",
            "UNAVAILABLE",
        )
    kind = data.value_type(schema).kind
    if kind == "int":
        # The native catalog uses the explicit INT constant (bundle11124) on
        # math parameters; normalize only the validation schema, not the DTO.
        schema["type"] = "integer"
        kind = "integer"
    if kind in {"duration", "glide_duration", "glide_time"}:
        logic_blocks.validate_value(kind, value, schema)
        return value
    if kind == "name_values" and value not in (None, "") and not data.whole_pill(value):
        if schema.get("attributes", {}).get("advanced_name_values"):
            raise CapabilityError(
                "Advanced Name-Values transform parameters require their declared advanced field-configuration contract.",
                "UNAVAILABLE",
            )
        if not isinstance(value, str):
            raise CapabilityError(
                "Name-Values parameters require the native JSON text value."
            )
        try:
            pairs = json.loads(value)
        except ValueError:
            raise CapabilityError(
                "Name-Values parameters require valid JSON text."
            ) from None
        if isinstance(pairs, dict):
            valid = all(
                isinstance(name, str) and isinstance(val, str)
                for name, val in pairs.items()
            )
        else:
            valid = isinstance(pairs, list) and all(
                isinstance(row, dict)
                and set(row) == {"name", "value"}
                and isinstance(row["name"], str)
                and isinstance(row["value"], str)
                for row in pairs
            )
        if not valid:
            raise CapabilityError(
                "Native Name-Values entries require string names and string values."
            )
        schema["type"] = "string"
    if (
        kind in {"number", "decimal", "float"}
        and value not in (None, "")
        and not data.whole_pill(value)
    ):
        if wire and isinstance(value, str):
            try:
                number = float(value)
            except ValueError:
                raise CapabilityError(
                    "Native numeric transform values are invalid."
                ) from None
        else:
            number = value
        if (
            not isinstance(number, int | float)
            or isinstance(number, bool)
            or not math.isfinite(number)
        ):
            raise CapabilityError(
                "Numeric transform parameters require finite JSON numbers."
            )
        value = value if wire else str(value)
        schema["type"] = "string"
    if (
        kind in {"integer", "long"}
        and wire
        and isinstance(value, str)
        and re.fullmatch(r"-?\d+", value)
    ):
        probe_value: Any = int(value)
    else:
        probe_value = value
    if kind in {"email", "html", "xml", "password2"}:
        schema["type"] = "string"
    probe = {"name": parameter["name"], "parameter": schema}
    data.set_value(probe, probe_value)
    if probe.get("children"):
        raise CapabilityError(
            "Transform parameters persist parameter.value only; this complex literal needs a children serializer not present in the native pharmacy contract.",
            "UNAVAILABLE",
        )
    if kind in {"integer", "long"} and type(probe_value) is int:
        attributes = parameter.get("attributes", {})
        for key, lower in (("min_value", True), ("max_value", False)):
            if key in attributes:
                bound = str(attributes[key])
                if not re.fullmatch(r"-?\d+", bound):
                    raise CapabilityError(
                        "Native integer parameter limits are malformed.", "UNAVAILABLE"
                    )
                if (lower and probe_value < int(bound)) or (
                    not lower and probe_value > int(bound)
                ):
                    raise CapabilityError(
                        "The transform parameter exceeds its native integer limits."
                    )
        return value if wire else str(probe_value)
    return value


def _parameter_bindings(parameter: dict[str, Any], value: Any) -> list[dict[str, Any]]:
    if (
        data.value_type(parameter).kind == "name_values"
        and value not in (None, "")
        and not data.whole_pill(value)
    ):
        # NameValue's source-defined cells are independent String glideelements
        # (53295-53304); their pills are not a whole Name-Values input pill.
        pairs = json.loads(value)  # _value has established this exact grammar
        rows = (
            [{"name": name, "value": item} for name, item in pairs.items()]
            if isinstance(pairs, dict)
            else pairs
        )
        return [
            {
                "name": parameter["name"],
                "parameter": {"type": "string"},
                "value": row[key],
            }
            for row in rows
            for key in ("name", "value")
        ]
    schema = (
        {**parameter, "type": "integer"}
        if parameter.get("type") == "int"
        else parameter
    )
    return [{"name": parameter["name"], "parameter": schema, "value": value}]


async def _parameters(
    definition: dict[str, Any],
    options: list[dict[str, Any]],
    resolver: PillResolver,
    consumer: tree.Step | None,
    *,
    new: bool = False,
    saved: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    declared = definition["transformCompositions"]
    supplied = {row["composition_sys_id"]: row["parameters"] for row in options}
    if set(supplied) - {row["id"] for row in declared}:
        raise CapabilityError("The selected transform has no such composition.")
    if saved is not None and (
        not isinstance(saved, list) or len(saved) != len(declared)
    ):
        raise CapabilityError(
            "Saved transform compositions differ from the native definition.",
            "UNAVAILABLE",
        )
    result = []
    for index, composition in enumerate(declared):
        values = supplied.get(composition["id"], {})
        if set(values) - {row["name"] for row in composition["parameters"]}:
            raise CapabilityError(
                "The transform composition contains an unknown parameter."
            )
        existing = saved[index] if saved is not None else None
        if existing is not None:
            if (
                not isinstance(existing, dict)
                or existing.get("id") != composition["id"]
                or "order" in existing
                and (type(existing["order"]) is not int or existing["order"] != index)
            ):
                raise CapabilityError(
                    "A saved transform composition identity or order changed.",
                    "UNAVAILABLE",
                )
            rows = existing.get("parameters")
            if not isinstance(rows, list) or len(rows) != len(
                composition["parameters"]
            ):
                raise CapabilityError(
                    "Saved transform parameters differ from the native definition.",
                    "UNAVAILABLE",
                )
        else:
            rows = []
        parameters = []
        for order, parameter in enumerate(composition["parameters"]):
            name = parameter["name"]
            current = rows[order] if existing is not None else parameter
            if not isinstance(current, dict) or current.get("name") != name:
                raise CapabilityError(
                    "A saved transform parameter identity or order changed.",
                    "UNAVAILABLE",
                )
            if existing is not None:
                for key, expected in parameter.items():
                    if (
                        key not in _PARAMETER_STATE
                        and key in current
                        and current[key] != expected
                    ):
                        raise CapabilityError(
                            "Saved transform parameter metadata differs from the current native definition.",
                            "STALE_PREVIEW",
                        )
                if "order" in current and (
                    type(current["order"]) is not int or current["order"] != order
                ):
                    raise CapabilityError(
                        "A saved transform parameter order changed.", "UNAVAILABLE"
                    )
            readonly = any(
                parameter.get(key) is True
                for key in ("readonly", "readOnly", "read_only")
            )
            if readonly and name in values:
                raise CapabilityError(
                    "Read-only transform parameters cannot be supplied."
                )
            if (
                readonly
                and existing is not None
                and current.get("value") != parameter.get("value")
            ):
                raise CapabilityError(
                    "A saved read-only transform parameter differs from its native value.",
                    "STALE_PREVIEW",
                )
            row = copy.deepcopy(current if existing is not None else parameter)
            if name in values or "value" in current:
                value = values[name] if name in values else current["value"]
                row["value"] = _value(
                    parameter, value, wire=existing is not None or name not in values
                )
                for binding in _parameter_bindings(parameter, row["value"]):
                    if consumer is not None:
                        await resolver.validate(binding, consumer)
                    else:
                        for pill in tree.pills(binding["value"]):
                            resolved = await resolver.resolve(pill)
                            produced = data.ValueType(
                                resolved["type"], resolved.get("reference") or ""
                            )
                            target = data.value_type(binding)
                            data.compatible(
                                produced,
                                target,
                                interpolated=binding["value"] != "{{" + pill + "}}",
                            )
                            if (
                                target.reference
                                and produced.reference
                                and not await resolver._is_table(
                                    produced.reference, target.reference
                                )
                            ):
                                raise CapabilityError(
                                    "The transform parameter pill reference table is incompatible."
                                )
            elif parameter.get("mandatory") is True:
                raise CapabilityError(
                    "A mandatory transform parameter has no native value."
                )
            if new:
                # Canonical pharmacy schema472650-472668 has order, not the
                # editor state's uiId. Existing saved metadata is left strict.
                row.pop("uiId", None)
                row["order"] = order
            parameters.append(row)
        config = (
            copy.deepcopy(existing)
            if existing is not None
            else {"id": composition["id"]}
        )
        config["parameters"] = parameters
        if new:
            # Canonical composition schema472637-472644 omits UI-only uiId.
            config["order"] = index
        result.append(config)
    if definition["label"] in {"String to Date", "Date to String"}:
        date_parameters = {
            row["name"]: row for config in result for row in config["parameters"]
        }
        selected = date_parameters.get("date_format", {}).get("value")
        # Both the local reference and the native descriptions make this field
        # conditionally mandatory; metadata.mandatory alone is false here.
        if (
            selected == "NULL_OVERRIDE" or tree.pills(selected)
        ) and date_parameters.get("custom_format", {}).get("value") in (None, ""):
            raise CapabilityError(
                "Custom Format is required when the date format selects or may select the native custom option."
            )
    return result


def _advance(
    definition: dict[str, Any],
    compositions: list[dict[str, Any]],
    current: dict[str, Any],
    schema: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    label = _NATIVE_LABELS.get(definition["label"], definition["label"])
    allowed, output = _SIGNATURES[label]
    kind = data.value_type(current)
    source_kind = (
        "string"
        if kind.kind in {"choice", "email", "html", "xml", "password2"}
        else kind.kind
    )
    if allowed is not None and source_kind not in allowed:
        raise CapabilityError(
            "The producer type is incompatible with the native transform function."
        )
    if label in {"Is Blank", "Is Not Blank"} and (
        kind.reference
        or kind.kind
        in {"reference", "document_id", "records", "glide_list", "array.reference"}
    ):
        raise CapabilityError(
            "Is Blank and Is Not Blank do not support reference inputs."
        )
    if label == "Split":
        separators = [
            row
            for composition in compositions
            for row in composition["parameters"]
            if row.get("label") == "Separator"
        ]
        if len(separators) != 1:
            raise CapabilityError(
                "Split requires the native Separator parameter metadata to establish its conditional output type.",
                "UNAVAILABLE",
            )
        separator = separators[0].get("value")
        if tree.pills(separator):
            raise CapabilityError(
                "A dynamic Split separator can return either String or Array.String; its final type cannot be established statically.",
                "UNAVAILABLE",
            )
        output = "string" if separator in (None, "") else "array.string"
    if output == "preserve":
        return copy.deepcopy(current), copy.deepcopy(schema)
    result = {"type": output, "reference": "", "base_type": output}
    if output == "object":
        if schema is None:
            raise CapabilityError(
                "Array item transforms require the actual producer item schema.",
                "UNAVAILABLE",
            )
        children = data.children(schema)
        if len(children) != 1 or data.value_type(children[0]).kind != "object":
            raise CapabilityError(
                "Array item transforms require a declared Complex Object item.",
                "UNAVAILABLE",
            )
        return result, copy.deepcopy(data.spec(children[0]))
    if output == "array.string":
        return result, {
            "type": output,
            "children": [{"name": "item", "type": "string", "children": []}],
        }
    return result, None


async def resolve(
    flow: dict[str, Any],
    pill: str,
    resolver: PillResolver,
    consumer: tree.Step | None = None,
) -> dict[str, Any]:
    """Resolve and validate saved nonidentity chains against fresh definitions."""
    path = source(flow, pill)
    current = copy.deepcopy(await resolver.resolve(path, consumer))
    if not pill.startswith("fd_transform:"):
        return current
    identity = pill.removeprefix("fd_transform:")
    rows = _pharmacy(flow)
    chain = [
        (key, row) for key, row in rows.items() if row.get("sourceUiId") == identity
    ]
    if not chain:
        return current
    metadata = await _metadata(resolver)
    catalog = {row["id"]: row for row in metadata["definitions"]}
    if any(type(row.get("order")) is not int or row["order"] < 1 for _, row in chain):
        raise CapabilityError("Saved transform orders are malformed.", "UNAVAILABLE")
    chain.sort(key=lambda entry: entry[1]["order"])
    schema = copy.deepcopy(resolver.schemas.get(path))
    if schema is not None and consumer is not None:
        schema = await resolver._whole_schema(path, consumer)
    active = getattr(resolver, "input_transform_active", set())
    if identity in active:
        raise CapabilityError("Transform parameter references contain a cycle.")
    resolver.input_transform_active = active
    active.add(identity)
    try:
        for order, (key, row) in enumerate(chain, start=1):
            _binding(flow, key, row)
            if row["order"] != order or row.get("prescription"):
                raise CapabilityError(
                    "Saved transform chain orders or sources are inconsistent.",
                    "UNAVAILABLE",
                )
            definition = _definition(catalog, row.get("transformDefinitionId", ""))
            compositions = await _parameters(
                definition, [], resolver, consumer, saved=row.get("compositions", [])
            )
            current, schema = _advance(definition, compositions, current, schema)
            metadata["transform_sys_ids"] = sorted(
                set(metadata["transform_sys_ids"]) | {definition["id"]}
            )
    finally:
        active.remove(identity)
    current.update(name=pill, label=current.get("label", path))
    if schema is not None:
        resolver.schemas[pill] = schema
    return current


def _referenced(flow: dict[str, Any], identity: str) -> bool:
    # componentInstances is the old mirror while ordered edits are in flight;
    # the planner rebuilds it from the authoritative three step collections.
    return f"fd_transform:{identity}" in tree.pills(
        {key: value for key, value in flow.items() if key != "componentInstances"}
    )


async def apply(
    flow: dict[str, Any],
    step: tree.Step,
    transforms: Any,
    resolver: PillResolver,
    *,
    seed: str,
) -> list[dict[str, Any]]:
    """Mutate only selected bindings and their native root/chain rows, atomically."""
    from servicenow_mcp.capabilities.flows import input_modes

    options = parse(transforms)
    rows = copy.deepcopy(_pharmacy(flow))
    staged: list[tuple[dict[str, Any], dict[str, Any]]] = []
    retired: set[str] = set()
    metadata = None
    for name, option in options.items():
        item = input_modes.input_binding(step, name)
        previous = data.whole_pill(item.get("value"))
        if any(
            data.spec(item).get(key) is True
            for key in ("readonly", "readOnly", "read_only")
        ) or item.get("scriptActive"):
            raise CapabilityError(
                "Read-only or scripted inputs cannot use transform authoring."
            )
        if option is None:
            if previous is None or not previous.startswith("fd_transform:"):
                raise CapabilityError(
                    "Clearing transforms requires an existing saved transform root."
                )
            await resolve(flow, previous, resolver, step)
            path = source(flow, previous)
            value = "{{" + path + "}}"
            candidate = copy.deepcopy(item)
            data.set_value(candidate, value)
            await resolver.validate(candidate, step)
            retired.add(previous.removeprefix("fd_transform:"))
        else:
            if previous and previous.startswith("fd_transform:"):
                await resolve(flow, previous, resolver, step)
            metadata = await _metadata(resolver)
            catalog = {row["id"]: row for row in metadata["definitions"]}
            path = data.whole_pill(option["source"])
            assert path is not None  # parse establishes an ordinary whole pill
            current = copy.deepcopy(await resolver.resolve(path, step))
            schema = copy.deepcopy(resolver.schemas.get(path))
            if schema is not None:
                schema = await resolver._whole_schema(path, step)
            namespace = uuid.uuid5(
                uuid.NAMESPACE_URL, f"servicenow/transform/{seed}/{step.uid}/{name}"
            )
            identity = str(uuid.uuid5(namespace, "root"))
            if identity in rows:
                raise CapabilityError(
                    "A deterministic transform identity collides with saved pharmacy metadata."
                )
            rows[identity] = {
                "uiId": identity,
                "order": 0,
                "prescription": option["source"],
            }
            for order, transform in enumerate(option["transforms"], start=1):
                definition = _definition(catalog, transform["transform_sys_id"])
                transform_id = str(uuid.uuid5(namespace, f"transform/{order}"))
                if transform_id in rows:
                    raise CapabilityError(
                        "A deterministic transform identity collides with saved pharmacy metadata."
                    )
                compositions = await _parameters(
                    definition,
                    transform.get("compositions", []),
                    resolver,
                    step,
                    new=True,
                )
                current, schema = _advance(definition, compositions, current, schema)
                compound = {
                    "uiId": transform_id,
                    "sourceUiId": identity,
                    "order": order,
                    "transformDefinitionId": definition["id"],
                }
                if compositions:
                    compound["compositions"] = compositions
                rows[transform_id] = compound
                metadata["transform_sys_ids"] = sorted(
                    set(metadata["transform_sys_ids"]) | {definition["id"]}
                )
            value = "{{fd_transform:" + identity + "}}"
            candidate = copy.deepcopy(item)
            data.set_value(candidate, value)
            # Reuse the normal final-value validation, including reference-table,
            # complex schema and choice checks, without mutating the flow first.
            had_pharmacy = "pharmacy" in flow
            old_pharmacy = flow.get("pharmacy")
            flow["pharmacy"] = {
                **copy.deepcopy(old_pharmacy or {}),
                "pharmacyCompound": rows,
            }
            try:
                await resolver.validate(candidate, step)
            finally:
                if had_pharmacy:
                    flow["pharmacy"] = old_pharmacy
                else:
                    flow.pop("pharmacy", None)
            if previous and previous.startswith("fd_transform:"):
                retired.add(previous.removeprefix("fd_transform:"))
        staged.append((item, candidate))
    for item, candidate in staged:
        item.clear()
        item.update(candidate)
        if isinstance(step.raw.get("data"), dict):
            step.raw["data"][item["name"]] = copy.deepcopy(item["value"])
    for identity in retired:
        if not _referenced(flow, identity):
            rows = {
                key: row
                for key, row in rows.items()
                if key != identity and row.get("sourceUiId") != identity
            }
    if options:
        flow["pharmacy"] = {
            **copy.deepcopy(flow.get("pharmacy") or {}),
            "pharmacyCompound": rows,
        }
    if metadata is None:
        metadata = getattr(resolver, "input_transform_metadata", None)
    if metadata is None:
        return []
    return [
        {
            **copy.deepcopy(metadata),
            "consumer_ui_id": step.uid,
            "input_names": sorted(options),
        }
    ]
