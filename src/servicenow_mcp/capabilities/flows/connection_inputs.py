"""Static native connection overrides; no connection, credential, or action execution.

Studio 27: NEW_ALIAS_ID:1651; offered aliases:64109-64206; instance edits:
314749-314796; root edits:457593-457635; deletion:253620-253682;
GraphQL mapping DTO:471907-471919. Native constructors omit sourceTable for new
mappings; server-returned sourceTable/sourceId and unrelated fields stay opaque.
"""

import copy
from collections import Counter
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import payloads, tree
from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

NEW_ALIAS_ID = ""
DefinitionCache = dict[tuple[bool, str], dict[str, Any]]
Component = tree.Step | dict[str, Any]


def _sys_id(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise CapabilityError(f"{label} requires a sys_id string.")
    try:
        validate_sys_id(value)
    except ValueError:
        raise CapabilityError(
            f"{label} requires a 32-character hexadecimal sys_id."
        ) from None
    return value


def _raw(component: Component) -> dict[str, Any]:
    raw = component.raw if isinstance(component, tree.Step) else component
    if not isinstance(raw, dict):
        raise CapabilityError(
            "Native connection call metadata is malformed.", "UNAVAILABLE"
        )
    return raw


def _components(flow: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for key, kind in (("actionInstances", "action"), ("subFlowInstances", "subflow")):
        rows = flow.get(key, [])
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or row.get("type") != kind for row in rows
        ):
            raise CapabilityError(
                "Native call instances are incomplete.", "UNAVAILABLE"
            )
        result.extend(row for row in rows if not row.get("deleted"))
    return result


def _mappings(owner: dict[str, Any]) -> list[dict[str, Any]]:
    override = owner.get("aliasOverride")
    if override is None:
        return []
    if not isinstance(override, dict) or not isinstance(
        override.get("aliasOverrideMappings"), list
    ):
        raise CapabilityError(
            "Native connection override mappings are incomplete.", "UNAVAILABLE"
        )
    rows = override["aliasOverrideMappings"]
    sources: set[str] = set()
    identities: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise CapabilityError(
                "Native connection mappings require objects.", "UNAVAILABLE"
            )
        identity = row.get("id", NEW_ALIAS_ID)
        if identity != NEW_ALIAS_ID:
            _sys_id(identity, "Connection mapping identity")
            if identity in identities:
                raise CapabilityError(
                    "Native connection mapping identities are ambiguous.", "UNAVAILABLE"
                )
            identities.add(identity)
        if "sourceTable" in row and row["sourceTable"] is not None:
            table = row["sourceTable"]
            if not isinstance(table, str):
                raise CapabilityError(
                    "Native mapping sourceTable metadata is malformed.", "UNAVAILABLE"
                )
            if table:
                try:
                    validate_identifier(table)
                except ValueError:
                    raise CapabilityError(
                        "Native mapping sourceTable metadata is malformed.",
                        "UNAVAILABLE",
                    ) from None
                if "." in table:
                    raise CapabilityError(
                        "Native mapping sourceTable requires a table name.",
                        "UNAVAILABLE",
                    )
        for field in ("sourceId", "sourceID"):
            if row.get(field) not in (None, ""):
                _sys_id(row[field], "Native mapping source identity")
        source, target = row.get("sourceAlias"), row.get("targetAlias")
        if source == "" and target == "":
            continue
        source = _sys_id(source, "Connection mapping source")
        _sys_id(target, "Connection mapping target")
        if source in sources:
            raise CapabilityError(
                "Native connection mapping sources are ambiguous.", "UNAVAILABLE"
            )
        sources.add(source)
    return rows


def parse(overrides: Any) -> dict[str, str | None]:
    """Parse the public source-alias -> declared-child-alias or null field."""
    if not isinstance(overrides, dict):
        raise CapabilityError(
            "connection_overrides must be an object of alias sys_ids or null defaults."
        )
    result = {}
    for source, target in overrides.items():
        source = _sys_id(source, "Connection override source")
        if target is not None:
            target = _sys_id(target, "Connection override target")
            if target == source:
                raise CapabilityError(
                    "Use null to select the default connection, not the source alias sys_id."
                )
        result[source] = target
    return result


def _configurations(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise CapabilityError(
            "Native connection_configurations metadata is unavailable.", "UNAVAILABLE"
        )
    result = {}
    children: dict[str, str] = {}
    for row in value:
        if not isinstance(row, dict):
            raise CapabilityError(
                "Native connection alias metadata is malformed.", "UNAVAILABLE"
            )
        identity = _sys_id(row.get("alias_id"), "Offered connection alias")
        if identity in result:
            raise CapabilityError(
                "Offered connection alias metadata is ambiguous.", "UNAVAILABLE"
            )
        if not isinstance(row.get("type"), str) or not row["type"]:
            raise CapabilityError(
                "Native connection alias type is unavailable.", "UNAVAILABLE"
            )
        if not isinstance(row.get("display_name"), str) or not row["display_name"]:
            raise CapabilityError(
                "Native connection alias label is unavailable.", "UNAVAILABLE"
            )
        offered = row.get("child_aliases")
        if not isinstance(offered, list):
            raise CapabilityError(
                "Native child-alias metadata is unavailable.", "UNAVAILABLE"
            )
        local: set[str] = set()
        for child in offered:
            if not isinstance(child, dict):
                raise CapabilityError(
                    "Native child-alias metadata is malformed.", "UNAVAILABLE"
                )
            child_id = _sys_id(child.get("alias_id"), "Offered child alias")
            if (
                child_id == identity
                or child_id in local
                or child_id in children
                and children[child_id] != identity
                or child_id in result
            ):
                raise CapabilityError(
                    "Native child-alias relations are ambiguous.", "UNAVAILABLE"
                )
            if (
                not isinstance(child.get("display_name"), str)
                or not child["display_name"]
            ):
                raise CapabilityError(
                    "Native child-alias labels are unavailable.", "UNAVAILABLE"
                )
            if child.get("type") == "CREDENTIAL":
                raise CapabilityError(
                    "Credential-only aliases cannot be connection targets.",
                    "UNAVAILABLE",
                )
            local.add(child_id)
            children[child_id] = identity
        if identity in children:
            raise CapabilityError(
                "Native alias parent/child relations are ambiguous.", "UNAVAILABLE"
            )
        result[identity] = row
    return result


def _alias_ids(component: dict[str, Any]) -> list[str] | None:
    if "aliasIds" not in component:
        return None
    values = component["aliasIds"]
    if not isinstance(values, list):
        raise CapabilityError(
            "Native component aliasIds metadata is malformed.", "UNAVAILABLE"
        )
    result = [_sys_id(value, "Native component alias") for value in values]
    if len(set(result)) != len(result):
        raise CapabilityError(
            "Native component aliasIds metadata is ambiguous.", "UNAVAILABLE"
        )
    return result


def _identity(component: dict[str, Any]) -> tuple[bool, str]:
    kind = component.get("type")
    if not isinstance(kind, str) or kind not in ("action", "subflow"):
        raise CapabilityError(
            "Connection overrides are available only on action and subflow calls."
        )
    subflow = kind == "subflow"
    return subflow, _sys_id(
        component.get("subflowSysId" if subflow else "actionTypeSysId"),
        "Native call definition",
    )


async def _definition(
    component: dict[str, Any],
    client: ServiceNowClient,
    definitions: DefinitionCache,
) -> dict[str, Any]:
    key = _identity(component)
    subflow, identity = key
    if key not in definitions:
        if subflow:
            definitions[key] = await client.studio_request(
                f"flow/{identity}", minimal=True
            )
        else:
            definitions[key] = await client.studio_request(
                f"action/action_types/{identity}"
            )
    definition = definitions[key]
    if not isinstance(definition, dict) or definition.get("id") != identity:
        raise CapabilityError(
            "Independent connection definition identity does not match the call.",
            "UNAVAILABLE",
        )
    security = definition.get("security")
    if (
        not isinstance(security, dict)
        or security.get("can_read") is not True
        or definition.get("isFlowEntitled" if subflow else "entitled") is not True
        or (
            subflow
            and (
                definition.get("type") != "subflow"
                or definition.get("snapshot") is not True
            )
        )
        or (not subflow and definition.get("is_snapshot") is not True)
    ):
        raise CapabilityError(
            "Connection definition metadata is not a readable entitled snapshot.",
            "UNAVAILABLE",
        )
    payloads.validate_definition(definition, field_name="connection definition")
    return definition


def _write(
    owner: dict[str, Any],
    overrides: dict[str, str | None],
    *,
    instance_defaults: bool = False,
) -> None:
    original = _mappings(owner)
    rows = copy.deepcopy(original)
    for source, target in overrides.items():
        index = next(
            (index for index, row in enumerate(rows) if row["sourceAlias"] == source),
            None,
        )
        if index is None:
            if target is not None:
                rows.append(
                    {"id": NEW_ALIAS_ID, "sourceAlias": source, "targetAlias": target}
                )
            elif instance_defaults and not any(
                row.get("id", NEW_ALIAS_ID) == NEW_ALIAS_ID
                and row["sourceAlias"] == ""
                and row["targetAlias"] == ""
                for row in rows
            ):
                # A first per-call default choice has no source match. Native
                # updateOverrideObject appends it, then updateOverrideObjInState
                # clears sourceAlias (253706-7, 314786-90). The scope marker
                # suppresses inherited provider overrides without inventing an ID.
                rows.append({"id": NEW_ALIAS_ID, "sourceAlias": "", "targetAlias": ""})
            continue
        row = rows[index]
        if target is None:
            if row.get("id", NEW_ALIAS_ID) == NEW_ALIAS_ID:
                rows.pop(index)
            else:
                row.update(sourceAlias="", targetAlias="")
        else:
            row["targetAlias"] = target
    if rows != original:
        owner["aliasOverride"] = {
            **copy.deepcopy(owner.get("aliasOverride") or {}),
            "aliasOverrideMappings": rows,
        }


async def _metadata(
    flow: dict[str, Any],
    component: Component | None,
    parsed: dict[str, str | None],
    *,
    client: ServiceNowClient,
    definitions: DefinitionCache | None = None,
) -> dict[str, Any]:
    """Resolve independent offered aliases without rewriting native mappings."""
    flow_id = _sys_id(flow.get("id"), "Connection override flow")
    cache = {} if definitions is None else definitions
    evidence: dict[str, Any] = {
        "flow_id": flow_id,
        "scope": "flow" if component is None else "instance",
    }
    used_definitions: dict[tuple[bool, str], dict[str, Any]] = {}
    if component is None:
        independent = await client.studio_request(f"flow/{flow_id}")
        if not isinstance(independent, dict) or independent.get("id") != flow_id:
            raise CapabilityError(
                "Independent flow connection metadata has a different identity.",
                "UNAVAILABLE",
            )
        native = independent.get("connection_configurations")
        root_aliases = _configurations(native)
        evidence["connection_configurations"] = copy.deepcopy(native)
        offered: dict[str, dict[str, Any]] = {}
        for row in _components(flow):
            aliases = _alias_ids(row)
            if aliases is not None:
                for identity in aliases:
                    if identity not in root_aliases:
                        raise CapabilityError(
                            "The current flow does not declare a used connection alias.",
                            "UNAVAILABLE",
                        )
                    offered[identity] = root_aliases[identity]
                continue
            definition = await _definition(row, client, cache)
            used_definitions[_identity(row)] = definition
            for identity, metadata in _configurations(
                definition.get("connection_configurations")
            ).items():
                if identity in offered and offered[identity] != metadata:
                    raise CapabilityError(
                        "Independent definitions disagree on a shared connection alias.",
                        "UNAVAILABLE",
                    )
                offered[identity] = metadata
        owner = flow
    else:
        owner = _raw(component)
        if not isinstance(owner, dict):
            raise CapabilityError(
                "Connection override call metadata is unavailable.", "UNAVAILABLE"
            )
        if owner.get("flowSysId") != flow_id:
            raise CapabilityError(
                "The connection override call belongs to another flow."
            )
        definition = await _definition(owner, client, cache)
        used_definitions[_identity(owner)] = definition
        offered = _configurations(definition.get("connection_configurations"))
        evidence["component_uid"] = owner.get("uiUniqueIdentifier")
    declared = []
    for source, target in parsed.items():
        metadata = offered.get(source)
        if metadata is None or metadata["type"] == "CREDENTIAL":
            raise CapabilityError(
                "The selected scope does not offer this connection alias."
            )
        child = (
            None
            if target is None
            else next(
                (row for row in metadata["child_aliases"] if row["alias_id"] == target),
                None,
            )
        )
        if target is not None and child is None:
            raise CapabilityError(
                "The target alias is not a declared child of the source alias."
            )
        declared.append(
            {
                "source_alias": source,
                "target_alias": target,
                "metadata": copy.deepcopy(child),
            }
        )
    evidence["definitions"] = [
        {"subflow": key[0], "id": key[1], "metadata": copy.deepcopy(value)}
        for key, value in sorted(used_definitions.items())
    ]
    evidence["targets"] = declared
    return evidence


async def apply(
    flow: dict[str, Any],
    component: Component | None,
    overrides: Any,
    *,
    client: ServiceNowClient,
    definitions: DefinitionCache | None = None,
) -> dict[str, Any]:
    """Edit one native scope and return full independent revision evidence.

    None selects root scope; a Step/raw call selects that instance. The optional
    cache contains independently read definitions keyed by (subflow, id). Root
    callers supply current flattened call collections including ordered edits.
    """
    parsed = parse(overrides)
    evidence = await _metadata(
        flow, component, parsed, client=client, definitions=definitions
    )
    owner = flow if component is None else _raw(component)
    _write(owner, parsed, instance_defaults=component is not None)
    return evidence


async def validate_existing(
    flow: dict[str, Any],
    component: Component | None,
    *,
    client: ServiceNowClient,
    definitions: DefinitionCache | None = None,
) -> dict[str, Any]:
    """Validate native saved/draft markers separately from public edit parsing.

    Empty-source/empty-target tombstones are internal DTO states, never public
    alias IDs. Validate all live source/target relations and bind the complete
    untouched native list, including tombstones, into revision evidence.
    """
    owner = flow if component is None else _raw(component)
    rows = _mappings(owner)
    requested = {
        row["sourceAlias"]: row["targetAlias"] for row in rows if row["sourceAlias"]
    }
    evidence = await _metadata(
        flow, component, requested, client=client, definitions=definitions
    )
    evidence["native_mappings"] = copy.deepcopy(rows)
    return evidence


def provider_context(
    owner_definition: dict[str, Any],
    requested_overrides: Any,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Bind a provider's explicit context to independently read owner metadata.

    The provider planner owns fetching/refreshing the complete owner and provider
    definitions. Eligible aliases belong to the target owner, not the provider
    action. Null means the native default, so it is not sent as an override.
    """
    identity = _sys_id(owner_definition.get("id"), "Provider owner definition")
    parsed = parse({} if requested_overrides is None else requested_overrides)
    native = owner_definition.get("connection_configurations")
    offered = (
        {}
        if not parsed and "connection_configurations" not in owner_definition
        else _configurations(native)
    )
    result = {}
    targets = []
    for source, target in parsed.items():
        metadata = offered.get(source)
        if metadata is None or metadata["type"] == "CREDENTIAL":
            raise CapabilityError(
                "The provider owner does not offer this connection alias."
            )
        child = (
            None
            if target is None
            else next(
                (row for row in metadata["child_aliases"] if row["alias_id"] == target),
                None,
            )
        )
        if target is not None:
            if child is None:
                raise CapabilityError(
                    "The provider target alias is not a declared child of the owner source alias."
                )
            result[source] = target
        targets.append(
            {
                "source_alias": source,
                "target_alias": target,
                "metadata": copy.deepcopy(child),
            }
        )
    return result, {
        "owner_definition_id": identity,
        "connection_configurations_present": "connection_configurations"
        in owner_definition,
        "connection_configurations": copy.deepcopy(native),
        "targets": targets,
    }


def effective(
    flow: dict[str, Any], component: Component | None = None
) -> dict[str, str]:
    """Native provider map, not an execution-time per-key inheritance simulation.

    Saga253847-253861 selects the entire instance scope when it contains any
    mappings (including deletion tombstones), otherwise the root scope. Its
    instance projection254043-254049 retains empty tombstone keys; only the root
    projection64088-64098 filters them. collectData234223-234228 forwards this map
    under ``snc_input_alias_override`` when nonempty.
    """
    local = _mappings(_raw(component)) if component is not None else []
    if local:
        return {row["sourceAlias"]: row["targetAlias"] for row in local}
    return {
        row["sourceAlias"]: row["targetAlias"]
        for row in _mappings(flow)
        if row["sourceAlias"] and row["targetAlias"]
    }


def _connections(component: dict[str, Any]) -> list[str]:
    if component.get("type") not in ("action", "subflow"):
        return []
    aliases = _alias_ids(component)
    if aliases is not None:
        return aliases
    definition = component.get(
        "subFlow" if component["type"] == "subflow" else "actionType"
    )
    if definition is None:
        return []
    if not isinstance(definition, dict):
        raise CapabilityError(
            "Native deleted-call metadata is malformed.", "UNAVAILABLE"
        )
    native = definition.get("connection_configurations", [])
    if not isinstance(native, list) or any(not isinstance(row, dict) for row in native):
        raise CapabilityError(
            "Native deleted-call connections are malformed.", "UNAVAILABLE"
        )
    result = [
        _sys_id(row.get("alias_id"), "Native deleted-call alias") for row in native
    ]
    if len(result) != len(set(result)):
        raise CapabilityError(
            "Native deleted-call connections are ambiguous.", "UNAVAILABLE"
        )
    return result


def cleanup_removed(
    flow: dict[str, Any],
    removed: Iterable[Component],
    *,
    components: Iterable[Component] | None = None,
) -> None:
    """Remove unused root mappings once for a whole deletion, using PRE-delete calls.

    Shared aliases survive while any call still uses them. Unsaved mappings
    disappear; saved mappings keep their identity and opaque fields as native
    empty-source/empty-target tombstones (253620-253662).
    """
    rows = _mappings(flow)
    if not rows:
        return
    before = (
        [_raw(row) for row in components]
        if components is not None
        else _components(flow)
    )
    deleting = [_raw(row) for row in removed]
    before_by_uid = {}
    for row in before:
        if row.get("type") not in ("action", "subflow"):
            continue
        uid = row.get("uiUniqueIdentifier")
        if not isinstance(uid, str) or not uid or uid in before_by_uid:
            raise CapabilityError(
                "Native pre-delete call identities are incomplete or ambiguous.",
                "UNAVAILABLE",
            )
        before_by_uid[uid] = row
    removed_uids = set()
    for row in deleting:
        if row.get("type") not in ("action", "subflow"):
            continue
        uid = row.get("uiUniqueIdentifier")
        if not isinstance(uid, str) or uid not in before_by_uid or uid in removed_uids:
            raise CapabilityError(
                "Deleted call identities are not unique references in the pre-delete flow.",
                "UNAVAILABLE",
            )
        if _connections(row) != _connections(before_by_uid[uid]):
            raise CapabilityError(
                "Deleted call aliases differ from the pre-delete flow.", "UNAVAILABLE"
            )
        removed_uids.add(uid)
    counts = Counter(identity for row in before for identity in _connections(row))
    removed_counts = Counter(
        identity for row in deleting for identity in _connections(row)
    )
    clears = {}
    for identity, count in removed_counts.items():
        if count > counts[identity]:
            raise CapabilityError(
                "Deleted connection references are not in the pre-delete flow.",
                "UNAVAILABLE",
            )
        if count == counts[identity]:
            clears[identity] = None
    _write(flow, clears)
