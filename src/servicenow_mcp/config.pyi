from os import PathLike

from pydantic import SecretStr
from pydantic_settings import BaseSettings

_DEFAULT_LARGE_TABLES: str

type EnvFilePath = str | PathLike[str]
type EnvFileValue = EnvFilePath | list[EnvFilePath] | tuple[EnvFilePath, ...] | None

class Settings(BaseSettings):
    servicenow_instance_url: str
    servicenow_username: str
    servicenow_password: SecretStr
    mcp_tool_package: str
    servicenow_env: str
    max_row_limit: int
    large_table_names_csv: str
    sentry_dsn: str
    sentry_environment: str
    def __init__(
        self,
        *,
        servicenow_instance_url: str = ...,
        servicenow_username: str = ...,
        servicenow_password: SecretStr = ...,
        mcp_tool_package: str = ...,
        servicenow_env: str = ...,
        max_row_limit: int = ...,
        large_table_names_csv: str = ...,
        sentry_dsn: str = ...,
        sentry_environment: str = ...,
        _env_file: EnvFileValue = ...,
    ) -> None: ...
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str: ...
    @classmethod
    def validate_max_row_limit(cls, v: int) -> int: ...
    @classmethod
    def validate_mcp_tool_package(cls, v: str) -> str: ...
    @property
    def large_table_names(self) -> frozenset[str]: ...
    @property
    def is_production(self) -> bool: ...
