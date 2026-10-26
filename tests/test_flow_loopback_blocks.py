"""Native loop blocks preserve topology and stable, source-bound target identities."""

import copy
from typing import Any

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, editing, loopbacks, semantics, tree
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.platform.client import ServiceNowClient
from tests.test_flow_editing import DEFS, FLOW, action, base_flow


def definition(
    identity: str,
    name: str,
    kind: str,
    attributes: dict[str, str],
    inputs: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    connected_to: str = "",
) -> dict[str, Any]:
    return {
        "id": identity,
        "name": name,
        "type": kind,
        "attributes": attributes,
        "inputs": list(inputs),
        "variables": [],
        "connectedTo": connected_to,
        "userCanRead": True,
    }


# Minimal metadata from the installed flow-logic catalog, not invented families.
NATIVE: dict[str, dict[str, Any]] = {
    row["type"]: copy.deepcopy(row) for row in DEFS["flow_logic"]
}
NATIVE.update(
    {
        "DOUNTIL": definition(
            "eaeb040687210300e6c2c88fc0e3ec89",
            "Do the following until",
            "DOUNTIL",
            {
                "allow_children": "true",
                "allow_siblings": "true",
                "can_be_nested": "false",
                "decorator_is_top": "true",
                "display_as_sandwich": "true",
                "first_label": "Do",
                "second_label": "until",
            },
            [
                {
                    "id": "bda2cb0473031300612c273ffff6a750",
                    "name": "condition_name",
                    "label": "Condition Label",
                    "type": "string",
                    "mandatory": False,
                    "maxsize": 255,
                    "defaultValue": "",
                },
                {
                    "id": "d7138f0473031300612c273ffff6a78c",
                    "name": "condition",
                    "label": "Condition",
                    "type": "string",
                    "mandatory": True,
                    "maxsize": 2000,
                    "defaultValue": "",
                    "attributes": {"is_scriptable": "false"},
                },
            ],
        ),
        "GOBACKTO": definition(
            "59ea75fd77502110b2b4ddd9cf5a99ef",
            "Go back to",
            "GOBACKTO",
            {
                "allow_children": "false",
                "allow_siblings": "true",
                "bounded_types": "PARALLEL",
                "exclude_placement_in_errorhandler": "true",
                "looping_types": "FOREACH|DOUNTIL",
                "set_target_placement": "true",
                "valid_flowlogic_placements": "IF|ELSEIF|ELSE|DECISIONBLOCK|CATCH|DECISION",
            },
            [
                {
                    "id": "7bf519994391e110e3bf457a7bb8f2fc",
                    "name": "go_back_to_step",
                    "label": "go_back_to_step",
                    "type": "string",
                    "mandatory": True,
                    "maxsize": 40,
                    "defaultValue": "",
                    "attributes": {},
                },
            ],
        ),
        "PARALLEL": definition(
            "5d31e5dedfe50300b61ecae83df26326",
            "Do the following in Parallel",
            "PARALLEL",
            {"add_parallel_block": "true", "can_be_nested": "false"},
        ),
        "PARALLELBLOCK": definition(
            "a1168de173311300b61e66476bf6a7d0",
            "Parallel Branch",
            "PARALLELBLOCK",
            {
                "allow_children": "true",
                "allow_siblings": "false",
                "can_be_nested": "false",
            },
            connected_to="5d31e5dedfe50300b61ecae83df26326",
        ),
        "DECISION": definition(
            "578e90270b2313009002cab937673a7d",
            "Make a decision",
            "DECISION",
            {
                "allow_children": "false",
                "allow_siblings": "true",
                "can_be_nested": "false",
            },
            [
                {
                    "id": "a9a0bc2b0b2313009002cab937673ac6",
                    "name": "decision_table",
                    "label": "Decision Table",
                    "type": "reference",
                    "reference": "sys_decision",
                    "mandatory": True,
                    "maxsize": 32,
                },
            ],
        ),
        "DECISIONBLOCK": definition(
            "b1f542d673331300612c273ffff6a7c7",
            "Decision",
            "DECISIONBLOCK",
            {
                "allow_children": "true",
                "allow_siblings": "false",
                "can_delete": "false",
                "can_move": "false",
            },
            [
                {
                    "id": "0a99f06e73402300612c273ffff6a771",
                    "name": "label",
                    "label": "Label",
                    "type": "string",
                    "mandatory": False,
                    "maxsize": 255,
                },
                {
                    "id": "70ef385873002300612c273ffff6a764",
                    "name": "answer",
                    "label": "Answer",
                    "type": "document_id",
                    "mandatory": False,
                    "maxsize": 32,
                    "use_dependent": True,
                    "dependent_on": "table",
                },
            ],
            connected_to="578e90270b2313009002cab937673a7d",
        ),
        "TRY": definition(
            "07223332de022010ec58424b73829721",
            "Try",
            "TRY",
            {"allow_siblings": "true"},
        ),
        "CATCH": definition(
            "35d60003e6022010a5e40cdd1254dd21",
            "Catch",
            "CATCH",
            {"allow_siblings": "true"},
            connected_to="07223332de022010ec58424b73829721",
        ),
        "TOP_LEVEL_TRY": definition(
            "e9060aa2c3013010ace8b740ad40dd51",
            "Top Level Try",
            "TOP_LEVEL_TRY",
            {"allow_siblings": "true", "visible_in_ui": "false"},
        ),
        "TOP_LEVEL_CATCH": definition(
            "35d60003e6022010a5e40cdd1254dd23",
            "Top Level Catch",
            "TOP_LEVEL_CATCH",
            {"allow_siblings": "true", "visible_in_ui": "false"},
            connected_to="07223332de022010ec58424b73829721,e9060aa2c3013010ace8b740ad40dd51",
        ),
        "ASSIGNSUBFLOWOUTPUTS": definition(
            "df4e1945c3e232002841b63b12d3ae3e",
            "Assign Subflow Outputs",
            "ASSIGNSUBFLOWOUTPUTS",
            {"allow_children": "false", "allow_siblings": "true"},
        ),
    }
)


