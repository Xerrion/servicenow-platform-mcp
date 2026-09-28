"""Value objects returned by ServiceNow dictionary discovery."""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class ScriptField(BaseModel):
    """A field that carries executable script or markup content."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    internal_type: str
    inherited_from: str | None
    via_heuristic: bool


class DictionaryField(BaseModel):
    """A normalized field from ``sys_dictionary``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    internal_type: str
    attributes: str
    inherited_from: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
