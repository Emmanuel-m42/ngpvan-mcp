"""The HTTP client against respx mocks shaped like the documented VAN responses."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from ngpvan_mcp.client import VanApiError, VanClient
from ngpvan_mcp.ratelimit import TokenBucket
from ngpvan_mcp.secrets import ApiKey

BASE = "https://api.securevan.com/v4"
KEY = "7c9e6679-7425-40de-944b-e07fc1f90ae7|1"


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, s: float) -> None:
        self.calls.append(s)


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def client(sleeps) -> VanClient:
    async def nosleep(_):
        return None

    return VanClient(app_name="acmeCrmProduct", api_key=ApiKey(KEY), bucket=TokenBucket(1000, 1000, sleep=nosleep),
                     sleep=sleeps, max_retries=3)


def envelope(code: str, text: str) -> dict:
    return {"errors": [{"code": code, "text": text}]}


@respx.mock
async def test_basic_auth_is_app_name_and_key_with_mode(client):
    route = respx.get(f"{BASE}/folders").mock(
        return_value=httpx.Response(200, json={"items": [{"folderId": 44, "name": "API Generated Lists"}],
                                               "nextPageLink": None, "count": 1})
    )
    await client.get("/folders")
    header = route.calls.last.request.headers["authorization"]
    assert base64.b64decode(header.removeprefix("Basic ")).decode() == f"acmeCrmProduct:{KEY}"


@respx.mock
async def test_429_waits_the_hinted_time_and_retries(client, sleeps):
    route = respx.get(f"{BASE}/folders").mock(side_effect=[
        httpx.Response(429, json=envelope("429", "API rate limit exceeded. Try again in 250 ms.")),
        httpx.Response(200, json={"items": [], "nextPageLink": None, "count": 0}),
    ])
    assert (await client.get("/folders"))["count"] == 0
    assert route.call_count == 2
    assert sleeps.calls == [pytest.approx(0.30)]
    assert client.stats.retries == 1


@respx.mock
async def test_429_gives_up_after_max_retries(client, sleeps):
    respx.get(f"{BASE}/folders").mock(
        return_value=httpx.Response(429, json=envelope("429", "Try again in 10 ms."))
    )
    with pytest.raises(VanApiError) as err:
        await client.get("/folders")
    assert err.value.status == 429 and err.value.kind == "rate_limited"
    assert len(sleeps.calls) == 3


@respx.mock
async def test_get_retries_server_errors(client):
    route = respx.get(f"{BASE}/events/1").mock(side_effect=[
        httpx.Response(500, json=envelope("INTERNAL_SERVER_ERROR", "boom")),
        httpx.Response(200, json={"eventId": 1}),
    ])
    assert (await client.get("/events/1"))["eventId"] == 1
    assert route.call_count == 2


@respx.mock
async def test_post_does_not_retry_server_errors(client):
    """A write that failed with a 5xx may have been applied; resending could double it."""
    route = respx.post(f"{BASE}/people/1/canvassResponses").mock(
        return_value=httpx.Response(500, json=envelope("INTERNAL_SERVER_ERROR", "boom"))
    )
    with pytest.raises(VanApiError):
        await client.post("/people/1/canvassResponses", {"resultCodeId": 1})
    assert route.call_count == 1


@respx.mock
async def test_post_does_retry_429(client):
    route = respx.post(f"{BASE}/people/1/canvassResponses").mock(side_effect=[
        httpx.Response(429, json=envelope("429", "Try again in 5 ms.")),
        httpx.Response(204),
    ])
    assert await client.post("/people/1/canvassResponses", {"resultCodeId": 1}) is None
    assert route.call_count == 2


@respx.mock
async def test_404_envelope_becomes_not_found(client):
    respx.get(f"{BASE}/people/5").mock(
        return_value=httpx.Response(404, json=envelope("NOT_FOUND", "The specified resource cannot be found."))
    )
    with pytest.raises(VanApiError) as err:
        await client.get("/people/5")
    assert err.value.kind == "not_found"
    assert err.value.text == "The specified resource cannot be found."


@respx.mock
async def test_401_explains_database_mode(client):
    respx.get(f"{BASE}/apiKeyProfiles").mock(return_value=httpx.Response(401, json=envelope("UNAUTHORIZED", "denied")))
    with pytest.raises(VanApiError) as err:
        await client.get("/apiKeyProfiles")
    assert "database mode" in err.value.text
    assert KEY not in str(err.value)


@respx.mock
async def test_html_page_is_reported_not_parsed(client):
    respx.get(f"{BASE}/people/1").mock(
        return_value=httpx.Response(200, text="<html>Oops</html>", headers={"content-type": "text/html"})
    )
    with pytest.raises(VanApiError) as err:
        await client.get("/people/1")
    assert err.value.code == "NOT_JSON"


@respx.mock
async def test_network_errors_retry_then_fail_cleanly(client):
    respx.get(f"{BASE}/folders").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(VanApiError) as err:
        await client.get("/folders")
    assert err.value.code == "NETWORK"


@respx.mock
async def test_paging_follows_next_page_link_and_respects_limit(client):
    def page(request):
        skip = int(request.url.params.get("$skip", 0))
        top = int(request.url.params["$top"])
        items = [{"savedListId": i} for i in range(skip, min(skip + top, 7))]
        more = skip + top < 7
        return httpx.Response(200, json={"items": items, "count": 7,
                                         "nextPageLink": f"{BASE}/savedLists?$skip={skip + top}" if more else None})

    respx.get(f"{BASE}/savedLists").mock(side_effect=page)
    items, count = await client.get_paged("/savedLists", limit=5, page_size=2)
    assert [i["savedListId"] for i in items] == [0, 1, 2, 3, 4]
    assert count == 7
    items, _ = await client.get_paged("/savedLists", limit=100, page_size=3)
    assert len(items) == 7


@respx.mock
async def test_bare_array_endpoints(client):
    respx.get(f"{BASE}/canvassResponses/contactTypes").mock(
        return_value=httpx.Response(200, json=[{"contactTypeId": 1, "name": "Phone"}, {"contactTypeId": 2, "name": "Walk"}])
    )
    items, count = await client.get_paged("/canvassResponses/contactTypes", limit=10)
    assert count == 2 and items[1]["name"] == "Walk"


def test_repr_never_shows_the_key(client):
    assert KEY not in repr(client)
    assert KEY not in repr(client.api_key) and KEY not in str(client.api_key)
