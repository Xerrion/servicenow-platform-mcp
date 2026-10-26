"""Native template column scripts and upsert coalescing, without executing code.

Studio 27: encoded fields 53733-53884; coalescing 53938-54004,54074-54104;
column scripts 54156-54179,108621-108677; marker updates 243966-243990.
"""

import copy
import json
import re
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    data,
    input_modes,
    script_refs,
    tree,
    widgets,
)
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_identifier, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.capabilities.flows.editing import PillResolver

TYPES = frozenset({"template_value", "dynamic_template"})
MARKER = "fd-scripted"
COALESCE = "coalesce_fields"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PASSWORDS = frozenset({"password", "password0", "password2"})


def parse_selector(
    step: tree.Step, qualified_name: str
) -> tuple[dict[str, Any], str] | None:
    """Resolve a qualified column selector; unqualified ordinary names return None.

    The selected column and its independent metadata are checked by ``apply``
    after the same edit's encoded basic value has been installed.
    """
    if "." not in qualified_name:
        return None
    parts = qualified_name.split(".")
    if (
        len(parts) != 2
        or any(not _NAME.fullmatch(part) for part in parts)
        or parts[1] == COALESCE
    ):
        raise CapabilityError(
            "Template script selectors require one input.column name."
        )
    name, column = parts
    matches = [
        item
        for collection in (
            "inputs",
            "dynamicInputs",
            "workflowInputs",
            "decisionTableInputs",
        )
        for item in step.raw.get(collection, [])
        if item.get("name") == name
    ]
    if (
        len(matches) != 1
        or matches[0] not in step.raw.get("inputs", [])
        or widgets.kind(matches[0]) not in TYPES
    ):
        raise CapabilityError(
            "The template script input is absent, ambiguous, or not a declared template."
        )
    return matches[0], column


def _map(item: dict[str, Any]) -> dict[str, Any]:
    result = input_modes._script_map(item)
    if item.get("scriptAsJsonString") not in (None, ""):
        try:
            mirror = json.loads(item["scriptAsJsonString"])
        except ValueError, TypeError:
            raise CapabilityError(
                "The template script JSON mirror is malformed.", "UNAVAILABLE"
            ) from None
        if mirror != result:
            raise CapabilityError(
                "The template script JSON mirror differs from its script map.",
                "UNAVAILABLE",
            )
    return result


def validate_markers(item: dict[str, Any]) -> None:
    """Bind every saved column script bidirectionally to its encoded marker."""
    if "scriptActive" in item and type(item["scriptActive"]) is not bool:
        raise CapabilityError(
            "The native template script aggregate requires a boolean.", "UNAVAILABLE"
        )
    fields = dict(widgets.template_fields(item.get("value")))
    scripts = _map(item)
    for name, entry in scripts.items():
        if name == COALESCE or name not in fields or not _NAME.fullmatch(name):
            raise CapabilityError(
                "A template script refers to an absent selected column.", "UNAVAILABLE"
            )
        if (
            type(entry.get("scriptActive")) is not bool
            or not isinstance(entry.get("script", ""), str)
            or not isinstance(entry.get("savedValue"), str)
            or entry["savedValue"] == MARKER
        ):
            raise CapabilityError(
                "The saved template column script is incomplete.", "UNAVAILABLE"
            )
        if entry["scriptActive"] != (fields[name] == MARKER):
            raise CapabilityError(
                "The template column script and encoded marker disagree.", "UNAVAILABLE"
            )
        try:
            script_refs.references(entry.get("script", ""))
        except ValueError as exc:
            raise CapabilityError(str(exc)) from None
    if any(value == MARKER and name not in scripts for name, value in fields.items()):
        raise CapabilityError(
            "A scripted template marker has no bound native script.", "UNAVAILABLE"
        )


