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
    script_allowed_root: str
    servicenow_allow_dangerous_bypass: bool
    sentry_dsn: str
    sentry_environment: str
    sentry_send_pii: bool
    sentry_traces_sample_rate: float
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
        script_allowed_root: str = ...,
        servicenow_allow_dangerous_bypass: bool = ...,
        sentry_dsn: str = ...,
        sentry_environment: str = ...,
        sentry_send_pii: bool = ...,
        sentry_traces_sample_rate: float = ...,
        _env_file: EnvFileValue = ...,
    ) -> None: ...
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str: ...
    @classmethod
    def validate_max_row_limit(cls, v: int) -> int: ...
    @classmethod
    def validate_mcp_tool_package(cls, v: str) -> str: ...
    @classmethod
    def validate_sentry_traces_sample_rate(cls, v: float) -> float: ...
    @property
    def large_table_names(self) -> frozenset[str]: ...
    @property
    def is_production(self) -> bool: ...
