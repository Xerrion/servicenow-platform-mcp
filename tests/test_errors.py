"""Tests for the custom exception hierarchy."""

from servicenow_mcp.errors import (
    AuthError,
    ForbiddenError,
    NotFoundError,
    PolicyError,
    QuerySafetyError,
    ServerError,
    ServiceNowMCPError,
)


class TestBaseError:
    """Tests for ServiceNowMCPError base class."""

    def test_base_error(self) -> None:
        """ServiceNowMCPError stores message and status_code correctly."""
        err = ServiceNowMCPError("something broke", status_code=418)
        assert str(err) == "something broke"
        assert err.status_code == 418

    def test_base_error_default_status_code(self) -> None:
        """ServiceNowMCPError defaults status_code to None."""
        err = ServiceNowMCPError("oops")
        assert str(err) == "oops"
        assert err.status_code is None


class TestHTTPErrors:
    """Tests for HTTP-mapped error subclasses."""

    def test_auth_error_defaults(self) -> None:
        """AuthError has status_code=401 and default message."""
        err = AuthError()
        assert err.status_code == 401
        assert str(err) == "Authentication failed"

    def test_forbidden_error_defaults(self) -> None:
        """ForbiddenError has status_code=403 and default message."""
        err = ForbiddenError()
        assert err.status_code == 403
        assert str(err) == "Access forbidden"

    def test_not_found_error_defaults(self) -> None:
        """NotFoundError has status_code=404 and default message."""
        err = NotFoundError()
        assert err.status_code == 404
        assert str(err) == "Resource not found"

    def test_server_error_defaults(self) -> None:
        """ServerError has status_code=500 and default message."""
        err = ServerError()
        assert err.status_code == 500
        assert str(err) == "Internal server error"

    def test_server_error_custom_status(self) -> None:
        """ServerError accepts a custom status_code for other 5xx errors."""
        err = ServerError("Bad gateway", status_code=502)
        assert err.status_code == 502
        assert str(err) == "Bad gateway"


class TestPolicyErrors:
    """Tests for policy error subclasses."""

    def test_policy_error_status_code(self) -> None:
        """PolicyError has status_code=403."""
        err = PolicyError()
        assert err.status_code == 403
        assert str(err) == "Policy violation"

    def test_query_safety_error_status_code(self) -> None:
        """QuerySafetyError has status_code=403."""
        err = QuerySafetyError()
        assert err.status_code == 403
        assert str(err) == "Query safety violation"


class TestErrorHierarchy:
    """Tests for isinstance relationships in the error hierarchy."""

    def test_auth_error_is_servicenow_mcp_error(self) -> None:
        """AuthError is a ServiceNowMCPError."""
        assert isinstance(AuthError(), ServiceNowMCPError)

    def test_forbidden_error_is_servicenow_mcp_error(self) -> None:
        """ForbiddenError is a ServiceNowMCPError."""
        assert isinstance(ForbiddenError(), ServiceNowMCPError)

    def test_not_found_error_is_servicenow_mcp_error(self) -> None:
        """NotFoundError is a ServiceNowMCPError."""
        assert isinstance(NotFoundError(), ServiceNowMCPError)

    def test_server_error_is_servicenow_mcp_error(self) -> None:
        """ServerError is a ServiceNowMCPError."""
        assert isinstance(ServerError(), ServiceNowMCPError)

    def test_policy_error_is_servicenow_mcp_error(self) -> None:
        """PolicyError is a ServiceNowMCPError."""
        assert isinstance(PolicyError(), ServiceNowMCPError)

    def test_query_safety_error_is_policy_error(self) -> None:
        """QuerySafetyError is a PolicyError."""
        assert isinstance(QuerySafetyError(), PolicyError)

    def test_query_safety_error_is_servicenow_mcp_error(self) -> None:
        """QuerySafetyError is a ServiceNowMCPError."""
        assert isinstance(QuerySafetyError(), ServiceNowMCPError)

    def test_all_errors_are_exceptions(self) -> None:
        """All error classes are Exception subclasses."""
        for cls in (AuthError, ForbiddenError, NotFoundError, ServerError, PolicyError, QuerySafetyError):
            assert isinstance(cls(), Exception)


class TestCustomMessages:
    """Tests that all errors accept custom messages."""

    def test_auth_error_custom_message(self) -> None:
        """AuthError accepts a custom message."""
        err = AuthError("token expired")
        assert str(err) == "token expired"
        assert err.status_code == 401

    def test_forbidden_error_custom_message(self) -> None:
        """ForbiddenError accepts a custom message."""
        err = ForbiddenError("insufficient scope")
        assert str(err) == "insufficient scope"
        assert err.status_code == 403

    def test_not_found_error_custom_message(self) -> None:
        """NotFoundError accepts a custom message."""
        err = NotFoundError("record does not exist")
        assert str(err) == "record does not exist"
        assert err.status_code == 404

    def test_server_error_custom_message(self) -> None:
        """ServerError accepts a custom message."""
        err = ServerError("service unavailable")
        assert str(err) == "service unavailable"
        assert err.status_code == 500

    def test_policy_error_custom_message(self) -> None:
        """PolicyError accepts a custom message."""
        err = PolicyError("table denied")
        assert str(err) == "table denied"
        assert err.status_code == 403

    def test_query_safety_error_custom_message(self) -> None:
        """QuerySafetyError accepts a custom message."""
        err = QuerySafetyError("missing date filter")
        assert str(err) == "missing date filter"
        assert err.status_code == 403