def prepare_basic(item: dict[str, Any], value: Any) -> dict[str, Any]:
    """Stage a basic replacement and prune scripts for removed native columns.

    getScriptWithActiveTemplates/updateInputScriptWithActiveTemplates:
    Studio 27:108757-108793. The ordinary value serializer commits the probe.
    """
    original = item if "value" in item else {**item, "value": ""}
    validate_markers(original)
    selected = {name for name, _ in widgets.template_fields(value) if name != COALESCE}
    probe = copy.deepcopy(original)
    previous = _map(probe)
    retained = {name: entry for name, entry in previous.items() if name in selected}
    if len(retained) != len(previous):
        probe["script"] = retained
        probe["scriptActive"] = any(
            entry.get("scriptActive") is True for entry in retained.values()
        )
        if "scriptAsJsonString" in probe:
            probe["scriptAsJsonString"] = json.dumps(
                retained, ensure_ascii=False, separators=(",", ":")
            )
    probe["value"] = value
    validate_markers(probe)
    return probe


def _attributes(parameter: dict[str, Any]) -> dict[str, Any]:
    result = parameter.get("attributes", {})
    if not isinstance(result, dict):
        raise CapabilityError(
            "Template scriptability attributes are malformed.", "UNAVAILABLE"
        )
    return result


def _column_allowed(column: dict[str, Any], *, scripted: bool) -> None:
    security = column.get("security", {})
    if not isinstance(security, dict):
        raise CapabilityError(
            "Template column security metadata is malformed.", "UNAVAILABLE"
        )
    for source in (column, security):
        for key in ("can_read", "can_write", "readonly", "readOnly", "read_only"):
            if (
                key in source
                and type(source[key]) is not bool
                and source[key] not in ("true", "false")
            ):
                raise CapabilityError(
                    "Template column access flags are malformed.", "UNAVAILABLE"
                )
    if any(
        source.get(key) in (False, "false")
        for source in (column, security)
        for key in ("can_read", "can_write")
    ):
        raise CapabilityError(
            "The template column is unreadable or not writable.", "UNAVAILABLE"
        )
    if input_modes.readonly(column):
        raise CapabilityError("Read-only template columns cannot be assigned.")
    if scripted and _attributes(column).get("is_scriptable", True) not in (
        True,
        "true",
    ):
        raise CapabilityError("The template column does not permit inline scripts.")
    if column.get("type") == "reference":
        table = column.get("reference")
        if not isinstance(table, str) or not table:
            raise CapabilityError(
                "The template reference column has no declared table.", "UNAVAILABLE"
            )
        validate_identifier(table)
        check_table_access(table)


async def _columns(
    binding: dict[str, Any],
    parameter: dict[str, Any],
    consumer: tree.Step,
    resolver: PillResolver,
) -> dict[str, dict[str, Any]]:
    schema = data.spec(parameter)
    if widgets.kind(parameter) == "dynamic_template":
        # adopt_bindings regenerates this metadata from the reviewed provider
        # artifact before final validation; callers cannot supply column DTOs.
        columns = binding.get("columns", schema.get("columns"))
        if not isinstance(columns, list) or not columns:
            raise CapabilityError(
                "Dynamic templates require independently resolved columns.",
                "UNAVAILABLE",
            )
        result = {}
        for column in columns:
            if (
                not isinstance(column, dict)
                or not isinstance(column.get("name"), str)
                or not _NAME.fullmatch(column["name"])
                or column["name"] in result
            ):
                raise CapabilityError(
                    "Dynamic template columns are malformed or ambiguous.",
                    "UNAVAILABLE",
                )
            result[column["name"]] = column
        return result
    table = schema.get("reference")
    dependency = schema.get("dependent_on")
    if dependency:
        if not isinstance(dependency, str):
            raise CapabilityError(
                "The template table dependency is malformed.", "UNAVAILABLE"
            )
        source = data.named(
            consumer.raw.get("inputs"), dependency.removeprefix("inputs.")
        )
        if (
            data.value_type(source).kind != "table_name"
            or not isinstance(source.get("value"), str)
            or tree.pills(source["value"])
            or source.get("scriptActive")
        ):
            raise CapabilityError(
                "Template columns require a static declared table dependency."
            )
        if table and source["value"] != table:
            raise CapabilityError(
                "The template table differs from its declared reference."
            )
        table = source["value"]
    if not isinstance(table, str) or not table:
        raise CapabilityError(
            "The template column table is unavailable.", "UNAVAILABLE"
        )
    validate_identifier(table)
    if "." in table:
        raise CapabilityError("Template columns require one declared table name.")
    check_table_access(table)
    cache_key = "template/table/" + table
    if cache_key not in resolver.definition_cache:
        resolver.definition_cache[
            cache_key
        ] = await resolver.client.studio_table_schema(table)
    descriptor = resolver.definition_cache[cache_key]
    columns = descriptor.get("columns")
    if not isinstance(columns, list):
        raise CapabilityError(
            "The native template table schema has no columns.", "UNAVAILABLE"
        )
    result: dict[str, dict[str, Any]] = {}
    for row in columns:
        column = row.get("definition") if isinstance(row, dict) else None
        if not isinstance(column, dict):
            raise CapabilityError(
                "The native template column definition is incomplete.", "UNAVAILABLE"
            )
        name = column.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in result:
            raise CapabilityError(
                "Native template column names are malformed or ambiguous.",
                "UNAVAILABLE",
            )
        if not isinstance(column.get("label"), str) or not column["label"]:
            raise CapabilityError(
                "The native template column has no readable label.", "UNAVAILABLE"
            )
        result[name] = column
    return result


