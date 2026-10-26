"""Source-native scalar wire values; schema metadata is never rewritten.

Studio bundle: types 11073-11156; name/value 53294-53423,47539-47623,
156006-156031,163799-163833; numeric 54310-54316,55722-55741,56022-56068;
text 55968-56002,56608-56627,56752-56846; references 193457-193484,
317798-317828; lists 49048-49094; due dates 48618-48933,126172-126193.
"""

import json
import math
import re
from decimal import Decimal
from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import tree
from servicenow_mcp.validation import validate_identifier, validate_sys_id

TEXT_TYPES = frozenset(
    {
        "string",
        "string_full_utf8",
        "text",
        "longtext",
        "multi_small",
        "html",
        "translated_html",
        "email",
        "url",
    }
)
NUMERIC_TYPES = frozenset({"float", "decimal", "double", "number"})
INTEGER_TYPES = frozenset({"integer", "longint"})
REFERENCE_TYPES = frozenset(
    {"reference", "document_id", "records", "glide_list", "table_name"}
)
TEMPORAL_TYPES = frozenset(
    {
        "glide_duration",
        "duration",
        "glide_time",
        "glide_date_time",
        "datetime",
        "date",
        "glide_date",
        "due_date",
        "calendar_date_time",
    }
)
TYPES = (
    TEXT_TYPES
    | NUMERIC_TYPES
    | INTEGER_TYPES
    | REFERENCE_TYPES
    | TEMPORAL_TYPES
    | {
        "slushbucket",
        "approval_rules",
        "schedule_date_time",
        "template_value",
        "conditions",
        "name_values",
        "simple_name_values",
        "field_list",
    }
)


def input_state(item: dict[str, Any]) -> dict[str, bool]:
    """Read native flags, not inferred dependency expressions or hidden defaults.

    Visibility is only a UI property (152823-152831); it does not cancel the
    parameter/binding mandatory OR (17135-17142) or permit read-only writes.
    """
    parameter = item.get("parameter")
    if not isinstance(parameter, dict):
        parameter = item
    attributes = parameter.get("attributes", {})
    return {
        "visible": not any(
            attributes.get(key) == "false"
            for key in ("visible_in_ui", "visible_in_fd", "visible_in_ad")
        ),
        "mandatory": any(
            row.get("mandatory") in (True, "true") for row in (item, parameter)
        ),
        "readonly": any(
            row.get(key) in (True, "true")
            for row in (item, parameter)
            for key in ("readonly", "readOnly", "read_only")
        ),
    }


def _json(value: str, label: str) -> Any:
    try:
        return json.loads(value)
    except ValueError:
        raise CapabilityError(f"{label} require native JSON text.") from None


def _pills(value: str, *, whole: bool = False) -> bool:
    unmatched = tree.PILL.sub("", value)
    if "{{" in unmatched or "}}" in unmatched:
        raise CapabilityError("The input contains a malformed pill reference.")
    found = bool(tree.pills(value))
    if found and whole and not tree.PILL.fullmatch(value):
        raise CapabilityError(
            "This native scalar requires one complete typed pill, not interpolation."
        )
    return found


