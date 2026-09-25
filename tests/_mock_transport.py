"""Minimal HTTP mock for unit tests, built on ``httpx2.MockTransport``.

Every ``httpx2.AsyncHTTPTransport`` created during a unit test routes through
``http_mock``. Requests that match no route fail the request and the test.
"""

import inspect
import re
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx2


Handler = Callable[[httpx2.Request], httpx2.Response | Awaitable[httpx2.Response]]
SideEffect = Handler | BaseException | type[BaseException] | list[Any] | Iterator[Any]


@dataclass(frozen=True)
class Call:
    request: httpx2.Request
    response: httpx2.Response | None


class CallList(list[Call]):
    @property
    def called(self) -> bool:
        return bool(self)

    @property
    def call_count(self) -> int:
        return len(self)

    @property
    def last(self) -> Call:
        if not self:
            raise AssertionError("No HTTP calls were recorded")
        return self[-1]


def _copy(response: httpx2.Response, request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(response.status_code, headers=response.headers, content=response.content, request=request)


@dataclass(eq=False)
class Route:
    method: str | None
    url: str | re.Pattern[str]
    return_value: httpx2.Response | None = None
    side_effect: SideEffect | None = None
    calls: CallList = field(default_factory=CallList)

    @property
    def called(self) -> bool:
        return self.calls.called

    @property
    def call_count(self) -> int:
        return self.calls.call_count

    def respond(self, status_code: int = 200, **kwargs: Any) -> "Route":
        return self.mock(return_value=httpx2.Response(status_code, **kwargs))

    def mock(self, *, return_value: httpx2.Response | None = None, side_effect: SideEffect | None = None) -> "Route":
        self.return_value = return_value
        self.side_effect = iter(side_effect) if isinstance(side_effect, list) else side_effect
        return self

    def matches(self, request: httpx2.Request) -> bool:
        if self.method is not None and request.method != self.method:
            return False
        if isinstance(self.url, re.Pattern):
            return self.url.search(str(request.url)) is not None
        expected = httpx2.URL(self.url)
        if (expected.scheme, expected.host, expected.port, expected.path) != (
            request.url.scheme,
            request.url.host,
            request.url.port,
            request.url.path,
        ):
            return False
        return not expected.query or expected.params == request.url.params

    async def resolve(self, request: httpx2.Request) -> httpx2.Response:
        effect = self.side_effect
        if isinstance(effect, Iterator):
            effect = next(effect)
        if isinstance(effect, BaseException) or (isinstance(effect, type) and issubclass(effect, BaseException)):
            raise effect
        if isinstance(effect, httpx2.Response):
            return _copy(effect, request)
        if callable(effect):
            response = effect(request)
            if inspect.isawaitable(response):
                response = await response
            if not isinstance(response, httpx2.Response):
                raise TypeError(f"Mock side effect must return httpx2.Response, got {response!r}")
            return response
        if effect is not None:
            raise TypeError(f"Unsupported mock side effect: {effect!r}")
        if self.return_value is None:
            return httpx2.Response(200, request=request)
        return _copy(self.return_value, request)


class HTTPMock:
    """Route table with request history. Later routes with the same pattern replace earlier ones."""

    def __init__(self) -> None:
        self.routes: list[Route] = []
        self.calls: CallList = CallList()
        self.unexpected: list[httpx2.Request] = []

    def __enter__(self) -> "HTTPMock":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def reset(self) -> None:
        self.routes.clear()
        self.calls.clear()
        self.unexpected.clear()

    def route(self, *, method: str | None = None, url: str | re.Pattern[str]) -> Route:
        for existing in self.routes:
            if (existing.method, existing.url) == (method, url):
                return existing
        route = Route(method, url)
        self.routes.append(route)
        return route

    def get(self, url: str | re.Pattern[str]) -> Route:
        return self.route(method="GET", url=url)

    def post(self, url: str | re.Pattern[str]) -> Route:
        return self.route(method="POST", url=url)

    def patch(self, url: str | re.Pattern[str]) -> Route:
        return self.route(method="PATCH", url=url)

    def delete(self, url: str | re.Pattern[str]) -> Route:
        return self.route(method="DELETE", url=url)

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        route = next((candidate for candidate in self.routes if candidate.matches(request)), None)
        if route is None:
            self.unexpected.append(request)
            raise AssertionError(f"Unexpected HTTP request: {request.method} {request.url}")
        try:
            response = await route.resolve(request)
        except BaseException:
            call = Call(request, None)
            route.calls.append(call)
            self.calls.append(call)
            raise
        call = Call(request, response)
        route.calls.append(call)
        self.calls.append(call)
        return response


http_mock = HTTPMock()
