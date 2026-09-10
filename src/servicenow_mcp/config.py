"""Configuration settings for the ServiceNow MCP server."""

import math
from functools import cached_property
from typing import ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_LARGE_TABLES = "syslog,sys_audit,sys_log_transaction,sys_email_log"


class Settings(BaseSettings):
    """ServiceNow MCP server configuration loaded from environment variables."""

    servicenow_instance_url: str
    servicenow_username: str = ""
    servicenow_password: SecretStr = SecretStr("")
    servicenow_api_key: SecretStr = SecretStr("")
    servicenow_oauth_client_id: str
    servicenow_oauth_scope: Literal["useraccount"]
    servicenow_oauth_redirect_uri: str = "http://127.0.0.1:8765/oauth/callback"
    servicenow_oauth_timeout_seconds: int = 180
    mcp_tool_package: str = "full"
    servicenow_env: str = "dev"
    max_row_limit: int = 100
    large_table_names_csv: str = _DEFAULT_LARGE_TABLES
    httpx_timeout_seconds: float = 30.0
    metadata_cache_ttl_seconds: int = 300

    sentry_dsn: str = ""
    sentry_environment: str = ""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=[".env", ".env.local"],
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @field_validator("servicenow_instance_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        """Strip trailing slash and validate HTTPS scheme."""
        try:
            url = urlsplit(v)
            port = url.port
        except ValueError:
            raise ValueError("Invalid ServiceNow HTTPS origin") from None
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or any(char.isspace() for char in v)
            or (port is not None and port == 0)
        ):
            raise ValueError("servicenow_instance_url must be an https:// origin without credentials, path or query")
        return v.rstrip("/")

    @model_validator(mode="after")
    def validate_auth_credentials(self) -> "Settings":
        """Reject legacy credentials instead of silently falling back from OAuth."""
        if (
            self.servicenow_api_key.get_secret_value()
            or self.servicenow_username
            or self.servicenow_password.get_secret_value()
        ):
            raise ValueError(
                "Basic Auth and API keys are no longer supported. Remove SERVICENOW_USERNAME, "
                "SERVICENOW_PASSWORD and SERVICENOW_API_KEY; configure ServiceNow OAuth authorization-code flow."
            )
        return self

    @field_validator("servicenow_oauth_client_id")
    @classmethod
    def validate_oauth_client_id(cls, v: str) -> str:
        """Require a non-empty printable ASCII client ID."""
        if not v.strip() or not v.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in v):
            raise ValueError("OAuth client ID must be non-empty printable ASCII")
        return v.strip()

    @field_validator("servicenow_oauth_redirect_uri")
    @classmethod
    def validate_oauth_redirect(cls, v: str) -> str:
        """Accept only the fixed callback path on an explicit IPv4 loopback port."""
        try:
            url = urlsplit(v)
            port = url.port
        except ValueError:
            raise ValueError("Invalid OAuth loopback redirect URI") from None
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or port is None
            or not 1024 <= port <= 65535
            or v != f"http://127.0.0.1:{port}/oauth/callback"
        ):
            raise ValueError("OAuth redirect URI must be http://127.0.0.1:<port>/oauth/callback (port 1024-65535)")
        return v

    @field_validator("servicenow_oauth_timeout_seconds")
    @classmethod
    def validate_oauth_timeout(cls, v: int) -> int:
        """Bound the interactive authorization wait."""
        if not 1 <= v <= 600:
            raise ValueError("OAuth timeout must be between 1 and 600 seconds")
        return v

    @field_validator("max_row_limit")
    @classmethod
    def validate_max_row_limit(cls, v: int) -> int:
        """Ensure max_row_limit is between 1 and 10000."""
        if v < 1 or v > 10000:
            raise ValueError("max_row_limit must be between 1 and 10000")
        return v

    @field_validator("httpx_timeout_seconds")
    @classmethod
    def validate_httpx_timeout(cls, v: float) -> float:
        """Ensure httpx_timeout_seconds is finite and between 1.0 and 600.0."""
        if not math.isfinite(v) or v < 1.0 or v > 600.0:
            raise ValueError("httpx_timeout_seconds must be between 1.0 and 600.0")
        return v

    @field_validator("metadata_cache_ttl_seconds")
    @classmethod
    def validate_metadata_cache_ttl(cls, v: int) -> int:
        """Ensure metadata_cache_ttl_seconds is between 1 second and 24 hours."""
        if v < 1 or v > 86400:
            raise ValueError("metadata_cache_ttl_seconds must be between 1 and 86400")
        return v

    @field_validator("mcp_tool_package")
    @classmethod
    def validate_mcp_tool_package(cls, v: str) -> str:
        """Validate mcp_tool_package against known packages or comma-separated groups."""
        from servicenow_mcp.packages import get_package

        try:
            get_package(v)
        except ValueError as e:
            raise ValueError(f"Invalid mcp_tool_package: {e}") from e
        return v

    @cached_property
    def large_table_names(self) -> frozenset[str]:
        """Parse comma-separated large table names into a frozenset."""
        return frozenset(t.strip() for t in self.large_table_names_csv.split(",") if t.strip())

    @property
    def is_production(self) -> bool:
        """Return True if the environment is production."""
        return self.servicenow_env.lower() in {"prod", "production"}
