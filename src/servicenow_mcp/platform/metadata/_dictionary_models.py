"""Value objects returned by ServiceNow dictionary discovery."""

from typing import Any, ClassVar, Literal

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


class TableInheritance(BaseModel):
    """Observed ancestry. Completion does not establish unrestricted metadata visibility."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    tables: list[str]
    complete: bool
    reason: (
        Literal["table_not_visible", "parent_not_visible", "inheritance_cycle", "inheritance_depth_limit"] | None
    ) = None


class FieldLookup(BaseModel):
    """Selected schema fields and limitations of their metadata reads."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    fields: list[DictionaryField]
    inheritance: TableInheritance
    limitations: list[str] = Field(default_factory=list)


class FieldPathResolution(BaseModel):
    """Positive schema evidence or an explicit unresolved field path."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    path: str
    status: Literal["valid", "unverified"]
    field: DictionaryField | None = None
    reason: str | None = None