def numeric(kind: str, value: Any, parameter: dict[str, Any]) -> None:
    """Preserve numeric widget strings and existing JSON numbers without coercion.

    Only integer/longint declare min_value/max_value handling. No native source
    establishes a universal 32/64-bit or floating-point magnitude limit.
    """
    integer = kind in INTEGER_TYPES
    if isinstance(value, str):
        pattern = (
            r"[+-]?[0-9]+"
            if integer
            else r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
        )
        if not re.fullmatch(pattern, value):
            raise CapabilityError(
                "Numeric inputs require finite native numeric text or JSON numbers."
            )
        try:
            number = Decimal(value)
        except ArithmeticError:
            raise CapabilityError(
                "The numeric text is outside the native parser's supported range."
            ) from None
    elif (
        type(value) is int
        or not integer
        and type(value) is float
        and math.isfinite(value)
    ):
        number = Decimal(str(value))
    else:
        raise CapabilityError(
            "Integer inputs require whole numbers."
            if integer
            else "Numeric inputs require finite numbers, not booleans or objects."
        )
    if integer:
        attributes = parameter.get("attributes", {})
        for key, upper in (("min_value", False), ("max_value", True)):
            bound = attributes.get(key)
            if not bound:
                continue
            # Native parseInt accepts the initial integer portion of metadata.
            match = re.match(r"\s*([+-]?)(?:0[xX]([0-9a-fA-F]+)|([0-9]+))", str(bound))
            if not match:
                raise CapabilityError(
                    "The native integer boundary metadata is unavailable.",
                    "UNAVAILABLE",
                )
            limit = int(match[2], 16) if match[2] else int(match[3])
            if match[1] == "-":
                limit = -limit
            if (upper and number > limit) or (not upper and number < limit):
                raise CapabilityError(
                    "The integer input is outside its declared limits."
                )


def name_values(value: str, parameter: dict[str, Any]) -> None:
    """Validate the two emitted JSON string formats, never an invented row DTO."""
    if value == "" or tree.PILL.fullmatch(value):
        return
    result = _json(value, "Name-value inputs")
    attributes = parameter.get("attributes", {})
    advanced = bool(attributes.get("advanced_name_values"))
    if advanced:
        if (
            not isinstance(result, dict)
            or set(result) != {"type", "value"}
            or result["type"] != "ADV_NV"
        ):
            raise CapabilityError(
                "Advanced name-value inputs require the native ADV_NV envelope."
            )
        result = result["value"]
        if attributes.get("advanced_inputs_config") not in (None, "", "OMIT"):
            raise CapabilityError(
                "This advanced name-value configuration is unavailable.", "UNAVAILABLE"
            )
    if not isinstance(result, list):
        raise CapabilityError("Name-value JSON requires an array of native rows.")
    for row in result:
        keys = {"name", "value", "attributes"} if advanced else {"name", "value"}
        if (
            not isinstance(row, dict)
            or set(row) != keys
            or any(not isinstance(row[key], str) for key in ("name", "value"))
        ):
            raise CapabilityError(
                "Name-value rows require native string name/value fields."
            )
        for key in ("name", "value"):
            if _pills(row[key]) and advanced and not attributes.get(key + "_candrop"):
                raise CapabilityError(
                    "This advanced name-value field does not permit data pills."
                )
        if advanced:
            extra = row["attributes"]
            allowed = (
                {"omit_if_empty", "mandatory"}
                if attributes.get("advanced_inputs_config") == "OMIT"
                else set()
            )
            if not isinstance(extra, dict) or set(extra) - allowed:
                raise CapabilityError(
                    "Advanced name-value attributes do not match the native configuration."
                )
            for key, flag in extra.items():
                if type(flag) is not bool and not (
                    key == "omit_if_empty"
                    and isinstance(flag, str)
                    and tree.PILL.fullmatch(flag)
                ):
                    raise CapabilityError(
                        "Advanced name-value options require native booleans or an allowed complete boolean pill."
                    )
            if extra.get("mandatory") is True and not row["value"]:
                raise CapabilityError("A mandatory advanced name-value entry is empty.")


def reference(value: str, parameter: dict[str, Any]) -> None:
    """Accept raw sys_ids or the picker-emitted JSON string, preserving both."""
    if not value.startswith("{"):
        validate_sys_id(value)
        return
    result = _json(value, "Reference inputs")
    if (
        not isinstance(result, dict)
        or not {"display", "value"} <= set(result)
        or set(result) - {"display", "value", "table", "sys_id", "data_source"}
    ):
        raise CapabilityError("Reference JSON requires the native picker fields.")
    if any(not isinstance(part, str) for part in result.values()):
        raise CapabilityError("Native reference picker fields require strings.")
    validate_sys_id(result["value"])
    if "sys_id" in result and result["sys_id"] != result["value"]:
        raise CapabilityError("The reference picker sys_id contradicts its value.")
    if result.get("table"):
        validate_identifier(result["table"])
    if "data_source" in result:
        if (
            parameter.get("attributes", {}).get("is_remote_table_reference") != "true"
            or not result["data_source"]
        ):
            raise CapabilityError(
                "Remote reference data sources require native remote-table metadata."
            )
        validate_identifier(result["data_source"])