def logic(
    kind: str,
    uid: str,
    *,
    children: tuple[tree.Step, ...] | list[tree.Step] = (),
    inputs: dict[str, Any] | None = None,
) -> tree.Step:
    values = {
        "IF": {"condition": "active=true"},
        "ELSEIF": {"condition": "active=false"},
        "DOUNTIL": {"condition": "active=false"},
        "FOREACH": {"items": "{{ui-1.records}}"},
        "DECISION": {"decision_table": "d" * 32},
    }.get(kind, {})
    step = tree.new_logic_step(
        FLOW, copy.deepcopy(NATIVE[kind]), values | (inputs or {})
    )
    step.raw["uiUniqueIdentifier"] = uid
    step.children = list(children)
    return step


def leaf(uid: str, number: int) -> tree.Step:
    return tree.Step(action(f"{number:032x}", uid, number, uid), "action")


def go_back(uid: str, target: str) -> tree.Step:
    return logic("GOBACKTO", uid, inputs={"go_back_to_step": target})


def target_binding(step: tree.Step) -> dict[str, Any]:
    return next(row for row in step.raw["inputs"] if row["name"] == "go_back_to_step")


def topology(roots: list[tree.Step]) -> list[tuple[str, Any]]:
    return [(step.uid, topology(step.children)) for step in roots]


def native_flow(rows: list[tuple[tree.Step, str, int | str]]) -> dict[str, Any]:
    """Construct saved rows from explicit wire parent/order facts, without the projector."""
    flow = base_flow()
    for collection in ("actionInstances", "flowLogicInstances", "subFlowInstances"):
        flow[collection] = []
    for position, (step, parent, order) in enumerate(rows):
        raw = copy.deepcopy(step.raw)
        raw.setdefault("id", f"{100 + position:032x}")
        raw.update(parent=parent, order=order, arrayPosition=position, isNew=False)
        if step.kind == "flowlogic":
            raw["children"] = [
                child.uid for child, owner, _ in rows if owner == step.uid
            ]
        flow[tree.COLLECTIONS[step.kind]].append(raw)
    return flow


async def plan(
    settings: Any, flow: dict[str, Any], edits: list[dict[str, Any]]
) -> dict[str, Any]:
    before = copy.deepcopy(flow)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await editing.plan(
            FlowCommand(action="edit", sys_id=FLOW, edits=copy.deepcopy(edits)),
            {
                "flow": flow,
                "logic": copy.deepcopy(list(NATIVE.values())),
                "triggers": [],
            },
            client,
        )
    assert flow == before
    return result["payload"]


def saved_loopback() -> dict[str, Any]:
    target = leaf("target", 1)
    alternate = leaf("alternate", 2)
    branch = logic("IF", "branch")
    source = go_back("source", target.uid)
    later = leaf("later", 3)
    return native_flow(
        [
            (target, "", 1),
            (alternate, "", 2),
            (branch, "", 3),
            (source, branch.uid, 4),
            (later, "", 5),
        ]
    )


