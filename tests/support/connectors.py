"""Mock HTTP for connector tests: route requests to canned JSON and record them."""

import json
from collections.abc import Callable
from typing import Any

import httpx

from backend.app.integrations.connectors.base import ConnectorClient


class MockApi:
    """Routes ``"METHOD /path"`` (path relative to the host) to a response body or callable."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # raw path keeps encoding such as GitLab's group%2Fproject
        key = f"{request.method} {request.url.raw_path.decode().split('?')[0]}"
        if key not in self.routes:
            return httpx.Response(404, json={"message": f"no route for {key}"})
        body = self.routes[key]
        if callable(body):
            body = body(request)
        if isinstance(body, httpx.Response):
            return body
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def json_body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content or b"null")

    def factory(self, name: str, base_url: str, **kwargs: Any) -> Callable[[], ConnectorClient]:
        return lambda: ConnectorClient(name, base_url=base_url, timeout=5,
                                       transport=httpx.MockTransport(self), **kwargs)  # fmt: skip
