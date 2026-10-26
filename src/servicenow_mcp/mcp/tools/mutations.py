"""Native preview and apply tools for every mutating capability."""

from typing import TYPE_CHECKING, Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field, JsonValue

from servicenow_mcp.capabilities.attachments.common import (
    MAX_ATTACHMENT_BYTES,
    decode_content_base64,
    ensure_attachment_size_within_limit,
)
from servicenow_mcp.capabilities.catalog.contracts import (
    CatalogMutationAction,
    CatalogView,
)
from servicenow_mcp.capabilities.contracts import CapabilityResult
from servicenow_mcp.capabilities.mutations.models import (
    AttachmentCommand,
    CatalogCommand,
    FlowCommand,
    RecordCommand,
)
from servicenow_mcp.mcp.handlers import tool_handler

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from servicenow_mcp.capabilities.mutations.coordinator import MutationCoordinator


PREVIEW_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
APPLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)


def register_tools(mcp: MCPServer, coordinator: MutationCoordinator) -> None:
    if "records" in coordinator.families:

        @mcp.tool(annotations=PREVIEW_ANNOTATIONS)
        @tool_handler
        async def record_preview(
            action: Literal["create", "update", "delete"],
            table: str,
            sys_id: str | None = None,
            data: dict[str, JsonValue] | None = None,
        ) -> CapabilityResult:
            """Preview a record mutation without changing ServiceNow.

            create requires data and forbids sys_id. update requires both. delete requires
            sys_id and forbids data. data is a native field/value object, limited to 256 KiB
            and depth 32. It can contain complete script or XML strings.
            Dictionary metadata checks inherited mandatory and XML fields.
            The result contains a masked summary, expiry, and a single-use preview_token.
            Use mutation_apply to execute. This token does not prove human approval.
            """
            return await coordinator.preview(
                RecordCommand(action=action, table=table, sys_id=sys_id, data=data)
            )

    if "attachments" in coordinator.families:

        @mcp.tool(annotations=PREVIEW_ANNOTATIONS)
        @tool_handler
        async def attachment_preview(
            action: Literal["upload", "delete"],
            table: str | None = None,
            table_sys_id: str | None = None,
            file_name: str | None = None,
            content_base64: str | None = None,
            content_type: str | None = "application/octet-stream",
            sys_id: str | None = None,
        ) -> CapabilityResult:
            """Preview an attachment upload or deletion without changing ServiceNow.

            upload requires table, table_sys_id, file_name, and content_base64.
            Decoded content is limited to 10 MiB. content_type defaults to application/octet-stream.
            delete requires only sys_id. Ownership comes from current attachment metadata.
            The result describes metadata, size, and digest, never file contents.
            Apply its single-use preview_token with mutation_apply before expiry.
            """
            if content_base64 is not None and len(content_base64) > 4 * (
                (MAX_ATTACHMENT_BYTES + 2) // 3
            ):
                raise ValueError(
                    "Attachment exceeds the maximum supported size of 10 MiB."
                )
            content = (
                decode_content_base64(content_base64)
                if content_base64 is not None
                else None
            )
            if content is not None:
                ensure_attachment_size_within_limit(content, operation="upload")
            command = AttachmentCommand(
                action=action,
                table=table,
                table_sys_id=table_sys_id,
                file_name=file_name,
                content=content,
                content_type=content_type or "application/octet-stream",
                sys_id=sys_id,
            )
            return await coordinator.preview(command)

    if "catalog" in coordinator.families:

        @mcp.tool(annotations=PREVIEW_ANNOTATIONS)
        @tool_handler
        async def service_catalog_preview(
            action: CatalogMutationAction,
            item_sys_id: str | None = None,
            variables: dict[str, JsonValue] | None = None,
            quantity: int | None = None,
            requested_for: str | None = None,
            also_request_for: list[str] | None = None,
            cart_item_id: str | None = None,
            cart_sys_id: str | None = None,
            items: list[dict[str, JsonValue]] | None = None,
            view: CatalogView | None = None,
            api_version: str | None = None,
        ) -> CapabilityResult:
            """Preview a catalog or cart mutation using read endpoints only.

            order_now and add_to_cart require item_sys_id. They accept variables, quantity,
            requested_for, and also_request_for (1-50 user sys_ids).
            submit_producer requires item_sys_id and accepts variables and view. It creates a record
            through the producer endpoint during apply. Preview never executes producer scripts.
            add_to_wishlist requires item_sys_id and accepts quantity.
            submit_guide requires item_sys_id and accepts variables.
            checkout_guide requires item_sys_id and items: [{sys_id, sysparm_quantity, variables}].
            Each guide quantity is a positive integer string. variables is a native object.
            cart_item_update requires cart_item_id and quantity. It accepts variables.
            cart_item_delete requires cart_item_id. cart_empty requires cart_sys_id.
            cart_empty can delete the cart and its contents, subject to ServiceNow roles.
            cart_submit and cart_checkout accept no item or variables.
            quantity is a non-negative integer. api_version optionally selects a version such as v1.
            Preview cannot simulate fulfillment, producer routing, or guarantee prices.
            Apply the single-use preview_token with mutation_apply. Detectable state changes require a new preview.
            """
            return await coordinator.preview(
                CatalogCommand(
                    action=action,
                    item_sys_id=item_sys_id,
                    variables=variables,
                    quantity=quantity,
                    requested_for=requested_for,
                    also_request_for=also_request_for,
                    cart_item_id=cart_item_id,
                    cart_sys_id=cart_sys_id,
                    items=items,
                    view=view,
                    api_version=api_version,
                ),
            )

    if "flows" in coordinator.families:

        @mcp.tool(annotations=PREVIEW_ANNOTATIONS)
        @tool_handler
        async def flow_preview(
            action: Literal[
                "create",
                "add_action",
                "add_subflow",
                "edit",
                "activate",
                "publish",
                "deactivate",
                "test",
                "resolve_schema",
            ],
            sys_id: str | None = None,
            name: str | None = None,
            scope: str | None = None,
            description: str | None = None,
            flow_type: Literal["flow", "subflow"] | None = None,
            action_type_sys_id: str | None = None,
            subflow_sys_id: str | None = None,
            input_name: str | None = None,
            inputs: dict[str, JsonValue] | None = None,
            connection_overrides: dict[str, str | None] | None = None,
            edits: Annotated[
                list[dict[str, JsonValue]],
                Field(
                    min_length=1,
                    max_length=200,
                    description="Ordered operations for complete create or incremental edit. New steps use request-local ref aliases.",
                ),
            ]
            | None = None,
            schema_tokens: Annotated[list[str], Field(max_length=200)] | None = None,
            expected_scope: str | None = None,
            expected_revision: str | None = None,
        ) -> CapabilityResult:
            """Preview native Flow CREATE/EDIT or a separately selected effect without changing ServiceNow.

            create requires name and explicit scope sys_id (or global); description is optional.
            flow_type=flow|subflow defaults to flow. A new owner is inactive/unpublished; subflows are triggerless.
            Supply 1-200 ordered edits for complete creation, or omit edits for an empty header. create/edit share
            the existing typed planner, serializers and request-local ref aliases; later pills use {{ref.output}}.
            Complete creation permits at most 200 nodes including generated handler wrappers. Plans are bounded
            to 256 KiB/depth 32 and validate before the property POST. Apply independently reads the new header/revision
            before one complete save. It returns refs mapped to persisted sys_id/ui_id/kind and a fresh revision.
            Creation is not transactional: UNKNOWN_OUTCOME reports known identity/progress in error.outcome.
            Never repeat uncertain creation; inspect the existing owner and preview incremental edits.
            CREATE/EDIT does not publish, activate, test, run the authored flow or implicitly execute schema providers.

            resolve_schema is a SEPARATE reviewed provider effect, not a flow save or authored-flow execution.
            It requires action_type_sys_id and input_name; inputs contains native sibling/dependency values.
            Published input metadata selects the provider. expected_scope optionally binds its application.
            connection_overrides supplies the complete inspected effective alias context, including aliases used
            by other calls. It maps declared source alias sys_ids to declared child sys_ids or null.
            Only resolve_schema accepts the exact native reset pair {"":""}; other empty keys/values reject.
            Preview reads metadata only. Its mutation_apply executes the provider once with potentially real
            side effects, then returns an opaque schema_token expiring after 300 seconds. Start a NEW create/edit
            preview with schema_tokens (at most 200 distinct tokens), the same dependencies/connections and normal inputs.
            Review resolved_schemas and the whole authoring plan before separately applying that authoring token.
            An accepted create/edit command consumes all schema tokens even if planning fails; tokens cannot be
            replayed, supplied as raw evidence, applied directly or shared across connection/authorization changes.
            Provider/master/snapshot metadata, revisions and output evidence are checked before/after execution
            and again for authoring. Scoped-name dispatch is not an atomic exact-version execution pin:
            observed_binding_verified and exact_execution_version_verified are distinct. Save/apply never reruns it.
            Resolved dynamic_inputs uses a native object, dynamic_choice a choice name, dynamic_template encoded
            selections. Unknown/nested provider output grammar or unresolved dependencies reject, never infer fields.

            Explicit lifecycle effects require sys_id, expected_scope and expected_revision:
              activate: flow-only snapshot publication AND trigger activation; this can start real work.
              publish: callable subflow-only publication; parent callers may use the new version.
                There is no flow publish-only action because native snapshot publication activates flows.
              deactivate: active flow-only; disables new trigger starts, does not cancel existing contexts.
              test: executes the saved editable owner asynchronously with real action side effects.
                inputs supplies every visible subflow input/Object field explicitly: static scalar or declared
                Object/Array.String/Integer/Boolean/Object values, or supported scalar classic-trigger outputs.
                Nested references, Array.Reference and record-list test values remain outside this test serializer.
                Record tests require explicit triggering record sys_ids. Native Test-modal children are separate
                from authoring bindings; these test limits are not CREATE/EDIT limitations.
                Returns an exact accepted context ID, not a promise of completion. Initial ownership/state may
                be pending with terminal=null. Use flow.status(execution_sys_id=...) for bounded read-only follow-up.
                Compiled-snapshot ownership does not prove authored version/content equality. Never replay uncertainty.
            flow.validate is read-only LOCAL validation, not remote compilation. Lifecycle apply sends one explicit
            effect with independent outcome reads. No independent production-run/remote-validator action is exposed.
            expected_scope checks the target application, not the selected session application. expected_revision
            checks the target sys_mod_count from flow.status. Both are required for versioned-owner edits; ordinary
            inactive drafts may omit them. Creation forbids expected_revision. ServiceNow controls update-set capture;
            MCP does not select, inspect or repair update sets or Customer Updates.

            add_action/add_subflow retain their narrower header-append contract: sys_id plus published
            action_type_sys_id/subflow_sys_id and native static scalar inputs. Use ordered edits for structured authoring.
            edit requires sys_id and edits, an ordered array of objects with op. create accepts the same operations:
              insert_logic: logic accepts native type/catalog name, inputs, optional ref and parent/after/before.
                Direct families: IF, ELSEIF, ELSE, FOREACH, DOUNTIL, GOBACKTO, DECISION, TIMER, RUNWORKFLOW,
                RUNDYNAMICFLOW, GETFLOWOUTPUTS, END, TRY, PARALLEL, PARALLELBLOCK, BREAK, CONTINUE.
                Else If/Else stay connected. Parallel Branch requires Parallel. Try generates its paired Catch.
                SETFLOWVARIABLES/ASSIGNSUBFLOWOUTPUTS use dedicated operations; CATCH/DECISIONBLOCK/top-level
                wrappers are generated/managed by their constructs, not generic insertion. UPDATEFLOWTEMPLATE
                is preserved system-locked/read-only; PLACEHOLDER is internal/transient, not an authorable block.
                Do Until inputs.condition is required, condition_name optional; native non-nesting and closing
                body-output scope apply. Unresolved nested closing orders require saved integer orderBottom.
                Go back to inputs.go_back_to_step accepts alias/UI ID/sys_id/order and pins native identity.
                It must terminate an eligible conditional/Decision Branch/Catch, outside the top-level handler,
                targeting native earlier sibling/ancestor construct starts within the enclosing Parallel boundary.
                Decision inputs: decision_table, optional decision_label, execution=first_match|run_all,
                has_branches/include_otherwise booleans; answer_table/outputs are derived read-only metadata.
                decision_inputs maps declared native inputs. Omit branches for authoritative answer order, or pass
                the COMPLETE ordered [{answer,label?,ref?}] list; Otherwise uses answer="" last.
                update_inputs accepts the same Decision fields. Table changes require explicit complete writable
                decision_inputs; removing populated generated branches requires children=delete.
                Generated branches permit label updates only. Result pills use declared answer.result_elements
                fields, or run_all records through For Each item.result_elements; metadata reads do not decide.
                TIMER: explicit duration_type; explicit_duration uses timer_duration; relative_duration also
                uses timer_relative_duration_operator=before|after and timer_relative_duration_datetime;
                percentage_duration uses timer_percentage=0-100 and percentage_timer_input_datetime.
                timer_schedule is optional. Durations use native epoch offsets, zero allowed, maximum 9999:59:59.
                RUNWORKFLOW: workflow_reference, workflow_inputs, explicit wait, table-bound current when declared;
                active published workflow availability and its readable native interface are required.
                RUNDYNAMICFLOW: published flow_template, flow, explicit wait_for_completion, dynamic_inputs.
                GETFLOWOUTPUTS: compatible context and optional published flow_template for declared outputs.
                Dynamic templates need native publication/scope access, not an invented activation gate.
                Workflow wait=false exposes no output pills; Dynamic Flow wait=false retains context only.
              insert_action/insert_subflow: published action_type_sys_id/subflow_sys_id, native inputs,
                optional ref and parent/after/before. Published entitled schemas bind preview/apply.
                Native text/rich-text/numeric/date/reference-picker/field-list/slushbucket grammars are preserved.
                Get Catalog Variables uses native CSV: bare item_option_new IDs or sys_id:source_table when the
                installed include_multirow_variablesets capability permits it. Selected scalar/Object/Array/MRVS
                output schemas/defaults/provenance are independently read, not guessed from sample values.
                approval_rules uses native goals, Any/All/count/percentage/manual clauses and typed approvers.
                schedule_date_time approval JSON uses action/date_type/date/duration/duration_type/schedule/
                schedule_label; active outcomes require a compatible Date/Time pill, none uses date="{{}}".
              update_inputs: step plus changed native inputs or supported input modes; omitted values remain.
                Pills use trigger paths, <producer UI ID>.<output>, flow_variable.<name> or subflow.<input>.
                Type/cardinality/reference tables and producer visibility are checked after all edits.
                Declared Object/Array values and compatible whole pills use complete recursive schemas.
                Array.String/Integer/Boolean/Object/Reference/Choice/DateTime are supported. Array.Array accepts
                [] or whole typed/item-Array pills; literal nested arrays reject because native save erases them.
                Array.Reference literals validate sys_id syntax/table metadata, not record existence or ACL access.
                No arbitrary array indexing, caller schema replacement or output-to-output pills.
              input_scripts on insert_action/insert_subflow/insert_logic/update_inputs/set_variables maps unique
                declared scriptable input names to source: nonempty enables, null selects basic mode retaining
                inactive code/dependencies, "" clears. Ordinary inputs can supply a retained basic fallback.
                Qualified template_input.column names configure selected template columns; coalesce_fields encoded
                in a template requires native upsert metadata and selected non-password columns. Whole template
                pills are limited to compatible standard-template subflow context, not flows/dynamic templates.
                Named fd_data references are lexically validated, pinned and rebound across ordering edits;
                ambiguous/computed forms fail closed. No JavaScript is evaluated or runtime return type certified.
              input_transforms maps input names to {source:"{{producer.path}}",transforms:[{transform_sys_id,
                compositions?:[{composition_sys_id,parameters:{nativeName:typedValue}}]}]}, or null to remove.
                Native metadata/types bind the full ordered transform chain; no transform executes.
                Same-field ordinary inputs or active scripts conflict; null removal rechecks restored source type.
              connection_overrides on insert_action/insert_subflow/update_inputs maps declared source alias sys_ids
                to declared child alias sys_ids or null. set_connection_overrides uses that field for root context.
                Native per-call lists replace, not per-key merge, root context; reset tombstones are preserved.
                Public authoring empty keys are forbidden. No credentials are read or integration invoked.
              move_step: step and parent/after/before; Try moves linked Catch and both subtrees together.
                Independent Catch/Decision Branch moves reject. Stages, topology and surviving pills remain checked.
              delete_step: step, children=reject|delete|promote; connected cascade requires cascade=connected and
                children=delete. Saved action/logic tombstones and subflow list omission preserve definitions.
                Connection mappings use native shared-reference cleanup. Dangling references never clear silently.
              set_trigger: type=service_catalog|record_create|record_update|record_create_or_update|daily|weekly|
                monthly|run_once|repeat|email|remote_table_query|rest_async|analytics|metric|sla_task, native inputs;
                replace=true explicitly replaces an existing trigger. update_trigger preserves unmentioned values;
                remove_trigger uses saved ID tombstones. Surviving consumers block replacement/removal.
                Record table/condition/run choices follow catalog/descriptor/dictionary metadata. Conditions are
                static encoded queries, not JavaScript/query directives; source table changes need explicit conditions.
                Classic daily time uses 1970-01-01 HH:MM:SS; weekly adds day_of_week, monthly day_of_month=1-31;
                run_once uses run_in timestamp; repeat is a positive epoch-offset duration meeting catalog minimum.
                Classic schedules use instance timezone. Do not guess catalog names or convert timezone values.
                Saved set_trigger.template_id requires readable native flow version other than "1". Recurrence,
                calendars/timezones follow selected writable metadata; inherited fromTemplate values stay read-only.
                This attaches an existing saved definition, not create/edit of the saved template itself.
                Saved Service Catalog/REST/external flow-owned interfaces follow exact native ownership metadata.
                Application types use native email/remote-table/analytics/metric reference and condition metadata;
                sla_task has no authored inputs in this catalog. Availability depends on installed features/access.
                REST set/update_trigger.interface_schemas maps declared roots to {type,children:[{name,label,type,
                mandatory?,maxsize?,children?}]}. Each supplied root replaces its complete schema; [] clears children.
                Root/child allowlists apply. Scalar inputs stay separate. A fixed metadata-only GraphQL query reads
                server base_path; path derives from display_path. Protected fields cannot be overridden.
                Trigger configuration never invokes REST, processes email, queries a remote table or starts a flow.
              set_error_handler: enabled boolean; current native wrappers or strictly identified legacy/Rome roots.
                Populated disabled contents stay read-only; disabling an empty handler removes wrappers.
              remove_error_handler requires empty contents. parent=main|error_handler selects the region.
                error_handler.<catalog leaf> pills stay handler-scoped; main outputs cannot cross into it.
                At most ten handler actions/logic/subflow calls; native sandwich/Parallel ordering remains enforced.
              set_variables: inputs mapping declared variables to native values, optional input_scripts/input_transforms,
                parent/after/before. Native input/flowVariables mirrors remain synchronized.
              assign_outputs: outputs mapping declared top-level names to native scalar/Object/Array values or pills.
                Without step inserts with optional ref/placement; with step updates/appends rows, retaining others.
              clear_output_assignment: step,name removes only that row. Assignments require a supported editable
                triggerless subflow, including eligible published working drafts. Loop/Parallel placement is legal
                with ordinary pill scope. No scripts/transforms on the native custom assignment widget.
                Nested targets use their parent Object value. Each named output replaces its complete value;
                omitted object fields use declared defaults/empty values. Used declarations cannot be deleted.
              create_field: collection=inputs|outputs|flowVariables, name,label,type, optional parent declaration path.
                Variables: string/integer/boolean/glide_date_time/decimal/float/reference/json; scalar only.
                Subflow interfaces additionally accept choice/number/object, array.string/integer/boolean/object/
                reference/choice/datetime/array and extra root widgets only when the native picker/serializer supports them.
                reference requires table metadata. Arrays create fixed item schemas at <path>.<local-name>_child0;
                add Array.Object fields beneath that path. Direct Object-child Reference/JSON creation is excluded.
              update_field: collection,name and native label/hint/type/reference/maxsize/default_value/mandatory,
                choices or choice_source={table,field}, choice_option="1"|"3", reference_qualifier, dependent_on,
                max_rows as applicable. Defaults are static scalar values; variables use set_variables instead.
                Native table-choice defaultChoices/provenance are retained, not flattened into a local choice union.
                new_name only renames newly created declarations or native-name-editable interface rows;
                saved variable names stay fixed and consumers must be explicitly rebound, never aliased.
                Type/reference changes require no consumers/stored values. Actual complex type changes require
                reset_structure=true or prior explicit child deletion; protected descendants/defaults still block loss.
                Object/Array whole-parent defaults are not exposed by native settings; scalar children and
                Array.Choice item defaults use their declared settings. max_rows is positive integer or null.
                reference_qualifier is static; dependent_on is a sibling root input name or "" on native dependent widgets.
              delete_field: collection,name full declaration path. Used descendants, assignments, pills, transforms,
                source links and scripts block removal; synthetic array items cannot be deleted independently.
                Interface CRUD requires supported editable triggerless subflows, including eligible published drafts.
                Flow-owned interfaces retain label/hint-only edits; trigger-owned interfaces remain protected.
              create_stage: label,value, optional duration/always_show/states/ref. update_stage adds zero-based order.
              attach_stage/detach_stage: stage key/ref,step; one per step, no implicit replacement.
              delete_stage: detach all steps first; dependent error stages block removal.
                create_stage(type=error) uses step/ref; native names/ancestry stay fixed. detach removes keyed error state.
                For Each/Parallel ancestry is excluded; Do Until/Try bodies use ordinary native placement guards.
                Stages report state, not exceptions, handler activation or execution.

            Steps use sys_id/UI ID/display order/ref. edit supports inactive drafts and native release 27800/engine-2
            active/published flow/subflow owners with expected_scope/revision, save-as-you-go explicitly disabled
            and valid runtime references. Immutable snapshots reject. Active plans/callable masters select runtime.
            Preservation binds complete JSON snapshot chunk storage or complete version-owned rows, not mutable GET
            facades/arbitrary test snapshots, including native subflow columns and actual referenced-block metadata.
            Apply revalidates then sends one draft PUT and independently checks version/content/trigger preservation.
            No edit lock or lifecycle effect occurs. Bounded unknown configuration metadata stays strict;
            drift returns UNKNOWN_OUTCOME even when runtime_preservation is verified. Never replay uncertain saves.
            Apply the single-use preview_token with mutation_apply; the upstream API is undocumented, no automatic retries.
            flow.describe separates installed authoring profiles, source/fixture evidence and narrowly scoped live proof.
            Saving does not certify engine instructions, runtime behavior or compatibility with every release/plugin.
            """
            return await coordinator.preview(
                FlowCommand(
                    action=action,
                    sys_id=sys_id,
                    name=name,
                    scope=scope,
                    description=description,
                    flow_type=flow_type,
                    action_type_sys_id=action_type_sys_id,
                    subflow_sys_id=subflow_sys_id,
                    input_name=input_name,
                    inputs=inputs,
                    connection_overrides=connection_overrides,
                    edits=edits,
                    schema_tokens=schema_tokens,
                    expected_scope=expected_scope,
                    expected_revision=expected_revision,
                ),
            )

    @mcp.tool(annotations=APPLY_ANNOTATIONS)
    @tool_handler
    async def mutation_apply(preview_token: str) -> CapabilityResult:
        """Apply one previously reviewed record, attachment, catalog, or flow preview.

        preview_token is single-use and expires after 300 seconds. It is consumed on success,
        rejection, cancellation, or failure. Apply rechecks policy, connection, authorization,
        and detectable state changes. No mutation is replayed automatically.
        Applying resolve_schema returns a schema_token, not a saved flow. Consume it only in a new
        flow_preview create/edit schema_tokens list, then review and apply that separate authoring preview.
        If the remote outcome is unknown, check ServiceNow before creating another preview.
        """
        return await coordinator.apply(preview_token)