def test_dountil_projects_one_native_sandwich_and_reconstructs_children():
    before, first, second, after = [
        leaf(name, n)
        for n, name in enumerate(("before", "first", "second", "after"), start=1)
    ]
    until = logic(
        "DOUNTIL",
        "until",
        children=[first, second],
        inputs={"condition": "active=false", "condition_name": "Finished"},
    )
    payload = tree.flatten(base_flow(), [before, until, after])
    assert len(payload["flowLogicInstances"]) == 1
    row = payload["flowLogicInstances"][0]
    assert (row["order"], row["orderBottom"], row["parent"]) == (2, 5, "")
    assert row["children"] == ["first", "second"]
    assert row["data"] == {"condition_name": "Finished", "condition": "active=false"}
    assert {
        item["uiUniqueIdentifier"]: (
            item["order"],
            item["parent"],
            item["arrayPosition"],
        )
        for item in payload["actionInstances"]
    } == {
        "before": (1, "", 0),
        "first": (3, "until", 2),
        "second": (4, "until", 3),
        "after": (6, "", 4),
    }
    reconstructed = tree.build_tree(copy.deepcopy(payload))
    assert topology(reconstructed) == [
        ("before", []),
        ("until", [("first", []), ("second", [])]),
        ("after", []),
    ]
    assert reconstructed[1].raw["orderBottom"] == 5
    tree.validate_structure(reconstructed)


@pytest.mark.parametrize("with_body", [False, True])
def test_final_dountil_has_a_closing_label_without_a_synthetic_step(with_body):
    until = logic("DOUNTIL", "until", children=[leaf("body", 1)] if with_body else [])
    payload = tree.flatten(base_flow(), [until])
    assert len(payload["flowLogicInstances"]) == 1
    assert payload["flowLogicInstances"][0]["order"] == 1
    assert payload["flowLogicInstances"][0]["orderBottom"] == (3 if with_body else 2)
    assert len(list(tree.walk(tree.build_tree(payload)))) == (2 if with_body else 1)


@pytest.mark.parametrize("inputs", [{}, {"condition": ""}, {"condition": None}])
def test_dountil_requires_its_native_condition(inputs):
    with pytest.raises(CapabilityError):
        tree.new_logic_step(FLOW, copy.deepcopy(NATIVE["DOUNTIL"]), inputs)


def test_dountil_condition_label_is_optional():
    until = tree.new_logic_step(
        FLOW, copy.deepcopy(NATIVE["DOUNTIL"]), {"condition": "active=false"}
    )
    assert until.raw["data"] == {"condition_name": "", "condition": "active=false"}


@pytest.mark.parametrize("intervening_branch", [False, True])
def test_dountil_rejects_same_family_ancestors_even_through_another_block(
    intervening_branch,
):
    inner = logic("DOUNTIL", "inner")
    child = logic("IF", "branch", children=[inner]) if intervening_branch else inner
    outer = logic("DOUNTIL", "outer", children=[child])
    with pytest.raises(CapabilityError):
        tree.validate_structure([outer])


@pytest.mark.parametrize(
    "outer_kind,inner_kind",
    [("IF", "DOUNTIL"), ("FOREACH", "DOUNTIL"), ("DOUNTIL", "IF")],
)
def test_dountil_allows_unrelated_block_nesting(outer_kind, inner_kind):
    outer = logic(outer_kind, "outer", children=[logic(inner_kind, "inner")])
    tree.validate_structure([outer])


def test_targets_follow_ancestor_constructs_not_flat_display_order():
    source = go_back("source", "loop_before")
    branch = logic("IF", "branch", children=[leaf("inside_branch", 1), source])
    until = logic(
        "DOUNTIL",
        "until",
        children=[leaf("loop_before", 2), branch, leaf("loop_after", 3)],
    )
    cousin = logic("IF", "cousin", children=[leaf("earlier_cousin_child", 4)])
    subflow = tree.Step(copy.deepcopy(base_flow()["subFlowInstances"][0]), "subflow")
    subflow.raw["uiUniqueIdentifier"] = "earlier_subflow"
    roots = [leaf("earlier", 5), subflow, cousin, until, leaf("future", 6)]
    assert loopbacks.targets(roots, source) == {
        "branch",
        "loop_before",
        "until",
        "earlier",
        "earlier_subflow",
        "cousin",
    }
    tree.validate_structure(roots)


