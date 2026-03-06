"""Tests for shared domain tool helpers."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from toon_format import decode as toon_decode

from servicenow_mcp.tools.domains._helpers import (
    fetch_record_by_number,
    lookup_record_by_number,
    parse_field_list,
    resolve_state,
    validate_int_range,
    validate_no_empty_changes,
    validate_number_prefix,
    validate_required_string,
)


CID = "test-correlation-id"


class TestValidateNumberPrefix:
    def test_valid_prefix_returns_none(self) -> None:
        assert validate_number_prefix("INC0010001", "INC", "incident", CID) is None

    def test_valid_prefix_case_insensitive(self) -> None:
        assert validate_number_prefix("inc0010001", "INC", "incident", CID) is None

    def test_invalid_prefix_returns_error(self) -> None:
        raw = validate_number_prefix("PRB001", "INC", "incident", CID)
        assert raw is not None
        result = toon_decode(raw)
        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert "Must start with INC prefix" in result["error"]["message"]

    def test_error_includes_entity_label(self) -> None:
        raw = validate_number_prefix("INC001", "CHG", "change request", CID)
        assert raw is not None
        result = toon_decode(raw)
        assert isinstance(result, dict)
        assert "Invalid change request number" in result["error"]["message"]


class TestLookupRecordByNumber:
    @pytest.mark.asyncio()
    async def test_found_returns_sys_id(self) -> None:
        client = AsyncMock()
        client.query_records.return_value = {"records": [{"sys_id": "abc123"}]}
        sys_id, error = await lookup_record_by_number(client, "incident", "INC001", "Incident", CID)
        assert sys_id == "abc123"
        assert error is None

    @pytest.mark.asyncio()
    async def test_not_found_returns_error(self) -> None:
        client = AsyncMock()
        client.query_records.return_value = {"records": []}
        sys_id, error = await lookup_record_by_number(client, "incident", "INC001", "Incident", CID)
        assert sys_id == ""
        assert error is not None
        result = toon_decode(error)
        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert "Incident INC001 not found" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_number_uppercased_in_query(self) -> None:
        client = AsyncMock()
        client.query_records.return_value = {"records": [{"sys_id": "xyz"}]}
        await lookup_record_by_number(client, "incident", "inc001", "Incident", CID)
        call_kwargs = client.query_records.call_args[1]
        assert "INC001" in call_kwargs["query"]


class TestFetchRecordByNumber:
    @pytest.mark.asyncio()
    async def test_found_returns_masked_record(self) -> None:
        client = AsyncMock()
        client.query_records.return_value = {"records": [{"sys_id": "abc", "short_description": "test"}]}
        raw = await fetch_record_by_number(client, "incident", "INC001", "Incident", CID)
        result = toon_decode(raw)
        assert isinstance(result, dict)
        assert result["status"] == "success"
        assert result["data"]["short_description"] == "test"

    @pytest.mark.asyncio()
    async def test_not_found_returns_error(self) -> None:
        client = AsyncMock()
        client.query_records.return_value = {"records": []}
        raw = await fetch_record_by_number(client, "incident", "INC001", "Incident", CID)
        result = toon_decode(raw)
        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert "Incident INC001 not found" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_uses_display_values(self) -> None:
        client = AsyncMock()
        client.query_records.return_value = {"records": [{"sys_id": "abc"}]}
        await fetch_record_by_number(client, "incident", "INC001", "Incident", CID)
        call_kwargs = client.query_records.call_args[1]
        assert call_kwargs["display_values"] is True


class TestValidateIntRange:
    def test_in_range_returns_none(self) -> None:
        assert validate_int_range(2, "urgency", 1, 4, CID) is None

    def test_at_min_returns_none(self) -> None:
        assert validate_int_range(1, "urgency", 1, 4, CID) is None

    def test_at_max_returns_none(self) -> None:
        assert validate_int_range(4, "urgency", 1, 4, CID) is None

    def test_below_min_returns_error(self) -> None:
        raw = validate_int_range(0, "urgency", 1, 4, CID)
        assert raw is not None
        result = toon_decode(raw)
        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert "urgency must be between 1 and 4, got 0" in result["error"]["message"]

    def test_above_max_returns_error(self) -> None:
        raw = validate_int_range(5, "impact", 1, 4, CID)
        assert raw is not None
        result = toon_decode(raw)
        assert isinstance(result, dict)
        assert "impact must be between 1 and 4, got 5" in result["error"]["message"]


class TestValidateRequiredString:
    def test_valid_returns_none(self) -> None:
        assert validate_required_string("hello", "short_description", CID) is None

    def test_empty_returns_error(self) -> None:
        raw = validate_required_string("", "short_description", CID)
        assert raw is not None
        result = toon_decode(raw)
        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert "short_description is required and cannot be empty" in result["error"]["message"]

    def test_whitespace_only_returns_error(self) -> None:
        raw = validate_required_string("   ", "close_code", CID)
        assert raw is not None
        result = toon_decode(raw)
        assert isinstance(result, dict)
        assert "close_code is required and cannot be empty" in result["error"]["message"]

    def test_none_like_empty(self) -> None:
        # In practice callers pass str, but test the falsy path
        raw = validate_required_string("", "cause_notes", CID)
        assert raw is not None


class TestValidateNoEmptyChanges:
    def test_non_empty_returns_none(self) -> None:
        assert validate_no_empty_changes({"state": "2"}, CID) is None

    def test_empty_returns_error(self) -> None:
        raw = validate_no_empty_changes({}, CID)
        assert raw is not None
        result = toon_decode(raw)
        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert "No fields to update provided" in result["error"]["message"]


class TestParseFieldList:
    def test_comma_separated(self) -> None:
        assert parse_field_list("number,state,priority") == ["number", "state", "priority"]

    def test_strips_whitespace(self) -> None:
        assert parse_field_list(" number , state ") == ["number", "state"]

    def test_empty_string_returns_none(self) -> None:
        assert parse_field_list("") is None

    def test_only_commas_returns_empty_list(self) -> None:
        # This matches current behavior: [f.strip() for f in ",,,".split(",") if f.strip()] == []
        assert parse_field_list(",,,") == []


class TestResolveState:
    @pytest.mark.asyncio()
    async def test_with_choices_resolves(self) -> None:
        choices = MagicMock()
        choices.resolve = AsyncMock(return_value="6")
        result = await resolve_state("incident", "resolved", choices)
        assert result == "6"
        choices.resolve.assert_called_once_with("incident", "state", "resolved")

    @pytest.mark.asyncio()
    async def test_without_choices_returns_passthrough(self) -> None:
        result = await resolve_state("incident", "resolved", None)
        assert result == "resolved"

    @pytest.mark.asyncio()
    async def test_lowercases_state(self) -> None:
        choices = MagicMock()
        choices.resolve = AsyncMock(return_value="1")
        await resolve_state("incident", "Open", choices)
        choices.resolve.assert_called_once_with("incident", "state", "open")
