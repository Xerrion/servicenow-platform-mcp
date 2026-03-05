"""Tests for policy engine."""

import logging

import pytest

from servicenow_mcp.errors import PolicyError, QuerySafetyError


class TestDenyList:
    """Test table deny list enforcement."""

    def test_denied_table_raises_policy_error(self):
        """Accessing a denied table raises PolicyError."""
        from servicenow_mcp.policy import check_table_access

        with pytest.raises(PolicyError, match="denied"):
            check_table_access("sys_user_has_password")

    def test_credential_table_denied(self):
        """Credential tables are denied."""
        from servicenow_mcp.policy import check_table_access

        with pytest.raises(PolicyError):
            check_table_access("oauth_credential")

    def test_allowed_table_passes(self):
        """Non-denied tables pass without error."""
        from servicenow_mcp.policy import check_table_access

        # Should not raise
        check_table_access("incident")

    def test_allowed_table_returns_none(self):
        """check_table_access returns None for allowed tables."""
        from servicenow_mcp.policy import check_table_access

        result = check_table_access("sys_script_include")
        assert result is None

    def test_denied_table_case_insensitive(self):
        """Denied table check is case-insensitive."""
        from servicenow_mcp.policy import check_table_access

        with pytest.raises(PolicyError, match="denied"):
            check_table_access("SYS_USER_HAS_PASSWORD")

    def test_denied_table_mixed_case(self):
        """Denied table check handles mixed case."""
        from servicenow_mcp.policy import check_table_access

        with pytest.raises(PolicyError, match="denied"):
            check_table_access("Oauth_Credential")


class TestSensitiveFieldMasking:
    """Test sensitive field masking."""

    def test_password_field_masked(self):
        """Password fields are masked."""
        from servicenow_mcp.policy import mask_sensitive_fields

        record = {"sys_id": "123", "password": "secret123", "name": "admin"}
        masked = mask_sensitive_fields(record)

        assert masked["password"] == "***MASKED***"
        assert masked["name"] == "admin"
        assert masked["sys_id"] == "123"

    def test_token_field_masked(self):
        """Token fields are masked."""
        from servicenow_mcp.policy import mask_sensitive_fields

        record = {"token": "abc123", "name": "test"}
        masked = mask_sensitive_fields(record)

        assert masked["token"] == "***MASKED***"

    def test_secret_field_masked(self):
        """Fields containing 'secret' are masked."""
        from servicenow_mcp.policy import mask_sensitive_fields

        record = {"client_secret": "xyz", "name": "test"}
        masked = mask_sensitive_fields(record)

        assert masked["client_secret"] == "***MASKED***"

    def test_non_sensitive_fields_unchanged(self):
        """Non-sensitive fields are not modified."""
        from servicenow_mcp.policy import mask_sensitive_fields

        record = {
            "sys_id": "123",
            "short_description": "Test",
            "number": "INC001",
        }
        masked = mask_sensitive_fields(record)

        assert masked == record

    def test_original_record_not_mutated(self):
        """Original record dict is not modified."""
        from servicenow_mcp.policy import mask_sensitive_fields

        record = {"password": "secret123"}
        mask_sensitive_fields(record)

        assert record["password"] == "secret123"


class TestAuditEntryMasking:
    """Test audit-style entry masking via mask_audit_entry."""

    def test_masks_sensitive_fieldname_values(self):
        """Masks oldvalue/newvalue when fieldname is sensitive."""
        from servicenow_mcp.policy import MASK_VALUE, mask_audit_entry

        entry = {
            "sys_id": "a1",
            "fieldname": "password",
            "oldvalue": "old_secret",
            "newvalue": "new_secret",
            "user": "admin",
        }
        masked = mask_audit_entry(entry)

        assert masked["oldvalue"] == MASK_VALUE
        assert masked["newvalue"] == MASK_VALUE
        assert masked["user"] == "admin"
        assert masked["fieldname"] == "password"

    def test_masks_token_fieldname(self):
        """Masks values when fieldname contains 'token'."""
        from servicenow_mcp.policy import MASK_VALUE, mask_audit_entry

        entry = {
            "fieldname": "api_token",
            "oldvalue": "",
            "newvalue": "tok_abc123",
        }
        masked = mask_audit_entry(entry)

        assert masked["newvalue"] == MASK_VALUE

    def test_masks_using_field_key(self):
        """Supports 'field' as an alternative key to 'fieldname'."""
        from servicenow_mcp.policy import MASK_VALUE, mask_audit_entry

        entry = {
            "field": "credential",
            "old_value": "cred_old",
            "new_value": "cred_new",
        }
        masked = mask_audit_entry(entry)

        assert masked["old_value"] == MASK_VALUE
        assert masked["new_value"] == MASK_VALUE

    def test_non_sensitive_fieldname_unchanged(self):
        """Non-sensitive fieldnames leave values untouched."""
        from servicenow_mcp.policy import mask_audit_entry

        entry = {
            "fieldname": "state",
            "oldvalue": "1",
            "newvalue": "2",
        }
        masked = mask_audit_entry(entry)

        assert masked["oldvalue"] == "1"
        assert masked["newvalue"] == "2"

    def test_original_entry_not_mutated(self):
        """Original entry dict is not modified."""
        from servicenow_mcp.policy import mask_audit_entry

        entry = {
            "fieldname": "password",
            "oldvalue": "secret",
            "newvalue": "new_secret",
        }
        mask_audit_entry(entry)

        assert entry["oldvalue"] == "secret"
        assert entry["newvalue"] == "new_secret"

    def test_no_fieldname_key_leaves_values_unchanged(self):
        """Entry without fieldname or field key leaves values unchanged."""
        from servicenow_mcp.policy import mask_audit_entry

        entry = {
            "oldvalue": "something",
            "newvalue": "else",
        }
        masked = mask_audit_entry(entry)

        assert masked["oldvalue"] == "something"
        assert masked["newvalue"] == "else"


