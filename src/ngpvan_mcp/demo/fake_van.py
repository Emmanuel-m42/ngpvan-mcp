"""A fake NGP VAN API over the Riverbend demo data.

It plugs into httpx as a transport, so the real client code runs end to
end: Basic auth, query parameters, the paged envelope, error envelopes and
429s all go through the same path they would against api.securevan.com.
It copies documented behaviour where the docs describe it:

- `GET /people` needs at least one search parameter; text matches are
  case-insensitive prefixes.
- Person collections are null unless named in `$expand`.
- `startingAfter` / `startingBefore` on `/events` exclude the given date.
- `/events` pages default to 10 and allow at most 50; `/activistCodes`
  defaults to 50 and allows 200.
- Lookups (`/signups/statuses`, `/canvassResponses/resultCodes`, ...) are
  bare arrays, not envelopes.
- A 429 carries the wait in the error text, with no Retry-After header.
"""

from __future__ import annotations

import base64
import copy
import json
import re
from datetime import date
from importlib import resources
from typing import Any
from urllib.parse import urlencode

import httpx

DEMO_APP_NAME = "ngpvan-mcp-demo"
DEMO_API_KEY = "00000000-0000-4000-8000-000000000000|1"
PERSON_EXPANDS = {
    "addresses", "biography", "cases", "codes", "contributionHistory", "customFields", "customProperties",
    "disclosureFieldValues", "districts", "electionRecords", "emails", "externalIds", "membershipStatuses",
    "notes", "organizationRoles", "phones", "pollingLocation", "preferences", "primaryContact",
    "recordedAddresses", "reportedDemographics", "scores", "suppressions",
}
BASE = "https://api.securevan.com/v4"


def load_demo_data() -> dict[str, Any]:
    text = resources.files("ngpvan_mcp.demo").joinpath("riverbend.json").read_text("utf-8")
    return json.loads(text)


def _err(status: int, code: str, text: str, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json={"errors": [{"code": code, "text": text}]}, headers=headers)


def _strip_private(obj: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in obj.items() if not k.startswith("_")}


