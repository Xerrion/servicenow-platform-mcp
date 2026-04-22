"""Tests for policy engine."""

import logging

import pytest

from servicenow_mcp.config import Settings
from servicenow_mcp.errors import PolicyError, QuerySafetyError


class TestDenyList:
    """Test table deny list enforcement."""

    def test_denied_table_raises_policy_error(self) -> None:
        """Accessing a denied table raises PolicyError."""
        from servicenow_mcp.policy import check_table_access

        with pytest.raises(PolicyError, match="denied"):
            check_table_access("sys_user_has_password")

    def test_credential_table_denied(self) -> None:
        """Credential tables are denied."""
        from servicenow_mcp.policy import check_table_access

        with pytest.raises(PolicyError):
            check_table_access("oauth_credential")

    def test_allowed_table_passes(self) -> None:
        """Non-denied tables pass without error."""
        from servicenow_mcp.policy import check_table_access

        # Should not raise
        check_table_access("incident")

    def test_denied_table_case_insensitive(self) -> None:
        """Denied table check is case-insensitive."""
        from servicenow_mcp.policy import check_table_access

        with pytest.raises(PolicyError, match="denied"):
            check_table_access("SYS_USER_HAS_PASSWORD")

    def test_denied_table_mixed_case(self) -> None:
        """Denied table check handles mixed case."""
        from servicenow_mcp.policy import check_table_access

        with pytest.raises(PolicyError, match="denied"):
            check_table_access("Oauth_Credential")

    @pytest.mark.parametrize(
        "table",
        [
            # Baseline denied tables
            "sys_user_has_password",
            "oauth_credential",
            "oauth_entity",
            "sys_certificate",
            "sys_ssh_key",
            "sys_credentials",
            "discovery_credentials",
            "sys_user_token",
            # Phase 4 additions - broadened to cover identity, configuration,
            # ACL, auth, data-source, workflow-variable, scripted-email, and
            # MID agent tables that can disclose secrets or sensitive posture.
            "sys_user",
            "sys_properties",
            "sys_security_acl",
            "sys_auth_profile",
            "sys_data_source",
            "sys_variable_value",
            "sys_script_email",
            "ecc_agent",
            "ecc_queue",
        ],
    )
    def test_every_denied_table_is_gated(self, table: str) -> None:
        """Each DENIED_TABLES entry is present and rejected by check_table_access.

        This is the single source of truth for the deny list - any future
        removal must be an explicit decision, not a silent regression.
        """
        from servicenow_mcp.policy import DENIED_TABLES, check_table_access

        assert table in DENIED_TABLES
        with pytest.raises(PolicyError, match="denied"):
            check_table_access(table)


class TestSensitiveFieldMasking:
    """Test sensitive field masking."""

    def test_password_field_masked(self) -> None:
        """Password fields are masked."""
        from servicenow_mcp.policy import mask_sensitive_fields

        record = {"sys_id": "123", "password": "secret123", "name": "admin"}
        masked = mask_sensitive_fields(record)

        assert masked["password"] == "***MASKED***"
        assert masked["name"] == "admin"
        assert masked["sys_id"] == "123"

    def test_token_field_masked(self) -> None:
        """Token fields are masked."""
        from servicenow_mcp.policy import mask_sensitive_fields

        record = {"token": "abc123", "name": "test"}
        masked = mask_sensitive_fields(record)

        assert masked["token"] == "***MASKED***"

    def test_secret_field_masked(self) -> None:
        """Fields containing 'secret' are masked."""
        from servicenow_mcp.policy import mask_sensitive_fields

        record = {"client_secret": "xyz", "name": "test"}
        masked = mask_sensitive_fields(record)

        assert masked["client_secret"] == "***MASKED***"

    def test_non_sensitive_fields_unchanged(self) -> None:
        """Non-sensitive fields are not modified."""
        from servicenow_mcp.policy import mask_sensitive_fields

        record = {
            "sys_id": "123",
            "short_description": "Test",
            "number": "INC001",
        }
        masked = mask_sensitive_fields(record)

        assert masked == record

    def test_original_record_not_mutated(self) -> None:
        """Original record dict is not modified."""
        from servicenow_mcp.policy import mask_sensitive_fields

        record = {"password": "secret123"}
        mask_sensitive_fields(record)

        assert record["password"] == "secret123"