@pytest.mark.parametrize("kind", ["ELSEIF", "ELSE", "CATCH"])
@pytest.mark.parametrize("native_link", [False, True])
def test_connected_branch_targets_use_the_construct_start(kind, native_link):
    start = logic("TRY" if kind == "CATCH" else "IF", "start")
    source = go_back("source", start.uid)
    branch = logic(kind, "connected", children=[source])
    if native_link or kind == "CATCH":
        branch.raw["connectedTo"] = start.uid
    roots = [leaf("earlier", 1), start, branch, leaf("future", 2)]
    assert loopbacks.targets(roots, source) == {"earlier", "start"}
    tree.validate_structure(roots)


def test_else_after_elseif_does_not_offer_either_connected_branch_as_a_target():
    start = logic("IF", "start")
    elseif = logic("ELSEIF", "elseif", children=[leaf("cousin_child", 1)])
    source = go_back("source", start.uid)
    otherwise = logic("ELSE", "otherwise", children=[source])
    roots = [start, elseif, otherwise]
    assert loopbacks.targets(roots, source) == {"start"}
    tree.validate_structure(roots)


def test_decision_branches_are_native_siblings_and_target_their_decision():
    source = go_back("source", "decision")
    first = logic("DECISIONBLOCK", "first", children=[leaf("cousin_child", 1)])
    second = logic("DECISIONBLOCK", "second", children=[source])
    decision = logic("DECISION", "decision", children=[first, second])
    roots = [leaf("earlier", 2), decision, leaf("future", 3)]
    tree.validate_structure(roots)
    assert loopbacks.targets(roots, source) == {"earlier", "decision"}
    payload = tree.flatten(base_flow(), roots)
    assert topology(tree.build_tree(payload)) == [
        ("earlier", []),
        ("decision", [("first", [("cousin_child", [])]), ("second", [("source", [])])]),
        ("future", []),
    ]


@pytest.mark.parametrize("parent_kind", [None, "IF", "DECISIONBLOCK"])
def test_decision_branch_requires_a_direct_decision_parent(parent_kind):
    branch = logic("DECISIONBLOCK", "branch")
    roots = (
        [branch]
        if parent_kind is None
        else [logic(parent_kind, "outer", children=[branch])]
    )
    with pytest.raises(CapabilityError):
        tree.validate_structure(roots)


@pytest.mark.parametrize("child_kind", ["IF", "GOBACKTO"])
def test_decision_rejects_nonbranch_direct_children(child_kind):
    child = (
        go_back("source", "decision")
        if child_kind == "GOBACKTO"
        else logic("IF", "child")
    )
    with pytest.raises(CapabilityError):
        tree.validate_structure([logic("DECISION", "decision", children=[child])])


def test_decision_branch_cannot_move_independently():
    first, second = [logic("DECISIONBLOCK", uid) for uid in ("first", "second")]
    roots = [logic("DECISION", "decision", children=[first, second])]
    before = topology(roots)
    with pytest.raises(CapabilityError):
        tree.move(roots, first.uid, parent=None, after=second.uid, before=None)
    assert topology(roots) == before


def test_parallel_boundary_excludes_itself_outer_targets_and_other_branches():
    source = go_back("source", "local_before")
    branch = logic("IF", "branch", children=[source])
    left = logic("PARALLELBLOCK", "left", children=[leaf("local_before", 1), branch])
    right = logic("PARALLELBLOCK", "right", children=[leaf("cousin", 2)])
    parallel = logic("PARALLEL", "parallel", children=[left, right])
    until = logic("DOUNTIL", "until", children=[parallel])
    roots = [leaf("outer_before", 3), until]
    assert loopbacks.targets(roots, source) == {"branch", "local_before"}
    tree.validate_structure(roots)
    target_binding(source)["value"] = until.uid
    with pytest.raises(CapabilityError):
        loopbacks.validate(roots)


def test_hidden_main_wrapper_is_not_a_loopback_target():
    source = go_back("source", "earlier")
    branch = logic("IF", "branch", children=[source])
    main = logic("TOP_LEVEL_TRY", "main", children=[leaf("earlier", 1), branch])
    handler = logic("TOP_LEVEL_CATCH", "handler")
    handler.raw["connectedTo"] = main.uid
    roots = [main, handler]
    assert loopbacks.targets(roots, source) == {"earlier", "branch"}
    tree.validate_structure(roots)
    target_binding(source)["value"] = main.uid
    with pytest.raises(CapabilityError):
        loopbacks.validate(roots)


