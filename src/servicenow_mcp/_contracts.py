"""Base model for curated, application-owned response structures."""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, SerializerFunctionWrapHandler, model_serializer


class ContractModel(BaseModel):
    """Owned response structure that dumps to the legacy dict shape.

    Field declaration order is the output key order. Fields named in
    ``omit_when_none`` are dropped at their own model level when None; every
    other None stays in the output. Dynamic ServiceNow data stays in
    ``dict[str, Any]`` / ``Any`` fields and passes through unvalidated.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    omit_when_none: ClassVar[frozenset[str]] = frozenset()

    @model_serializer(mode="wrap")
    def _omit_none(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        payload: dict[str, Any] = handler(self)
        for name in self.omit_when_none:
            if payload.get(name) is None:
                payload.pop(name, None)
        return payload

    def to_payload(self) -> dict[str, Any]:
        """Return the legacy dict shape for response serialization."""
        return self.model_dump(mode="python", round_trip=True)
