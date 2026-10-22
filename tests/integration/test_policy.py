"""Integration tests for policy enforcement against a live ServiceNow instance."""

import pytest

from servicenow_mcp.errors import PolicyError
from servicenow_mcp.policy import check_table_access

pytestmark = pytest.mark.integration


class TestPolicy:
    """Test policy enforcement with real config."""

    def test_denied_table_raises_policy_error(self) -> None:
        """Accessing a denied table must raise PolicyError."""
        with pytest.raises(PolicyError):
            check_table_access("sys_user_has_password")