def test_loopback_is_forbidden_even_in_a_branch_nested_inside_the_hidden_handler():
    source = go_back("source", "handler_branch")
    main = logic("TOP_LEVEL_TRY", "main")
    branch = logic("IF", "handler_branch", children=[source])
    handler = logic("TOP_LEVEL_CATCH", "handler", children=[branch])
    handler.raw["connectedTo"] = main.uid
    with pytest.raises(CapabilityError):
        tree.validate_structure([main, handler])


@pytest.mark.parametrize("parent_kind", [None, "DOUNTIL", "FOREACH", "PARALLELBLOCK"])
def test_loopback_requires_a_direct_native_branch_parent(parent_kind):
    source = go_back("source", "earlier")
    if parent_kind is None:
        roots = [leaf("earlier", 1), source]
    elif parent_kind == "PARALLELBLOCK":
        block = logic(parent_kind, "block", children=[source])
        roots = [leaf("earlier", 1), logic("PARALLEL", "parallel", children=[block])]
    else:
        loop = logic(parent_kind, "loop", children=[source])
        roots = [leaf("earlier", 1), logic("IF", "branch", children=[loop])]
    with pytest.raises(CapabilityError):
        tree.validate_structure(roots)


def test_loopback_must_be_terminal_and_cannot_have_children():
    source = go_back("source", "branch")
    branch = logic("IF", "branch", children=[source, leaf("following", 1)])
    with pytest.raises(CapabilityError):
        tree.validate_structure([branch])
    branch.children.pop()
    source.children = [leaf("nested", 2)]
    with pytest.raises(CapabilityError):
        tree.validate_structure([branch])


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        1,
        True,
        {},
        [],
        "missing",
        "1",
        "00000000000000000000000000000001",
        "{{target.value}}",
        "source",
        "later",
    ],
)
def test_saved_loopback_target_must_be_one_eligible_ui_identity(value):
    roots = tree.build_tree(saved_loopback())
    source = next(step for step in tree.walk(roots) if step.uid == "source")
    target_binding(source)["value"] = value
    with pytest.raises(CapabilityError):
        loopbacks.validate(roots)


@pytest.mark.parametrize("defect", ["absent", "duplicate", "script"])
def test_saved_loopback_rejects_ambiguous_or_scripted_target_bindings(defect):
    roots = tree.build_tree(saved_loopback())
    source = next(step for step in tree.walk(roots) if step.uid == "source")
    if defect == "absent":
        source.raw["inputs"] = []
    elif defect == "duplicate":
        source.raw["inputs"].append(copy.deepcopy(target_binding(source)))
    else:
        target_binding(source)["scriptActive"] = True
    with pytest.raises(CapabilityError):
        loopbacks.validate(roots)


@pytest.mark.parametrize("change", ["delete", "move_after_branch", "move_into_cousin"])
def test_target_drift_is_rejected_without_clearing_the_selected_uid(change):
    roots = tree.build_tree(saved_loopback())
    source = next(step for step in tree.walk(roots) if step.uid == "source")
    if change == "delete":
        tree.detach(roots, "target")
    elif change == "move_after_branch":
        tree.move(roots, "target", parent=None, after="branch", before=None)
    else:
        cousin = logic("IF", "cousin")
        roots.append(cousin)
        tree.move(roots, "target", parent=cousin.uid, after=None, before=None)
    with pytest.raises(CapabilityError):
        tree.flatten(base_flow(), roots)
    assert target_binding(source)["value"] == "target"


def test_renumber_preserves_target_uid_and_refreshes_native_source_locations():
    flow = saved_loopback()
    flow["actionInstances"][0]["goBackToSources"] = [
        {"uiUniqueIdentifier": "old_source", "arrayPosition": 99, "order": 100}
    ]
    flow["actionInstances"][1]["goBackToSources"] = [
        {"uiUniqueIdentifier": "source", "arrayPosition": 3, "order": 4}
    ]
    roots = tree.build_tree(flow)
    tree.move(roots, "later", parent=None, after=None, before="target")
    payload = tree.flatten(flow, roots)
    target = next(
        row
        for row in payload["actionInstances"]
        if row["uiUniqueIdentifier"] == "target"
    )
    alternate = next(
        row
        for row in payload["actionInstances"]
        if row["uiUniqueIdentifier"] == "alternate"
    )
    source = next(
        row
        for row in payload["flowLogicInstances"]
        if row["uiUniqueIdentifier"] == "source"
    )
    assert target["order"] == 2
    assert target["goBackToSources"] == [
        {"uiUniqueIdentifier": "source", "arrayPosition": 4, "order": 5}
    ]
    assert "goBackToSources" not in alternate
    assert (
        next(
            row["value"] for row in source["inputs"] if row["name"] == "go_back_to_step"
        )
        == "target"
    )
    reconstructed = tree.build_tree(copy.deepcopy(payload))
    assert topology(reconstructed) == [
        ("later", []),
        ("target", []),
        ("alternate", []),
        ("branch", [("source", [])]),
    ]
    tree.validate_structure(reconstructed)


