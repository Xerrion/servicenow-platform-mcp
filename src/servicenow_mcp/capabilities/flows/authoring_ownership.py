"""Bind new native row metadata within the independently read flow payload.

Studio 27 pharmacy constructors/serialization omit saved identity metadata
(designer-bundle.min.js:61068-61116,226399-226460,472619-472629).
New connection mappings use id='' and omit sourceTable/sourceId
(314749-314796,457593-457635,472757-472758). Nothing else is normalized.
The caller compares both scoped flow GET and echo, then compares them strictly.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, NoReturn

from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

_TRANSFORM_METADATA = frozenset({"id", "attachedTo", "scopeId"})
_CONNECTION_METADATA = frozenset({"id", "sourceTable", "sourceId"})


def _fail(path: str = "flow.authoringRows") -> NoReturn:
    from servicenow_mcp.capabilities.flows.semantics import SemanticMismatch

    raise SemanticMismatch(path, "AUTHORING_ROW_MISMATCH", "actual")


def _identity(value: Any, path: str = "flow.authoringRows.id") -> str:
    if not isinstance(value, str):
        _fail(path)
    try:
        validate_sys_id(value)
    except ValueError:
        _fail(path)
    return value


def _equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return bool(left == right)


def _candidate(
    planned: dict[str, Any], saved: Any, fields: frozenset[str], path: str
) -> dict[str, Any]:
    if not isinstance(saved, dict) or planned.get("id", "") != "":
        _fail(path)
    candidate = copy.deepcopy(planned)
    for field in fields:
        if field in saved and (
            field not in planned or field == "id" and planned[field] == ""
        ):
            candidate[field] = copy.deepcopy(saved[field])
    _identity(candidate.get("id"), path + ".id")
    if not _equal(candidate, saved):
        _fail(path)
    return candidate


def _compounds(flow: dict[str, Any]) -> dict[str, Any]:
    pharmacy = flow.get("pharmacy", {})
    if not isinstance(pharmacy, dict) or not isinstance(
        pharmacy.get("pharmacyCompound", {}), dict
    ):
        _fail("flow.pharmacy.pharmacyCompound")
    return pharmacy.get("pharmacyCompound", {})


def _owner(flow: dict[str, Any], uid: str | None) -> dict[str, Any]:
    if uid is None:
        return flow
    matches = [
        row
        for key in ("actionInstances", "subFlowInstances")
        for row in flow.get(key, [])
        if isinstance(row, dict) and row.get("uiUniqueIdentifier") == uid
    ]
    if len(matches) != 1:
        _fail("flow.componentInstances")
    return matches[0]


def _mappings(owner: dict[str, Any]) -> list[dict[str, Any]]:
    override = owner.get("aliasOverride")
    if not isinstance(override, dict) or not isinstance(
        override.get("aliasOverrideMappings"), list
    ):
        _fail("flow.aliasOverride.aliasOverrideMappings")
    rows = override["aliasOverrideMappings"]
    if any(not isinstance(row, dict) for row in rows):
        _fail("flow.aliasOverride.aliasOverrideMappings")
    return rows


def _mapping(owner: dict[str, Any], source: str) -> dict[str, Any]:
    matches = [row for row in _mappings(owner) if row.get("sourceAlias") == source]
    if len(matches) != 1:
        _fail("flow.aliasOverride.aliasOverrideMappings")
    return matches[0]


async def bind(
    client: ServiceNowClient,
    expected: dict[str, Any],
    saved: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Deep-copy expected, binding only manifest-listed new native metadata.

    Manifest: ``{'transforms': [uiId], 'connections':
    [{'component_ui_id': str | None, 'source_alias': sys_id | ''}],
    'alias_ids': [new_action_ui_id]}``.
    ``None`` selects root connection scope; an empty source selects only an
    explicit new per-call empty-source/target default marker. No requests are
    made here: the verifier supplies the exact flow payload and independently
    reads it once.
    Existing rows, definitions, compositions, parameter values, alias choices,
    and all unknown fields remain strict, including echo/readback agreement.
    """
    bound = copy.deepcopy(expected)
    if manifest is None:
        return bound
    if not isinstance(manifest, dict) or set(manifest) - {
        "transforms",
        "connections",
        "alias_ids",
    }:
        _fail()
    transforms, connections = (
        manifest.get("transforms", []),
        manifest.get("connections", []),
    )
    alias_ids = manifest.get("alias_ids", [])
    if (
        not isinstance(alias_ids, list)
        or any(not isinstance(uid, str) or not uid for uid in alias_ids)
        or len(set(alias_ids)) != len(alias_ids)
    ):
        _fail()
    if (
        not isinstance(transforms, list)
        or any(not isinstance(uid, str) or not uid for uid in transforms)
        or len(set(transforms)) != len(transforms)
    ):
        _fail()
    if not isinstance(connections, list):
        _fail()
    if not transforms and not connections and not alias_ids:
        return bound
    flow_id = _identity(expected.get("id"))
    scope = expected.get("scope")
    if scope != "global":
        _identity(scope)
    if saved.get("id") != flow_id or saved.get("scope") != scope:
        _fail()

    planned_rows, actual_rows = _compounds(expected), _compounds(saved)
    identities = {
        row["id"]
        for row in planned_rows.values()
        if isinstance(row, dict) and row.get("id")
    }
    for uid in transforms:
        planned = planned_rows.get(uid)
        if not isinstance(planned, dict) or planned.get("uiId") != uid:
            _fail()
        actual = actual_rows.get(uid)
        requested = copy.deepcopy(planned)
        # Observed native GET (2026-10-26). Only these
        # omitted EMPTY slots are getter defaults; nonempty/present values stay
        # exact, as do complete composition and parameter objects.
        defaults = (
            {"sourceUiId": "", "transformDefinitionId": "", "compositions": []}
            if planned.get("order") == 0
            else {"prescription": ""}
        )
        if isinstance(actual, dict):
            for field, empty in defaults.items():
                if (
                    field not in planned
                    and field in actual
                    and _equal(actual[field], empty)
                ):
                    requested[field] = copy.deepcopy(empty)
        candidate = _candidate(
            requested, actual, _TRANSFORM_METADATA, "flow.pharmacy.pharmacyCompound"
        )
        if candidate["id"] in identities:
            _fail()
        for field in ("attachedTo", "scopeId"):
            if field in candidate:
                if field == "scopeId" and candidate[field] == "global":
                    continue
                _identity(candidate[field])
        identities.add(candidate["id"])
        _compounds(bound)[uid] = candidate

    selected = set()
    mapping_ids = []
    for owner in [
        saved,
        *saved.get("actionInstances", []),
        *saved.get("subFlowInstances", []),
    ]:
        override = owner.get("aliasOverride")
        if isinstance(override, dict) and isinstance(
            override.get("aliasOverrideMappings"), list
        ):
            mapping_ids.extend(
                row.get("id")
                for row in override["aliasOverrideMappings"]
                if isinstance(row, dict) and row.get("id")
            )
    for entry in connections:
        if not isinstance(entry, dict) or set(entry) != {
            "component_ui_id",
            "source_alias",
        }:
            _fail()
        uid, source = entry["component_ui_id"], entry["source_alias"]
        if source == "":
            if uid is None:
                _fail("flow.aliasOverride.aliasOverrideMappings")
        else:
            _identity(source, "flow.aliasOverride.aliasOverrideMappings.sourceAlias")
        if uid is not None and (not isinstance(uid, str) or not uid):
            _fail()
        if (uid, source) in selected:
            _fail()
        selected.add((uid, source))
        planned_owner, actual_owner = _owner(expected, uid), _owner(saved, uid)
        planned = _mapping(planned_owner, source)
        if planned.get("id") != "":
            _fail()
        if source == "" and planned.get("targetAlias") != "":
            _fail()
        candidate = _candidate(
            planned,
            _mapping(actual_owner, source),
            _CONNECTION_METADATA,
            "flow.aliasOverride.aliasOverrideMappings",
        )
        if source != "":
            _identity(
                candidate.get("targetAlias"),
                "flow.aliasOverride.aliasOverrideMappings.targetAlias",
            )
        if mapping_ids.count(candidate["id"]) != 1:
            _fail()
        if "sourceId" in candidate:
            _identity(candidate["sourceId"])
        if "sourceTable" in candidate:
            table = candidate["sourceTable"]
            if not isinstance(table, str) or "." in table:
                _fail()
            try:
                validate_identifier(table)
            except ValueError:
                _fail()
        bound_owner = _owner(bound, uid)
        _mapping(bound_owner, source).update(candidate)
        for mirror in bound.get("componentInstances", []):
            if (
                uid is not None
                and isinstance(mirror, dict)
                and mirror.get("uiUniqueIdentifier") == uid
            ):
                mirrored = _mapping(mirror, source)
                if not _equal(mirrored, planned):
                    _fail()
                mirrored.update(copy.deepcopy(candidate))
    for uid in alias_ids:
        planned, actual = _owner(bound, uid), _owner(saved, uid)
        if (
            planned.get("type") != "action"
            or planned.get("id", "") != ""
            or "aliasIds" in planned
        ):
            _fail("flow.actionInstances.aliasIds")
        if "aliasIds" not in actual:
            continue
        aliases = actual["aliasIds"]
        if not isinstance(aliases, list):
            _fail("flow.actionInstances.aliasIds")
        for alias in aliases:
            _identity(alias, "flow.actionInstances.aliasIds")
        _identity(actual.get("id"))
        # The complete comparator below this adapter owns step serialization
        # (including topology/cache fields and numeric order). Do not compare
        # the entire raw step twice or introduce a second normalization list.
        if any(
            not _equal(planned.get(field), actual.get(field))
            for field in ("type", "uiUniqueIdentifier", "flowSysId", "actionTypeSysId")
        ):
            _fail("flow.actionInstances.aliasIds")
        planned["aliasIds"] = copy.deepcopy(aliases)
        for mirror in bound.get("componentInstances", []):
            if isinstance(mirror, dict) and mirror.get("uiUniqueIdentifier") == uid:
                if mirror.get("id", "") != "" or "aliasIds" in mirror:
                    _fail()
                mirror["aliasIds"] = copy.deepcopy(aliases)
    return bound
