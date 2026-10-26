"""Studio declaration trees and packed array item schemas, without platform I/O.

UI registration: bundle:22837-22878. Save packs the immediate array item
into attributes or fieldFacetMap:23273-23319. Read restores it:23379-23434.
Only edited roots are converted. Unknown metadata stays on its owner.
"""

import copy
import re
from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError

ARRAYS = {
    "array.string": "string",
    "array.integer": "integer",
    "array.boolean": "boolean",
    "array.object": "object",
    "array.reference": "reference",
    "array.choice": "choice",
    "array.datetime": "glide_date_time",
    "array.array": "array",
}
LABELS = {
    "object": "Object",
    "array.string": "Array.String",
    "array.integer": "Array.Integer",
    "array.boolean": "Array.True/False",
    "array.object": "Array.Object",
    "array.reference": "Array.Reference",
    "array.choice": "Array.Choice",
    "array.datetime": "Array.Date/Time",
    "array.array": "Array.Array",
}
PACKED = (
    "name",
    "label",
    "type",
    "type_label",
    "defaultValue",
    "hint",
    "maxsize",
    "uiUniqueId",
)
CHOICE_FIELDS = (
    "choices",
    "defaultChoices",
    "defaultValue",
    "choiceOption",
    "choiceType",
    "table",
    "columnName",
)