async def _basic(
    value: str, column: dict[str, Any], consumer: tree.Step, resolver: PillResolver
) -> None:
    kind = column.get("type")
    if not isinstance(kind, str) or not kind:
        raise CapabilityError("The template column type is unavailable.", "UNAVAILABLE")
    pills = tree.pills(value)
    if pills:
        target = data.ValueType(
            "string" if kind in {"html", "translated_html", "journal_input"} else kind,
            column.get("reference", ""),
        )
        for pill in pills:
            resolved = await resolver.resolve(pill, consumer)
            source = data.ValueType(resolved["type"], resolved.get("reference") or "")
            data.compatible(source, target, interpolated=value != "{{" + pill + "}}")
            if target.reference and not await resolver._is_table(
                source.reference, target.reference
            ):
                raise CapabilityError(
                    "The template reference pill has an incompatible table."
                )
        return
    if kind == "reference" and value:
        try:
            reference = json.loads(value)
        except ValueError:
            raise CapabilityError(
                "Template references require native reference JSON."
            ) from None
        if (
            not isinstance(reference, dict)
            or reference.get("table") != column.get("reference")
            or reference.get("value") != reference.get("sys_id")
        ):
            raise CapabilityError(
                "Template reference JSON identity or table is inconsistent."
            )
        validate_sys_id(reference.get("value", ""))
    elif value and kind in {"integer", "longint", "boolean"}:
        if not re.fullmatch(
            r"-?\d+" if kind != "boolean" else r"true|false|0|1", value
        ):
            raise CapabilityError(
                "The template scalar value has an invalid native type."
            )
    elif kind in widgets.TYPES:
        effective = {**column, "mandatory": False}
        widgets.validate(kind, value, effective)


async def validate_binding(
    binding: dict[str, Any],
    parameter: dict[str, Any],
    consumer: tree.Step,
    resolver: PillResolver,
) -> None:
    """Validate native scripts, saved basic values, coalesce controls and pills."""
    validate_markers(binding)
    schema = data.spec(parameter)
    fields = dict(widgets.template_fields(binding.get("value")))
    scripts = _map(binding)
    columns = await _columns(binding, parameter, consumer, resolver)
    if COALESCE in fields:
        if not _attributes(schema).get("upsert_action"):
            raise CapabilityError(
                "Coalescing is available only for native upsert templates."
            )
        names = fields[COALESCE].split(",") if fields[COALESCE] else []
        if len(names) != len(set(names)) or any(
            not _NAME.fullmatch(name)
            or name not in fields
            or name == COALESCE
            or name not in columns
            for name in names
        ):
            raise CapabilityError(
                "Coalesce fields must name unique selected template columns."
            )
        if any(columns[name].get("type") in _PASSWORDS for name in names):
            raise CapabilityError(
                "Password template columns cannot be coalesce fields."
            )
    value = binding.get("value", "")
    first = value.split("^", 1)[0]
    if data.whole_pill(first):
        if (
            widgets.kind(parameter) == "dynamic_template"
            or resolver.flow.get("type") == "flow"
        ):
            raise CapabilityError(
                "Whole-template pills are available only for native subflow template inputs.",
                "UNAVAILABLE",
            )
        resolved = await resolver.resolve(next(iter(tree.pills(first))), consumer)
        source = data.ValueType(resolved["type"], resolved.get("reference") or "")
        target = data.ValueType("template_value", schema.get("reference") or "")
        data.compatible(source, target, interpolated=False)
        if target.reference and not await resolver._is_table(
            source.reference, target.reference
        ):
            raise CapabilityError(
                "The whole-template pill has an incompatible reference table."
            )
    for name, encoded in fields.items():
        if name == COALESCE:
            continue
        if name not in columns:
            raise CapabilityError(
                "The template selects an unknown or unresolved column."
            )
        entry = scripts.get(name)
        _column_allowed(
            columns[name], scripted=bool(entry and entry.get("scriptActive"))
        )
        if entry:
            if entry.get("scriptActive") and _attributes(schema).get(
                "is_scriptable", True
            ) not in (True, "true"):
                raise CapabilityError(
                    "The template definition does not permit column scripts."
                )
            await _basic(entry["savedValue"], columns[name], consumer, resolver)
        if encoded != MARKER:
            await _basic(encoded, columns[name], consumer, resolver)
    await input_modes.validate(binding, consumer, resolver)


