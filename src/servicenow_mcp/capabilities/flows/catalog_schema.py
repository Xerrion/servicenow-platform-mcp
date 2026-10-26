"""Consumer-only catalog schemas from Studio's native array definition projection.

The wire DTO remains its owner's data. Catalog pills rebuild array items locally
(bundle:62844-62850,23379-23434,23539-23543,23662-23709), without UI IDs.
"""

import copy
import re
from typing import Any, Never

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import declarations

__all__ = ["view"]

_CATALOG_TABLES = {"item_option_new", "item_option_new_set", "sys_flow_cat_variable"}
_JS_SPACE = "\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff"
_JS_TRIM = re.compile(
    rf"(?:\A|(?<=[\n\r\u2028\u2029]))[{_JS_SPACE}]+"
    rf"|[{_JS_SPACE}]+(?=\Z|[\n\r\u2028\u2029])"
)
_JS_FIRST_SPACE = re.compile(rf"[{_JS_SPACE}]\Z")


def _unavailable(message: str) -> Never:
    raise CapabilityError(message, "UNAVAILABLE")


def _spec(item: dict[str, Any]) -> dict[str, Any]:
    parameter = item.get("parameter")
    return parameter if isinstance(parameter, dict) else item


def _default_item(label: str) -> tuple[str, str]:
    # createArrayChild and createLegalName(label, false), bundle:21979-21985.
    label += "_child0"
    if "0" <= label[0] <= "9" or _JS_FIRST_SPACE.fullmatch(label[0]):
        label = "_" + label
    name = _JS_TRIM.sub("", label.lower()).replace(" ", "_")
    return label, re.sub(r"[\W_]+", "_", name, flags=re.ASCII)


def _metadata(row: dict[str, Any], *, nested: bool) -> dict[str, Any]:
    attributes = row.get("attributes", {})
    facets = row.get("fieldFacetMap", {})
    metadata = facets if nested else attributes or facets
    # Contradictory packed types cannot be repaired by generating an item.
    for source in (attributes, facets):
        if (
            "child_type" in source
            and source["child_type"] != declarations.ARRAYS[row["type"]]
        ):
            _unavailable("Catalog array item type contradicts its native array type.")
        for key in (
            "child_name",
            "child_label",
            "child_type_label",
            "child_uiUniqueId",
        ):
            if key in source and not isinstance(source[key], str):
                _unavailable("Catalog array item metadata is malformed.")
        if "child_name" in source and (
            not source["child_name"]
            or not source["child_name"].split(".")[-1]
            or any(marker in source["child_name"] for marker in ("{{", "}}"))
        ):
            _unavailable("Catalog array item name is incomplete.")
    for key in declarations.PACKED:
        packed = "child_" + key
        if packed not in attributes or packed not in facets:
            continue
        left, right = attributes[packed], facets[packed]
        if key == "name":
            left, right = left.split(".")[-1], right.split(".")[-1]
        if left != right:
            _unavailable("Catalog array item metadata is contradictory.")
    return metadata


