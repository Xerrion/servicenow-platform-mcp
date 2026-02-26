"""Tests for policy engine."""

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

        record = {"sys_id": "123", "short_description": "Test", "number": "INC001"}
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