def test_native_target_cache_distinguishes_multiple_sources_after_branch_reordering():
    first = go_back("first_source", "target")
    second = go_back("second_source", "target")
    left = logic("IF", "left", children=[first])
    right = logic("IF", "right", children=[second])
    roots = [leaf("target", 1), left, right]
    tree.move(roots, right.uid, parent=None, after=None, before=left.uid)
    payload = tree.flatten(base_flow(), roots)
    assert payload["actionInstances"][0]["goBackToSources"] == [
        {"uiUniqueIdentifier": "second_source", "arrayPosition": 2, "order": 3},
        {"uiUniqueIdentifier": "first_source", "arrayPosition": 4, "order": 5},
    ]
    rebuilt = tree.build_tree(payload)
    assert all(
        target_binding(step)["value"] == "target"
        for step in tree.walk(rebuilt)
        if step.logic_type == "GOBACKTO"
    )
    tree.validate_structure(rebuilt)


@pytest.mark.parametrize("reference", ["branch_alias", "1" * 32, "ui-1", "1"])
async def test_planner_insert_pins_alias_database_ui_and_display_targets_before_renumber(
    settings, reference
):
    payload = await plan(
        settings,
        base_flow(),
        [
            {
                "op": "insert_logic",
                "logic": "IF",
                "after": "1" * 32,
                "ref": "branch_alias",
                "inputs": {"condition": "active=true"},
            },
            {
                "op": "insert_logic",
                "logic": "GOBACKTO",
                "parent": "branch_alias",
                "ref": "back",
                "inputs": {"go_back_to_step": reference},
            },
            {"op": "move_step", "step": "2" * 32, "before": "1" * 32},
        ],
    )
    rebuilt = tree.build_tree(payload)
    branch = next(step for step in tree.walk(rebuilt) if step.logic_type == "IF")
    source = next(step for step in tree.walk(rebuilt) if step.logic_type == "GOBACKTO")
    expected = branch.uid if reference == "branch_alias" else "ui-1"
    assert target_binding(source)["value"] == expected
    assert source.raw["parent"] == branch.uid
    assert source.raw["order"] == 4
    assert source.raw["goBackTo"] is True
    binding = target_binding(source)
    assert "id" not in binding
    assert binding["parameter"]["id"] == NATIVE["GOBACKTO"]["inputs"][0]["id"]
    assert loopbacks.targets(rebuilt, source) == {"ui-2", "ui-1", branch.uid}
    tree.validate_structure(rebuilt)


@pytest.mark.parametrize(
    "reference", ["new_target", "00000000000000000000000000000002", "alternate", "2"]
)
async def test_planner_update_pins_the_target_identity_before_later_edits(
    settings, reference
):
    flow = saved_loopback()
    edits = []
    if reference == "new_target":
        edits.append(
            {
                "op": "insert_logic",
                "logic": "IF",
                "before": "target",
                "ref": "new_target",
                "inputs": {"condition": "active=true"},
            }
        )
    edits.extend(
        [
            {
                "op": "update_inputs",
                "step": "source",
                "inputs": {"go_back_to_step": reference},
            },
            {"op": "move_step", "step": "later", "before": "target"},
        ]
    )
    payload = await plan(settings, flow, edits)
    rebuilt = tree.build_tree(payload)
    source = next(step for step in tree.walk(rebuilt) if step.uid == "source")
    expected = (
        next(
            step.uid
            for step in rebuilt
            if step.logic_type == "IF" and step.uid != "branch"
        )
        if reference == "new_target"
        else "alternate"
    )
    assert target_binding(source)["value"] == expected
    assert source.raw["data"]["go_back_to_step"] == expected
    tree.validate_structure(rebuilt)


@pytest.mark.parametrize("operation", ["move_step", "delete_step"])
async def test_planner_rejects_edits_that_invalidate_an_existing_target(
    settings, operation
):
    flow = saved_loopback()
    before = copy.deepcopy(flow)
    edit = {"op": operation, "step": "target"}
    if operation == "move_step":
        edit["after"] = "branch"
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [edit])
    assert flow == before


