"""One-shot coordination with family policy, state, and authorization checks."""

from typing import TYPE_CHECKING

from servicenow_mcp.auth import OAuthPKCEProvider, require_authorization_generation
from servicenow_mcp.capabilities.attachments import changes as attachments
from servicenow_mcp.capabilities.catalog import changes as catalog
from servicenow_mcp.capabilities.changes.models import (
    AttachmentCommand,
    CatalogCommand,
    ChangeCommand,
    ChangePreview,
    RecordCommand,
    StagedChange,
)
from servicenow_mcp.capabilities.changes.store import PreviewTokenStore
from servicenow_mcp.capabilities.contracts import CapabilityError, CapabilityResult
from servicenow_mcp.capabilities.records import changes as records
from servicenow_mcp.policy import (
    gate_write,
    production_write_blocked,
    require_change_policy,
)

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings
    from servicenow_mcp.platform.client import ServiceNowClientProvider
    from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry


class ChangeCoordinator:
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

    def _gate(self, command: ChangeCommand, owning_table: str | None = None) -> None:
        production_write_blocked(self.settings)
        if command.family not in self.families:
            raise CapabilityError(
                "This change family is not enabled.", "CHANGE_DISABLED"
            )
        if isinstance(command, RecordCommand | AttachmentCommand):
            table = command.table
        elif isinstance(command, CatalogCommand):
            table = catalog.target_table(command)
        else:
            raise CapabilityError(
                "This change family is not supported.", "CHANGE_DISABLED"
            )
        if table or owning_table:
            gate_write(table or owning_table or "", self.settings)
        if isinstance(command, CatalogCommand) and owning_table:
            gate_write(owning_table, self.settings)
        if isinstance(command, CatalogCommand) and command.action in {
            "cart_empty",
            "checkout_guide",
        }:
            gate_write("sc_cart_item", self.settings)

    def _apply_gate(self, staged: StagedChange) -> None:
        self._gate(staged.command, staged.owning_table)
        if staged.instance != self.settings.servicenow_instance_url:
            raise CapabilityError(
                "The connection changed. Create a new preview.",
                "PREVIEW_CONNECTION_CHANGED",
            )

    async def preview(self, command: ChangeCommand) -> CapabilityResult:
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
                elif isinstance(command, CatalogCommand):
                    summary, expected = await catalog.preview(command, client)
                    table = (
                        summary["target_table"]
                        if command.action == "submit_producer"
                        else catalog.target_table(command)
                    )
                else:
                    raise CapabilityError(
                        "This change family is not supported.", "CHANGE_DISABLED"
                    )
        self._gate(command, table)
        token = await self.store.create(
            StagedChange(
                command=command,
                instance=self.settings.servicenow_instance_url,
                authorization_generation=generation,
                fingerprint=expected,
                owning_table=table,
            ),
        )
        limitations = [
            "A preview token does not prove human approval. Apply rechecks policy and detectable state changes.",
            "State checks and the upstream change are not atomic.",
        ]
        if isinstance(command, CatalogCommand):
            limitations.extend(
                [
                    "Preview does not execute producer scripts or guide rules.",
                    "Preview cannot predict fulfillment, prices, routing, or all server-side validation.",
                ],
            )
        data = ChangePreview(
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
            require_change_policy(lambda: self._apply_gate(staged)),
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
                elif isinstance(command, CatalogCommand):
                    value = await catalog.apply(command, staged.fingerprint, client)
                else:
                    raise CapabilityError(
                        "This change family is not supported.", "CHANGE_DISABLED"
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
                "Change response did not establish success. Remote outcome is unknown. Check ServiceNow before "
                "another preview.",
                "UNKNOWN_OUTCOME",
            )
        return CapabilityResult(data=value)
