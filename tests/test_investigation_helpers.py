"""Tests for investigation_helpers — shared utilities for investigation modules."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from servicenow_mcp.capabilities.investigations.helpers import (
    fetch_and_explain,
    parse_int_param,
)

# ── parse_int_param ──────────────────────────────────────────────────────


class TestParseIntParam:
    """Tests for parse_int_param()."""

    def test_rejects_non_integer_input(self) -> None:
        from servicenow_mcp.capabilities.contracts import CapabilityError

        with pytest.raises(CapabilityError, match="must be an integer"):
            parse_int_param({"limit": "abc"}, "limit", 20)


# ── parse_element_id ─────────────────────────────────────────────────────


# ── build_investigation_result ───────────────────────────────────────────


# ── fetch_and_explain ────────────────────────────────────────────────────


class TestFetchAndExplain:
    """Tests for fetch_and_explain()."""

    @pytest.mark.asyncio()
    @patch("servicenow_mcp.capabilities.investigations.helpers.check_table_access")
    @patch("servicenow_mcp.capabilities.investigations.helpers.mask_sensitive_fields")
    @patch("servicenow_mcp.capabilities.investigations.helpers.validate_identifier")
    async def test_calls_mask_sensitive_fields_on_record(
        self,
        _mock_validate: AsyncMock,
        mock_mask: AsyncMock,
        _mock_check: AsyncMock,
    ) -> None:
        """Passes the raw record through mask_sensitive_fields."""
        raw_record = {"sys_id": "r001", "password": "secret"}
        masked_record = {"sys_id": "r001", "password": "***MASKED***"}
        mock_mask.return_value = masked_record

        client = AsyncMock()
        client.get_record.return_value = raw_record

        result = await fetch_and_explain(
            client=client,
            element_id="incident:r001",
            allowed_tables=None,
            build_explanation=lambda t, s, r: [f"Record {r['sys_id']}"],
        )

        mock_mask.assert_called_once_with(raw_record)
        assert result["record"] is masked_record

    @pytest.mark.asyncio()
    @patch("servicenow_mcp.capabilities.investigations.helpers.check_table_access")
    @patch("servicenow_mcp.capabilities.investigations.helpers.mask_sensitive_fields")
    @patch("servicenow_mcp.capabilities.investigations.helpers.validate_identifier")
    async def test_calls_build_explanation_with_masked_record(
        self,
        _mock_validate: AsyncMock,
        mock_mask: AsyncMock,
        _mock_check: AsyncMock,
    ) -> None:
        """Passes the masked record (not the raw one) to the build_explanation callback."""
        raw_record = {"sys_id": "r001", "name": "Raw"}
        masked_record = {"sys_id": "r001", "name": "Masked"}
        mock_mask.return_value = masked_record

        client = AsyncMock()
        client.get_record.return_value = raw_record

        received_records: list[dict[str, Any]] = []

        def capture_explanation(
            _table: str, _sys_id: str, record: dict[str, Any]
        ) -> list[str]:
            received_records.append(record)
            return ["explained"]

        await fetch_and_explain(
            client=client,
            element_id="incident:r001",
            allowed_tables=None,
            build_explanation=capture_explanation,
        )

        assert len(received_records) == 1
        assert received_records[0] is masked_record

    @pytest.mark.asyncio()
    async def test_raises_value_error_for_disallowed_table(self) -> None:
        """Raises ValueError when the table is not in the allowed set."""
        client = AsyncMock()

        with pytest.raises(ValueError, match="not in the allowed tables"):
            await fetch_and_explain(
                client=client,
                element_id="sys_user:abc123",
                allowed_tables={"incident", "syslog"},
                build_explanation=lambda t, s, r: ["ok"],
            )