def _encoded(fields: list[tuple[str, str]], original: str) -> str:
    first = original.split("^", 1)[0]
    prefix = first + "^" if data.whole_pill(first) else ""
    return prefix + "^".join(
        name + "=" + value.replace("^", "^^") for name, value in fields
    )


async def apply(
    flow: dict[str, Any],
    step: tree.Step,
    modes: dict[str, str | None],
    resolver: PillResolver,
) -> None:
    """Apply qualified input_scripts entries using the native per-column map."""
    pending: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for selector, code in modes.items():
        selected = parse_selector(step, selector)
        if selected is None:
            raise CapabilityError(
                "Template column modes require qualified input.column selectors."
            )
        item, column = selected
        if code is not None and not isinstance(code, str):
            raise CapabilityError("Template script modes require script text or null.")
        if input_modes.readonly(item):
            raise CapabilityError("Read-only template scripts cannot be changed.")
        name = item["name"]
        if name not in pending:
            await validate_binding(item, data.spec(item), step, resolver)
            pending[name] = (item, copy.deepcopy(item))
        probe = pending[name][1]
        fields = widgets.template_fields(probe.get("value"))
        current = dict(fields)
        if column not in current:
            raise CapabilityError(
                "Only selected template columns can change script mode."
            )
        scripts = copy.deepcopy(_map(probe))
        entry = copy.deepcopy(scripts.get(column, {}))
        for previous in scripts.values():
            previous["showScript"] = False
        saved = entry.get("savedValue", "")
        if current[column] and current[column] != MARKER:
            saved = current[column]
        if code is not None:
            try:
                script_refs.references(code)
            except ValueError as exc:
                raise CapabilityError(str(exc)) from None
            entry["script"] = code
        else:
            entry.setdefault("script", "")
        entry.update(
            scriptActive=bool(code),
            showScript=bool(code),
            savedValue="" if code == "" else saved,
        )
        scripts[column] = entry
        value = (
            MARKER
            if code
            else ""
            if code == ""
            else current[column]
            if current[column] and current[column] != MARKER
            else saved
        )
        probe["value"] = _encoded(
            [(name, value if name == column else old) for name, old in fields],
            probe["value"],
        )
        probe["script"] = scripts
        probe["scriptActive"] = any(
            row.get("scriptActive") is True for row in scripts.values()
        )
        probe["displayValue"] = "" if tree.pills(probe["value"]) else probe["value"]
        if "scriptAsJsonString" in probe:
            probe["scriptAsJsonString"] = json.dumps(
                scripts, ensure_ascii=False, separators=(",", ":")
            )
    for item, probe in pending.values():
        await validate_binding(probe, data.spec(probe), step, resolver)
    for item, probe in pending.values():
        item.update(probe)
        if "data" in step.raw:
            step.raw["data"][item["name"]] = item.get("value", "")
