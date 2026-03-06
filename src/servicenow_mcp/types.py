"""Shared typing helpers for callable metadata."""

import inspect
from collections.abc import Callable, Coroutine
from typing import Any, Protocol


class SignatureMutableCallable(Protocol):
    """Callable that exposes writable signature metadata for introspection."""

    __signature__: inspect.Signature
    __wrapped__: Callable[..., Coroutine[Any, Any, str]]

    def __call__(self, *args: Any, **kwargs: Any) -> Coroutine[Any, Any, str]: ...
