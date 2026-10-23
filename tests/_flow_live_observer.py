"""In-memory live evidence capture. Observation must not change an API result."""

import copy
from typing import Any


class FlowLiveObserver:
    """Retain request, echo, and GET objects in memory, never in a payload log."""

    def __init__(self) -> None:
        self.exchanges: list[tuple[str, dict[str, Any] | None, dict[str, Any]]] = []
        self.errors: list[str] = []

    def capture(
        self, resource: str, request: dict[str, Any] | None, result: dict[str, Any]
    ) -> None:
        try:
            self.exchanges.append(
                (resource, copy.deepcopy(request), copy.deepcopy(result))
            )
        except Exception as exc:  # noqa: BLE001 - test observation must not interrupt an effect
            self.errors.append(type(exc).__name__)
