"""One-shot coordination with family policy, state, and authorization checks."""

from typing import TYPE_CHECKING

from servicenow_mcp.auth import OAuthPKCEProvider, require_authorization_generation
from servicenow_mcp.capabilities.attachments import mutations as attachments
from servicenow_mcp.capabilities.catalog import mutations as catalog
from servicenow_mcp.capabilities.contracts import CapabilityError, CapabilityResult
from servicenow_mcp.capabilities.flows import mutations as flows
from servicenow_mcp.capabilities.mutations.models import (
    AttachmentCommand,
    CatalogCommand,
    FlowCommand,
    MutationCommand,
    MutationPreview,
    RecordCommand,
    StagedMutation,
)
from servicenow_mcp.capabilities.mutations.store import PreviewTokenStore
from servicenow_mcp.capabilities.records import mutations as records
from servicenow_mcp.policy import (
    gate_write,
    production_write_blocked,
    require_mutation_policy,
)
from servicenow_mcp.telemetry import trace_flow_preview_boundary

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings
    from servicenow_mcp.platform.client import ServiceNowClientProvider
    from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry


class MutationCoordinator:
    def __init__(
        self,
        settings: Settings,
        auth: OAuthPKCEProvider,
        clients: ServiceNowClientProvider,
        dictionary: DictionaryRegistry,
        families: frozenset[str],
        store: PreviewTokenStore | None = None,
    ) -> None:
        self.settings = settings
        self.auth = auth
        self.clients = clients
        self.dictionary = dictionary
        self.families = families
        self.store = store if store is not None else PreviewTokenStore()

    def _gate(self, command: MutationCommand, owning_table: str | None = None) -> None:
        production_write_blocked(self.settings)
        if command.family not in self.families:
            raise CapabilityError(
                "This mutation family is not enabled.", "MUTATION_DISABLED"
            )
        if isinstance(command, RecordCommand | AttachmentCommand):
            table = command.table
        elif isinstance(command, CatalogCommand):
            table = catalog.target_table(command)
        elif isinstance(command, FlowCommand):
            table = "sys_hub_flow"
        else:
            raise CapabilityError(
                "This mutation family is not supported.", "MUTATION_DISABLED"
            )
        if isinstance(command, FlowCommand) and command.action == "add_action":
            gate_write("sys_hub_action_instance_v2", self.settings)
        if isinstance(command, FlowCommand) and command.action == "add_subflow":
            gate_write("sys_hub_sub_flow_instance_v2", self.settings)
        if isinstance(command, FlowCommand) and command.action in {
            "activate",
            "publish",
            "deactivate",
            "test",
        }:
            gate_write("sys_hub_flow_snapshot", self.settings)
            for component in (
                "sys_hub_action_instance_v2",
                "sys_hub_sub_flow_instance_v2",
                "sys_hub_flow_logic_instance_v2",
                "sys_hub_trigger_instance_v2",
                "sys_hub_flow_stage",
            ):
                gate_write(component, self.settings)
            if command.action == "test":
                gate_write("sys_flow_context", self.settings)
        if isinstance(command, FlowCommand) and (
            command.action == "edit" or command.action == "create" and command.edits
        ):
            for edit in command.edits or []:
                if edit.get("op") in {"create_field", "update_field", "delete_field"}:
                    field_table = {
                        "inputs": "sys_hub_flow_input",
                        "outputs": "sys_hub_flow_output",
                        "flowVariables": "sys_hub_flow_variable",
                    }.get(str(edit.get("collection")))
                    if field_table:
                        gate_write(field_table, self.settings)
            if any(
                edit.get("op")
                in {
                    "create_stage",
                    "update_stage",
                    "delete_stage",
                    "attach_stage",
                    "detach_stage",
                    "set_error_handler",
                    "remove_error_handler",
                    "move_step",
                    "delete_step",
                    "insert_logic",
                    "set_variables",
                    "assign_outputs",
                    "insert_action",
                    "insert_subflow",
                }
                for edit in command.edits or []
            ):
                gate_write("sys_hub_flow_stage", self.settings)
            for component in (
                "sys_hub_action_instance_v2",
                "sys_hub_flow_logic_instance_v2",
                "sys_hub_sub_flow_instance_v2",
                "sys_hub_trigger_instance_v2",
            ):
                gate_write(component, self.settings)
        if table or owning_table:
            gate_write(table or owning_table or "", self.settings)
        if isinstance(command, CatalogCommand) and owning_table:
            gate_write(owning_table, self.settings)
        if isinstance(command, CatalogCommand) and command.action in {
            "cart_empty",
            "checkout_guide",
        }:
            gate_write("sc_cart_item", self.settings)

    def _apply_gate(self, staged: StagedMutation) -> None:
        self._gate(staged.command, staged.owning_table)
        if staged.instance != self.settings.servicenow_instance_url:
            raise CapabilityError(
                "The connection changed. Create a new preview.",
                "PREVIEW_CONNECTION_CHANGED",
            )

    async def preview(self, command: MutationCommand) -> CapabilityResult:
        if isinstance(command, FlowCommand):
            trace_flow_preview_boundary(
                "command", action=command.action, edits=command.edits
            )
        self._gate(command)
        await self.auth.get_headers()
        generation = self.auth.authorization_generation
        async with self.clients() as client:
            with require_authorization_generation(generation):
                if isinstance(command, RecordCommand):
                    summary, expected = await records.preview(
                        command, client, self.dictionary
                    )
                    table = command.table
                elif isinstance(command, AttachmentCommand):
                    summary, table, expected = await attachments.preview(
                        command, client
                    )
                elif isinstance(command, FlowCommand):
                    summary, expected = await flows.preview(command, client)
                    table = "sys_hub_flow"
                elif isinstance(command, CatalogCommand):
                    summary, expected = await catalog.preview(command, client)
                    table = (
                        summary["target_table"]
                        if command.action == "submit_producer"
                        else catalog.target_table(command)
                    )
                else:
                    raise CapabilityError(
                        "This mutation family is not supported.", "MUTATION_DISABLED"
                    )
        self._gate(command, table)
        token = await self.store.create(
            StagedMutation(
                command=command,
                instance=self.settings.servicenow_instance_url,
                authorization_generation=generation,
                fingerprint=expected,
                owning_table=table,
            ),
        )
        limitations = [
            "A preview token does not prove human approval. Apply rechecks policy and detectable state changes.",
            "State checks and the upstream mutation are not atomic.",
        ]
        if isinstance(command, CatalogCommand):
            limitations.extend(
                [
                    "Preview does not execute producer scripts or guide rules.",
                    "Preview cannot predict fulfillment, prices, routing, or all server-side validation.",
                ],
            )
        if isinstance(command, FlowCommand):
            limitations.append(
                "Experimental undocumented Workflow Studio API. Source-derived offline verification only. ServiceNow controls update-set capture. MCP does not inspect or manage update sets."
            )
            if command.action in {"activate", "publish", "deactivate", "test"}:
                limitations.append(
                    "This is an explicit lifecycle effect. Test executes real actions. Activate publishes and enables triggers. Existing executions are not cancelled by deactivation."
                )
        data = MutationPreview(
            family=command.family,
            action=command.action,
            preview_token=token,
            expires_in_seconds=self.store.ttl_seconds,
            summary=summary,
            limitations=limitations,
        )
        return CapabilityResult(data=data.model_dump(mode="json"))

    async def apply(self, preview_token: str) -> CapabilityResult:
        staged = await self.store.consume(preview_token)
        command = staged.command
        self._apply_gate(staged)
        with (
            require_authorization_generation(staged.authorization_generation),
            require_mutation_policy(lambda: self._apply_gate(staged)),
        ):
            async with self.clients() as client:
                if isinstance(command, RecordCommand):
                    value = await records.apply(
                        command, staged.fingerprint, client, self.dictionary
                    )
                elif isinstance(command, AttachmentCommand):
                    value = await attachments.apply(
                        command, staged.owning_table or "", staged.fingerprint, client
                    )
                elif isinstance(command, FlowCommand):
                    value = await flows.apply(command, staged.fingerprint, client)
                elif isinstance(command, CatalogCommand):
                    value = await catalog.apply(command, staged.fingerprint, client)
                else:
                    raise CapabilityError(
                        "This mutation family is not supported.", "MUTATION_DISABLED"
                    )
        empty_guide = (
            isinstance(command, CatalogCommand)
            and command.action == "submit_guide"
            and value == []
        )
        if not empty_guide and (
            value is None
            or value == {}
            or value == []
            or (isinstance(value, dict) and "error" in value)
        ):
            raise CapabilityError(
                "Mutation response did not establish success. Remote outcome is unknown. Check ServiceNow before "
                "another preview.",
                "UNKNOWN_OUTCOME",
            )
        return CapabilityResult(data=value)