def view(item: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy the authoritative spec into an expanded, read-only schema view.

    Array items use native packed metadata or native defaults, never sample
    values. Catalog-record provenance and packed child keys are removed only
    from this local projection; generic declaration readers remain strict.
    """
    active: set[int] = set()

    def project(
        row: dict[str, Any], parent: str = "", depth: int = 0
    ) -> dict[str, Any]:
        if depth > 10 or not isinstance(row, dict):
            _unavailable("Catalog schema nesting is unavailable or exceeds ten levels.")
        source = _spec(row)
        if id(source) in active:
            _unavailable("Catalog schema children contain a cycle.")
        name, kind = source.get("name"), source.get("type")
        if (
            not isinstance(name, str)
            or not name
            or any(not part for part in name.split("."))
            or any(marker in name for marker in ("{{", "}}"))
            or not isinstance(kind, str)
            or not kind
        ):
            _unavailable("Catalog schema names or types are incomplete.")
        suffix = name.removeprefix(parent + ".") if parent else name
        if parent and "." in suffix:
            _unavailable("Catalog child names contradict their parent registration.")
        path = (
            parent + "." + name
            if parent and not name.startswith(parent + ".")
            else name
        )
        if source.get("parent") not in (None, "", parent):
            _unavailable("Catalog child parents contradict their schema registration.")
        for key in ("attributes", "fieldFacetMap"):
            if key in source and (
                not isinstance(source[key], dict)
                or any(not isinstance(key, str) for key in source[key])
            ):
                _unavailable("Catalog schema metadata is malformed.")
        if kind.startswith("array.") and kind not in declarations.ARRAYS:
            _unavailable("This catalog array type requires another schema adapter.")
        children = source.get("children", [])
        if (
            not isinstance(children, list)
            or any(not isinstance(child, dict) for child in children)
            or kind in {"object", "array", *declarations.ARRAYS}
            and "children" not in source
        ):
            _unavailable("Catalog schema children are incomplete.")
        child_names: set[str] = set()
        for child in children:
            child_name = _spec(child).get("name")
            if isinstance(child_name, str):
                local_name = child_name.split(".")[-1]
                if local_name in child_names:
                    _unavailable("Catalog child schemas have ambiguous names.")
                child_names.add(local_name)
        if children and kind not in {"object", "array.object"}:
            _unavailable("A scalar catalog schema contains unexpected children.")
        active.add(id(source))
        try:
            result = copy.deepcopy(
                {key: value for key, value in source.items() if key != "children"}
            )
            if (
                isinstance(result.get("table"), str)
                and result["table"] in _CATALOG_TABLES
            ):
                del result["table"]
            child_parent = path
            child_depth = depth + 1
            if kind in declarations.ARRAYS:
                label = source.get("label")
                if not isinstance(label, str):
                    _unavailable(
                        "Catalog arrays require their native label for item defaults."
                    )
                metadata = _metadata(source, nested=bool(parent))
                default_label, default_name = _default_item(label)
                child_name = metadata.get("child_name", default_name).split(".")[-1]
                child_parent = path + "." + child_name
                generated = {
                    key: copy.deepcopy(metadata["child_" + key])
                    for key in declarations.PACKED
                    if key != "uiUniqueId" and "child_" + key in metadata
                }
                generated.update(
                    type=declarations.ARRAYS[kind],
                    type_label=metadata.get(
                        "child_type_label",
                        {"object": "Object", "array": "Array"}.get(
                            declarations.ARRAYS[kind], ""
                        ),
                    ),
                    name=child_parent,
                    label=metadata.get("child_label", default_label),
                    parent=path,
                    cannotBeDeleted=True,
                    children=[],
                )
                if kind == "array.reference":
                    generated["reference"] = declarations.reference_table(result)
                if kind == "array.choice":
                    declarations.choices(result)
                    generated.pop("defaultValue", None)
                    for key in declarations.CHOICE_FIELDS:
                        if key in result:
                            generated[key] = copy.deepcopy(result[key])
                if kind == "array.object" and children:
                    first = _spec(children[0])
                    co_type = source.get("attributes", {}).get("co_type_name")
                    if first.get("name") == "$COCollectionField" or (
                        isinstance(co_type, str)
                        and co_type
                        and first.get("name") == co_type
                    ):
                        if len(children) != 1 or first.get("type") not in (
                            None,
                            "object",
                        ):
                            _unavailable(
                                "Catalog collection wrappers contradict their object schema."
                            )
                        children = first.get("children")
                        if not isinstance(children, list) or any(
                            not isinstance(child, dict) for child in children
                        ):
                            _unavailable(
                                "Catalog collection wrapper children are incomplete."
                            )
                for key in ("attributes", "fieldFacetMap"):
                    if key in result:
                        result[key] = {
                            name: value
                            for name, value in result[key].items()
                            if not name.startswith("child_")
                        }
                if child_depth > 10:
                    _unavailable("Catalog schema nesting exceeds ten levels.")
                child_depth += 1
                generated["children"] = [
                    project(child, child_parent, child_depth) for child in children
                ]
                result["children"] = [generated]
            elif "children" in source:
                result["children"] = [
                    project(child, child_parent, child_depth) for child in children
                ]
            projected = result.get("children", [])
            if len({child["name"].split(".")[-1] for child in projected}) != len(
                projected
            ):
                _unavailable("Catalog child schemas are ambiguous.")
            return result
        except RecursionError as error:
            raise CapabilityError(
                "Catalog schema nesting is unavailable.", "UNAVAILABLE"
            ) from error
        finally:
            active.remove(id(source))

    return project(item)
