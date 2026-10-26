"""Native Decision Table metadata, branch construction, and output projection.

Studio 27: table hydration 450018-450141; branch definitions 219510-219538;
otherwise 450295-450363; readback references 92899-92938; outputs 163433-163456.
All platform operations here are metadata reads.
"""

import copy
import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, input_modes, native, tree
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import resolve_ref_value, validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

_TABLE = "sys_decision"
_RECORD_FIELDS = [
    "sys_id",
    "name",
    "active",
    "sys_class_name",
    "sys_scope",
    "sys_updated_on",
    "sys_mod_count",
]
_RUNTIME = {
    "value",
    "displayValue",
    "display_value",
    "script",
    "scriptActive",
    "canDrop",
    "isNew",
    "isDirty",
    "field_name",
    "parent",
    "data_format",
}
_TYPES = {
    "decision_table": "reference",
    "answer_table": "table_name",
    "execution": "choice",
    "has_branches": "boolean",
    "include_otherwise": "boolean",
}
_OUTPUTS = {
    "answer": "document_id",
    "table_name": "table_name",
    "records": "records",
    "ordered_ids": "glide_list",
    "count": "integer",
}


def _unavailable(message: str) -> CapabilityError:
    return CapabilityError(message, "UNAVAILABLE")


def _rows(value: Any, description: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise _unavailable(f"The native {description} metadata is unreadable.")
    names = [data.spec(row).get("name") for row in value]
    if any(not isinstance(name, str) or not name for name in names) or len(
        names
    ) != len(set(names)):
        raise _unavailable(
            f"The native {description} names are incomplete or ambiguous."
        )
    for row in value:
        parameter = data.spec(row)
        if "parameter" in row and not isinstance(row["parameter"], dict):
            raise _unavailable(f"The native {description} parameter is malformed.")
        if row.get("name") != parameter["name"]:
            raise _unavailable(
                f"The native {description} parameter identity is contradictory."
            )
        if row.get("type", parameter.get("type")) != parameter.get("type"):
            raise _unavailable(
                f"The native {description} parameter type is contradictory."
            )
        data.value_type(row)
        if not isinstance(parameter.get("attributes", {}), dict):
            raise _unavailable(f"The native {description} attributes are malformed.")
        children = parameter.get("children", [])
        _rows(children, description + " child")
    return value


def _schema(row: dict[str, Any]) -> dict[str, Any]:
    """Compare parameter contracts, not GET's optional redundant binding fields."""
    parameter = native.parameter(data.spec(row))
    result = {
        key: copy.deepcopy(value)
        for key, value in parameter.items()
        if key not in _RUNTIME and key != "children"
    }
    result["children"] = [_schema(child) for child in parameter.get("children", [])]
    return result


def _definition_schema(
    definition: dict[str, Any], *, branch: bool = False
) -> dict[str, Any]:
    result = copy.deepcopy(definition)
    if branch:
        result.pop("name", None)
    result["inputs"] = [_schema(row) for row in definition["inputs"]]
    return result


def _catalog(catalog: Any) -> dict[str, dict[str, Any]]:
    if isinstance(catalog, dict):
        catalog = catalog.get("logic", catalog.get("flow_logic"))
    if not isinstance(catalog, list) or any(
        not isinstance(row, dict) for row in catalog
    ):
        raise _unavailable("The native flow-logic catalog is unreadable.")
    result = {}
    for kind in ("DECISION", "DECISIONBLOCK"):
        matches = [row for row in catalog if row.get("type") == kind]
        if len(matches) != 1:
            raise _unavailable(f"The native {kind} definition is absent or ambiguous.")
        definition = matches[0]
        validate_sys_id(definition.get("id", ""))
        if definition.get("userCanRead") is not True or not isinstance(
            definition.get("attributes"), dict
        ):
            raise _unavailable(f"The native {kind} definition is not readable.")
        rows = _rows(definition.get("inputs"), kind + " input")
        expected = (
            _TYPES
            if kind == "DECISION"
            else {"label": "string", "answer": "document_id"}
        )
        for name, expected_type in expected.items():
            row = data.named(rows, name)
            if data.value_type(row).kind != expected_type:
                raise _unavailable(f"The native {kind} input contract changed.")
        if kind == "DECISION":
            if (
                data.spec(data.named(rows, "decision_table")).get("reference") != _TABLE
                or data.spec(data.named(rows, "answer_table")).get("readonly")
                is not True
            ):
                raise _unavailable(
                    "The native Decision Table or derived Answer Table contract changed."
                )
            choices = data.spec(data.named(rows, "execution")).get("choices")
            if (
                not isinstance(choices, list)
                or any(
                    not isinstance(choice, dict)
                    or not isinstance(choice.get("value"), str)
                    for choice in choices
                )
                or {choice["value"] for choice in choices} != {"first_match", "run_all"}
            ):
                raise _unavailable("The native decision execution choices changed.")
            variables = _rows(definition.get("variables"), "decision output")
            for name, expected_type in _OUTPUTS.items():
                output = data.named(variables, name)
                if (
                    data.value_type(output).kind != expected_type
                    or name != "count"
                    and output.get("internal_link") != "answer_table"
                ):
                    raise _unavailable("The native decision output contract changed.")
        else:
            if any(
                definition["attributes"].get(key) not in (False, "false")
                for key in ("can_move", "can_delete")
            ):
                raise _unavailable(
                    "Native decision branches must not be independently movable or deletable."
                )
        result[kind] = definition
    if result["DECISIONBLOCK"].get("connectedTo") != result["DECISION"]["id"]:
        raise _unavailable(
            "The native decision branch definition belongs to another block."
        )
    return result


def _definition_matches(
    step: tree.Step, definitions: dict[str, dict[str, Any]]
) -> None:
    definition = step.raw.get("flowLogicDefinition")
    current = definitions.get(step.logic_type or "")
    if not isinstance(definition, dict) or current is None:
        raise _unavailable("The native decision definition is unavailable.")
    _rows(definition.get("inputs"), "stored decision definition")
    if step.raw.get("definitionId") != current["id"] or _definition_schema(
        definition, branch=step.logic_type == "DECISIONBLOCK"
    ) != _definition_schema(current, branch=step.logic_type == "DECISIONBLOCK"):
        raise _unavailable(
            "The stored decision definition differs from the current native catalog."
        )
    actual = _rows(step.raw.get("inputs"), "stored decision input")
    if [row["name"] for row in actual] != [
        row["name"] for row in current["inputs"]
    ] or any(
        _schema(row) != _schema(declared)
        for row, declared in zip(actual, current["inputs"], strict=True)
    ):
        raise _unavailable(
            "The stored decision input schema differs from its native definition."
        )


def _table_name(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value):
        raise _unavailable("Decision metadata requires a static native table name.")
    check_table_access(value)
    return value


async def _table(table: str, client: ServiceNowClient) -> dict[str, Any]:
    _table_name(table)
    check_table_access("sys_db_object")
    response = await client.query_records(
        "sys_db_object", f"name={table}", fields=["name", "label", "sys_id"], limit=2
    )
    records = response.get("records") if isinstance(response, dict) else None
    if (
        not isinstance(records, list)
        or len(records) != 1
        or not isinstance(records[0], dict)
        or records[0].get("name") != table
        or not records[0].get("label")
    ):
        raise _unavailable(
            "A decision reference table is absent, ambiguous, or unreadable."
        )
    validate_sys_id(records[0].get("sys_id", ""))
    descriptor = await client.studio_table_descriptor(table)
    if (
        not isinstance(descriptor, dict)
        or not isinstance(descriptor.get("displayField"), str)
        or not re.fullmatch(r"[a-z][a-z0-9_]*", descriptor["displayField"])
    ):
        raise _unavailable(
            "A decision reference table has no readable display-field descriptor."
        )
    return {"table": records[0], "descriptor": descriptor}


def _persisted_answers(answers: Any) -> list[dict[str, Any]]:
    """Persisted answers plus native getter validity fields supplied by the table API."""
    if not isinstance(answers, list) or any(
        not isinstance(answer, dict) for answer in answers
    ):
        raise _unavailable("The saved native decision answers are malformed.")
    result = []
    for answer in answers:
        projected = {
            key: copy.deepcopy(answer[key])
            for key in ("label", "multipleAnswerRecord", "table", "value", "valid")
            if key in answer
        }
        if "answerElementValues" in answer:
            elements = answer["answerElementValues"]
            if not isinstance(elements, list) or any(
                not isinstance(element, dict) for element in elements
            ):
                raise _unavailable("The native decision answer elements are malformed.")
            projected["answerElementValues"] = [
                {
                    key: copy.deepcopy(element[key])
                    for key in (
                        "answerElementName",
                        "label",
                        "table",
                        "type",
                        "value",
                        "valid",
                    )
                    if key in element
                }
                for element in elements
            ]
        result.append(projected)
    return result


def _persisted_information(information: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: copy.deepcopy(information[key])
        for key in ("display_value", "active")
        if key in information
    }
    result["decision_answers"] = _persisted_answers(information.get("decision_answers"))
    return result


async def _read(identity: str, client: ServiceNowClient) -> dict[str, Any]:
    validate_sys_id(identity)
    check_table_access(_TABLE)
    record = await client.get_record(_TABLE, identity, fields=_RECORD_FIELDS)
    metadata = await client.studio_decision_table(identity)
    if not isinstance(record, dict):
        raise _unavailable("The Decision Table record is unreadable.")
    revision = record.get("sys_mod_count")
    if (
        record.get("sys_id") != identity
        or record.get("active") not in (True, "true")
        or not isinstance(record.get("sys_class_name"), str)
        or not record["sys_class_name"]
        or record["sys_class_name"] == "sys_decision_delta"
    ):
        raise _unavailable(
            "The Decision Table is not an active native decision definition."
        )
    if not (
        type(revision) is int
        and revision >= 0
        or isinstance(revision, str)
        and revision.isdecimal()
    ) or not resolve_ref_value(record.get("sys_scope")):
        raise _unavailable(
            "The Decision Table revision or scope metadata is unreadable."
        )
    if (
        not isinstance(metadata, dict)
        or metadata.get("id", identity) != identity
        or metadata.get("sys_id", identity) != identity
        or metadata.get("userCanRead") is False
        or not isinstance(metadata.get("security", {}), dict)
        or metadata.get("security", {}).get("can_read") is False
    ):
        raise _unavailable(
            "The native Decision Table response is unreadable or has another identity."
        )
    if metadata.get("active") is not True:
        raise _unavailable("The native Decision Table metadata is not active.")
    if (
        not isinstance(metadata.get("name"), str)
        or not metadata["name"]
        or record.get("name") != metadata["name"]
    ):
        raise _unavailable("The native Decision Table name disagrees with its record.")
    inputs = _rows(metadata.get("inputs"), "Decision Table input")
    for row in inputs:
        if "parameter" in row or "value" in row or row.get("scriptActive"):
            raise _unavailable(
                "Decision Table inputs must be native parameter descriptors, not runtime bindings."
            )
        validate_sys_id(row.get("id", ""))
    answer_table = metadata.get("answerTable")
    if (
        not isinstance(answer_table, dict)
        or not isinstance(answer_table.get("displayValue"), str)
        or not answer_table["displayValue"]
    ):
        raise _unavailable("The native Decision Table Answer Table is incomplete.")
    _table_name(answer_table.get("value"))
    answers = metadata.get("distinctAnswers")
    if not isinstance(answers, list):
        raise _unavailable("The native Decision Table answers are unreadable.")
    identities = []
    for answer in answers:
        if (
            not isinstance(answer, dict)
            or not isinstance(answer.get("label"), str)
            or not answer["label"]
        ):
            raise _unavailable(
                "The native Decision Table answer metadata is incomplete."
            )
        if answer.get("valid") is not True:
            raise _unavailable(
                "The native Decision Table contains an invalid or incomplete answer."
            )
        elements = answer.get("answerElementValues", [])
        if not isinstance(elements, list) or any(
            not isinstance(element, dict) or element.get("valid") is not True
            for element in elements
        ):
            raise _unavailable(
                "The native Decision Table answer elements are invalid or incomplete."
            )
        validate_sys_id(answer.get("value", ""))
        identities.append(answer["value"])
    if len(identities) != len(set(identities)):
        raise _unavailable("The native Decision Table answer identities are ambiguous.")
    _persisted_answers(answers)
    tables = {answer_table["value"]}
    for row in data.bindings(inputs):
        parameter = data.spec(row)
        reference = data.value_type(row).reference
        if reference:
            tables.add(_table_name(reference))
        if parameter.get("dynamic") or parameter.get("type") in {
            "dynamic_inputs",
            "dynamic_choice",
            "dynamic_template",
        }:
            raise _unavailable(
                "Dynamic Decision Table parameters require their native schema adapter."
            )
    table_evidence = {table: await _table(table, client) for table in sorted(tables)}
    return {
        "identity": identity,
        "record": record,
        "metadata": metadata,
        "tables": table_evidence,
    }


def _values(step: tree.Step) -> tuple[str, str, bool, bool]:
    rows = _rows(step.raw.get("inputs"), "decision input")
    for row in rows:
        if row["name"] in _TYPES and row.get("scriptActive"):
            raise CapabilityError(
                "Decision control inputs require static native values."
            )
        if not data.spec(row).get("readonly") and not data.spec(row).get("readOnly"):
            _binding_value(row, row)
    values = {row["name"]: row.get("value") for row in rows}
    identity = values.get("decision_table")
    if not isinstance(identity, str):
        raise CapabilityError("A decision requires a static Decision Table sys_id.")
    validate_sys_id(identity)
    execution = values.get("execution")
    branches = values.get("has_branches")
    otherwise = values.get("include_otherwise")
    if (
        not isinstance(execution, str)
        or execution not in {"first_match", "run_all"}
        or type(branches) is not bool
        or type(otherwise) is not bool
    ):
        raise CapabilityError(
            "Decision execution and branch toggles require native choices and JSON booleans."
        )
    if otherwise and not branches:
        raise CapabilityError("Include Otherwise requires Use Branches.")
    return identity, execution, branches, otherwise


def _expected_answers(step: tree.Step, metadata: dict[str, Any]) -> list[str]:
    _, _, use_branches, otherwise = _values(step)
    result: list[str] = (
        [answer["value"] for answer in metadata["distinctAnswers"]]
        if use_branches
        else []
    )
    if otherwise:
        result.append("")
    return result


def _binding_value(
    row: dict[str, Any], declared: dict[str, Any], *, scripted: bool = False
) -> None:
    if "value" not in row:
        raise _unavailable("A Decision Table input binding is incomplete.")
    if (
        data.spec(declared).get("readonly") is True
        or data.spec(declared).get("readOnly") is True
    ):
        native_value = declared.get(
            "value", data.spec(declared).get("defaultValue", "")
        )
        if data.value_type(row).kind == "boolean":
            native_value = native_value in (True, "true", "1")
        if scripted or row.get("scriptActive") or row["value"] != native_value:
            raise CapabilityError(
                "A read-only Decision Table input differs from its native value."
            )
        return
    if row.get("scriptActive") and (
        not isinstance(row.get("script"), dict) or not row["script"]
    ):
        raise _unavailable("A scripted Decision Table input has no native script map.")
    # The common input-mode validator resolves scripts after final alias remapping.
    probe = copy.deepcopy(row)
    active_script = scripted or bool(row.get("scriptActive"))
    input_modes.value(probe, row["value"], scripted=active_script)
    if active_script:
        return
    kind = data.value_type(row).kind
    actual_children = row.get("children", [])
    if not isinstance(actual_children, list) or any(
        not isinstance(child, dict) for child in actual_children
    ):
        raise _unavailable("Decision Table child bindings are malformed.")
    if data.whole_pill(row["value"]) and actual_children:
        raise _unavailable(
            "A whole Decision Table input pill retains stale child bindings."
        )
    if row["value"] != "" or not actual_children:
        return
    declared_children = data.children(declared)
    if kind == "object":
        _rows(actual_children, "Decision Table Object child")
        if [child["name"] for child in actual_children] != [
            child["name"] for child in declared_children
        ]:
            raise _unavailable(
                "Decision Table Object children differ from their native schema."
            )
        pairs = zip(actual_children, declared_children, strict=True)
    elif kind.startswith("array.") and len(declared_children) == 1:
        pairs = ((child, declared_children[0]) for child in actual_children)
    else:
        raise _unavailable(
            "A scalar Decision Table input retains unexpected child bindings."
        )
    for child, declared_child in pairs:
        if _schema(child) != _schema(declared_child):
            raise _unavailable(
                "A Decision Table child binding differs from its native schema."
            )
        _binding_value(child, declared_child)


def _snapshot(
    step: tree.Step,
    evidence: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
    *,
    check_values: bool = True,
) -> None:
    metadata = evidence["metadata"]
    identity = evidence["identity"]
    information = step.raw.get("decisionTableInformation")
    if (
        step.raw.get("decisionTableReference") != identity
        or not isinstance(information, dict)
        or information.get("display_value") != metadata["name"]
        or information.get("active") != "true"
        or information.get("table") not in (None, "", metadata["answerTable"]["value"])
        or _persisted_answers(information.get("decision_answers"))
        != _persisted_answers(metadata["distinctAnswers"])
    ):
        raise _unavailable(
            "The saved Decision Table metadata is stale or incomplete; refusing to replace it implicitly."
        )
    actual = _rows(step.raw.get("decisionTableInputs"), "saved Decision Table input")
    declared = metadata["inputs"]
    if [row["name"] for row in actual] != [row["name"] for row in declared] or any(
        row.get("id") != native["id"] or _schema(row) != _schema(native)
        for row, native in zip(actual, declared, strict=True)
    ):
        raise _unavailable(
            "The saved Decision Table input schema differs from current native metadata."
        )
    if check_values:
        for row, native in zip(actual, declared, strict=True):
            _binding_value(row, native)
    source = data.named(step.raw["inputs"], "answer_table")
    table = metadata["answerTable"]
    # The native value serializer omits these derived editor mirrors (472017-472030).
    if (
        source.get("value") != table["value"]
        or any(
            source.get(key) not in (None, "", table["value"])
            for key in ("reference", "table", "data_format")
        )
        or any(
            source.get(key) not in (None, "", table["value"], table["displayValue"])
            for key in ("displayValue", "reference_display")
        )
    ):
        raise _unavailable(
            "The derived Answer Table binding disagrees with native decision metadata."
        )
    child_definition = step.raw.get("childBlockDefinition")
    if child_definition is not None and (
        not isinstance(child_definition, dict)
        or "inputs" not in child_definition
        or _definition_schema(child_definition)
        != _definition_schema(definitions["DECISIONBLOCK"])
    ):
        raise _unavailable(
            "The saved Decision branch template differs from the native catalog."
        )


def _children(
    step: tree.Step, metadata: dict[str, Any], definitions: dict[str, dict[str, Any]]
) -> dict[str, tree.Step]:
    result = {}
    order = [answer["value"] for answer in metadata["distinctAnswers"]]
    table = metadata["answerTable"]["value"]
    for branch in step.children:
        if branch.logic_type != "DECISIONBLOCK" or branch.raw.get("parent") != step.uid:
            raise _unavailable(
                "A decision contains a child that is not its native answer branch."
            )
        _definition_matches(branch, definitions)
        for binding in branch.raw["inputs"]:
            _binding_value(binding, binding)
        answer = data.named(branch.raw["inputs"], "answer")
        if answer.get("scriptActive"):
            raise CapabilityError(
                "Decision branch answer identities cannot be scripted."
            )
        value = answer.get("value")
        if (
            not isinstance(value, str)
            or value in result
            or value
            and value not in order
        ):
            raise _unavailable(
                "A saved decision branch has a stale or ambiguous answer identity."
            )
        label = next(
            (
                answer["label"]
                for answer in metadata["distinctAnswers"]
                if answer["value"] == value
            ),
            "Otherwise",
        )
        if branch.raw["flowLogicDefinition"].get("name") not in (
            label,
            definitions["DECISIONBLOCK"]["name"],
        ):
            raise _unavailable(
                "A saved decision branch definition no longer names its native answer."
            )
        if any(
            answer.get(key) not in (None, "", table) for key in ("reference", "table")
        ):
            raise _unavailable(
                "A saved decision branch references another Answer Table."
            )
        result[value] = branch
    actual = list(result)
    if actual and actual not in (order, [*order, ""]):
        raise _unavailable(
            "Saved decision branches do not contain every native answer in order."
        )
    return result


async def _dependencies(
    rows: list[dict[str, Any]], client: ServiceNowClient
) -> dict[str, Any]:
    evidence = {}
    for row in data.bindings(rows):
        parameter = data.spec(row)
        dependency = parameter.get("dependent_on")
        if not dependency:
            continue
        if not isinstance(dependency, str) or data.value_type(row).kind not in {
            "document_id",
            "field_name",
            "conditions",
        }:
            raise _unavailable(
                "This Decision Table input dependency requires another native adapter."
            )
        source = data.named(rows, dependency.removeprefix("inputs."))
        if data.value_type(source).kind != "table_name" or source.get("scriptActive"):
            raise _unavailable(
                "A Decision Table input dependency must name a static declared Table Name input."
            )
        table = _table_name(source.get("value"))
        if table not in evidence:
            evidence[table] = await _table(table, client)
        if any(
            row.get(key) not in (None, "", table)
            for key in ("reference", "table", "data_format")
        ):
            raise _unavailable(
                "A Decision Table dependent input is not bound to its declared table."
            )
    return evidence


def _branch_requests(
    branches: list[dict[str, Any]] | None, answers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if branches is None:
        return [{"answer": answer["value"]} for answer in answers]
    if not isinstance(branches, list) or any(
        not isinstance(branch, dict) or set(branch) - {"answer", "label", "ref"}
        for branch in branches
    ):
        raise CapabilityError(
            "Decision branches require answer, optional label, and optional ref fields."
        )
    if [branch.get("answer") for branch in branches] != [
        answer["value"] for answer in answers
    ]:
        raise CapabilityError(
            "Decision branches must list every native answer in order, with Otherwise last when enabled."
        )
    aliases = []
    for branch in branches:
        for key in ("label", "ref"):
            if key in branch and (not isinstance(branch[key], str) or not branch[key]):
                raise CapabilityError(
                    f"A decision branch {key} must be a nonempty string."
                )
        if "ref" in branch:
            aliases.append(branch["ref"])
    if len(aliases) != len(set(aliases)):
        raise CapabilityError("Decision branch refs must be unique.")
    return branches


def _new_branch(
    flow: dict[str, Any],
    parent: tree.Step,
    definition: dict[str, Any],
    answer: dict[str, Any],
    seed: str,
) -> tree.Step:
    definition = copy.deepcopy(definition)
    definition["name"] = answer["label"]
    data.named(definition["inputs"], "answer")["value"] = answer["value"]
    branch = tree.new_logic_step(flow["id"], definition, {"answer": answer["value"]})
    digest = hashlib.sha256(
        json.dumps([seed, answer["value"]], separators=(",", ":")).encode()
    ).hexdigest()
    branch.raw["uiUniqueIdentifier"] = str(uuid.UUID(hex=digest[:32], version=4))
    branch.raw["parent"] = parent.uid
    answer_input = data.named(branch.raw["inputs"], "answer")
    data.set_value(answer_input, answer["value"])
    table = data.named(parent.raw["inputs"], "answer_table")["value"]
    answer_input.update(reference=table, table=table)
    branch.raw["data"]["answer"] = answer["value"]
    return branch


async def configure(
    step: tree.Step,
    flow: dict[str, Any],
    catalog: Any,
    client: ServiceNowClient,
    *,
    decision_inputs: dict[str, Any] | None,
    branches: list[dict[str, Any]] | None,
    delete_children: bool,
    seed: str,
    new: bool,
    previous: tree.Step | None = None,
    script_inputs: set[str] | None = None,
) -> dict[str, Any]:
    """Configure a Decision after ordinary inputs, preserving retained branch objects.

    A table replacement requires a complete new input mapping. Removing populated
    branches also requires explicit child deletion. Removed Steps include descendants.
    Rejections do not mutate the supplied step beyond edits already made by callers.
    """
    if step.logic_type != "DECISION":
        raise CapabilityError(
            "Only a native Decision can configure decision inputs or branches."
        )
    if decision_inputs is not None and not isinstance(decision_inputs, dict):
        raise CapabilityError("decision_inputs must be an object.")
    definitions = _catalog(catalog)
    if step.raw.get("flowSysId") != flow.get("id"):
        raise _unavailable("The Decision instance belongs to another flow.")
    _definition_matches(step, definitions)
    identity, _, use_branches, otherwise = _values(step)
    evidence = await _read(identity, client)
    previous_table = step.raw.get("decisionTableReference")
    table_changed = not new and previous_table != identity
    if not new:
        if not isinstance(previous_table, str) or not previous_table:
            raise _unavailable("The saved Decision Table reference is missing.")
        old_evidence = (
            await _read(previous_table, client) if table_changed else evidence
        )
        _snapshot(step, old_evidence, definitions, check_values=previous is None)
        existing = _children(step, old_evidence["metadata"], definitions)
        if previous is not None:
            if (
                previous.uid != step.uid
                or previous.logic_type != "DECISION"
                or _values(previous)[0] != previous_table
            ):
                raise _unavailable(
                    "The prior Decision snapshot has another native identity."
                )
            _definition_matches(previous, definitions)
            _snapshot(previous, old_evidence, definitions)
            prior_children = _children(previous, old_evidence["metadata"], definitions)
            if list(prior_children) != _expected_answers(
                previous, old_evidence["metadata"]
            ):
                raise _unavailable(
                    "The prior decision branches disagree with the saved native branch toggles."
                )
        elif (
            not existing
            and use_branches
            and old_evidence["metadata"]["distinctAnswers"]
        ):
            raise _unavailable(
                "Enabling decision branches requires a prior Decision snapshot; missing saved branches cannot be repaired implicitly."
            )
    else:
        if step.children:
            raise CapabilityError("A new Decision cannot adopt existing branches.")
        source = data.named(step.raw["inputs"], "answer_table")
        if source.get("value") != data.spec(source).get("defaultValue", ""):
            raise CapabilityError(
                "Answer Table is derived and cannot be supplied by callers."
            )
        existing = {}
    if table_changed and decision_inputs is None:
        raise CapabilityError(
            "Changing a Decision Table requires explicit decision_inputs rebinding."
        )
    metadata = evidence["metadata"]
    declared = metadata["inputs"]
    names = {row["name"] for row in declared}
    scripted_names = (script_inputs or set()) & names
    values = decision_inputs or {}
    if set(values) - names:
        raise CapabilityError(
            "decision_inputs contains an unknown native Decision Table input."
        )
    writable = {
        row["name"]
        for row in declared
        if not data.spec(row).get("readonly") and not data.spec(row).get("readOnly")
    }
    if table_changed and not writable.issubset(set(values) | scripted_names):
        raise CapabilityError(
            "Changing a Decision Table requires an explicit value for every new writable Decision Table input."
        )
    if new or table_changed:
        bindings = []
        for parameter in declared:
            name = parameter["name"]
            default = copy.deepcopy(parameter.get("defaultValue", ""))
            if (
                parameter.get("mandatory") is True
                and name in writable
                and default in ("", None)
                and name not in values
                and name not in scripted_names
            ):
                raise CapabilityError(
                    f"The Decision Table requires explicit input '{name}'."
                )
            if parameter["type"] == "boolean":
                default = default in (True, "true", "1")
            bindings.append(
                {
                    "id": parameter["id"],
                    "name": name,
                    "parameter": copy.deepcopy(parameter),
                    "value": default,
                    "displayValue": copy.deepcopy(
                        parameter.get("defaultDisplayValue", "")
                    ),
                    "scriptActive": False,
                }
            )
    else:
        bindings = copy.deepcopy(step.raw["decisionTableInputs"])
    for row, declared_input in zip(bindings, declared, strict=True):
        if row["name"] in values:
            input_modes.value(
                row, values[row["name"]], scripted=row["name"] in scripted_names
            )
        _binding_value(row, declared_input, scripted=row["name"] in scripted_names)
    # Native dependency metadata follows its selected static Table Name value.
    for row in data.bindings(bindings):
        dependency = data.spec(row).get("dependent_on")
        if isinstance(dependency, str) and dependency:
            source = data.named(bindings, dependency.removeprefix("inputs."))
            if not new and not table_changed:
                old_source = data.named(
                    step.raw["decisionTableInputs"], dependency.removeprefix("inputs.")
                )
                if (
                    old_source.get("value") != source.get("value")
                    and row["name"] not in values
                ):
                    raise CapabilityError(
                        "Changing a Decision Table input's dependency requires explicit rebinding of its dependent value."
                    )
            if data.value_type(source).kind == "table_name":
                table = _table_name(source.get("value"))
                for key in ("reference", "table", "data_format"):
                    if new or table_changed or key in row:
                        row[key] = table
    evidence["dependencies"] = await _dependencies(bindings, client)
    answers = copy.deepcopy(metadata["distinctAnswers"]) if use_branches else []
    if otherwise:
        answers.append({"value": "", "label": "Otherwise"})
    requests = _branch_requests(branches, answers)
    retained = {} if table_changed else existing
    wanted = {answer["value"] for answer in answers}
    dropped = [
        branch
        for value, branch in existing.items()
        if table_changed or value not in wanted
    ]
    if any(branch.children for branch in dropped) and not delete_children:
        raise CapabilityError(
            "Removing populated decision branches requires children='delete'."
        )
    planned = copy.deepcopy(step.raw)
    information = planned.get("decisionTableInformation", {})
    if not isinstance(information, dict):
        raise _unavailable("The saved Decision Table information is malformed.")
    if new or table_changed:
        information.update(
            display_value=metadata["name"],
            decision_answers=copy.deepcopy(metadata["distinctAnswers"])
            if new
            else _persisted_answers(metadata["distinctAnswers"]),
        )
        if new or "active" in information:
            information["active"] = "true"
        if new or "table" in information:
            information["table"] = metadata["answerTable"]["value"]
    planned.update(
        decisionTableReference=identity,
        decisionTableInputs=bindings,
        decisionTableInformation=information,
    )
    if new:
        planned["childBlockDefinition"] = copy.deepcopy(definitions["DECISIONBLOCK"])
    source = data.named(planned["inputs"], "answer_table")
    table = metadata["answerTable"]
    if new or table_changed:
        source.update(value=table["value"], displayValue=table["displayValue"])
        for key in ("reference", "table", "data_format"):
            if new or key in source:
                source[key] = table["value"]
        if new or "reference_display" in source:
            source["reference_display"] = table["displayValue"]
    if "data" in planned:
        if not isinstance(planned["data"], dict):
            raise _unavailable("The native decision input data mirror is unreadable.")
        planned["data"]["answer_table"] = table["value"]
    planned_parent = tree.Step(planned, "flowlogic")
    children = []
    updates = []
    created = []
    for answer, request in zip(answers, requests, strict=True):
        branch = retained.get(answer["value"])
        if branch is None:
            branch = _new_branch(
                flow, planned_parent, definitions["DECISIONBLOCK"], answer, seed
            )
            created.append(branch.uid)
        raw = copy.deepcopy(branch.raw)
        if "label" in request:
            data.set_value(data.named(raw["inputs"], "label"), request["label"])
            if "data" in raw:
                if not isinstance(raw["data"], dict):
                    raise _unavailable(
                        "The native branch input data mirror is unreadable."
                    )
                raw["data"]["label"] = request["label"]
        if "ref" in request:
            raw["_alias"] = request["ref"]
        if raw != branch.raw:
            updates.append((branch, raw))
        children.append(branch)
    for branch, raw in updates:
        branch.raw = raw
    step.raw.update(planned)
    step.children = children
    return {
        "evidence": evidence,
        "created": created,
        "removed": list(tree.walk(dropped)),
    }


async def answer_fields(decision_id: str, client: ServiceNowClient) -> dict[str, Any]:
    """Read the exact result_elements virtual table used by Studio (248347-248370)."""
    validate_sys_id(decision_id)
    table = "var__m_sys_decision_multi_result_element_" + decision_id
    check_table_access("sys_decision_multi_result")
    check_table_access(table)
    schema = await client.studio_table_schema(table)
    columns = schema.get("columns") if isinstance(schema, dict) else None
    if not isinstance(columns, list) or any(
        not isinstance(column, dict) or not isinstance(column.get("definition"), dict)
        for column in columns
    ):
        raise _unavailable("The native decision answer field schema is unreadable.")
    if schema.get("table", table) != table:
        raise _unavailable(
            "The native decision answer fields belong to another virtual table."
        )
    fields = _rows(
        [column["definition"] for column in columns], "decision answer field"
    )
    return {
        "table": table,
        "fields": copy.deepcopy(fields),
        "schema": copy.deepcopy(schema),
    }


async def validate(
    step: tree.Step, flow: dict[str, Any], client: ServiceNowClient
) -> dict[str, Any]:
    """Compare stored native metadata with current read-only dependencies; never repair."""
    if step.logic_type != "DECISION":
        return {}
    definitions = _catalog(await client.studio_definitions("flow_logic"))
    _definition_matches(step, definitions)
    if step.raw.get("flowSysId") != flow.get("id"):
        raise _unavailable("The Decision instance belongs to another flow.")
    identity, _, _, _ = _values(step)
    evidence = await _read(identity, client)
    _snapshot(step, evidence, definitions)
    children = _children(step, evidence["metadata"], definitions)
    if list(children) != _expected_answers(step, evidence["metadata"]):
        raise _unavailable(
            "The saved decision branches disagree with the native branch toggles."
        )
    evidence["dependencies"] = await _dependencies(
        step.raw["decisionTableInputs"], client
    )
    evidence["definitions"] = copy.deepcopy(definitions)
    return evidence


def serialize_new(step: tree.Step) -> dict[str, Any]:
    """Build only fresh REST DTOs; saved decisions retain their original wire shape."""
    if step.logic_type not in {"DECISION", "DECISIONBLOCK"} or step.raw.get("id"):
        raise CapabilityError(
            "Only newly created native decision instances can be serialized by this constructor."
        )
    raw = step.raw
    scaffolding = {
        "data",
        "isNew",
        "isDirty",
        "errorHandlingInstance",
        "validation_errors",
        "_alias",
    }
    result = {
        key: copy.deepcopy(value)
        for key, value in raw.items()
        if key not in scaffolding
    }
    result.update(native.logic(raw))
    for row in result["inputs"]:
        row.pop("id", None)
    # Decision Table bindings retain the native parameter descriptors in full;
    # they are separate from editor-created ordinary control input wrappers.
    for key in (
        "decisionTableReference",
        "decisionTableInputs",
        "decisionTableInformation",
        "childBlockDefinition",
    ):
        if key in raw:
            result[key] = copy.deepcopy(raw[key])
    if step.logic_type == "DECISION":
        # The new-only persisted mapper excludes the editor's table mirror and
        # API-only extensions; existing saved DTOs never pass through this mapper.
        result["decisionTableInformation"] = _persisted_information(
            raw["decisionTableInformation"]
        )
    if step.logic_type == "DECISIONBLOCK":
        data.named(result["flowLogicDefinition"]["inputs"], "answer").pop("value", None)
        data.named(result["inputs"], "answer")["parameter"].pop("value", None)
    metadata = raw.get("metadata", {})
    if isinstance(metadata, dict):
        result["metadata"] = {
            key: copy.deepcopy(value)
            for key, value in metadata.items()
            if key != "predicates" or value != []
        }
    return result


def output_schema(
    step: tree.Step, roots: Iterable[tree.Step] | None = None
) -> list[dict[str, Any]] | None:
    """Project native execution outputs; branch outputs use their actual Decision parent."""
    if step.logic_type not in {"DECISION", "DECISIONBLOCK"}:
        return None
    parent = step
    branch = step.logic_type == "DECISIONBLOCK"
    if branch:
        if roots is None:
            raise _unavailable(
                "Decision branch outputs require their native Decision parent."
            )
        matches = {
            candidate.uid: candidate
            for candidate in tree.walk(list(roots))
            if candidate.uid == step.raw.get("parent")
            and candidate.logic_type == "DECISION"
        }
        if len(matches) != 1:
            raise _unavailable(
                "The Decision branch output parent is absent or ambiguous."
            )
        parent = next(iter(matches.values()))
    identity, execution, has_branches, _ = _values(parent)
    if not branch and has_branches:
        return []
    if branch and (
        not has_branches or not any(child.uid == step.uid for child in parent.children)
    ):
        raise _unavailable("Decision branch outputs require an attached native branch.")
    outputs = _rows(
        parent.raw.get("flowLogicDefinition", {}).get("variables"), "decision output"
    )
    names = (
        {"answer", "table_name"}
        if branch or execution == "first_match"
        else {"table_name", "records", "count", "ordered_ids"}
    )
    source = data.named(parent.raw["inputs"], "answer_table")
    table = _table_name(source.get("value"))
    result = []
    for output in outputs:
        if output["name"] not in names:
            continue
        output = copy.deepcopy(output)
        if output.get("internal_link") == "answer_table":
            output.update(
                reference=table,
                reference_display=source.get("reference_display")
                or source.get("displayValue")
                or table,
            )
            if output["type"] == "document_id":
                output["type"] = "reference"
        if output["name"] == "answer":
            output.setdefault("attributes", {})["decision_table"] = identity
        result.append(output)
    if {output["name"] for output in result} != names:
        raise _unavailable("The native decision output schema is incomplete.")
    return result