def condition_paths(value: str) -> list[list[str]]:
    """Bounded native query fields. Values never establish an output enum."""
    if any(
        marker in value.lower()
        for marker in ("javascript:", "^nq", "^orderby", "^groupby")
    ):
        raise CapabilityError(
            "Conditions require an encoded query without script or query directives."
        )
    if value in ("", "^EQ"):
        return []
    paths: list[list[str]] = []
    for term in value.split("^"):
        if term == "EQ":
            continue
        term = term.removeprefix("OR")
        match = re.fullmatch(
            r"([a-z][a-z0-9_.]*?)(!=|>=|<=|=|>|<|NOT IN|IN|ISNOTEMPTY|ISEMPTY|STARTSWITH|ENDSWITH|NOT LIKE|LIKE)(.*)",
            term,
        )
        if not match or len(paths) >= 50:
            raise CapabilityError(
                "Conditions require at most 50 supported field/operator clauses.",
                "UNAVAILABLE",
            )
        path = match[1].split(".")
        if len(path) > 10 or any(not segment for segment in path):
            raise CapabilityError("Condition paths require at most ten named fields.")
        if match[2] in {"ISNOTEMPTY", "ISEMPTY"} and match[3]:
            raise CapabilityError(
                "Empty-field condition operators do not accept a value."
            )
        paths.append(path)
    return paths


def template_fields(value: Any) -> list[tuple[str, str]]:
    """Parse native escaped field values; specialized modes validate elsewhere.

    splitData/processField/convertToValue:53733-53884. A leading template pill
    is independent from field values; fd-scripted and coalesce_fields belong
    to the template-mode adapter, not ordinary dictionary literal validation.
    """
    if not isinstance(value, str):
        raise CapabilityError("Template values require native encoded text.")
    if not value:
        return []
    # convertToValue([], data) emits the leading pill plus its separator.
    if value.endswith("^") and tree.PILL.fullmatch(value[:-1]):
        return []
    parts = re.findall(r"(?:[^\^]|\^\^)+", value)
    if "^".join(parts) != value:
        raise CapabilityError("Template values contain an empty native field clause.")
    if parts and tree.PILL.fullmatch(parts[0]):
        parts = parts[1:]
    fields: list[tuple[str, str]] = []
    for part in parts:
        name, separator, encoded = part.partition("=")
        if not separator or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
            raise CapabilityError("Template fields require static column=value pairs.")
        if (
            "javascript:" in encoded.lower()
            or "fd-scripted" in encoded.lower()
            and encoded != "fd-scripted"
        ):
            raise CapabilityError(
                "Template script values require the exact native fd-scripted marker."
            )
        fields.append((name, encoded.replace("^^", "^")))
    if len({name for name, _ in fields}) != len(fields):
        raise CapabilityError("Template field names must be unique.")
    return fields


def kind(item: dict[str, Any]) -> str | None:
    parameter = item.get("parameter")
    if not isinstance(parameter, dict):
        parameter = item
    result = parameter.get("type") or item.get("type") or item.get("internalType")
    return result if isinstance(result, str) else None


def selections(value: str, parameter: dict[str, Any]) -> list[tuple[str, str]]:
    """Parse catalog-wrapper CSV; legacy bare IDs belong to item_option_new."""
    if not value:
        return []
    result: list[tuple[str, str]] = []
    for entry in value.split(","):
        identity, separator, table = entry.partition(":")
        validate_sys_id(identity)
        if separator:
            validate_identifier(table)
            if "." in table:
                raise CapabilityError(
                    "Selections require a single native source table name."
                )
        pair = identity, table if separator else "item_option_new"
        if pair in result:
            raise CapabilityError("Duplicate native selections are not allowed.")
        result.append(pair)
    return result


