"""Native scalar widget contracts observed in Studio and the live action schemas.

Slushbucket:130897-130911,51782-51794. Approval rules:50075-50162.
Due-date JSON:48618-48646,48770-48774,126172-126193.
This module validates wire values. It never rewrites caller strings or metadata.
"""

import json
import re
from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.validation import validate_sys_id

TYPES = {
    "slushbucket",
    "approval_rules",
    "schedule_date_time",
    "template_value",
    "conditions",
}


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
    """Parse native field=value pairs without rewriting JSON, HTML, or ^^.

    Studio splitData/processField/convertToValue:53733-53884.
    Whole-template pills, scripted fields, and coalesce controls need adapters.
    """
    if not isinstance(value, str):
        raise CapabilityError("Template values require native encoded text.")
    if not value:
        return []
    parts = re.split(r"(?<!\^)\^(?!\^)", value)
    fields: list[tuple[str, str]] = []
    for part in parts:
        name, separator, encoded = part.partition("=")
        if not separator or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
            raise CapabilityError("Template fields require static column=value pairs.")
        if (
            name == "coalesce_fields"
            or "fd_scripted" in encoded.lower()
            or "javascript:" in encoded.lower()
        ):
            raise CapabilityError(
                "Scripted or coalescing templates require another adapter.",
                "UNAVAILABLE",
            )
        fields.append((name, encoded.replace("^^", "^")))
    if len(fields) > 50 or len({name for name, _ in fields}) != len(fields):
        raise CapabilityError(
            "Template fields must be unique and limited to 50 columns."
        )
    return fields


def kind(item: dict[str, Any]) -> str | None:
    parameter = item.get("parameter")
    if not isinstance(parameter, dict):
        parameter = item
    result = parameter.get("type") or item.get("type") or item.get("internalType")
    return result if isinstance(result, str) else None


def selections(value: str, parameter: dict[str, Any]) -> list[tuple[str, str]]:
    source = parameter.get("attributes", {}).get("source_table")
    if not isinstance(source, str) or source != "item_option_new":
        raise CapabilityError(
            "This slushbucket source requires another selection adapter.", "UNAVAILABLE"
        )
    if not value:
        return []
    result: list[tuple[str, str]] = []
    for entry in value.split(","):
        identity, separator, table = entry.partition(":")
        validate_sys_id(identity)
        if separator and table != source:
            raise CapabilityError(
                "Catalog variable selections require the declared source table."
            )
        pair = identity, table or source
        if pair in result:
            raise CapabilityError(
                "Duplicate catalog variable selections are not allowed."
            )
        result.append(pair)
    if len(result) > 100:
        raise CapabilityError(
            "Catalog variable selection supports at most 100 fields.", "UNAVAILABLE"
        )
    return result


def approval_references(value: str) -> list[tuple[str, str]]:
    """Bounded Any/All user/group clauses. Manual/unknown approvers are excluded."""
    atom = r"\{\{[a-zA-Z0-9_ -]+(?:\.[a-zA-Z0-9_ -]+)+\}\}"
    group = rf"[UG]\[{atom}(?:,{atom})*\]"
    rule = rf"(?:Any|All)(?:{group})+"
    clause = rf"(?:ApprovesRejects|Approves|Rejects){rule}(?:[&|]{rule})*"
    if (
        not value
        or len(value) > 8000
        or not re.fullmatch(rf"{clause}(?:Or{clause})*", value)
    ):
        raise CapabilityError(
            "Approval rules require native Any/All clauses with explicit typed user/group pills. Manual and unknown approver forms are unavailable.",
            "UNAVAILABLE",
        )
    references: list[tuple[str, str]] = []
    for kind, pills in re.findall(r"([UG])\[([^\]]+)\]", value):
        references.extend(
            (pill[2:-2], "sys_user" if kind == "U" else "sys_user_group")
            for pill in pills.split(",")
        )
    if any(
        len({table for candidate, table in references if candidate == pill}) != 1
        for pill, _ in references
    ):
        raise CapabilityError(
            "An approval pill cannot represent both a user and a group."
        )
    return references


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
    if result["action"] not in {"none", "approve", "reject", "cancel"} or result[
        "date_type"
    ] not in {"actual", "relative"}:
        raise CapabilityError("The approval due-date action or date type is invalid.")
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
    if result["action"] != "none":
        # Keep this compatibility increment on the observed no-deadline form.
        # Automatic deadline outcomes need their own explicit authoring scope.
        raise CapabilityError(
            "Automatic approval deadline outcomes are not supported by this compatibility adapter.",
            "UNAVAILABLE",
        )
    if result["date"] != "{{}}":
        raise CapabilityError(
            "A disabled approval deadline requires the native empty-date sentinel."
        )
    return result


def validate(kind: str, value: Any, parameter: dict[str, Any]) -> None:
    if not isinstance(value, str):
        raise CapabilityError(
            "Native scalar widget values require strings, not objects or arrays."
        )
    maximum = (
        16000
        if kind == "schedule_date_time"
        else int(parameter.get("maxsize") or 65000)
    )
    if len(value) > maximum:
        raise CapabilityError("The native widget value exceeds its supported length.")
    if parameter.get("mandatory") is True and not value:
        raise CapabilityError("The mandatory widget value is empty.")
    if kind == "slushbucket":
        selections(value, parameter)
    elif kind == "approval_rules":
        approval_references(value)
    elif kind == "template_value":
        template_fields(value)
    elif kind == "conditions":
        condition_paths(value)
    else:
        due_date(value, parameter)


def pill_text(item: dict[str, Any], kind: str | None, parameter: dict[str, Any]) -> Any:
    value = item.get("value")
    if kind == "schedule_date_time" and isinstance(value, str):
        due_date(value, parameter)
        return ""  # {{}} is a native disabled-date sentinel, not a malformed pill.
    return value
