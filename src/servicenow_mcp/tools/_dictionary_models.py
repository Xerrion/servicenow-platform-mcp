"""Value objects returned by ServiceNow dictionary discovery."""

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any


@dataclass(frozen=True, slots=True)
class ScriptField:
    """A field that carries executable script or markup content."""

    name: str
    internal_type: str
    inherited_from: str | None
    via_heuristic: bool


@dataclass(frozen=True, slots=True)
class DictionaryField:
    """A normalized field from ``sys_dictionary``."""

    name: str
    internal_type: str
    attributes: str
    inherited_from: str | None
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)
