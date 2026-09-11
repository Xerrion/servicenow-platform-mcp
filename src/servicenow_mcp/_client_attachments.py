"""Attachment API operations."""

from typing import Any

from servicenow_mcp._client_transport import ServiceNowRequestClient
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.validation import validate_identifier, validate_sys_id


class AttachmentApiClient(ServiceNowRequestClient):
    """Implement attachment metadata and binary content operations."""

    def _attachment_url(self, sys_id: str | None = None) -> str:
        """Build an Attachment API URL."""
        base = f"{self._settings.servicenow_instance_url}/api/now/attachment"
        if sys_id is None:
            return base
        validate_sys_id(sys_id)
        return f"{base}/{sys_id}"

    def _attachment_file_url(self, sys_id: str | None = None) -> str:
        """Build an Attachment content URL."""
        return f"{self._attachment_url(sys_id)}/file"

    async def list_attachments(
        self,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str | None = None,
    ) -> dict[str, Any]:
        """List attachment metadata."""
        params = {"sysparm_limit": str(limit), "sysparm_offset": str(offset)}
        effective_query = query
        if order_by:
            order_clause = ServiceNowQuery().order_by(order_by).build()
            effective_query = f"{query}^{order_clause}" if query else order_clause
        if effective_query:
            params["sysparm_query"] = effective_query
        response = await self._ensure_client().get(
            self._attachment_url(),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_status(response)
        return {
            "records": self._extract_result(response.json()),
            "count": self._parse_total_count(response),
        }

    async def get_attachment(self, sys_id: str) -> dict[str, Any]:
        """Fetch attachment metadata by sys_id."""
        response = await self._ensure_client().get(self._attachment_url(sys_id), headers=await self._headers())
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def upload_attachment(
        self,
        table_name: str,
        table_sys_id: str,
        file_name: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        encryption_context: str | None = None,
        creation_time: str | None = None,
    ) -> dict[str, Any]:
        """Upload binary content as an attachment."""
        validate_identifier(table_name)
        validate_sys_id(table_sys_id)
        params = {"table_name": table_name, "table_sys_id": table_sys_id, "file_name": file_name}
        if encryption_context:
            params["encryption_context"] = encryption_context
        if creation_time:
            params["creation_time"] = creation_time
        headers = await self._headers()
        headers["Content-Type"] = content_type
        response = await self._ensure_client().post(
            self._attachment_file_url(),
            headers=headers,
            params=params,
            content=content,
        )
        self._raise_for_status(response)
        return self._extract_result(response.json())

    async def download_attachment(self, sys_id: str) -> bytes:
        """Download attachment content by sys_id."""
        response = await self._ensure_client().get(self._attachment_file_url(sys_id), headers=await self._headers())
        self._raise_for_status(response)
        return response.content

    async def delete_attachment(self, sys_id: str) -> bool:
        """Delete an attachment by sys_id."""
        response = await self._ensure_client().delete(self._attachment_url(sys_id), headers=await self._headers())
        self._raise_for_status(response)
        return True