def slushbucket(value: Any, parameter: dict[str, Any]) -> None:
    """The generic widget emits option arrays; its catalog wrapper emits CSV."""
    if isinstance(value, str):
        selections(value, parameter)
        return
    if not isinstance(value, list):
        raise CapabilityError(
            "Native slushbucket selections require option arrays or catalog CSV."
        )
    if parameter.get("attributes", {}).get("use_flow_designer_wrapper"):
        raise CapabilityError(
            "The catalog slushbucket wrapper requires native CSV text."
        )
    if not value:
        return
    available = parameter.get("availableList")
    if not isinstance(available, list) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("value"), str)
        or not isinstance(row.get("label"), str)
        for row in available
    ):
        raise CapabilityError(
            "The generic slushbucket's native available options are unavailable.",
            "UNAVAILABLE",
        )
    options = {row["value"]: row["label"] for row in available}
    seen: set[str] = set()
    for row in value:
        if (
            not isinstance(row, dict)
            or set(row) != {"label", "value"}
            or not isinstance(row["value"], str)
            or not isinstance(row["label"], str)
        ):
            raise CapabilityError(
                "Generic slushbucket selections require native label/value rows."
            )
        if row["value"] not in options or options[row["value"]] != row["label"]:
            raise CapabilityError(
                "The selected value or label is not a declared native option."
            )
        if row["value"] in seen:
            raise CapabilityError("Duplicate native selections are not allowed.")
        seen.add(row["value"])


def approval_references(value: str) -> list[tuple[str, str]]:
    """Parse every emitted approval operator and approver family, without execution.

    Encoder 50075-50162; static nodes 49596-49635; decoder184799-184950;
    operator choices184985-185011; numeric controls49705-49730.
    """
    atom = r"\{\{[^{}]+\}\}"
    group = rf"[UGZ]\[{atom}(?:,{atom})*\]"
    rule = rf"(?:Any|All|Res|[0-9]+[%#])(?:M|(?:{group})+)"
    clause = rf"(?:ApprovesRejects|Approves|Rejects){rule}(?:[&|]{rule})*"
    if not value or not re.fullmatch(rf"{clause}(?:Or{clause})*", value):
        raise CapabilityError(
            "Approval rules require native goals, operators, and manual or typed approver groups."
        )
    for count, unit in re.findall(r"([0-9]+)([%#])", tree.PILL.sub("", value)):
        if not 1 <= int(count) <= (100 if unit == "%" else 500):
            raise CapabilityError(
                "The approval threshold is outside its native control limits."
            )
    references: list[tuple[str, str]] = []
    for group_kind, pills in re.findall(rf"([UGZ])\[({atom}(?:,{atom})*)\]", value):
        table = {"U": "sys_user", "G": "sys_user_group", "Z": "unknown"}[group_kind]
        for pill in tree.pills(pills):
            if pill.startswith("static."):
                validate_sys_id(pill.removeprefix("static."))
            references.append((pill, table))
    types: dict[str, str] = {}
    for pill, table in references:
        if pill in types and types[pill] != table:
            raise CapabilityError(
                "An approval pill cannot represent conflicting approver groups."
            )
        types[pill] = table
    return references


def approval_literal_pills(value: str) -> set[str]:
    """Return only source-validated static approver paths, never producer paths."""
    return {
        pill for pill, _ in approval_references(value) if pill.startswith("static.")
    }


