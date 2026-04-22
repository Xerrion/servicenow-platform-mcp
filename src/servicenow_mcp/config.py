"""Configuration settings for the ServiceNow MCP server."""

from functools import cached_property
from typing import ClassVar

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_LARGE_TABLES = (
    "syslog,sys_audit,sys_log_transaction,sys_email_log,cmdb_ci,cmdb_rel_ci,cmdb_ci_server,cmdb_ci_service"
)


class Settings(BaseSettings):
    """ServiceNow MCP server configuration loaded from environment variables."""

    servicenow_instance_url: str
    servicenow_username: str
    servicenow_password: SecretStr
    mcp_tool_package: str = "full"
    servicenow_env: str = "dev"
    max_row_limit: int = 100
    large_table_names_csv: str = _DEFAULT_LARGE_TABLES
    script_allowed_root: str = ""
    servicenow_allow_dangerous_bypass: bool = False

    sentry_dsn: str = ""
    sentry_environment: str = ""
    sentry_send_pii: bool = False
    sentry_traces_sample_rate: float = 0.05

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=[".env", ".env.local"],
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    @field_validator("servicenow_instance_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        """Strip trailing slash and validate HTTPS scheme."""
        if not v.startswith("https://"):
            raise ValueError("servicenow_instance_url must start with https://")
        return v.rstrip("/")

    @field_validator("max_row_limit")
    @classmethod
    def validate_max_row_limit(cls, v: int) -> int:
        """Ensure max_row_limit is between 1 and 10000."""
        if v < 1 or v > 10000:
            raise ValueError("max_row_limit must be between 1 and 10000")
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

    @field_validator("sentry_traces_sample_rate")
    @classmethod
    def validate_sentry_traces_sample_rate(cls, v: float) -> float:
        """Ensure sentry_traces_sample_rate is between 0.0 and 1.0 inclusive."""
        if v < 0.0 or v > 1.0:
            raise ValueError("sentry_traces_sample_rate must be between 0.0 and 1.0")
        return v

    @cached_property
    def large_table_names(self) -> frozenset[str]:
        """Parse comma-separated large table names into a frozenset."""
        return frozenset(t.strip() for t in self.large_table_names_csv.split(",") if t.strip())

    @property
    def is_production(self) -> bool:
        """Return True if the environment is production."""
        return self.servicenow_env.lower() in {"prod", "production"}
