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
                "validate_remote",
                "run",
            ],
            sys_id: str | None = None,
            name: str | None = None,
            scope: str | None = None,
            description: str | None = None,
            flow_type: Literal["flow", "subflow"] | None = None,
            action_type_sys_id: str | None = None,
            subflow_sys_id: str | None = None,
            inputs: dict[str, JsonValue] | None = None,
            edits: Annotated[
                list[dict[str, JsonValue]],
                Field(
                    min_length=1,
                    max_length=200,
                    description="Ordered operations for complete create or incremental edit. New steps use request-local ref aliases.",
                ),
            ]
            | None = None,
            expected_scope: str | None = None,
            expected_update_set: str | None = None,
            expected_revision: str | None = None,
        ) -> CapabilityResult:
            """Preview Workflow Studio authoring or an explicitly selected lifecycle effect without changing ServiceNow.

            create requires name and explicit scope sys_id (or global). description is optional.
            Supply edits to create a COMPLETE flow in this one request. The server resolves definitions and node identities.
            Use the same ordered edit operations below, with ref aliases for new steps and {{ref.output}} pills.
            The entire plan validates before the property POST. Apply then reads the created header and revision before one full save.
            This is not transactional. UNKNOWN_OUTCOME includes error.outcome with the known sys_id and persisted/unknown progress.
            Never repeat creation after an uncertain response. Inspect the existing flow, then preview incremental edits.
            Apply returns refs mapping aliases to persisted sys_id/ui_id/kind. Bound writes also return a fresh revision.
            No activation, publication, testing, or execution occurs. Omitting edits retains header-only creation.
            flow_type=flow|subflow selects creation type. Omit for flow. A new subflow is inactive, unpublished, and triggerless.
            Lifecycle actions require sys_id plus expected_scope, expected_update_set, and expected_revision.
              activate: flow-only. Publishes its snapshot AND enables triggers. This can start real work after activation.
              publish: subflow-only. Publishes a callable subflow and can update versions used by parent flows.
                Flow publish-only is unavailable because snapshot publication activates flows.
              deactivate: active flow-only. Disables new trigger starts. Does not cancel existing executions or unpublish a subflow.
              test: executes the saved inactive draft asynchronously with real action side effects. inputs supplies static scalar
                subflow input values or supported classic trigger output values. Record tests require explicit triggering record sys_ids.
                Returns an exact execution context ID and observed state, not a promise of successful completion.
                Use flow.status with execution_sys_id for bounded read-only follow-up. Never replay an uncertain test.
              validate_remote and run: explicitly unavailable. No separate compiler or production-run contract exists in the local source.
                flow.validate remains read-only local validation. Saving and testing are not aliases for remote validation or production run.
            Lifecycle preview binds complete current definitions/dependencies. Apply sends one explicit effect and independently reads its outcome.
            Optional strict context binding: expected_scope and expected_update_set are required together.
            Existing flows also require expected_revision (sys_mod_count from flow.status).
            Binding reads the current application/update set and never switches them.
            Unreadable context blocks the write.
            Apply checks fresh flow-header Customer Update metadata. It does not prove child-artifact capture.
            add_action requires sys_id and a published action_type_sys_id snapshot.
            add_subflow requires sys_id and a published subflow definition subflow_sys_id.
            inputs is a native object of static scalar values for add_action and add_subflow.
            edit requires sys_id and edits: an ordered native array of 1-200 objects with an op key (256 KiB, depth 32).
            create accepts the same edits. Complete creation permits at most 200 nodes, including hidden handler wrappers.
              set_trigger: type=service_catalog|record_create|record_update|record_create_or_update|daily|weekly|monthly|run_once|repeat,
                static native inputs, replace=true to replace an existing trigger. No activation or execution.
                Record inputs: table, condition (static encoded query), and run/session/user choices from the current catalog.
                Changing tables requires explicit conditions and consumers updated first. Current-record pills use <trigger label>_1.current.
                daily: glide_time='1970-01-01 HH:MM:SS'. weekly also needs day_of_week from the catalog choice list.
                monthly also needs integer day 1-31. run_once: glide_date_time='YYYY-MM-DD HH:MM:SS'.
                repeat: glide_duration is an epoch-based timestamp after '1970-01-01 00:00:00', at least the catalog minimum.
                Classic schedules use the instance timezone. No timezone conversion, Recurrence, or saved trigger definitions.
              update_trigger: nonempty inputs. Preserves trigger identity, other values, and opaque metadata.
              remove_trigger: no fields. Removes the saved instance with a native ID tombstone. Used pills block removal.
              set_error_handler: enabled=boolean. Creates current-catalog native top-level Try/Catch wrappers when enabled=true.
                Disabling a populated handler preserves its contents as read-only. Disabling an empty handler removes the wrappers.
                This configures draft error handling only. It does not activate, publish, or execute the flow.
              remove_error_handler: no fields. Requires an empty handler. Delete or move its contents explicitly first.
                parent=main|error_handler selects a native wrapper region for supported insert_logic/move_step operations.
                error_handler.<catalog output path> pills resolve to the Catch UI identity. Use leaf paths from preview error_handler.outputs.
                Error Status pills are visible only inside their handler. Main-step outputs cannot cross into it.
                The handler contains at most ten actions, flow-logic steps, or subflow calls. Legacy/sandwich wrapper contracts are unavailable.
              insert_logic: logic (If, Else If, Else, For Each, Try, Parallel, Parallel Branch, End,
                Exit Loop, Skip Iteration), inputs, and one of parent,
                after, or before. ref names the new block for later edits in the same call.
              insert_action: action_type_sys_id (published snapshot), inputs, optional ref and parent/after/before.
              insert_subflow: subflow_sys_id (published definition), inputs, optional ref and parent/after/before.
                Both insert into existing structured drafts, including enabled handler branches. Inputs use the existing scalar/complex serializers.
                Published readable/entitled snapshots and their inputs bind the preview. Dynamic/dependent insertion inputs remain unavailable
                except native document/field parameters resolved from a static table or a record pill with a known reference table.
                Read-only table defaults are not caller overrides. Get Catalog Variables supports simple item_option_new selections
                as comma-separated sys_id:item_option_new tokens. Its selected outputs are read independently and bound to preview/apply.
                approval_rules accepts native Any/All U/G pill clauses with user/group reference checks.
                schedule_date_time accepts the native approval no-deadline JSON with action=none and date={{}}.
                No automatic approval deadline, complex catalog-variable, or template-value edit adapter is enabled.
                Later edits can use {{ref.output_path}} and placement refs. Subflow calls wait for completion.
              move_step: step, and one of parent, after, or before. Moving Try includes its paired Catch and both subtrees.
                Catch cannot move independently. Pair moves recheck stages, handler regions, and all pill dependencies.
              update_inputs: step, inputs. Native objects and arrays require declared child schemas.
                Pills use trigger paths, <producer UI ID>.<output>, flow_variable.<name>, or subflow.<input>.
                Type, cardinality, reference tables, and producer visibility are checked after all edits.
                Whole Object/Array.String/Integer/Boolean/Object pills use one exact {{producer.path}} value and no child values.
                Source and target must have the same recursive Object fields and types. Array item schemas must match.
                Published action/subflow producer definitions are read independently. Subflow input schemas are also supported.
                References require compatible tables. No implicit schema replacement or dynamic schema inference.
              delete_step: step, children=reject|delete|promote. Connected branches need cascade=connected
                and children=delete. Saved action/logic deletion uses tombstones. Saved subflow calls are omitted
                from the complete draft list. Referenced definitions are not deleted. Connection overrides block deletion.
                Dangling references fail. Values are never cleared automatically.
              set_variables: inputs mapping existing variable names to values, parent/after/before.
              assign_outputs: outputs mapping top-level declared output names to native values or scalar typed pills.
                Without step, inserts a native Assign Subflow Outputs step using parent/after/before and optional ref.
                With step, updates named rows and appends missing rows. Unmentioned rows retain their order and values.
              clear_output_assignment: step, name. Removes that assignment row, not the declared output or logic step.
                Assignments require an unpublished triggerless subflow. Declare outputs first, including within the same edits.
                Blank value assigns an empty value. Clearing removes the row. Used output declarations cannot be deleted.
                Object values use local child keys. Array.String/Integer/Boolean/Object values use native arrays.
                Nested values may mix literals and scalar leaf pills. Each named output replaces its complete value.
                Omitted object fields use declared defaults or empty values. Unmentioned output rows remain unchanged.
                Assign a nested field inside its parent Object value, not as a separate dotted output target.
                Whole Object/Array pills are supported for compatible schemas, including inside native complex values.
                Loop/parallel assignments, scripts, and output-to-output pills are unavailable.
              create_field: collection=inputs|outputs|flowVariables, name, label, type=string|integer|boolean|reference|json.
                reference requires a table name. Optional hint/maxsize. Subflow inputs accept mandatory boolean.
                Subflow interfaces accept static default_value. Variables are initialized with set_variables.
                Subflow interfaces also support object, array.string, array.integer, array.boolean, array.object.
                To add a child, supply parent as a declaration path and name as a local identifier.
                Arrays register one fixed item schema automatically: <array path>.<local array name>_child0.
                Add Array.Object fields under that item path. New nested input leaf pills use subflow.<path>.
              update_field: collection, name, label/hint/type/reference/maxsize. Interfaces also accept default_value.
                Only subflow inputs accept mandatory. Names cannot be renamed.
                Identity/name remain fixed. Type/reference changes require no consumers and no stored value.
                Nested name is a full declaration path. Array item type is fixed. Complex type replacement is unavailable.
              delete_field: collection, name. References, assignment targets, transforms, and scripts block removal.
                Interface CRUD requires an unpublished triggerless subflow. Flow-owned interfaces retain label/hint edits only.
                Object removal includes its declared children. Used descendants block removal. Array items cannot be deleted separately.
                Complex flow variables, complex defaults, direct nested Reference/JSON creation, and Array.Reference/Array.Array/Choice are unavailable.
                New variables and input pills work in later edits within the same request.
              create_stage: label, value, optional duration (epoch-based timestamp), always_show boolean,
                states (pending/in_progress/skipped/complete/error labels), ref for later stage edits.
              update_stage: stage key/ref, label/value/duration/always_show/states, or zero-based order.
              attach_stage/detach_stage: stage key/ref and step. One stage per step. No implicit replacement.
              delete_stage: stage key/ref. Detach all steps first. Dependent error stages block deletion.
                create_stage(type=error): step and optional ref only. Creates and attaches a conditional Error state.
                The last eligible standard-stage attachment before the enclosing If/Else If/Else supplies its ancestor.
                Error names and ancestry are native and cannot be renamed. update_stage supports duration, always_show, and state labels.
                delete_stage removes the error state. detach_stage also removes it, matching Studio. Recreate to attach elsewhere.
                Error stages report state only. They neither throw exceptions nor enable a handler or activate the flow.
                These operations author stage state, not flow-logic steps. For Each/Parallel children are excluded.
            Steps are referenced by sys_id, UI identifier, display order, or a ref alias.
            Draft authoring supports only inactive, unpublished flow/subflow drafts. Lifecycle effects require their explicit action names.
            Apply the single-use preview_token with mutation_apply. The upstream API is undocumented.
            Apply uses independent semantic readback. Verify update-set capture. No automatic retries.
            flow.describe reports adapter support separately from catalog discovery and live verification.
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
                    inputs=inputs,
                    edits=edits,
                    expected_scope=expected_scope,
                    expected_update_set=expected_update_set,
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
        If the remote outcome is unknown, check ServiceNow before creating another preview.
        """
        return await coordinator.apply(preview_token)
