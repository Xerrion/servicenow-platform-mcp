"""Authentication providers for ServiceNow REST API."""

import base64

from servicenow_mcp.config import Settings


class BasicAuthProvider:
    """Basic HTTP authentication provider."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def get_headers(self) -> dict[str, str]:
        """Return HTTP headers with Basic auth credentials."""
        credentials = f"{self._settings.servicenow_username}:{self._settings.servicenow_password.get_secret_value()}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


def create_auth(settings: Settings) -> BasicAuthProvider:
    """Create an authentication provider based on settings."""
    return BasicAuthProvider(settings)
