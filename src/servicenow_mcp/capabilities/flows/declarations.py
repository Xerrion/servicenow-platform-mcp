"""Studio declaration trees and packed array item schemas, without platform I/O.

UI registration: bundle:22837-22878. Save packs the immediate array item
into attributes or fieldFacetMap:23273-23319. Read restores it:23379-23434.
Only edited roots are converted. Unknown metadata stays on its owner.
"""

import copy
from typing import Any

from servicenow_mcp.capabilities.contracts import CapabilityError

ARRAYS = {
    "array.string": "string",
    "array.integer": "integer",
    "array.boolean": "boolean",
    "array.object": "object",
}
LABELS = {
    "object": "Object",
    "array.string": "Array.String",
    "array.integer": "Array.Integer",
    "array.boolean": "Array.True/False",
    "array.object": "Array.Object",
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
