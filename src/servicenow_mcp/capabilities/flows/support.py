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
        "completion_scope": {
            "implemented": [
                "inactive flow/subflow headers",
                "ordered published call insertion",
                "supported logic trees and paired Try/Catch moves",
                "classic triggers",
                "variables and nested subflow interfaces",
                "native output assignments and schema-bound complex pills",
                "standard/error stages",
                "top-level handlers",
                "local validation and exact execution status",
                "explicit context-bound activate/publish/deactivate/test",
            ],
            "core_contract_gaps": [
                "No independent compiler/remote-validator contract in local Studio source",
                "No independent production-run endpoint/version contract in local Studio source",
                "No flow publish-only operation: native snapshot publication activates",
            ],
            "variant_gaps": [
                "saved Recurrence/business-calendar/timezone triggers",
                "dynamic/dependent call-input insertion",
                "sandwich/legacy handler order projections",
                "complex/list test inputs",
                "additional array subtypes and dynamic schema adoption",
            ],
            "live_effect_verification": False,
            "snapshot_content_verified": False,
        },
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
                "reason": "Studio 27 has no independent compiler/validator endpoint in the local source. Save persists; snapshot publishes/activates; test executes. None is a validation alias.",
            },
            "publish": {
                "available": True,
                "verification": "source_derived_offline_only",
                "live_verified": False,
                "effect": "callable_subflow_publication",
                "reason": (
                    "Subflow-only POST flow/{id}/subflow. Flow snapshot publication also activates, so flow publish-only is rejected. "
                    "Strict context, dependency, echo/GET, header, and snapshot-ownership checks. Snapshot content is not independently compared."
                ),
            },
            "activate": {
                "available": True,
                "verification": "source_derived_offline_only",
                "live_verified": False,
                "effect": "publication_and_trigger_activation",
                "reason": (
                    "Flow-only snapshot POST with complete saved draft. Enables triggers and can cause real work. "
                    "Single-use context-bound preview and independent outcome reads. No automatic replay. No live effect verification."
                ),
            },
            "deactivate": {
                "available": True,
                "verification": "source_derived_offline_only",
                "live_verified": False,
                "effect": "deactivation",
                "reason": (
                    "Effectful GET flow/{id}/deactivate is dispatched only by mutation_apply. Never a read tool. "
                    "Active published flow-only; independent content/header checks preserve publication version. Does not cancel contexts."
                ),
            },
            "test": {
                "available": True,
                "verification": "source_derived_offline_only",
                "live_verified": False,
                "effect": "execution",
                "reason": (
                    "Explicit saved-draft test POST with flow, outputMap, runOnThread=false. Static scalar subflow/classic trigger mapping only. "
                    "Test records are revision-bound. Returns exact independently read context state, not completion. "
                    "Bounded flow.status follow-up, no replay or polling effects. Runtime and snapshot content remain unverified."
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
            "structured_call_insertion": entry(
                True,
                "insert_action/insert_subflow use independently read published snapshots, existing native input serializers, placement/pill aliases, and tree/dependency validation. Structured drafts and enabled handler branches supported. Dynamic/dependent call-input insertion remains unavailable. Source-derived offline only.",
            ),
            "subflow_creation": entry(
                True,
                "create(flow_type=subflow) uses native property POST with an inactive triggerless draft. Independent type/property/identity readback. Source-derived offline only.",
            ),
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
                True,
                "move_step on Try moves its adjacent identity-linked Catch and both subtrees. Independent Catch moves are rejected. "
                "Stage, handler, script, and pill guards remain in force. Source-derived offline regression only.",
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
                "remaining nested interface/variable variants, saved Recurrence/timezone trigger contracts, dynamic complex schema adoption, "
                "and any independent production-run/compiler endpoint. Lifecycle effects have offline adapters, not live proof."
            ),
        },
    }