async def test_planner_can_explicitly_retarget_before_deleting_the_old_target(settings):
    payload = await plan(
        settings,
        saved_loopback(),
        [
            {
                "op": "update_inputs",
                "step": "source",
                "inputs": {"go_back_to_step": "alternate"},
            },
            {"op": "delete_step", "step": "target"},
        ],
    )
    rebuilt = tree.build_tree(payload)
    assert "target" not in {step.uid for step in tree.walk(rebuilt)}
    source = next(step for step in tree.walk(rebuilt) if step.uid == "source")
    assert target_binding(source)["value"] == "alternate"
    tree.validate_structure(rebuilt)


@pytest.mark.parametrize("operation", ["insert_logic", "update_inputs"])
async def test_planner_rejects_a_resolved_but_ineligible_future_target(
    settings, operation
):
    flow = saved_loopback()
    if operation == "insert_logic":
        flow["flowLogicInstances"] = [
            row
            for row in flow["flowLogicInstances"]
            if row["uiUniqueIdentifier"] != "source"
        ]
        flow["flowLogicInstances"][0]["children"] = []
        edit = {
            "op": operation,
            "logic": "GOBACKTO",
            "parent": "branch",
            "inputs": {"go_back_to_step": "later"},
        }
    else:
        edit = {
            "op": operation,
            "step": "source",
            "inputs": {"go_back_to_step": "later"},
        }
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [edit])


async def test_planner_generic_delete_cannot_remove_a_managed_decision_branch(settings):
    decision = logic("DECISION", "decision")
    branch = logic("DECISIONBLOCK", "branch")
    flow = native_flow([(decision, "", 1), (branch, decision.uid, 2)])
    before = copy.deepcopy(flow)
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [{"op": "delete_step", "step": "branch"}])
    assert flow == before


@pytest.mark.parametrize("exportable", [None, True, "true", False, "false"])
def test_until_closing_condition_can_read_only_exportable_direct_body_outputs(
    exportable,
):
    producer = leaf("producer", 1)
    producer.raw["actionType"]["outputs"] = [{"name": "result", "type": "string"}]
    if exportable is not None:
        producer.raw["actionType"]["attributes"] = {
            "pills_draggable_outside_block": exportable
        }
    until = logic("DOUNTIL", "until", children=[producer])
    scope = data.Scope([until])
    if exportable in (None, True, "true"):
        scope.visible(producer, until, ["result"])
    else:
        with pytest.raises(CapabilityError):
            scope.visible(producer, until, ["result"])


@pytest.mark.parametrize("region", ["IF", "FOREACH", "PARALLEL"])
def test_until_closing_condition_cannot_read_a_nested_path_output(region):
    producer = leaf("producer", 1)
    producer.raw["actionType"]["outputs"] = [{"name": "result", "type": "string"}]
    if region == "PARALLEL":
        child = logic(
            region,
            "parallel",
            children=[logic("PARALLELBLOCK", "branch", children=[producer])],
        )
    else:
        child = logic(region, "nested", children=[producer])
    until = logic("DOUNTIL", "until", children=[child])
    tree.validate_structure([until])
    with pytest.raises(CapabilityError):
        data.Scope([until]).visible(producer, until, ["result"])


def test_until_closing_condition_cannot_read_an_inner_foreach_item():
    each = logic("FOREACH", "each", children=[leaf("body", 1)])
    until = logic("DOUNTIL", "until", children=[each])
    with pytest.raises(CapabilityError):
        data.Scope([until]).visible(each, until, ["item"])


def test_decision_branch_outputs_are_private_to_their_own_descendants():
    own_consumer = leaf("own_consumer", 1)
    sibling_consumer = leaf("sibling_consumer", 2)
    first = logic("DECISIONBLOCK", "first", children=[own_consumer])
    second = logic("DECISIONBLOCK", "second", children=[sibling_consumer])
    decision = logic("DECISION", "decision", children=[first, second])
    after = leaf("after", 3)
    scope = data.Scope([decision, after])
    scope.visible(first, own_consumer, ["result"])
    for consumer in (sibling_consumer, after):
        with pytest.raises(CapabilityError):
            scope.visible(first, consumer, ["result"])


