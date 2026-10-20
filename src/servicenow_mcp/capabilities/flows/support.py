"""Versioned authoring support, distinct from catalogs and live verification."""

from typing import Any

ADAPTER = "studio-27-v2"
LOGIC_TYPES = {
    "IF",
    "ELSEIF",
    "ELSE",
    "FOREACH",
    "END",
    "TRY",
    "PARALLEL",
    "PARALLELBLOCK",
    "BREAK",
    "CONTINUE",
}


def ledger() -> dict[str, Any]:
    def entry(authorable: bool, reason: str) -> dict[str, Any]:
        return {
            "discovered": "instance_catalog_required",
            "inspectable": "bounded_readback_only",
            "authorable": authorable,
            "verification": "offline_regression" if authorable else "unavailable",
            "live_verified": False,
            "reason": reason,
        }

    return {
        "adapter": ADAPTER,
        "contract_capture": {
            "state": "blocked_bootstrap",
            "method": "unsaved Studio UI with default-deny request interception",
            "save_body_captures": 0,
            "effect_requests_forwarded": 0,
            "blocked_requests": [
                "POST /api/now/v1/batch",
                "POST /api/now/uxframework/component_drivers",
                "POST /amb/handshake",
                "POST /web/config",
            ],
            "guard": (
                "Only allowlisted same-origin GET navigation/catalog/static reads; redirects, mutation methods, "
                "effectful GETs, and WebSockets blocked."
            ),
            "worker_limit": (
                "A root service worker reappeared despite page bypass and registration cleanup. Capture stopped; "
                "no authoring interactions followed."
            ),
            "resolution": (
                "Use an authenticated Studio bootstrap that works with GET-only reads and no active service "
                "worker, or supply sanitized version-matched request bodies. Do not relax the effect block or "
                "infer effect responses."
            ),
        },
        "lifecycle": {
            "validate_local": {
                "available": True,
                "effect": "read_only_local_checks",
                "verification": "offline_regression",
                "limitation": (
                    "Supported inactive V2 drafts only. Unknown schemas/families return valid=null, "
                    "complete=false. No remote compile."
                ),
            },
            "status": {
                "available": True,
                "effect": "bounded_record_reads",
                "verification": "offline_regression",
                "limitation": (
                    "Exact context ID only. No execution inputs/outputs. Context-to-version correlation remains "
                    "unverified."
                ),
            },
            "capture_context": {
                "available": "optional_fail_closed_binding",
                "verification": "offline_regression",
                "limitation": (
                    "Installed concourse current schema; OAuth compatibility is not live-verified. Flow-header "
                    "Customer Update metadata only, not payload/children."
                ),
            },
            "validate_remote": {
                "available": False,
                "effect": "potential_compile_or_persistence",
                "reason": "No separate remote compile/validation request-response contract established.",
            },
            "publish": {
                "available": False,
                "effect": "publication",
                "reason": (
                    "Installed publishFlowToServer uses POST flow/{id}/snapshot for flows and /subflow for "
                    "subflows. No separate flow publish-only contract established; activation must not be aliased."
                ),
            },
            "activate": {
                "available": False,
                "effect": "activation",
                "reason": (
                    "Snapshot POST request is observed, but complete response, dependency/version preconditions, "
                    "and OAuth effect reconciliation are unverified."
                ),
            },
            "deactivate": {
                "available": False,
                "effect": "deactivation",
                "reason": (
                    "Installed deactivateFlowCall uses effectful GET flow/{id}/deactivate. Response/OAuth/context "
                    "contract is unverified. Never call as a read."
                ),
            },
            "test": {
                "available": False,
                "effect": "execution",
                "reason": (
                    "sendTestToServer posts flow, outputMap, runOnThread to flow/{id}/test. Input mapping, "
                    "returned context, async states, and OAuth contract are incomplete."
                ),
            },
            "run": {
                "available": False,
                "effect": "execution",
                "reason": "No independent run endpoint/input/version contract established. Test is not a run alias.",
            },
        },
        "logic": {
            kind: entry(True, "Typed input, placement, and reference validation.")
            for kind in sorted(LOGIC_TYPES)
        },
        "features": {
            "step_output_pills": entry(
                True, "Stable UI producer ID and readable output schema required."
            ),
            "variable_pills": entry(
                True,
                "Uses declared flow_variable fields. Values are mutable and may be null.",
            ),
            "interface_pills": entry(
                True,
                "Declared subflow input pills only. Trigger-owned inputs use trigger pills. Outputs are not producers.",
            ),
            "complex_inputs": entry(
                True,
                "Object and Array children require a complete declared schema. Whole complex pills use native "
                "value bindings without child values. Raw script/parameter replacement remains unavailable.",
            ),
            "whole_complex_pills": entry(
                True,
                "Whole Object and Array.String/Integer/Boolean/Object pills in action/subflow inputs and output assignments. "
                "Same recursive field names/types, compatible reference tables, one array item schema, and producer visibility required. "
                "Action/subflow producer snapshots are read independently and bound to preview. "
                "Declared subflow inputs also produce whole pills. No schema replacement, dynamic inference, "
                "output-to-output pills, or arbitrary array indexing. Source-derived offline regression only.",
            ),
            "try_catch_pair_moves": entry(
                False,
                "No guarded UI save-body capture. Need pair movement payloads with connectedTo, children, parent, "
                "order, and ancestry before and after.",
            ),
            "delete_saved_action_logic": entry(
                True,
                "Observed deletedActions/deletedFlowLogicInstances ID tombstones. Explicit child/connected cascade "
                "choice required.",
            ),
            "delete_saved_subflow": entry(
                True,
                "Studio removeActionFromFlow removes the call UI identity; the save splitter rebuilds "
                "subFlowInstances without a tombstone. Independent read checks both removed identities. "
                "Published definitions stay unchanged. Connection override cleanup remains unavailable.",
            ),
            "field_metadata": entry(
                True,
                "Scalar declaration settings preserve identity and opaque metadata. Type/reference changes require "
                "no surviving consumers. Existing flow-owned interfaces retain label/hint-only support.",
            ),
            "set_variables": entry(
                True, "Observed Set Flow Variables inputs plus flowVariables mirror."
            ),
            "output_assignment": entry(
                True,
                "Native Assign Subflow Outputs steps with ordered scalar, Object, and Array rows. Create or update with assign_outputs, "
                "remove a row with clear_output_assignment. Only unpublished triggerless subflows. "
                "Literal/typed pills, independent readback, and explicit declaration deletion guards. "
                "Nested literals and scalar leaf pills use declared children. Array.String/Integer/Boolean/Object values are supported. "
                "Whole pills require proven compatible schemas. Loop/parallel placement, scripts, and output-to-output pills remain unavailable. "
                "Source-derived offline regression only.",
            ),
            "create_delete_interface_variable": entry(
                True,
                "Top-level string/integer/boolean/reference/JSON flow variables and subflow input/output declarations. "
                "Client UI IDs, complete-list omission, assigned database IDs, and dependency rejection. "
                "Interface rename and trigger-owned interfaces remain unavailable. "
                "Source-derived offline regression only.",
            ),
            "nested_interface_declarations": entry(
                True,
                "Subflow input/output Object and Array.String/Integer/Boolean/Object declaration CRUD. "
                "Ordered child registration, attributes/fieldFacetMap array item packing, fixed item identities, "
                "typed input leaf pills, and recursive deletion guards. Complex flow variables are excluded by "
                "Studio's type picker. Direct nested Reference/JSON creation, Array.Reference/Array.Array/Choice, "
                "complex defaults and type replacement remain unavailable. "
                "Source-derived offline regression only.",
            ),
            "stage_metadata": entry(
                True,
                "Standard stages: label, value, duration, state labels, always-show, and order. "
                "Legacy metadata-only stages retain label/value support.",
            ),
            "create_delete_stage": entry(
                True,
                "Native keyed standard stages with client stageId and optional assigned id. "
                "Explicit attach/detach uses stable step identities. Attached or referenced stages cannot be deleted. "
                "Conditional error stages attach during create_stage(type=error); detach removes their keyed state. "
                "Explicit error-stage deletion, duration/state/display updates, and stable ancestry reindexing. Source-derived offline regression only.",
            ),
            "handler_toggle": entry(
                True,
                "set_error_handler(enabled=boolean) uses the native Catch enabled input. Populated disabled handlers retain read-only contents. "
                "Disabling an empty handler unwraps it. No flow activation or execution. Source-derived offline regression only.",
            ),
            "handler_creation": entry(
                True,
                "Current catalog TOP_LEVEL_TRY/CATCH wrappers, order zero, parent regions, and boundary index. "
                "main/error_handler placement aliases and catalog-defined Error Status leaf pills. remove_error_handler requires an empty handler. "
                "Legacy wrappers and sandwich/Parallel handler wrapping remain unavailable. Source-derived offline regression only.",
            ),
            "scheduled_triggers": entry(
                True,
                "Classic daily/weekly/monthly/run_once/repeat draft triggers with native epoch time/duration and date/time fields. "
                "set_trigger, update_trigger, remove_trigger; instance timezone only. Explicit schedule values required. "
                "Saved Recurrence/business-calendar/timezone-specific triggers remain unavailable. Source-derived offline regression only.",
            ),
            "record_triggers": entry(
                True,
                "record_create/record_update/record_create_or_update draft triggers from the current catalog. "
                "Static conditions, catalog run/session/user choices, descriptor-backed table metadata, internal_link current-record pills, "
                "explicit replacement/removal with dependency rejection. System/Requested Item tables, JavaScript conditions, "
                "saved/template triggers, and nonempty parsed-condition metadata updates remain unavailable. Source-derived offline regression only.",
            ),
            "service_catalog_trigger": entry(
                True, "Observed native trigger serializer and trigger output schema."
            ),
            "other_logic": entry(
                False,
                "Family-specific dynamic inputs, end conditions, or outputs require additional adapter evidence.",
            ),
        },
        "contract_evidence": {
            "studio_client": (
                "designer-bundle.min.js version=27.0.13: constructInstanceValuesForArrays, addToDeletedInstances, "
                "removeTriggerFromFlow, prepArgsForTryCatchFlowLogicInstance, updateStageIndexes"
            ),
            "scope_docs": (
                "docs/ServiceNowDocs/markdown/build-workflows/workflow-studio/flow-logic-if.md; "
                "flow-logic-for-each.md; flow-logic-do-following-in-parallel.md; flow-logic-try.md"
            ),
            "missing_contracts": (
                "Guarded UI bootstrap blocked; no new request bodies captured. Need full serialized create/delete "
                "remaining nested interface/variable variants, saved Recurrence/timezone trigger contracts, dynamic complex schema adoption, and paired Try/Catch "
                "moves."
            ),
        },
    }