class FakeVan:
    """Holds the data and answers requests. `rate_limit_every=n` returns a 429 on every nth request."""

    def __init__(self, data: dict[str, Any] | None = None, rate_limit_every: int = 0) -> None:
        self.data = copy.deepcopy(data if data is not None else load_demo_data())
        self.rate_limit_every = rate_limit_every
        self.requests: list[httpx.Request] = []
        self.written_canvass_responses: list[dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # -- plumbing ---------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._authorized(request):
            return _err(401, "UNAUTHORIZED", "Authorization has been denied for this request.")
        if self.rate_limit_every and len(self.requests) % self.rate_limit_every == 0:
            return _err(429, "429", "API rate limit exceeded. Try again in 20 ms.")
        path = request.url.path.split("/v4", 1)[-1].rstrip("/") or "/"
        q = {k: v for k, v in request.url.params.items()}
        for pattern, method, fn in self._routes():
            m = re.fullmatch(pattern, path)
            if m and request.method == method:
                return fn(request, q, *m.groups())
        return _err(404, "NOT_FOUND", "The specified resource cannot be found.")

    def _authorized(self, request: httpx.Request) -> bool:
        header = request.headers.get("authorization", "")
        if not header.startswith("Basic "):
            return False
        user, _, password = base64.b64decode(header[6:]).decode().partition(":")
        return bool(user) and password == DEMO_API_KEY

    def _routes(self):
        return [
            (r"/apiKeyProfiles", "GET", self.api_key_profiles),
            (r"/people", "GET", self.search_people),
            (r"/people/(\d+)", "GET", self.get_person),
            (r"/people/(\d+)/activistCodes", "GET", self.person_activist_codes),
            (r"/people/(\d+)/canvassResponses", "GET", self.person_canvass_responses),
            (r"/people/(\d+)/canvassResponses", "POST", self.post_canvass_response),
            (r"/events", "GET", self.list_events),
            (r"/events/(\d+)", "GET", self.get_event),
            (r"/signups", "GET", self.list_signups),
            (r"/signups/statuses", "GET", self.signup_statuses),
            (r"/folders", "GET", self.list_folders),
            (r"/savedLists", "GET", self.list_saved_lists),
            (r"/savedLists/(\d+)", "GET", self.get_saved_list),
            (r"/surveyQuestions", "GET", self.list_survey_questions),
            (r"/activistCodes", "GET", self.list_activist_codes),
            (r"/canvassResponses/resultCodes", "GET", lambda r, q: httpx.Response(200, json=self.data["resultCodes"])),
            (r"/canvassResponses/contactTypes", "GET", lambda r, q: httpx.Response(200, json=self.data["contactTypes"])),
        ]

    def _page(self, request: httpx.Request, q: dict[str, str], items: list[Any], default: int, maximum: int):
        try:
            top = int(q.get("$top", default))
            skip = int(q.get("$skip", 0))
        except ValueError:
            return _err(400, "INVALID_PARAMETER", "$top and $skip must be integers")
        if top > maximum:
            return _err(400, "INVALID_PARAMETER", f"$top must be at most {maximum}")
        page = items[skip : skip + top]
        next_link = None
        if skip + top < len(items):
            params = dict(q)
            params["$skip"] = str(skip + top)
            params["$top"] = str(top)
            next_link = f"{BASE}{request.url.path.split('/v4', 1)[-1]}?{urlencode(params)}"
        return httpx.Response(200, json={"items": page, "nextPageLink": next_link, "count": len(items)})

    def _person(self, van_id: str) -> dict[str, Any] | None:
        for p in self.data["people"]:
            if str(p["vanId"]) == van_id:
                return p
        return None

    # -- endpoints --------------------------------------------------------

    def api_key_profiles(self, request, q):
        return httpx.Response(200, json={"items": [self.data["apiKeyProfile"]], "nextPageLink": None, "count": 1})

    def search_people(self, request, q):
        fields = {"firstName": "firstName", "lastName": "lastName", "middleName": "middleName",
                  "city": "city", "zipOrPostalCode": "zipOrPostalCode", "streetAddress": "addressLine1",
                  "phoneNumber": "phoneNumber", "email": "email"}
        crit = {k: v for k, v in q.items() if k in fields and v}
        if not crit:
            return _err(400, "INVALID_PARAMETER", "This endpoint requires at least one search parameter to be set.")
        expand = set(filter(None, q.get("$expand", "").split(",")))
        out = []
        for p in self.data["people"]:
            voting = (p["_contact"]["addresses"] or [{}])[0]
            values = {
                "firstName": p["firstName"], "lastName": p["lastName"], "middleName": p.get("middleName"),
                "city": voting.get("city"), "zipOrPostalCode": voting.get("zipOrPostalCode"),
                "addressLine1": voting.get("addressLine1"),
                "phoneNumber": (p["_contact"]["phones"] or [{}])[0].get("phoneNumber"),
                "email": " ".join(e["email"] for e in p["_contact"]["emails"]),
            }
            if all(str(values[fields[k]] or "").lower().startswith(v.lower()) for k, v in crit.items()):
                out.append(self._render_person(p, expand))
        return self._page(request, q, out, 50, 200)

    def _render_person(self, p: dict[str, Any], expand: set[str]) -> dict[str, Any]:
        person = _strip_private(p)
        for section in ("emails", "phones", "addresses"):
            person[section] = copy.deepcopy(p["_contact"][section]) if section in expand else None
        return person

    def get_person(self, request, q, van_id):
        expand = set(filter(None, q.get("$expand", "").split(",")))
        bad = expand - PERSON_EXPANDS
        if bad:
            return _err(400, "INVALID_PARAMETER", f"Invalid $expand value(s): {', '.join(sorted(bad))}")
        if "scores" in expand:
            return _err(403, "FORBIDDEN", "Access to this action is restricted")
        p = self._person(van_id)
        if not p:
            return _err(404, "NOT_FOUND", "The specified resource cannot be found.")
        return httpx.Response(200, json=self._render_person(p, expand))

    def person_activist_codes(self, request, q, van_id):
        if not self._person(van_id):
            return _err(404, "NOT_FOUND", "The specified resource cannot be found.")
        return self._page(request, q, self.data["personActivistCodes"].get(van_id, []), 50, 200)

    def person_canvass_responses(self, request, q, van_id):
        if not self._person(van_id):
            return _err(404, "NOT_FOUND", "The specified resource cannot be found.")
        return self._page(request, q, self.data["canvassHistory"].get(van_id, []), 50, 200)

    def post_canvass_response(self, request, q, van_id):
        if not self._person(van_id):
            return _err(404, "NOT_FOUND", "The specified resource cannot be found.")
        body = json.loads(request.content or b"{}")
        if body.get("resultCodeId") is not None and body.get("responses"):
            return _err(400, "INVALID_PARAMETER", "resultCodeId must be null when responses are specified")
        if body.get("resultCodeId") is None and not body.get("responses"):
            return _err(400, "INVALID_PARAMETER", "Either resultCodeId or responses must be specified")
        self.written_canvass_responses.append({"vanId": int(van_id), **body})
        return httpx.Response(204)

    def list_events(self, request, q):
        events = self.data["events"]
        try:
            if q.get("startingAfter"):
                after = date.fromisoformat(q["startingAfter"])
                events = [e for e in events if date.fromisoformat(e["startDate"][:10]) > after]
            if q.get("startingBefore"):
                before = date.fromisoformat(q["startingBefore"])
                events = [e for e in events if date.fromisoformat(e["startDate"][:10]) < before]
        except ValueError:
            return _err(400, "INVALID_PARAMETER", "dates must be in the format yyyy-MM-dd")
        if q.get("eventTypeIds"):
            ids = {int(x) for x in q["eventTypeIds"].split(",")}
            events = [e for e in events if e["eventType"]["eventTypeId"] in ids]
        expand = set(filter(None, q.get("$expand", "").split(",")))
        out = []
        for e in sorted(events, key=lambda e: e["startDate"]):
            ev = _strip_private(e)
            for section in ("locations", "codes", "shifts", "roles", "notes"):
                if section not in expand:
                    ev[section] = None
            out.append(ev)
        return self._page(request, q, out, 10, 50)

    def get_event(self, request, q, event_id):
        for e in self.data["events"]:
            if str(e["eventId"]) == event_id:
                return httpx.Response(200, json=_strip_private(e))
        return _err(404, "NOT_FOUND", "The specified resource cannot be found.")

    def list_signups(self, request, q):
        if not q.get("eventId") and not q.get("vanId"):
            return _err(400, "INVALID_PARAMETER", "Either eventId or vanId must be specified")
        rows = self.data["signups"]
        if q.get("eventId"):
            if not any(str(e["eventId"]) == q["eventId"] for e in self.data["events"]):
                return _err(404, "NOT_FOUND", "The specified resource cannot be found.")
            rows = [s for s in rows if str(s["event"]["eventId"]) == q["eventId"]]
        if q.get("vanId"):
            if not self._person(q["vanId"]):
                return _err(404, "NOT_FOUND", "The specified resource cannot be found.")
            rows = [s for s in rows if str(s["person"]["vanId"]) == q["vanId"]]
        return self._page(request, q, rows, 50, 200)

    def signup_statuses(self, request, q):
        if not q.get("eventId") and not q.get("eventTypeId"):
            return _err(400, "INVALID_PARAMETER", "Either eventId or eventTypeId must be specified")
        return httpx.Response(200, json=self.data["signupStatuses"])

    def list_folders(self, request, q):
        return self._page(request, q, self.data["folders"], 50, 200)

    def list_saved_lists(self, request, q):
        rows = self.data["savedLists"]
        if q.get("folderId"):
            rows = [r for r in rows if str(r["_folderId"]) == q["folderId"]]
        if q.get("maxDoorCount"):
            rows = [r for r in rows if r["doorCount"] <= int(q["maxDoorCount"])]
        if q.get("maxPeopleCount"):
            rows = [r for r in rows if r["listCount"] <= int(q["maxPeopleCount"])]
        return self._page(request, q, [_strip_private(r) for r in rows], 50, 200)

    def get_saved_list(self, request, q, list_id):
        for r in self.data["savedLists"]:
            if str(r["savedListId"]) == list_id:
                return httpx.Response(200, json=_strip_private(r))
        return _err(404, "NOT_FOUND", "The specified resource cannot be found.")

    def _by_status(self, rows, q):
        statuses = {s.strip() for s in q.get("statuses", "Active").split(",")}
        rows = [r for r in rows if r["status"] in statuses]
        if q.get("name"):
            rows = [r for r in rows if r["name"].lower().startswith(q["name"].lower())]
        if q.get("type"):
            rows = [r for r in rows if r["type"] == q["type"]]
        return rows

    def list_survey_questions(self, request, q):
        rows = self._by_status(self.data["surveyQuestions"], q)
        if q.get("cycle"):
            rows = [r for r in rows if str(r["cycle"]) == q["cycle"]]
        return self._page(request, q, rows, 50, 200)

    def list_activist_codes(self, request, q):
        return self._page(request, q, self._by_status(self.data["activistCodes"], q), 50, 200)


def demo_today() -> date:
    """The date the demo data treats as today."""
    return date.fromisoformat(load_demo_data()["today"])
