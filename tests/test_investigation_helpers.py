"""Tests for investigation_helpers — shared utilities for investigation modules."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from servicenow_mcp.investigation_helpers import (
    build_investigation_result,
    fetch_and_explain,
    parse_element_id,
    parse_int_param,
)


# ── parse_int_param ──────────────────────────────────────────────────────


class TestParseIntParam:
    """Tests for parse_int_param()."""

    def test_returns_parsed_integer_when_present(self) -> None:
        """Returns the integer value when key exists and is numeric."""
        assert parse_int_param({"limit": "42"}, "limit", 20) == 42

    def test_returns_default_when_key_missing(self) -> None:
        """Falls back to default when the key is absent."""
        assert parse_int_param({}, "limit", 20) == 20

    def test_returns_default_for_non_numeric_string(self) -> None:
        """Falls back to default when the value cannot be parsed as int."""
        assert parse_int_param({"limit": "abc"}, "limit", 20) == 20

    def test_returns_default_when_value_is_none(self) -> None:
        """Falls back to default when the value is explicitly None."""
        assert parse_int_param({"limit": None}, "limit", 20) == 20

    def test_handles_zero(self) -> None:
        """Correctly parses zero as a valid integer."""
        assert parse_int_param({"limit": "0"}, "limit", 20) == 0

    def test_handles_negative_values(self) -> None:
        """Correctly parses negative integers."""
        assert parse_int_param({"limit": "-5"}, "limit", 20) == -5

    def test_handles_integer_value_directly(self) -> None:
        """Handles an already-integer value without conversion issues."""
        assert parse_int_param({"limit": 10}, "limit", 20) == 10


# ── parse_element_id ─────────────────────────────────────────────────────


class TestParseElementId:
    """Tests for parse_element_id()."""

    def test_parses_valid_table_sys_id_format(self) -> None:
        """Splits a well-formed 'table:sys_id' into components."""
        table, sys_id = parse_element_id("flow_context:fc001")
        assert table == "flow_context"
        assert sys_id == "fc001"

    def test_raises_for_missing_colon(self) -> None:
        """Raises ValueError when the element_id has no colon separator."""
        with pytest.raises(ValueError, match="expected 'table:sys_id'"):
            parse_element_id("invalid_no_colon")

    def test_splits_on_first_colon_only(self) -> None:
        """When sys_id contains colons, splits only on the first one."""
        table, sys_id = parse_element_id("syslog:abc:def:ghi")
        assert table == "syslog"
        assert sys_id == "abc:def:ghi"

    def test_validates_against_allowed_tables(self) -> None:
        """Returns components when the table is in the allowed set."""
        allowed = {"incident", "syslog"}
        table, sys_id = parse_element_id("syslog:log001", allowed_tables=allowed)
        assert table == "syslog"
        assert sys_id == "log001"

    def test_raises_when_table_not_in_allowed_set(self) -> None:
        """Raises ValueError when the table is not in the allowed set."""
        allowed = {"incident", "syslog"}
        with pytest.raises(ValueError, match="not in the allowed tables"):
            parse_element_id("sys_user:abc123", allowed_tables=allowed)

    def test_skips_validation_when_allowed_tables_is_none(self) -> None:
        """Accepts any table when allowed_tables is None."""
        table, sys_id = parse_element_id("any_table:any_id", allowed_tables=None)
        assert table == "any_table"
        assert sys_id == "any_id"

    def test_raises_for_empty_string(self) -> None:
        """Raises ValueError for an empty element_id string."""
        with pytest.raises(ValueError, match="expected 'table:sys_id'"):
            parse_element_id("")


# ── build_investigation_result ───────────────────────────────────────────


class TestBuildInvestigationResult:
    """Tests for build_investigation_result()."""

    def test_builds_correct_envelope_with_findings(self) -> None:
        """Creates the standard envelope structure with findings."""
        findings = [{"category": "stuck_flow", "element_id": "flow_context:fc001"}]
        result = build_investigation_result("stale_automations", findings)

        assert result["investigation"] == "stale_automations"
        assert result["finding_count"] == 1
        assert result["findings"] is findings

    def test_sets_finding_count_from_list_length(self) -> None:
        """finding_count reflects the actual length of the findings list."""
        findings = [{"a": 1}, {"b": 2}, {"c": 3}]
        result = build_investigation_result("test", findings)
        assert result["finding_count"] == 3

    def test_includes_extra_kwargs_in_result(self) -> None:
        """Extra keyword arguments are merged into the envelope."""
        result = build_investigation_result(
            "error_analysis",
            [],
            params={"hours": 24},
            total_errors=50,
        )
        assert result["params"] == {"hours": 24}
        assert result["total_errors"] == 50

    def test_works_with_empty_findings(self) -> None:
        """Returns zero count for an empty findings list."""
        result = build_investigation_result("clean", [])
        assert result["finding_count"] == 0
        assert result["findings"] == []


# ── fetch_and_explain ────────────────────────────────────────────────────


class TestFetchAndExplain:
    """Tests for fetch_and_explain()."""

    @pytest.mark.asyncio()
    @patch("servicenow_mcp.investigation_helpers.check_table_access")
    @patch("servicenow_mcp.investigation_helpers.mask_sensitive_fields")
    @patch("servicenow_mcp.investigation_helpers.validate_identifier")
    async def test_returns_correct_structure(
        self,
        _mock_validate: AsyncMock,
        mock_mask: AsyncMock,
        _mock_check: AsyncMock,
    ) -> None:
        """Returns dict with element, explanation, and record keys."""
        test_record = {
            "sys_id": "fc001",
            "name": "Test Flow",
            "state": "IN_PROGRESS",
        }
        mock_mask.return_value = test_record

        client = AsyncMock()
        client.get_record.return_value = test_record

        def build_explanation(_table: str, _sys_id: str, record: dict[str, Any]) -> list[str]:
            return [f"Flow '{record['name']}' is stuck."]

        result = await fetch_and_explain(
            client=client,
            element_id="flow_context:fc001",
            allowed_tables={"flow_context"},
            build_explanation=build_explanation,
        )

        assert result["element"] == "flow_context:fc001"
        assert result["explanation"] == "Flow 'Test Flow' is stuck."
        assert result["record"] is test_record

    @pytest.mark.asyncio()
    @patch("servicenow_mcp.investigation_helpers.check_table_access")
    @patch("servicenow_mcp.investigation_helpers.mask_sensitive_fields")
    @patch("servicenow_mcp.investigation_helpers.validate_identifier")
    async def test_calls_validate_identifier_with_sys_id(
        self,
        mock_validate: AsyncMock,
        mock_mask: AsyncMock,
        _mock_check: AsyncMock,
    ) -> None:
        """Calls validate_identifier with the parsed sys_id."""
        mock_mask.return_value = {"sys_id": "abc123"}
        client = AsyncMock()
        client.get_record.return_value = {"sys_id": "abc123"}

        await fetch_and_explain(
            client=client,
            element_id="syslog:abc123",
            allowed_tables=None,
            build_explanation=lambda t, s, r: ["ok"],
        )

        mock_validate.assert_called_once_with("abc123")

    @pytest.mark.asyncio()
    @patch("servicenow_mcp.investigation_helpers.check_table_access")
    @patch("servicenow_mcp.investigation_helpers.mask_sensitive_fields")
    @patch("servicenow_mcp.investigation_helpers.validate_identifier")
    async def test_calls_check_table_access_with_table(
        self,
        _mock_validate: AsyncMock,
        mock_mask: AsyncMock,
        mock_check: AsyncMock,
    ) -> None:
        """Calls check_table_access with the parsed table name."""
        mock_mask.return_value = {"sys_id": "x"}
        client = AsyncMock()
        client.get_record.return_value = {"sys_id": "x"}

        await fetch_and_explain(
            client=client,
            element_id="incident:x",
            allowed_tables=None,
            build_explanation=lambda t, s, r: ["ok"],
        )

        mock_check.assert_called_once_with("incident")

    @pytest.mark.asyncio()
    @patch("servicenow_mcp.investigation_helpers.check_table_access")
    @patch("servicenow_mcp.investigation_helpers.mask_sensitive_fields")
    @patch("servicenow_mcp.investigation_helpers.validate_identifier")
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
    @patch("servicenow_mcp.investigation_helpers.check_table_access")
    @patch("servicenow_mcp.investigation_helpers.mask_sensitive_fields")
    @patch("servicenow_mcp.investigation_helpers.validate_identifier")
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

        def capture_explanation(_table: str, _sys_id: str, record: dict[str, Any]) -> list[str]:
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
    @patch("servicenow_mcp.investigation_helpers.check_table_access")
    @patch("servicenow_mcp.investigation_helpers.mask_sensitive_fields")
    @patch("servicenow_mcp.investigation_helpers.validate_identifier")
    async def test_joins_explanation_parts_with_spaces(
        self,
        _mock_validate: AsyncMock,
        mock_mask: AsyncMock,
        _mock_check: AsyncMock,
    ) -> None:
        """Joins the explanation parts list into a single space-separated string."""
        mock_mask.return_value = {"sys_id": "x"}
        client = AsyncMock()
        client.get_record.return_value = {"sys_id": "x"}

        result = await fetch_and_explain(
            client=client,
            element_id="syslog:x",
            allowed_tables=None,
            build_explanation=lambda t, s, r: [
                "Part one.",
                "Part two.",
                "Part three.",
            ],
        )

        assert result["explanation"] == "Part one. Part two. Part three."

    @pytest.mark.asyncio()
    async def test_raises_value_error_for_invalid_element_id(self) -> None:
        """Raises ValueError when element_id is missing a colon."""
        client = AsyncMock()

        with pytest.raises(ValueError, match="expected 'table:sys_id'"):
            await fetch_and_explain(
                client=client,
                element_id="no_colon_here",
                allowed_tables=None,
                build_explanation=lambda t, s, r: ["ok"],
            )

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