def due_date(value: str, parameter: dict[str, Any]) -> dict[str, Any]:
    if parameter.get("attributes", {}).get("ui_approval") not in (True, "true"):
        raise CapabilityError(
            "This schedule/date widget is not an approval due-date builder.",
            "UNAVAILABLE",
        )
    try:
        result = json.loads(value)
    except ValueError:
        raise CapabilityError("Approval due dates require native JSON text.") from None
    required = {
        "action",
        "date_type",
        "date",
        "duration",
        "duration_type",
        "schedule",
        "schedule_label",
    }
    if not isinstance(result, dict) or set(result) != required:
        raise CapabilityError(
            "Approval due-date JSON fields are incomplete or unsupported."
        )
    if not isinstance(result["action"], str) or result["action"] not in {
        "none",
        "approve",
        "reject",
        "cancel",
    }:
        raise CapabilityError("The approval due-date action is invalid.")
    if not isinstance(result["date_type"], str) or result["date_type"] not in {
        "actual",
        "relative",
    }:
        raise CapabilityError("The approval due-date date type is invalid.")
    if not all(
        isinstance(result[key], str)
        for key in ("date", "duration_type", "schedule", "schedule_label")
    ):
        raise CapabilityError("Approval due-date fields have invalid native types.")
    duration = result["duration"]
    if not (
        type(duration) is int
        and duration > 0
        or isinstance(duration, str)
        and duration.isascii()
        and duration.isdecimal()
        and int(duration) > 0
    ):
        raise CapabilityError(
            "Approval duration requires a positive integer or numeric widget string."
        )
    if result["duration_type"] not in {
        "minutes",
        "hours",
        "days",
        "weeks",
        "months",
        "quarters",
        "years",
    }:
        raise CapabilityError("The approval duration unit is invalid.")
    if result["schedule"]:
        validate_sys_id(result["schedule"])
    if result["action"] != "none" and not tree.PILL.fullmatch(result["date"]):
        raise CapabilityError(
            "An enabled approval deadline requires one native date/time pill."
        )
    if result["date"] != "{{}}":
        _pills(result["date"], whole=True)
    return result


def validate(kind: str, value: Any, parameter: dict[str, Any]) -> None:
    if kind not in TYPES:
        raise CapabilityError(
            "This scalar widget requires another adapter.", "UNAVAILABLE"
        )
    state = input_state(parameter)
    if state["mandatory"] and value in ("", None):
        raise CapabilityError("The mandatory widget value is empty.")
    if (
        value is None
        and kind
        in TEXT_TYPES
        | NUMERIC_TYPES
        | INTEGER_TYPES
        | REFERENCE_TYPES
        | TEMPORAL_TYPES
        | {"field_list"}
    ):
        return
    if kind == "slushbucket" and isinstance(value, list):
        if state["mandatory"] and not value:
            raise CapabilityError("The mandatory widget value is empty.")
        slushbucket(value, parameter)
        return
    if not isinstance(value, str) and kind not in NUMERIC_TYPES | INTEGER_TYPES:
        raise CapabilityError(
            "Native scalar widget values require strings, not objects or arrays."
        )
    approval = kind == "schedule_date_time" and parameter.get("attributes", {}).get(
        "ui_approval"
    ) in (True, "true")
    if isinstance(value, str):
        # Approval maxsize describes the embedded date widget, not its envelope.
        maximum = (
            None
            if approval
            else parameter.get("maxsize") or parameter.get("max_length")
        )
        # Reference maxsize constrains an identity, not the picker JSON or pill.
        identity = value
        picker_json = (
            kind in {"reference", "document_id"}
            and value.startswith("{")
            and not tree.PILL.fullmatch(value)
        )
        if picker_json:
            parsed = _json(value, "Reference inputs")
            identity = parsed.get("value", "") if isinstance(parsed, dict) else value
        if (
            maximum
            and isinstance(identity, str)
            and not (kind in REFERENCE_TYPES and tree.PILL.fullmatch(value))
            and sum(2 if ord(character) > 0xFFFF else 1 for character in identity)
            > int(maximum)
        ):
            raise CapabilityError("The native widget value exceeds its maximum length.")
        if (
            not approval
            and not picker_json
            and kind
            not in {
                "name_values",
                "simple_name_values",
                "approval_rules",
                "template_value",
                "conditions",
            }
            and _pills(
                value,
                whole=kind
                in NUMERIC_TYPES
                | INTEGER_TYPES
                | REFERENCE_TYPES
                | TEMPORAL_TYPES
                | {"schedule_date_time"},
            )
        ):
            return
        if value == "" and kind != "approval_rules" and not approval:
            return
    if kind in NUMERIC_TYPES | INTEGER_TYPES:
        numeric(kind, value, parameter)
    elif kind in TEMPORAL_TYPES or kind == "schedule_date_time" and not approval:
        from servicenow_mcp.capabilities.flows import logic_blocks

        logic_blocks.validate_value(
            "glide_date_time" if kind == "schedule_date_time" else kind,
            value,
            parameter,
        )
    elif kind in {"reference", "document_id"}:
        reference(value, parameter)
    elif kind in {"records", "glide_list"}:
        identities = value.split(",")
        for identity in identities:
            validate_sys_id(identity)
        if kind == "glide_list" and len(set(identities)) != len(identities):
            raise CapabilityError(
                "Native list selections require unique record identities."
            )
    elif kind == "table_name":
        validate_identifier(value)
        if "." in value:
            raise CapabilityError("Table inputs require one native table name.")
    elif (
        kind == "field_list"
        and value
        and not parameter.get("reference")
        and not parameter.get("dependent_on")
    ):
        raise CapabilityError(
            "Field List values require their native reference or table dependency metadata.",
            "UNAVAILABLE",
        )
    elif kind in {"name_values", "simple_name_values"}:
        name_values(value, parameter)
    elif kind == "slushbucket":
        slushbucket(value, parameter)
    elif kind == "approval_rules":
        approval_references(value)
    elif kind == "template_value":
        template_fields(value)
    elif kind == "conditions":
        condition_paths(value)
    elif kind == "schedule_date_time":
        due_date(value, parameter)