@pytest.mark.parametrize("value", ["complete", "{{producer.result}}"])
async def test_nested_until_preserves_literal_and_typed_body_output_assignments(
    settings, value
):
    branch = logic("IF", "branch")
    until = logic(
        "DOUNTIL", "until", inputs={"condition": "{{producer.result}}=complete"}
    )
    until.raw["orderBottom"] = 4
    producer = leaf("producer", 1)
    producer.raw["actionType"]["outputs"] = [
        {"name": "result", "label": "Result", "type": "string", "children": []}
    ]
    flow = native_flow(
        [(branch, "", 1), (until, branch.uid, 2), (producer, until.uid, 3)]
    )
    flow["type"] = "subflow"
    flow["outputs"] = [
        {"id": "b" * 32, "name": "result", "label": "Result", "type": "string"}
    ]
    payload = await plan(
        settings,
        flow,
        [{"op": "assign_outputs", "parent": "until", "outputs": {"result": value}}],
    )
    assigned = next(
        row
        for row in payload["flowLogicInstances"]
        if row["flowLogicDefinition"]["type"] == "ASSIGNSUBFLOWOUTPUTS"
    )
    assert assigned["parent"] == "until"
    assert assigned["order"] == 4
    assert assigned["data"] == {"result": value}
    assert assigned["outputsToAssign"] == [
        {
            **flow["outputs"][0],
            "value": value,
            "displayValue": value,
            "scriptActive": False,
        }
    ]
    rebuilt = tree.build_tree(copy.deepcopy(payload))
    assert topology(rebuilt) == [
        (
            "branch",
            [("until", [("producer", []), (assigned["uiUniqueIdentifier"], [])])],
        )
    ]
    rebuilt_until = rebuilt[0].children[0]
    assert rebuilt_until.raw["orderBottom"] == 5
    assert (
        rebuilt_until.children[1].raw["outputsToAssign"] == assigned["outputsToAssign"]
    )
    tree.validate_structure(rebuilt)


@pytest.mark.parametrize("corrupt_cache", [None, "orderBottom", "arrayPosition"])
async def test_native_loop_getter_can_omit_projected_caches_but_not_contradict_them(
    settings, corrupt_cache
):
    expected = await plan(
        settings,
        base_flow(),
        [
            {
                "op": "insert_logic",
                "logic": "DOUNTIL",
                "after": "ui-1",
                "ref": "until",
                "inputs": {"condition": "active=false"},
            },
            {"op": "move_step", "step": "ui-2", "parent": "until"},
            {
                "op": "insert_logic",
                "logic": "IF",
                "after": "until",
                "ref": "branch",
                "inputs": {"condition": "active=true"},
            },
            {
                "op": "insert_logic",
                "logic": "GOBACKTO",
                "parent": "branch",
                "inputs": {"go_back_to_step": "until"},
            },
        ],
    )
    until = next(
        row
        for row in expected["flowLogicInstances"]
        if row["flowLogicDefinition"]["type"] == "DOUNTIL"
    )
    assert until["goBackTo"] is False
    assert (until["order"], until["orderBottom"], until["arrayPosition"]) == (2, 4, 1)
    assert {row["name"]: row["parameter"]["id"] for row in until["inputs"]} == {
        row["name"]: row["id"] for row in NATIVE["DOUNTIL"]["inputs"]
    }
    assert all("id" not in row for row in until["inputs"])
    actual = copy.deepcopy(expected)
    for index, row in enumerate(actual["flowLogicInstances"], start=300):
        row["id"] = f"{index:032x}"
    for collection in tree.COLLECTIONS.values():
        for row in actual[collection]:
            row.pop("orderBottom", None)
            row.pop("arrayPosition", None)
    if corrupt_cache is not None:
        saved_until = next(
            row
            for row in actual["flowLogicInstances"]
            if row["uiUniqueIdentifier"] == until["uiUniqueIdentifier"]
        )
        saved_until[corrupt_cache] = 99
        with pytest.raises(CapabilityError):
            semantics.compare_saved(expected, actual, generated_ids={""})
    else:
        before = copy.deepcopy(actual)
        semantics.compare_saved(expected, actual, generated_ids={""})
        assert actual == before
        rebuilt = tree.build_tree(actual)
        rebuilt_until = next(step for step in rebuilt if step.logic_type == "DOUNTIL")
        assert topology(rebuilt_until.children) == [("ui-2", [])]
        source = next(
            step for step in tree.walk(rebuilt) if step.logic_type == "GOBACKTO"
        )
        assert target_binding(source)["value"] == until["uiUniqueIdentifier"]
        tree.validate_structure(rebuilt)
