"""A small async client for the NGP VAN API (v4).

Only what the tools need: Basic auth with the application name and the API
key (with its database mode suffix), the standard paged envelope
(`items`, `nextPageLink`, `count`), the rate limiter and retries.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx

from .ratelimit import TokenBucket, retry_delay
from .secrets import ApiKey

Sleep = Callable[[float], Awaitable[None]]

# httpx logs every request URL at INFO, query string included, and search
# queries carry names. The audit log is the record of calls instead.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class VanApiError(Exception):
    """A VAN API failure, with a message that is safe to show the model."""

    def __init__(self, status: int, code: str, text: str) -> None:
        self.status = status
        self.code = code
        self.text = text
        super().__init__(f"VAN API {status} {code}: {text}")

    @property
    def kind(self) -> str:
        return {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            429: "rate_limited",
        }.get(self.status, "server_error" if self.status >= 500 else "error")


@dataclass
class CallStats:
    requests: int = 0
    retries: int = 0
    throttled_seconds: float = 0.0


@dataclass
class VanClient:
    app_name: str
    api_key: ApiKey
    base_url: str = "https://api.securevan.com/v4"
    bucket: TokenBucket = field(default_factory=lambda: TokenBucket(5.0, 5))
    max_retries: int = 4
    timeout: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None
    sleep: Sleep = anyio.sleep
    stats: CallStats = field(default_factory=CallStats)

    def __post_init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=self.base_url.rstrip("/") + "/",
            auth=httpx.BasicAuth(self.app_name, self.api_key.reveal()),
            headers={"Accept": "application/json", "User-Agent": "ngpvan-mcp/0.1"},
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        return f"VanClient(app_name={self.app_name!r}, base_url={self.base_url!r})"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        idempotent = method.upper() == "GET"
        attempt = 0
        while True:
            self.stats.throttled_seconds += await self.bucket.acquire()
            self.stats.requests += 1
            try:
                resp = await self._http.request(method, path.lstrip("/"), params=params, json=json)
            except httpx.TransportError as exc:
                if idempotent and attempt < self.max_retries:
                    await self._wait(attempt, None)
                    attempt += 1
                    continue
                raise VanApiError(0, "NETWORK", f"could not reach the VAN API ({type(exc).__name__})") from None
            if resp.status_code == 429 or (resp.status_code >= 500 and idempotent):
                # A 429 was never processed, so even a write is safe to resend.
                # A 5xx on a write might have been, so writes don't retry it.
                if attempt < self.max_retries:
                    await self._wait(attempt, _error_text(resp))
                    attempt += 1
                    continue
            if resp.status_code >= 400:
                raise _to_error(resp)
            if resp.status_code == 204 or not resp.content:
                return None
            if "json" not in resp.headers.get("content-type", ""):
                # VAN answers some malformed paths with an IIS or web-app HTML page.
                raise VanApiError(resp.status_code, "NOT_JSON", "the VAN API returned a non-JSON page")
            return resp.json()

    async def _wait(self, attempt: int, text: str | None) -> None:
        self.stats.retries += 1
        await self.sleep(retry_delay(attempt, text))

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body: Any) -> Any:
        return await self.request("POST", path, json=body)

    async def get_paged(
        self, path: str, *, limit: int, page_size: int = 50, **params: Any
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Up to `limit` items from a paged endpoint, plus VAN's total `count`."""
        items: list[dict[str, Any]] = []
        skip = 0
        count: int | None = None
        while len(items) < limit:
            top = min(page_size, limit - len(items))
            page = await self.get(path, **params, **{"$top": top, "$skip": skip})
            if isinstance(page, list):  # a few endpoints return a bare array
                return page[:limit], len(page)
            batch = page.get("items") or []
            count = page.get("count", count)
            items.extend(batch)
            if not page.get("nextPageLink") or not batch:
                break
            skip += len(batch)
        return items[:limit], count


def _error_text(resp: httpx.Response) -> str | None:
    try:
        errors = resp.json().get("errors") or []
        return " ".join(str(e.get("text", "")) for e in errors) or None
    except Exception:
        return None


def _to_error(resp: httpx.Response) -> VanApiError:
    code, text = "HTTP_ERROR", resp.reason_phrase or "request failed"
    try:
        errors = resp.json().get("errors") or []
        if errors:
            code = str(errors[0].get("code") or code)
            text = "; ".join(str(e.get("text", "")) for e in errors if e.get("text")) or text
    except Exception:
        pass
    if resp.status_code == 401:
        text = (
            "the application name or API key was rejected, or the key has no access to this "
            "database mode (My Voters |0 or My Campaign |1)"
        )
    return VanApiError(resp.status_code, code, text)
