"""Versioned authoring support, distinct from catalogs and live verification."""

from typing import Any

ADAPTER = "studio-27-v2"
LOGIC_TYPES = {
    "IF",
    "ELSEIF",
    "ELSE",
    "FOREACH",
    "DOUNTIL",
    "GOBACKTO",
    "DECISION",
    "TIMER",
    "RUNWORKFLOW",
    "RUNDYNAMICFLOW",
    "GETFLOWOUTPUTS",
    "END",
    "TRY",
    "PARALLEL",
    "PARALLELBLOCK",
    "BREAK",
    "CONTINUE",
}


def ledger() -> dict[str, Any]:
    def entry(
        authorable: bool, reason: str, *, live_scope: str | None = None
    ) -> dict[str, Any]:
        return {
            "discovered": "instance_catalog_required",
            "inspectable": "bounded_readback_only",
            "authorable": authorable,
            "verification": "bounded_development_and_offline_regression"
            if live_scope
            else "offline_regression"
            if authorable
            else "unavailable",
            "live_verified": live_scope is not None,
            "live_scope": live_scope,
            "reason": reason,
        }

    return {
        "adapter": ADAPTER,
        "completion_scope": {
            "authoring_profile": "Installed readable native definitions for this adapter; catalog presence is not entitlement or universal release compatibility.",
            "implemented": [
                "inactive flow/subflow creation with complete ordered edit plans",
                "published action/subflow call insertion and native scalar/complex input widgets",
                "17 direct logic families, dedicated variable/output assignments, and generated structural companions",
                "all 15 native trigger families and eligible metadata-driven saved picker definitions",
                "scalar flow variables and native root/nested subflow declarations, including Choice, Date/Time and Array.Array",
                "typed pills, saved inline scripts, template-column modes, transforms and declared connection overrides",
                "separately reviewed schema-provider resolution with single-use evidence for a new authoring preview",
                "standard/error stages, paired Try/Catch and current/strictly identified legacy top-level handlers",
                "sandwich/Parallel order projections, Decision branches and validated Go back to targets",
                "working drafts of supported active/published flow and subflow owners with runtime storage preservation",
                "local validation and independent semantic draft readback",
            ],
            "core_contract_gaps": [],
            "variant_gaps": [],
            "boundaries": [
                "Unknown catalog profiles, missing licensed metadata and unresolved schemas fail strictly; fields and generated identities are not invented.",
                "Native topology, read-only fields, picker exclusions and source-erased nested literal arrays remain authoring constraints.",
                "Independent remote compilation/validation, production run and flow publish-only are separate unsupported effect contracts, not authoring gaps.",
                "Explicit activate/publish/deactivate/test effects retain their separate lifecycle entries and narrower evidence/input contracts.",
                "Source/fixture evidence does not certify engine instructions, every installed feature or live compatibility across releases.",
            ],
            "live_effect_verification": True,
            "live_effect_scope": "tv2dev, 2026-10-23, native release 27800/engine 2: owned future-2035 Run Once Log flow and uncalled scalar-input Log subflow only",
            "snapshot_content_verified": False,
        },
        "contract_capture": {
            "historical_browser_attempt": True,
            "current_evidence": "Local bundle, sanitized native HTTP fixtures and actual Python 3.15 stdio lifecycle matrix. See docs/flow-live-verification.md.",
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
                    "Supported inactive or native versioned V2 owners. Unknown schemas/families return valid=null, "
                    "complete=false. No remote compile."
                ),
            },
            "status": {
                "available": True,
                "effect": "bounded_record_reads",
                "verification": "offline_regression",
                "limitation": (
                    "Exact context ID only. Independently checks the exact compiled sys_hub_snapshot identity/parent. "
                    "No execution inputs/outputs, authored-version correlation, or compiled-content equality. Missing snapshot evidence remains partial."
                ),
            },
            "target_binding": {
                "available": "scope_and_revision",
                "verification": "offline_regression",
                "limitation": (
                    "Expected scope and revision refer to the target flow, not session selection. "
                    "No update-set or Customer Update reads. ServiceNow controls normal capture."
                ),
            },
            "publish": {
                "available": True,
                "verification": "bounded_development_and_offline_regression",
                "live_verified": True,
                "live_scope": "tv2dev native Log-only subflow, 2026-10-23; caller updates and other families not live-tested",
                "effect": "callable_subflow_publication",
                "reason": (
                    "Subflow-only POST flow/{id}/subflow. Flow snapshot publication also activates, so flow publish-only is rejected. "
                    "Target scope/revision, dependency, echo/GET, header, and snapshot-ownership checks. Snapshot content is not independently compared."
                ),
            },
            "activate": {
                "available": True,
                "verification": "bounded_development_and_offline_regression",
                "live_verified": True,
                "live_scope": "tv2dev native future-2035 Run Once Log flow, 2026-10-23; no automatic execution",
                "effect": "publication_and_trigger_activation",
                "reason": (
                    "Flow-only snapshot POST with complete saved draft. Enables triggers and can cause real work. "
                    "Single-use scope/revision-bound preview and independent outcome reads. No automatic replay."
                ),
            },
            "deactivate": {
                "available": True,
                "verification": "bounded_development_and_offline_regression",
                "live_verified": True,
                "live_scope": "tv2dev owned future Run Once flow, including an active working draft, 2026-10-23",
                "effect": "deactivation",
                "reason": (
                    "Effectful GET flow/{id}/deactivate is dispatched only by mutation_apply. Never a read tool. "
                    "Active flow owner, published or supported working draft; independent content/header checks preserve publication version. Does not cancel contexts."
                ),
            },
            "test": {
                "available": True,
                "verification": "bounded_development_and_offline_regression",
                "live_verified": True,
                "live_scope": "tv2dev Log-only scalar String subflow working draft; exact context reached COMPLETE, 2026-10-23",
                "effect": "execution",
                "reason": (
                    "Explicit saved-draft test POST with flow, outputMap, runOnThread=false. Static scalar and declared Object/Array.String/Integer/Boolean/Object subflow values; "
                    "classic trigger mappings remain scalar. Every visible subflow input and Object field is explicit; no selected defaults. "
                    "Native Test-modal children serialization is separate from authoring bindings. Nested references, Array.Reference tests and record lists remain unavailable. "
                    "Test records are revision-bound. Returns the exact accepted context; initial ownership/state/snapshot evidence can be pending. "
                    "Bounded status/readback verifies compiled snapshot ownership separately; authored version/content remain unverified. No replay."
                ),
            },
        },
        "logic": {
            kind: entry(
                True,
                "Direct insert_logic family with definition-backed inputs, typed pills, native placement and reference validation.",
            )
            for kind in sorted(LOGIC_TYPES)
        },
        "features": {
            "active_draft_editing": entry(
                True,
                "edit supports native release 27800/engine-2 active/published flow and subflow owners with disabled save-as-you-go and required target scope/revision. "
                "Active trigger plans or callable master references select runtime content. Complete JSON snapshot chunk storage is preserved; native versions bind complete version-owned persisted rows, including native subflow columns and referenced blocks from actual metadata, not mutable GET facades or arbitrary test snapshots. "
                "One native draft PUT, no lifecycle effects or edit locks. Unknown configuration metadata differences remain UNKNOWN_OUTCOME. "
                "Published-to-draft saves preserve activation, selected version content, references, and trigger behavior. Other variants retain offline-only evidence.",
                live_scope="tv2dev 2026-10-23: active Log flow reorder/insert/input update, inactive published flow edit, and published Log subflow insert/update",
            ),
            "complete_creation": entry(
                True,
                "create with 1-200 ordered edits uses the existing typed planner, local refs, and server-side definition discovery. "
                "Preflight precedes the property POST. Independent header/revision reads precede one complete PUT. "
                "Nontransactional partial outcomes report the known ID. No retry, compensation, activation, or execution. "
                "Alias-to-persisted-ID mapping supports later incremental edits.",
                live_scope="tv2dev 2026-10-23: native seven-step flow, future Run Once flow and String-input Log subflow complete creation",
            ),
            "structured_call_insertion": entry(
                True,
                "insert_action/insert_subflow use independently read published snapshots, existing native input serializers, placement/pill aliases, and tree/dependency validation. Structured drafts and enabled handlers are supported. Table-backed conditions, document/field inputs, internal_link outputs and native catalog-variable selections retain their independent metadata evidence. Dynamic inputs/choices/templates consume schema_tokens from a separately reviewed resolve_schema effect; create/edit preview and save never execute a provider implicitly.",
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
                "Object and Array values require a complete declared schema. Whole complex pills use native value bindings without child values. "
                "Declared children, native script modes and retained dynamic-schema evidence are supported; callers cannot replace raw parameter/schema DTOs.",
            ),
            "whole_complex_pills": entry(
                True,
                "Whole Object and Array.String/Integer/Boolean/Object/Reference/Choice/DateTime/Array pills in action/subflow inputs and output assignments. "
                "Recursive field names/types, reference tables, fixed array item schemas and producer visibility must be compatible. "
                "Action/subflow producer snapshots are read independently; declared subflow inputs also produce pills. "
                "Array.Array accepts an empty outer array, a whole typed pill or typed generic-Array item pills; literal nested arrays reject because the native serializer erases them. "
                "No output-to-output pills, arbitrary array indexing or schema inference from sample values.",
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
                "Published definitions stay unchanged. Native connection mappings are preserved or cleaned up according to their remaining call references.",
            ),
            "field_metadata": entry(
                True,
                "Native scalar/root/nested declaration settings preserve identities and unknown metadata. new_name applies only to newly created or native name-editable interface rows; saved variable names stay read-only. "
                "Type/reference changes require no surviving consumers or stored values. reset_structure explicitly authorizes an actual type change but never discards protected descendants/defaults. "
                "reference_qualifier, dependent_on, max_rows, choices, choice_source, choice_option and default_value follow native widget metadata. Existing flow-owned interfaces retain label/hint-only support.",
            ),
            "set_variables": entry(
                True,
                "Native Set Flow Variables inputs and flowVariables mirror. Declared basic values, input_scripts and input_transforms share the source-approved assignment input modes without code evaluation.",
            ),
            "output_assignment": entry(
                True,
                "Native Assign Subflow Outputs steps with ordered scalar, Object and Array rows in supported editable triggerless subflows, including eligible published working drafts. "
                "assign_outputs inserts or updates named rows; clear_output_assignment removes a row, not its declaration. "
                "Literal/typed pills, declared children, compatible whole complex pills, independent readback and declaration-deletion guards apply. "
                "Native loop/Parallel placement is allowed subject to producer visibility. The custom assignment widget exposes neither scripts nor transforms; output-to-output pills remain unavailable.",
            ),
            "create_delete_interface_variable": entry(
                True,
                "Scalar flow variables include string/integer/boolean/glide_date_time/decimal/float/reference/JSON. Subflow inputs/outputs use native scalar types and additional root widgets only when the installed field-type picker and existing serializer support them. "
                "Client UI IDs, complete-list omission, assigned database IDs and dependency rejection remain strict. Interface CRUD requires a supported editable triggerless subflow, including eligible published working drafts; trigger-owned interfaces remain protected.",
            ),
            "nested_interface_declarations": entry(
                True,
                "Subflow Object and Array.String/Integer/Boolean/Object/Reference/Choice/DateTime/Array declaration CRUD uses ordered child registration, native attributes/fieldFacetMap packing and fixed item identities. "
                "Array.Reference keeps its table on the parent; literal sys_ids prove syntax, not record existence/access. Choice supports static choices or native table/column choice_source with exact picker defaultChoices. "
                "Native Object/Array settings do not expose whole-parent defaults; scalar child and Array.Choice item defaults retain their own settings. "
                "Flow variables remain scalar; direct nested Reference/JSON creation remains excluded by the native Object child picker. Complex type changes use explicit guarded reset_structure or unused deletion/recreation.",
            ),
            "stage_metadata": entry(
                True,
                "Standard stages: label, value, duration, state labels, always-show, and order. "
                "Legacy metadata-only stages retain label/value support.",
            ),
            "create_delete_stage": entry(
                True,
                "Native keyed standard stages with client stageId and optional assigned id. "
                "Explicit attach/detach uses stable step identities. Attached or referenced stages cannot be deleted. Do Until permits native stage placement; For Each/Parallel exclusions remain. "
                "Conditional error stages attach during create_stage(type=error); detach removes their keyed state. "
                "Explicit error-stage deletion, duration/state/display updates and stable ancestry reindexing use native metadata.",
            ),
            "handler_toggle": entry(
                True,
                "set_error_handler(enabled=boolean) uses the native Catch enabled input. Populated disabled handlers retain read-only contents. "
                "Disabling an empty handler unwraps it. No flow activation or execution. Source-derived offline regression only.",
            ),
            "handler_creation": entry(
                True,
                "Current catalog TOP_LEVEL_TRY/CATCH wrappers, order zero, parent regions and boundary index; complete metadata-associated legacy/Rome TRY/CATCH roots are also supported. "
                "Native Catch enabled and optional FDACTIONSTATUS __status__ inputs retain opaque runtime status metadata. "
                "main/error_handler placement aliases and catalog-defined Error Status leaf pills are scoped to their handler. remove_error_handler requires an empty handler. "
                "Native sandwich/Parallel orders use the Studio projector and strict readback. Populated nested closing orders still require native integer orderBottom when the source cannot resolve them.",
            ),
            "scheduled_triggers": entry(
                True,
                "daily/weekly/monthly/run_once/repeat use native epoch time/duration/date fields through set_trigger/update_trigger/remove_trigger. Classic schedules use the instance timezone and explicit values. "
                "Recurrence/calendar/timezone variants use eligible saved template_id metadata, not guessed fields or timezone conversion. This configures drafts only; classic Run Once live evidence does not establish other schedule runtime behavior.",
            ),
            "record_triggers": entry(
                True,
                "record_create/record_update/record_create_or_update use catalog choices, static encoded conditions, table descriptors, dictionary evidence and internal_link current-record pills. "
                "Replacement/removal rejects surviving consumers. System/Requested Item tables, JavaScript/query directives and nonempty parsed-condition metadata updates retain native/source-profile restrictions. "
                "Saved definitions use their separate metadata-driven template_id route.",
            ),
            "service_catalog_trigger": entry(
                True,
                "Native Service Catalog trigger serializer, output schemas and backend-generated Requested Item call interface; saved definitions derive that interface from their selected flat metadata.",
            ),
            "native_logic_roles": entry(
                True,
                "LOGIC_TYPES contains 17 direct insert_logic families, not all 25 catalog representations. SETFLOWVARIABLES and ASSIGNSUBFLOWOUTPUTS use dedicated operations. "
                "CATCH, DECISIONBLOCK and top-level wrappers are generated/managed by their owning constructs; Parallel Branch requires Parallel. "
                "UPDATEFLOWTEMPLATE is preserved as system-locked/read-only; PLACEHOLDER is internal/transient, never standalone authoring.",
            ),
            "structural_logic": entry(
                True,
                "DOUNTIL uses condition/condition_name, native non-nesting and closing-body pill scope. GOBACKTO go_back_to_step pins an eligible previous sibling/ancestor construct UI identity within native Parallel/handler boundaries. "
                "DECISION uses an active readable decision_table, execution/branch flags and typed decision_inputs; answer_table and outputs are derived. "
                "Omitted branches generate authoritative answer order; an explicit answer/label/ref list must cover it completely, with Otherwise last. Removing populated generated branches requires explicit children=delete. Metadata reads do not evaluate a decision.",
            ),
            "utility_logic": entry(
                True,
                "TIMER uses declared duration_type and mode-specific duration/datetime/percentage/schedule inputs. RUNWORKFLOW uses workflow_reference, workflow_inputs, wait and table-bound current. "
                "RUNDYNAMICFLOW uses flow_template, flow, wait_for_completion and dynamic_inputs; GETFLOWOUTPUTS uses compatible context and optional published flow_template. "
                "Independently read published interfaces and native scope/type rules bind configuration, not execution readiness. Workflow wait=false exposes no output pills; Dynamic Flow wait=false retains context but not dynamic template outputs. No workflow, flow or context executes during authoring.",
            ),
            "saved_trigger_templates": entry(
                True,
                "set_trigger(template_id=...) attaches an independently read saved picker definition only when native flow version is a string other than 1. Inherited fromTemplate values are read-only. "
                "Recurrence/calendar/timezone fields use exact writable metadata, static qualified references and retained opaque values; no universal field-name schema or conversion is invented. "
                "Saved Service Catalog, REST and external interfaces follow native ownership/schema projection. Template, picker, underlying-definition and referenced-record revisions bind preview/apply/readback; templates themselves are not created or edited.",
            ),
            "application_triggers": entry(
                True,
                "email, remote_table_query, rest_async, analytics, metric and sla_task use installed native definitions, completing all 15 catalog trigger families with existing record/schedule/Service Catalog routes. "
                "REST interface_schemas maps declared roots to complete type/children schemas; nested children use name/label/type/mandatory/maxsize/children and native allowlists. Omitted roots remain; children=[] explicitly clears. "
                "Scalar inputs remain separate. Server base_path comes from a fixed metadata-only GraphQL query, path derives from display_path, and protected media_type is retained. No REST endpoint, inbound-email processor or application trigger is invoked.",
            ),
            "scripted_inputs": entry(
                True,
                "input_scripts on insert_action/insert_subflow/insert_logic/update_inputs/set_variables saves declared scriptable input code. Nonempty enables, null selects basic mode retaining inactive code/dependencies, and empty string clears. "
                "Qualified template_input.column names configure selected native template columns; encoded coalesce_fields requires upsert metadata and selected non-password columns. Whole standard-template pills are limited to compatible subflow context. "
                "Direct fd_data producer references are lexically pinned/rebound through topology edits; ambiguous/computed syntax fails closed. No JavaScript is evaluated or runtime type certified.",
            ),
            "input_transforms": entry(
                True,
                "input_transforms supplies a typed source pill and ordered transform_sys_id/composition_sys_id/parameters metadata on supported insertion/update/set_variables inputs. Null removes a transform only when its restored ordinary source is compatible. "
                "Exact readable metadata and documented per-function types bind the preview; unsupported or contradictory signatures and unresolved conditional/advanced forms reject. No transform executes during authoring.",
            ),
            "connection_overrides": entry(
                True,
                "Per-call connection_overrides and flow-global set_connection_overrides map declared source aliases to declared child aliases or null for source defaults. Native per-call lists replace rather than merge root context, including retained reset tombstones. "
                "Relationships, revisions, effective context, shared mapping cleanup and unknown saved fields remain bound. Empty public keys are forbidden here; the exact native reset marker is accepted only by separately reviewed resolve_schema. No credentials are read or integrations invoked.",
            ),
            "reviewed_dynamic_schemas": entry(
                True,
                "resolve_schema previews the exact metadata-derived provider and native dependencies/connection context. Its separately authorized apply executes that provider once with potentially real side effects, never the authored flow. "
                "It returns opaque 300-second single-use schema_token evidence for a NEW create/edit preview schema_tokens list (at most 200). Accepted commands consume all tokens; save/apply does not rerun providers or accept raw caller schemas. "
                "Provider/snapshot/master metadata, revisions, dependencies and schemas are observed before/after and rebound for authoring. Scoped-name dispatch does not atomically prove the exact executed snapshot; observed binding and exact execution-version verification remain distinct.",
            ),
            "unknown_catalog_profiles": entry(
                False,
                "Unknown plugin families, unrecognized native DTO/schema profiles and unavailable licensed metadata are not inferred from catalog labels or sample values. Generated/read-only/internal representations are not missing standalone authoring adapters.",
            ),
            "native_action_widgets": entry(
                True,
                "Native text/rich-text/numeric/date/reference/list/field-list/Name-Values/slushbucket widgets, table-backed conditions/templates, catalog variables and proven MRVS selections, full typed approval rules and explicit approval deadlines use source-defined grammars. "
                "Selected catalog output schemas/defaults and provenance are independently read and retained, not inferred from values or flattened to scalar stubs. Template column scripts/coalescing use their explicit modes. "
                "Unresolved widget/provider metadata and contradictory transform signatures reject; configuration is not runtime behavior or engine certification.",
            ),
        },
        "contract_evidence": {
            "studio_client": (
                "designer-bundle.min.js version=27.0.13: constructInstanceValuesForArrays, addToDeletedInstances, "
                "removeTriggerFromFlow, prepArgsForTryCatchFlowLogicInstance, updateStageIndexes, updateActionInstanceIndices, constructTestModalData"
            ),
            "scope_docs": (
                "docs/ServiceNowDocs/markdown/build-workflows/workflow-studio/flow-logic-if.md; "
                "flow-logic-for-each.md; flow-logic-do-following-in-parallel.md; flow-logic-try.md"
            ),
            "missing_contracts": (
                "Historical guarded UI bootstrap did not capture bodies. Current authoring contracts use version-matched local source, sanitized DTOs and bounded independent metadata/readback evidence. "
                "Unknown release/plugin profiles and unavailable schemas remain strict boundaries, not guessed implementations. Independent production-run/compiler endpoints and authored-to-compiled content/version mapping are not established; they are outside CREATE/EDIT. "
                "Lifecycle live proof remains limited to documented owned Log fixtures; newly covered authoring families are not thereby live-verified."
            ),
        },
    }
