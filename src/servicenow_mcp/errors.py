"""Custom exceptions for the ServiceNow MCP server."""


class ServiceNowMCPError(Exception):
    """Base exception for all ServiceNow MCP errors."""

    status_code: int | None

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthError(ServiceNowMCPError):
    """Authentication failure (HTTP 401)."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message, status_code=401)


class ForbiddenError(ServiceNowMCPError):
    """Authorization failure (HTTP 403)."""

    def __init__(self, message: str = "Access forbidden") -> None:
        super().__init__(message, status_code=403)


class NotFoundError(ServiceNowMCPError):
    """Resource not found (HTTP 404)."""

    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, status_code=404)


class ServerError(ServiceNowMCPError):
    """ServiceNow server error (HTTP 5xx)."""

    def __init__(self, message: str = "Internal server error", status_code: int = 500) -> None:
        super().__init__(message, status_code=status_code)


class PolicyError(ServiceNowMCPError):
    """Access denied by policy (deny list, masking, etc.)."""

    def __init__(self, message: str = "Policy violation", status_code: int = 403) -> None:
        super().__init__(message, status_code=status_code)


class QuerySafetyError(PolicyError):
    """Query violates safety policies."""

    def __init__(self, message: str = "Query safety violation") -> None:
        super().__init__(message, status_code=403)
