"""Read-only native declaration choice sources and their revision evidence.

The column picker reads table/schema?get_choices=true (Studio bundle module 513,
226655-226768), then offers only definition.type == 'choice' (300687-300699).
Choice configuration copies the selected column's choices to defaultChoices and
persists choices=[] for table sources (331779-331830). Native metadata remains
a table reference, not a static list synthesized from sys_choice.
"""

import copy
import re
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import declarations
from servicenow_mcp.capabilities.flows.target import scalar
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

MAX_DEPTH = 10
MAX_CHOICES = 5000
REVISION_FIELDS = ["sys_id", "sys_mod_count", "sys_updated_on"]
TABLE_FIELDS = [*REVISION_FIELDS, "name", "super_class", "super_class.name"]
DICTIONARY_FIELDS = [
    *REVISION_FIELDS,
    "name",
    "element",
    "internal_type",
    "choice",
    "dependent",
    "choice_table",
    "choice_field",
    "reference_qual",
    "reference_qual_condition",
    "dynamic_ref_qual",
    "attributes",
]
CHOICE_FIELDS = [
    *REVISION_FIELDS,
    "name",
    "element",
    "value",
    "label",
    "language",
    "inactive",
    "dependent_value",
    "sequence",
]
NAME = re.compile(r"[a-z][a-z0-9_]{0,79}")


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise CapabilityError("choice_source requires table and field identifiers.")
    return value


def _revision(row: dict[str, Any]) -> None:
    identity = scalar(row.get("sys_id"))
    revision = scalar(row.get("sys_mod_count"))
    if (
        not identity
        or not revision
        or not revision.isascii()
        or not revision.isdecimal()
        or not scalar(row.get("sys_updated_on"))
    ):
        raise CapabilityError(
            "Choice source revision metadata is incomplete.", "UNAVAILABLE"
        )
    validate_sys_id(identity)


async def _records(
    client: ServiceNowClient, table: str, query: str, fields: list[str], maximum: int
) -> list[dict[str, Any]]:
    check_table_access(table)
    result = await client.query_records(table, query, fields=fields, limit=maximum + 1)
    rows = result.get("records")
    count = result.get("count")
    if (
        not isinstance(rows, list)
        or any(not isinstance(row, dict) for row in rows)
        or len(rows) > maximum
        or type(count) is int
        and count > len(rows)
    ):
        raise CapabilityError(
            "Choice source metadata is incomplete or exceeds its bound.", "UNAVAILABLE"
        )
    for row in rows:
        _revision(row)
    if len({scalar(row.get("sys_id")) for row in rows}) != len(rows):
        raise CapabilityError(
            "Choice source metadata identities are ambiguous.", "UNAVAILABLE"
        )
    return sorted(rows, key=lambda row: scalar(row.get("sys_id")) or "")


def _existing(flow: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any] | None:
    if edit.get("op") == "create_field":
        return None
    section, name = edit.get("collection"), edit.get("name")
    if section not in {"inputs", "outputs", "flowVariables"} or not isinstance(
        name, str
    ):
        return None
    root = next(
        (row for row in flow[section] if row.get("name") == name.split(".")[0]), None
    )
    if root is None:
        return None
    if root.get("name") == name:
        return root
    return next(
        (
            row
            for path, row in declarations.walk([declarations.unpack(root)])
            if path == name
        ),
        None,
    )