class TestQuerySafety:
    """Test query safety enforcement."""

    def test_limit_capped_at_max(self, settings):
        """Limit is capped at max_row_limit."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety("incident", "active=true", limit=500, settings=settings)
        assert result["limit"] <= settings.max_row_limit

    def test_default_limit_applied(self, settings):
        """Default limit is applied when none specified."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety("incident", "active=true", limit=None, settings=settings)
        assert result["limit"] == settings.max_row_limit

    def test_large_table_requires_date_filter(self, settings):
        """Large tables require a date-bounded filter."""
        from servicenow_mcp.policy import enforce_query_safety

        with pytest.raises(QuerySafetyError, match="date"):
            enforce_query_safety("syslog", "level=error", limit=50, settings=settings)

    def test_large_table_with_date_filter_passes(self, settings):
        """Large tables with date filter pass."""
        from servicenow_mcp.policy import enforce_query_safety

        # Should not raise
        result = enforce_query_safety(
            "syslog",
            "sys_created_on>=2024-01-01^level=error",
            limit=50,
            settings=settings,
        )
        assert result["limit"] == 50

    def test_normal_table_no_date_required(self, settings):
        """Normal tables do not require date filters."""
        from servicenow_mcp.policy import enforce_query_safety

        # Should not raise
        result = enforce_query_safety("incident", "active=true", limit=50, settings=settings)
        assert result["limit"] == 50

    def test_date_filter_bypass_substring_in_value(self, settings):
        """Date field appearing only as a value (not a filter field) should not pass."""
        from servicenow_mcp.policy import enforce_query_safety

        with pytest.raises(QuerySafetyError, match="date"):
            enforce_query_safety(
                "syslog",
                "description=sys_created_on",
                limit=50,
                settings=settings,
            )

    def test_date_filter_with_operator_passes(self, settings):
        """Date field with a comparison operator passes the check."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety(
            "syslog",
            "sys_created_on>=2024-01-01",
            limit=50,
            settings=settings,
        )
        assert result["limit"] == 50

    def test_date_filter_with_gs_function_passes(self, settings):
        """Date field with gs.*Ago function passes the check."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety(
            "syslog",
            "sys_created_on>=javascript:gs.hoursAgoStart(24)",
            limit=50,
            settings=settings,
        )
        assert result["limit"] == 50

    def test_limit_zero_floored_to_one(self, settings):
        """Limit of 0 is floored to 1."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety("incident", "active=true", limit=0, settings=settings)
        assert result["limit"] == 1

    def test_limit_negative_floored_to_one(self, settings):
        """Negative limit is floored to 1."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety("incident", "active=true", limit=-5, settings=settings)
        assert result["limit"] == 1


class TestWriteGating:
    """Test write operation gating."""

    def test_write_allowed_in_dev(self, settings):
        """Writes are allowed in dev environment."""
        from servicenow_mcp.policy import can_write

        assert can_write("incident", settings) is True

    def test_write_blocked_in_prod(self, prod_settings):
        """Writes are blocked in production by default."""
        from servicenow_mcp.policy import can_write

        assert can_write("incident", prod_settings) is False

    def test_write_allowed_in_prod_with_override(self, prod_settings):
        """Writes can be overridden in production."""
        from servicenow_mcp.policy import can_write

        assert can_write("incident", prod_settings, override=True) is True

    def test_write_to_denied_table_blocked(self, settings):
        """Writes to denied tables are always blocked."""
        from servicenow_mcp.policy import can_write

        assert can_write("sys_user_has_password", settings) is False

    def test_write_to_denied_table_case_insensitive(self, settings):
        """Write deny-list check is case-insensitive."""
        from servicenow_mcp.policy import can_write

        assert can_write("SYS_USER_HAS_PASSWORD", settings) is False

    def test_write_blocked_denied_table_logs_warning(self, settings, caplog):
        """Write blocked by deny list logs a warning."""
        from servicenow_mcp.policy import can_write

        with caplog.at_level(logging.WARNING, logger="servicenow_mcp.policy"):
            can_write("sys_user_has_password", settings)

        assert any("restricted table" in record.message for record in caplog.records)

    def test_write_blocked_in_prod_logs_warning(self, prod_settings, caplog):
        """Write blocked in production logs a warning."""
        from servicenow_mcp.policy import can_write

        with caplog.at_level(logging.WARNING, logger="servicenow_mcp.policy"):
            can_write("incident", prod_settings)

        assert any("production environment" in record.message for record in caplog.records)