def pill_text(item: dict[str, Any], kind: str | None, parameter: dict[str, Any]) -> Any:
    value = item.get("value")
    if (
        kind == "schedule_date_time"
        and parameter.get("attributes", {}).get("ui_approval") in (True, "true")
        and isinstance(value, str)
    ):
        deadline = due_date(value, parameter)
        return "" if deadline["action"] == "none" else deadline["date"]
    return value


def pill_type(kind: str | None, parameter: dict[str, Any]) -> str | None:
    """An approval envelope binds its embedded date, not the whole JSON object."""
    if kind == "schedule_date_time":
        return "glide_date_time"
    return kind


def pill_bindings(
    item: dict[str, Any], kind: str | None, parameter: dict[str, Any]
) -> list[tuple[Any, str | None]]:
    """Expose native embedded values so producer types are checked per field."""
    value = item.get("value", "")
    if (
        kind == "json"
        and isinstance(value, str)
        and value
        and not tree.PILL.fullmatch(value)
    ):

        def text_values(raw: Any) -> list[tuple[Any, str | None]]:
            if isinstance(raw, str):
                return [(raw, "json")]
            if isinstance(raw, dict):
                return [leaf for child in raw.values() for leaf in text_values(child)]
            if isinstance(raw, list):
                return [leaf for child in raw for leaf in text_values(child)]
            return []

        return text_values(_json(value, "JSON inputs"))
    if (
        kind in {"reference", "document_id"}
        and isinstance(value, str)
        and value.startswith("{")
        and not tree.PILL.fullmatch(value)
    ):
        reference(value, parameter)
        return []
    if kind == "approval_rules":
        return [
            ("{{" + pill + "}}", "reference")
            for pill, _ in approval_references(value)
            if not pill.startswith("static.")
        ]
    if (
        kind not in {"name_values", "simple_name_values"}
        or not value
        or isinstance(value, str)
        and tree.PILL.fullmatch(value)
    ):
        return [(pill_text(item, kind, parameter), pill_type(kind, parameter))]
    name_values(value, parameter)
    parsed = _json(value, "Name-value inputs")
    rows = parsed["value"] if isinstance(parsed, dict) else parsed
    result: list[tuple[Any, str | None]] = []
    for row in rows:
        result.extend((row[key], "string") for key in ("name", "value"))
        if isinstance(row.get("attributes", {}).get("omit_if_empty"), str):
            result.append((row["attributes"]["omit_if_empty"], "boolean"))
    return result