async def _source(
    client: ServiceNowClient, source: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    table, field = source["table"], source["field"]
    check_table_access(table)
    schema = await client.studio_table_schema(table)
    columns = schema.get("columns") if isinstance(schema, dict) else None
    if (
        not isinstance(columns, list)
        or not 1 <= len(columns) <= MAX_CHOICES
        or any(
            not isinstance(column, dict)
            or not isinstance(column.get("definition"), dict)
            for column in columns
        )
    ):
        raise CapabilityError(
            "The native choice column picker metadata is incomplete.", "UNAVAILABLE"
        )
    candidates = [
        column for column in columns if column["definition"].get("name") == field
    ]
    if len(candidates) != 1 or candidates[0]["definition"].get("type") != "choice":
        raise CapabilityError(
            "The source field is not uniquely offered by the native choice picker.",
            "UNAVAILABLE",
        )
    selected = candidates[0]
    definition = selected["definition"]
    if any(definition.get(key) for key in ("dynamic", "data_structure")):
        raise CapabilityError(
            "The native choice field has an unresolved dynamic provider.", "UNAVAILABLE"
        )
    settings = {
        "choiceType": "2",
        "table": table,
        "columnName": field,
        "choices": [],
        "defaultChoices": copy.deepcopy(definition.get("choices")),
    }
    native_choices = declarations.choices(settings)
    ancestry: list[str] = []
    tables: list[dict[str, Any]] = []
    current = table
    for _ in range(MAX_DEPTH):
        if current in ancestry:
            raise CapabilityError(
                "Choice source table inheritance is cyclic.", "UNAVAILABLE"
            )
        rows = await _records(
            client, "sys_db_object", f"name={current}", TABLE_FIELDS, 1
        )
        if len(rows) != 1 or scalar(rows[0].get("name")) != current:
            raise CapabilityError(
                "The choice source table ancestry is not readable.", "UNAVAILABLE"
            )
        row = rows[0]
        if "super_class" not in row or "super_class.name" not in row:
            raise CapabilityError(
                "Choice source table ancestry metadata is incomplete.", "UNAVAILABLE"
            )
        ancestry.append(current)
        tables.append(row)
        parent = scalar(row.get("super_class.name"))
        if not parent:
            if scalar(row.get("super_class")):
                raise CapabilityError(
                    "The choice source parent table is not readable.", "UNAVAILABLE"
                )
            break
        current = _identifier(parent)
    else:
        raise CapabilityError(
            "Choice source table inheritance exceeds its bound.", "UNAVAILABLE"
        )

    dictionary = await _records(
        client,
        "sys_dictionary",
        f"nameIN{','.join(ancestry)}^element={field}",
        DICTIONARY_FIELDS,
        MAX_DEPTH,
    )
    by_table: dict[str, dict[str, Any]] = {}
    for row in dictionary:
        owner = scalar(row.get("name"))
        if (
            owner not in ancestry
            or scalar(row.get("element")) != field
            or owner in by_table
        ):
            raise CapabilityError(
                "Choice source dictionary identities are ambiguous.", "UNAVAILABLE"
            )
        if not scalar(row.get("internal_type")) or scalar(row.get("choice")) not in {
            "1",
            "2",
            "3",
        }:
            raise CapabilityError(
                "The choice dictionary field metadata is incomplete.", "UNAVAILABLE"
            )
        by_table[owner] = row
    owner = next((name for name in ancestry if name in by_table), None)
    if owner is None:
        raise CapabilityError(
            "The source choice dictionary field is not readable.", "UNAVAILABLE"
        )

    choices = await _records(
        client,
        "sys_choice",
        f"nameIN{','.join(ancestry)}^element={field}^ORDERBYname^ORDERBYsequence^ORDERBYsys_id",
        CHOICE_FIELDS,
        MAX_CHOICES,
    )
    selected_dictionary = by_table[owner]
    redirect_table = scalar(selected_dictionary.get("choice_table")) or None
    redirect_field = scalar(selected_dictionary.get("choice_field")) or None
    origins: list[list[tuple[str, str]]] = []
    for choice in native_choices:
        parameters = choice.get("parameters", {})
        origin_table = parameters.get("name") or redirect_table
        origin_field = parameters.get("element") or redirect_field or field
        if (
            not isinstance(origin_field, str)
            or not NAME.fullmatch(origin_field)
            or origin_table is not None
            and (not isinstance(origin_table, str) or not NAME.fullmatch(origin_table))
        ):
            raise CapabilityError(
                "The native choice origin table or field is not independently identifiable.",
                "UNAVAILABLE",
            )
        origins.append(
            [
                (name, origin_field)
                for name in ([origin_table] if origin_table is not None else ancestry)
            ]
        )
    queried = {(name, field) for name in ancestry}
    additional = {pair for candidates in origins for pair in candidates} - queried
    if len(additional) > MAX_DEPTH:
        raise CapabilityError(
            "Native choice origin metadata exceeds its bounded limit.", "UNAVAILABLE"
        )
    origin_tables = []
    for origin_table in sorted({name for name, _ in additional} - set(ancestry)):
        check_table_access(origin_table)
        rows = await _records(
            client, "sys_db_object", f"name={origin_table}", TABLE_FIELDS, 1
        )
        if len(rows) != 1 or scalar(rows[0].get("name")) != origin_table:
            raise CapabilityError(
                "The native choice origin table metadata is not readable.",
                "UNAVAILABLE",
            )
        origin_tables.extend(rows)
    for origin_table, origin_field in sorted(additional):
        choices.extend(
            await _records(
                client,
                "sys_choice",
                f"nameIN{origin_table}^element={origin_field}^ORDERBYsequence^ORDERBYsys_id",
                CHOICE_FIELDS,
                MAX_CHOICES - len(choices),
            )
        )
    queried.update(additional)
    active: set[tuple[str, str, str, str]] = set()
    for row in choices:
        if (
            (scalar(row.get("name")), scalar(row.get("element"))) not in queried
            or not isinstance(scalar(row.get("value")), str)
            or not scalar(row.get("label"))
            or not set(CHOICE_FIELDS) <= row.keys()
            or scalar(row.get("inactive")) not in {"true", "false", "0", "1"}
        ):
            raise CapabilityError(
                "Choice origin records are incomplete or contradictory.", "UNAVAILABLE"
            )
        if scalar(row.get("inactive")) in {"false", "0"}:
            active.add(
                (
                    scalar(row.get("name")) or "",
                    scalar(row.get("element")) or "",
                    scalar(row.get("value")) or "",
                    scalar(row.get("label")) or "",
                )
            )
    # Native metadata owns dependency, qualifier, inheritance and language
    # selection. Readable origin records substantiate options, never replace them.
    for choice, candidates in zip(native_choices, origins, strict=True):
        if not any(
            (name, column, choice["value"], choice["label"]) in active
            for name, column in candidates
        ):
            raise CapabilityError(
                "The native choice origin records are not readable for the offered options.",
                "UNAVAILABLE",
            )
    return settings, {
        "source": copy.deepcopy(source),
        "native_column": copy.deepcopy(selected),
        "ancestry": ancestry,
        "dictionary_owner": owner,
        "tables": tables,
        "dictionary": dictionary,
        "choices": sorted(choices, key=lambda row: scalar(row.get("sys_id")) or ""),
        "origin_tables": origin_tables,
    }


async def resolve(
    client: ServiceNowClient, flow: dict[str, Any], edit: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve requested/current native choices before ``fields.edit_field``.

    ``choice_source`` is exactly ``{table, field}``; ``choice_option`` is '1'
    (with None) or '3' (without None). Return native field settings and separate
    evidence for the planner's reference_revision. No registry or execution
    fallback is permitted. Array item lookup uses the authoritative packed root.
    """
    if edit.get("op") not in {"create_field", "update_field"}:
        return None, None
    existing = _existing(flow, edit)
    kind = edit.get("type", (existing or {}).get("type"))
    if "choice_source" in edit:
        requested = edit["choice_source"]
        if (
            edit.get("collection") not in {"inputs", "outputs"}
            or kind not in {"choice", "array.choice"}
            or not isinstance(requested, dict)
            or set(requested) != {"table", "field"}
            or "choices" in edit
        ):
            raise CapabilityError(
                "choice_source is exclusive with choices and requires a Choice interface declaration."
            )
        source = {
            "table": _identifier(requested["table"]),
            "field": _identifier(requested["field"]),
        }
    else:
        source = None
    if "choice_option" in edit and (
        not isinstance(edit["choice_option"], str)
        or edit["choice_option"] not in {"1", "3"}
    ):
        raise CapabilityError("choice_option must be '1' or '3'.")
    current = (
        declarations.choice_source(existing)
        if existing and existing.get("type") in {"choice", "array.choice"}
        else None
    )
    if (
        existing
        and (
            source is not None
            and source != current
            or current is not None
            and "choices" in edit
        )
        and "default_value" not in edit
    ):
        raise CapabilityError(
            "Changing the choice source requires an explicit default_value, including an explicit clear."
        )
    evidence: dict[str, Any] = {}
    if current is not None:
        settings, observed = await _source(client, current)
        evidence["current"] = observed
    else:
        settings = None
    if source is not None and source != current:
        settings, observed = await _source(client, source)
        evidence["requested"] = observed
    if "choices" in edit or kind not in {"choice", "array.choice"}:
        settings = None
    return settings, evidence or None