class TestAuditEntryMasking:
    """Test audit-style entry masking via mask_audit_entry."""

    def test_masks_sensitive_fieldname_values(self) -> None:
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

    def test_masks_token_fieldname(self) -> None:
        """Masks values when fieldname contains 'token'."""
        from servicenow_mcp.policy import MASK_VALUE, mask_audit_entry

        entry = {
            "fieldname": "api_token",
            "oldvalue": "",
            "newvalue": "tok_abc123",
        }
        masked = mask_audit_entry(entry)

        assert masked["newvalue"] == MASK_VALUE

    def test_masks_using_field_key(self) -> None:
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

    def test_non_sensitive_fieldname_unchanged(self) -> None:
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

    def test_original_entry_not_mutated(self) -> None:
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

    def test_no_fieldname_key_leaves_values_unchanged(self) -> None:
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

    def test_limit_capped_at_max(self, settings: Settings) -> None:
        """Limit is capped at max_row_limit."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety("incident", "active=true", limit=500, settings=settings)
        assert result["limit"] <= settings.max_row_limit

    def test_default_limit_applied(self, settings: Settings) -> None:
        """Default limit is applied when none specified."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety("incident", "active=true", limit=None, settings=settings)
        assert result["limit"] == settings.max_row_limit

    def test_large_table_requires_date_filter(self, settings: Settings) -> None:
        """Large tables require a date-bounded filter."""
        from servicenow_mcp.policy import enforce_query_safety

        with pytest.raises(QuerySafetyError, match="date"):
            enforce_query_safety("syslog", "level=error", limit=50, settings=settings)

    def test_large_table_with_date_filter_passes(self, settings: Settings) -> None:
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

    def test_normal_table_no_date_required(self, settings: Settings) -> None:
        """Normal tables do not require date filters."""
        from servicenow_mcp.policy import enforce_query_safety

        # Should not raise
        result = enforce_query_safety("incident", "active=true", limit=50, settings=settings)
        assert result["limit"] == 50

    def test_date_filter_bypass_substring_in_value(self, settings: Settings) -> None:
        """Date field appearing only as a value (not a filter field) should not pass."""
        from servicenow_mcp.policy import enforce_query_safety

        with pytest.raises(QuerySafetyError, match="date"):
            enforce_query_safety(
                "syslog",
                "description=sys_created_on",
                limit=50,
                settings=settings,
            )

    def test_date_filter_with_operator_passes(self, settings: Settings) -> None:
        """Date field with a comparison operator passes the check."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety(
            "syslog",
            "sys_created_on>=2024-01-01",
            limit=50,
            settings=settings,
        )
        assert result["limit"] == 50

    def test_date_filter_with_gs_function_passes(self, settings: Settings) -> None:
        """Date field with gs.*Ago function passes the check."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety(
            "syslog",
            "sys_created_on>=javascript:gs.hoursAgoStart(24)",
            limit=50,
            settings=settings,
        )
        assert result["limit"] == 50

    def test_limit_zero_floored_to_one(self, settings: Settings) -> None:
        """Limit of 0 is floored to 1."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety("incident", "active=true", limit=0, settings=settings)
        assert result["limit"] == 1

    def test_limit_negative_floored_to_one(self, settings: Settings) -> None:
        """Negative limit is floored to 1."""
        from servicenow_mcp.policy import enforce_query_safety

        result = enforce_query_safety("incident", "active=true", limit=-5, settings=settings)
        assert result["limit"] == 1


class TestWriteGating:
    """Test write operation gating."""

    def test_write_allowed_in_dev(self, settings: Settings) -> None:
        """Writes are allowed in dev environment."""
        from servicenow_mcp.policy import can_write

        assert can_write("incident", settings) is True

    def test_write_blocked_in_prod(self, prod_settings: Settings) -> None:
        """Writes are blocked in production by default."""
        from servicenow_mcp.policy import can_write

        assert can_write("incident", prod_settings) is False

    def test_write_allowed_in_prod_with_override(self, prod_settings: Settings) -> None:
        """Writes can be overridden in production."""
        from servicenow_mcp.policy import can_write

        assert can_write("incident", prod_settings, override=True) is True

    def test_write_to_denied_table_blocked(self, settings: Settings) -> None:
        """Writes to denied tables are always blocked."""
        from servicenow_mcp.policy import can_write

        assert can_write("sys_user_has_password", settings) is False

    def test_write_to_denied_table_case_insensitive(self, settings: Settings) -> None:
        """Write deny-list check is case-insensitive."""
        from servicenow_mcp.policy import can_write

        assert can_write("SYS_USER_HAS_PASSWORD", settings) is False

    def test_write_blocked_denied_table_logs_warning(
        self, settings: Settings, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Write blocked by deny list logs a warning."""
        from servicenow_mcp.policy import can_write

        with caplog.at_level(logging.WARNING, logger="servicenow_mcp.policy"):
            can_write("sys_user_has_password", settings)

        assert any("restricted table" in record.message for record in caplog.records)

    def test_write_blocked_in_prod_logs_warning(
        self, prod_settings: Settings, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Write blocked in production logs a warning."""
        from servicenow_mcp.policy import can_write

        with caplog.at_level(logging.WARNING, logger="servicenow_mcp.policy"):
            can_write("incident", prod_settings)

        assert any("production environment" in record.message for record in caplog.records)
