"""Register the unified ``record_write`` and ``record_apply`` MCP tools."""

from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.state import PreviewTokenStore
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools._record_write_mutations import apply_preview_payload, run_record_write
from servicenow_mcp.tools._record_write_payload import prepare_write_payload
from servicenow_mcp.tools._record_write_preview import RecordWritePreviewManager
from servicenow_mcp.tools._record_write_validation import validate_write_request


TOOL_NAMES: list[str] = ["record_write", "record_apply"]


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``record_write`` and ``record_apply`` tools."""
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))
    if dictionary is None:
        dictionary = DictionaryRegistry(settings, auth_provider, client_factory)
    dict_registry = dictionary
    previews = RecordWritePreviewManager(PreviewTokenStore())

    @mcp.tool()
    @tool_handler
    async def record_write(
        action: str,
        table: str = "",
        sys_id: str = "",
        data: str = "",
        preview: bool = True,
    ) -> str:
        """Create, update, or delete a record. Defaults to preview mode.

        Supply all field values, including complete script or markup strings,
        in ``data``. Omitted fields stay unchanged on update. Dictionary
        metadata identifies supplied XML fields, including inherited fields;
        malformed XML is rejected before preview creation or mutation.
        Creates also check inherited mandatory fields, with child declarations
        taking precedence. Metadata request errors block writes.

        Args:
            action: 'create' | 'update' | 'delete'.
            table: Target table. Required.
            sys_id: Required for 'update' and 'delete'.
            data: JSON string mapping field names to values, including any
                script fields. Required for 'create' and 'update'. Maximum
                256 KiB of UTF-8 JSON, including escaping and field names.
            preview: When True (default) returns a preview_token; caller
                invokes record_apply to commit. When False, write commits
                immediately.
        """
        request = validate_write_request(action, table, sys_id, data, preview, settings)
        if isinstance(request, str):
            return request

        parsed_data = await prepare_write_payload(request, dict_registry)
        if isinstance(parsed_data, str):
            return parsed_data

        async with client_factory() as client:
            return await run_record_write(client, request, parsed_data, previews, dict_registry)

    @mcp.tool()
    @tool_handler
    async def record_apply(preview_token: str) -> str:
        """Commit a previously previewed write. Single-use token.

        Args:
            preview_token: The token returned by ``record_write`` in preview
                mode. Single-use - consumed on success or failure.
        """
        payload = await previews.consume_for_apply(preview_token, settings)
        if isinstance(payload, str):
            return payload

        async with client_factory() as client:
            return await apply_preview_payload(client, payload, dict_registry)
