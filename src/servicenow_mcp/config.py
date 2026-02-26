"""Configuration settings for the ServiceNow MCP server."""

from pydantic import field_validator
from pydantic_settings import BaseSettings

_DEFAULT_LARGE_TABLES = "syslog,sys_audit,sys_log_transaction,sys_email_log"


class Settings(BaseSettings):
    """ServiceNow MCP server configuration loaded from environment variables."""

    servicenow_instance_url: str
    servicenow_username: str
    servicenow_password: str
    mcp_tool_package: str = "full"
    servicenow_env: str = "dev"
    max_row_limit: int = 100
    large_table_names_csv: str = _DEFAULT_LARGE_TABLES

    model_config = {
        "env_file": [".env", ".env.local"],
        "env_file_encoding": "utf-8",
        "env_prefix": "",
        "extra": "ignore",
    }

    @field_validator("servicenow_instance_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def large_table_names(self) -> list[str]:
        """Parse comma-separated large table names into a list."""
        return [t.strip() for t in self.large_table_names_csv.split(",") if t.strip()]

    @property
    def is_production(self) -> bool:
        return self.servicenow_env == "prod"