def choice_source(row: dict[str, Any]) -> dict[str, str] | None:
    """Read a native static/table choice identity; unresolved providers fail closed."""
    table, field = row.get("table"), row.get("columnName")
    table_backed = bool(table or field or row.get("choiceType") == "2")
    if (
        any(row.get(key) for key in ("dynamic", "data_structure"))
        or not table_backed
        and any(
            row.get(key)
            for key in (
                "dependent_on",
                "dependent",
                "dependent_value",
                "ref_qual",
                "reference_qual",
                "reference_qual_condition",
                "dynamic_ref_qual",
            )
        )
        or row.get("choiceOption") not in (None, "", "1", "3")
    ):
        raise CapabilityError(
            "Dependent, qualified, or dynamic choice schemas require another resolver.",
            "UNAVAILABLE",
        )
    if table or field or row.get("choiceType") == "2":
        if (
            row.get("choiceType") not in (None, "", "2")
            or not isinstance(table, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", table)
            or not isinstance(field, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", field)
            or row.get("choices") != []
        ):
            raise CapabilityError(
                "Table-backed choices require a table, columnName, and empty native choices.",
                "UNAVAILABLE",
            )
        return {"table": table, "field": field}
    if row.get("choiceType") not in (None, "", "1"):
        raise CapabilityError("The native choice source is unresolved.", "UNAVAILABLE")
    return None


def choices(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Read native allowed choices without flattening table-backed declarations."""
    # Parameter descriptors also carry owner table/column and dependency facets.
    # Only explicit authoring mode 2 uses defaultChoices; a resolved descriptor's
    # choices remain authoritative, including when they are empty.
    if (
        any(row.get(key) for key in ("dynamic", "data_structure"))
        or row.get("choiceType") not in (None, "", "1", "2")
        or row.get("choiceOption") not in (None, "", "1", "3")
    ):
        raise CapabilityError(
            "The native choice provider or mode is unresolved.", "UNAVAILABLE"
        )
    source = choice_source(row) if row.get("choiceType") == "2" else None
    result = row.get("defaultChoices" if source else "choices")
    if (
        not isinstance(result, list)
        or not 1 <= len(result) <= 5000
        or any(
            not isinstance(choice, dict)
            or not isinstance(choice.get("value"), str)
            or not isinstance(choice.get("label"), str)
            or not choice["label"].strip()
            for choice in result
        )
        or source is None
        and len({choice["value"] for choice in result}) != len(result)
        or source is not None
        and any(
            not isinstance(choice.get("parameters", {}), dict)
            for choice in result
            if isinstance(choice, dict)
        )
    ):
        raise CapabilityError(
            "Choice schemas require one through 5000 labeled string options, uniquely valued for static lists.",
            "UNAVAILABLE",
        )
    return result


def reference_table(row: dict[str, Any]) -> str:
    """Read Array.Reference's authoritative parent table, never child metadata."""
    table = row.get("reference")
    if not isinstance(table, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", table):
        raise CapabilityError(
            "Array.Reference declarations require a static reference table.",
            "UNAVAILABLE",
        )
    return table


def identity(row: dict[str, Any]) -> str | None:
    value = (
        row.get("uiUniqueId")
        or row.get("fieldFacetMap", {}).get("uiUniqueId")
        or row.get("attributes", {}).get("uiUniqueId")
    )
    if value is not None and not isinstance(value, str):
        raise CapabilityError(
            "Declaration UI identity must be a string.", "UNAVAILABLE"
        )
    return value


def unpack(row: dict[str, Any], *, recursive: bool = True) -> dict[str, Any]:
    """Restore the synthetic array item for a local schema view, not a payload."""
    result = copy.deepcopy(row)
    kind = result.get("type")
    if kind in ARRAYS:
        storage = "fieldFacetMap" if result.get("parent") else "attributes"
        metadata = result.get(storage)
        if not isinstance(metadata, dict) or not all(
            key in metadata for key in ("child_name", "child_type", "child_uiUniqueId")
        ):
            raise CapabilityError(
                "Array declaration item metadata is incomplete.", "UNAVAILABLE"
            )
        if (
            metadata["child_type"] != ARRAYS[kind]
            or not isinstance(metadata["child_name"], str)
            or not metadata["child_name"]
            or not isinstance(metadata["child_uiUniqueId"], str)
            or not metadata["child_uiUniqueId"]
        ):
            raise CapabilityError(
                "Array declaration item identity or type is contradictory.",
                "UNAVAILABLE",
            )
        item = {
            key: copy.deepcopy(metadata["child_" + key])
            for key in PACKED
            if "child_" + key in metadata
        }
        item.update(
            name=result["name"] + "." + str(item["name"]).split(".")[-1],
            parent=result["name"],
            cannotBeDeleted=True,
        )
        item["fieldFacetMap"] = {"uiUniqueId": item["uiUniqueId"]}
        if kind == "array.reference":
            item["reference"] = reference_table(result)
        if kind == "array.choice":
            choices(result)
            for key in CHOICE_FIELDS:
                if key in result:
                    item[key] = copy.deepcopy(result[key])
        item["children"] = result.get("children", []) if kind == "array.object" else []
        if kind != "array.object" and result.get("children"):
            raise CapabilityError(
                "A primitive array declaration contains unexpected children.",
                "UNAVAILABLE",
            )
        result["children"] = [item]
    children = result.get("children", [])
    if not isinstance(children, list) or any(
        not isinstance(child, dict) for child in children
    ):
        raise CapabilityError(
            "Nested declaration registration is incomplete.", "UNAVAILABLE"
        )
    result["children"] = (
        [unpack(child) for child in children] if recursive else children
    )
    if "children" not in row and not children:
        result.pop("children", None)
    return result


def pack(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize one edited UI declaration tree without rewriting other roots."""
    result = copy.deepcopy(row)
    if result.get("type") in ARRAYS:
        items = result.get("children", [])
        if len(items) != 1 or items[0].get("type") != ARRAYS[result["type"]]:
            raise CapabilityError(
                "Array declarations require exactly one matching item schema.",
                "UNAVAILABLE",
            )
        item = items[0]
        if result["type"] == "array.reference":
            table = reference_table(result)
            if item.get("reference", table) != table or item.get("children"):
                raise CapabilityError(
                    "Array.Reference item schema contradicts its parent table or scalar type.",
                    "UNAVAILABLE",
                )
        if result["type"] == "array.choice":
            choices(item)
            for key in CHOICE_FIELDS:
                if key in item:
                    result[key] = copy.deepcopy(item[key])
        if result["type"] != "array.object" and item.get("children"):
            raise CapabilityError(
                "A scalar Array item schema cannot contain child declarations.",
                "UNAVAILABLE",
            )
        storage = "fieldFacetMap" if result.get("parent") else "attributes"
        metadata = result.setdefault(storage, {})
        for key in PACKED:
            if key in item:
                metadata["child_" + key] = copy.deepcopy(
                    item[key].split(".")[-1] if key == "name" else item[key]
                )
        result["children"] = (
            [pack(child) for child in item.get("children", [])]
            if result["type"] == "array.object"
            else []
        )
    elif "children" in result:
        result["children"] = [pack(child) for child in result["children"]]
    return result


def validate(row: dict[str, Any]) -> None:
    """Reject ambiguous registration and conflicting UI identities before edits."""
    identities = set()
    for path, node in walk([row]):
        siblings = node.get("children", [])
        if len({child["name"].split(".")[-1] for child in siblings}) != len(siblings):
            raise CapabilityError(
                "Nested declaration names are ambiguous.", "UNAVAILABLE"
            )
        uid = identity(node)
        copies = [
            node.get("uiUniqueId"),
            node.get("fieldFacetMap", {}).get("uiUniqueId"),
            node.get("attributes", {}).get("uiUniqueId"),
        ]
        if any(value and value != uid for value in copies) or uid and uid in identities:
            raise CapabilityError(
                "Nested declaration UI identities are contradictory.", "UNAVAILABLE"
            )
        if uid:
            identities.add(uid)
        if node.get("parent") and node["parent"] != path.rpartition(".")[0]:
            raise CapabilityError(
                "Nested declaration parent registration is contradictory.",
                "UNAVAILABLE",
            )
        if node.get("children") and node.get("type") not in {"object", *ARRAYS}:
            raise CapabilityError(
                "A scalar declaration contains child registrations.", "UNAVAILABLE"
            )


def walk(
    rows: list[dict[str, Any]], prefix: str = ""
) -> list[tuple[str, dict[str, Any]]]:
    """Visit persisted nodes. Array item UI identities live in packed metadata."""
    result = []
    for row in rows:
        path = (
            row["name"]
            if not prefix or row["name"].startswith(prefix + ".")
            else prefix + "." + row["name"]
        )
        result.append((path, row))
        child_prefix = path
        if row.get("type") == "array.object":
            metadata = row.get(
                "fieldFacetMap" if row.get("parent") else "attributes", {}
            )
            item_name = (
                metadata.get("child_name") if isinstance(metadata, dict) else None
            )
            if (
                isinstance(item_name, str)
                and item_name
                and not any(
                    child.get("cannotBeDeleted") for child in row.get("children", [])
                )
            ):
                child_prefix = path + "." + item_name.split(".")[-1]
        result.extend(walk(row.get("children", []), child_prefix))
    return result


def locate(
    rows: list[dict[str, Any]], path: str, prefix: str = ""
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    for row in rows:
        qualified = (
            row["name"]
            if not prefix or row["name"].startswith(prefix + ".")
            else prefix + "." + row["name"]
        )
        if qualified == path:
            return rows, row
        if path.startswith(qualified + "."):
            return locate(row.get("children", []), path, qualified)
    raise CapabilityError("The declaration path is absent or ambiguous.")
