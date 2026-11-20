"""What each tool does, independent of MCP.

Every function takes a `Toolbox` (the VAN client plus settings) and returns
a plain dict built from an allowlist of fields. Counts and summaries come
first; individual records only where an organizer needs them (a shift
roster), and then with names limited by the privacy policy.

Each result names the VAN endpoints it read under `source`, so an answer
can cite where a number came from.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .client import VanClient
from .config import Config
from .privacy import display_name, mask_text


class ToolInputError(ValueError):
    """The arguments can't be used; the message says how to fix them."""


class WriteDisabledError(PermissionError):
    pass


@dataclass
class Toolbox:
    van: VanClient
    config: Config
    _cache: dict[str, Any] = field(default_factory=dict)
    _confirm_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))

    async def cached(self, key: str, path: str, **params: Any) -> Any:
        if key not in self._cache:
            self._cache[key] = await self.van.get(path, **params)
        return self._cache[key]

    def name(self, first: str | None, last: str | None) -> str | None:
        return display_name(first, last, self.config.names)


def _src(tool: str, *endpoints: str) -> dict[str, Any]:
    return {"tool": tool, "endpoints": list(endpoints)}


# -- connection -----------------------------------------------------------


async def check_connection(tb: Toolbox) -> dict[str, Any]:
    page = await tb.van.get("/apiKeyProfiles")
    profile = (page.get("items") or [{}])[0]
    return {
        "committee": profile.get("committeeName"),
        "database": profile.get("databaseName"),
        "database_mode": "My Campaign" if tb.config.database_mode == 1 else "My Voters",
        "key_has_my_voters": profile.get("hasMyVoters"),
        "key_has_my_campaign": profile.get("hasMyCampaign"),
        "key_type": profile.get("apiKeyTypeName"),
        "name_policy": tb.config.names,
        "write_tools_enabled": sorted(k for k, v in tb.config.writes.items() if v),
        "source": _src("check_connection", "GET /apiKeyProfiles"),
    }


# -- people ---------------------------------------------------------------


def _voting_city(person: dict[str, Any]) -> str | None:
    for addr in person.get("addresses") or []:
        if addr.get("type") == "Voting" or addr.get("isBest"):
            return addr.get("city")
    return None


async def search_people(
    tb: Toolbox,
    first_name: str | None = None,
    last_name: str | None = None,
    city: str | None = None,
    zip_code: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    if not any((first_name, last_name, city, zip_code)):
        raise ToolInputError("give at least one of first_name, last_name, city or zip_code")
    if tb.config.names == "none" and (first_name or last_name):
        raise ToolInputError("name search is turned off on this server (privacy.names = none)")
    limit = max(1, min(limit, tb.config.limits.max_items))
    items, count = await tb.van.get_paged(
        "/people",
        limit=limit,
        page_size=limit,
        firstName=first_name,
        lastName=last_name,
        city=city,
        zipOrPostalCode=zip_code,
        **{"$expand": "addresses"},
    )
    people = [
        {"van_id": p.get("vanId"), "name": tb.name(p.get("firstName"), p.get("lastName")), "city": _voting_city(p)}
        for p in items
    ]
    return {
        "total_matches": count if count is not None else len(people),
        "returned": len(people),
        "people": people,
        "note": "Matches are name/city prefixes, so check the candidates before acting on one.",
        "source": _src("search_people", "GET /people"),
    }


async def get_person(tb: Toolbox, van_id: int) -> dict[str, Any]:
    p = await tb.van.get(f"/people/{int(van_id)}", **{"$expand": "phones,emails,addresses"})
    phones = p.get("phones") or []
    emails = p.get("emails") or []
    addresses = p.get("addresses") or []
    cells = [ph for ph in phones if (ph.get("phoneType") == "Cell" or ph.get("deviceType") == "Cell")]
    best_cell = cells[0] if cells else None
    voting = next((a for a in addresses if a.get("type") == "Voting"), addresses[0] if addresses else None)
    return {
        "van_id": p.get("vanId"),
        "name": tb.name(p.get("firstName"), p.get("lastName")),
        "city": voting.get("city") if voting else None,
        "state": voting.get("stateOrProvince") if voting else None,
        "contact_on_file": {
            "has_phone": bool(phones),
            "has_cell_phone": bool(cells),
            "sms_opt_in": best_cell.get("smsOptInStatus") if best_cell else None,
            "has_email": bool(emails),
            "email_subscribed": any(e.get("subscriptionStatus") == "S" for e in emails),
            "has_address": bool(addresses),
        },
        "is_deceased": p.get("isDeceased"),
        "note": "Phone numbers, emails, street addresses and birth dates are not returned by this server.",
        "source": _src("get_person", f"GET /people/{int(van_id)}?$expand=phones,emails,addresses"),
    }


async def person_activist_codes(tb: Toolbox, van_id: int) -> dict[str, Any]:
    items, count = await tb.van.get_paged(
        f"/people/{int(van_id)}/activistCodes", limit=tb.config.limits.max_items
    )
    return {
        "van_id": int(van_id),
        "count": count if count is not None else len(items),
        "activist_codes": [
            {"id": a.get("activistCodeId"), "name": a.get("activistCodeName"),
             "date_applied": (a.get("dateCanvassed") or a.get("dateCreated") or "")[:10] or None}
            for a in items
        ],
        "source": _src("person_activist_codes", f"GET /people/{int(van_id)}/activistCodes"),
    }


async def person_signups(tb: Toolbox, van_id: int) -> dict[str, Any]:
    items, count = await tb.van.get_paged("/signups", limit=tb.config.limits.max_items, vanId=int(van_id))
    return {
        "van_id": int(van_id),
        "count": count if count is not None else len(items),
        "signups": [
            {"event_id": (s.get("event") or {}).get("eventId"), "event": (s.get("event") or {}).get("name"),
             "shift": (s.get("shift") or {}).get("name"), "start": s.get("startTimeOverride"),
             "role": (s.get("role") or {}).get("name"), "status": (s.get("status") or {}).get("name")}
            for s in items
        ],
        "source": _src("person_signups", f"GET /signups?vanId={int(van_id)}"),
    }


async def _survey_lookup(tb: Toolbox) -> tuple[dict[int, str], dict[int, str]]:
    page = await tb.cached("sq", "/surveyQuestions", statuses="Active,Archived,Inactive", **{"$top": 200})
    questions, responses = {}, {}
    for q in page.get("items") or []:
        questions[q["surveyQuestionId"]] = q.get("name")
        for r in q.get("responses") or []:
            responses[r["surveyResponseId"]] = r.get("name")
    return questions, responses


async def _code_lookup(tb: Toolbox) -> tuple[dict[int, str], dict[int, str]]:
    results = await tb.cached("rc", "/canvassResponses/resultCodes")
    contacts = await tb.cached("ct", "/canvassResponses/contactTypes")
    return ({r["resultCodeId"]: r["name"] for r in results}, {c["contactTypeId"]: c["name"] for c in contacts})


async def contact_history_summary(
    tb: Toolbox, van_ids: list[int], since: str | None = None
) -> dict[str, Any]:
    cap = tb.config.limits.max_people_per_history_call
    ids = list(dict.fromkeys(int(v) for v in van_ids))
    if not ids:
        raise ToolInputError("give at least one van_id")
    if len(ids) > cap:
        raise ToolInputError(
            f"at most {cap} people per call; for a whole list, use an Export Job in VAN "
            "(this server doesn't run those)"
        )
    since_d = date.fromisoformat(since) if since else None
    result_names, contact_names = await _code_lookup(tb)
    q_names, r_names = await _survey_lookup(tb)
    by_type: Counter[str] = Counter()
    by_result: Counter[str] = Counter()
    survey: dict[str, Counter[str]] = defaultdict(Counter)
    people = []
    attempts = 0
    for vid in ids:
        items, _ = await tb.van.get_paged(f"/people/{vid}/canvassResponses", limit=200)
        person_attempts = 0
        last = None
        for item in items:
            ctx = item.get("canvassContext") or {}
            day = ctx.get("dateCanvassed")
            if since_d and day and date.fromisoformat(day[:10]) < since_d:
                continue
            person_attempts += 1
            by_type[contact_names.get(ctx.get("contactTypeId"), f"type {ctx.get('contactTypeId')}")] += 1
            if item.get("resultCodeId") is not None:
                by_result[result_names.get(item["resultCodeId"], f"result {item['resultCodeId']}")] += 1
            else:
                by_result["Canvassed"] += 1
            for r in item.get("responses") or []:
                if r.get("type") == "SurveyResponse":
                    qn = q_names.get(r.get("surveyQuestionId"), f"question {r.get('surveyQuestionId')}")
                    survey[qn][r_names.get(r.get("surveyResponseId"), f"response {r.get('surveyResponseId')}")] += 1
            last = max(last or "", (day or "")[:10]) or last
        attempts += person_attempts
        people.append({"van_id": vid, "attempts": person_attempts, "last_attempt": last})
    return {
        "people_checked": len(ids),
        "people_reached": sum(1 for p in people if p["attempts"]),
        "total_attempts": attempts,
        "by_contact_type": dict(by_type.most_common()),
        "by_result": dict(by_result.most_common()),
        "survey_responses": {q: dict(c.most_common()) for q, c in survey.items()},
        "per_person": people,
        "since": since,
        "source": _src("contact_history_summary", "GET /people/{vanId}/canvassResponses",
                       "GET /canvassResponses/resultCodes", "GET /canvassResponses/contactTypes",
                       "GET /surveyQuestions"),
    }


# -- events ---------------------------------------------------------------


def _iso(day: str, name: str) -> date:
    try:
        return date.fromisoformat(day)
    except ValueError:
        raise ToolInputError(f"{name} must be a date like 2026-11-21") from None


async def list_events(
    tb: Toolbox, start_date: str, end_date: str | None = None, event_type: str | None = None
) -> dict[str, Any]:
    start = _iso(start_date, "start_date")
    end = _iso(end_date, "end_date") if end_date else start
    if end < start:
        raise ToolInputError("end_date is before start_date")
    if (end - start).days > 92:
        raise ToolInputError("pick a range of three months or less")
    # VAN's startingAfter and startingBefore both exclude the date given,
    # so an inclusive range needs one day of padding on each side.
    items, count = await tb.van.get_paged(
        "/events",
        limit=tb.config.limits.max_items,
        page_size=50,
        startingAfter=(start - timedelta(days=1)).isoformat(),
        startingBefore=(end + timedelta(days=1)).isoformat(),
        **{"$expand": "shifts,locations"},
    )
    if event_type:
        items = [e for e in items if ((e.get("eventType") or {}).get("name") or "").lower() == event_type.lower()]
    events = []
    for e in items:
        events.append({
            "event_id": e.get("eventId"),
            "name": e.get("name"),
            "type": (e.get("eventType") or {}).get("name"),
            "date": (e.get("startDate") or "")[:10],
            "start": e.get("startDate"),
            "end": e.get("endDate"),
            "location": ", ".join(loc.get("name", "") for loc in e.get("locations") or []) or None,
            "description": mask_text(e.get("description") or "") or None,
            "shifts": [
                {"shift_id": s.get("eventShiftId"), "name": s.get("name"), "start": s.get("startTime"),
                 "end": s.get("endTime")}
                for s in e.get("shifts") or []
            ],
        })
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(events),
        "events": events,
        "source": _src("list_events", "GET /events"),
    }


COMMITTED = ("Scheduled", "Confirmed", "Walk In", "Completed")


async def event_signups_summary(
    tb: Toolbox, event_id: int, shift: str | None = None, include_roster: bool = True
) -> dict[str, Any]:
    event = await tb.van.get(f"/events/{int(event_id)}", **{"$expand": "shifts"})
    rows, _ = await tb.van.get_paged("/signups", limit=1000, page_size=200, eventId=int(event_id))
    shifts = event.get("shifts") or []
    if shift:
        want = shift.strip().lower()
        matched = [s for s in shifts if str(s.get("eventShiftId")) == want or (s.get("name") or "").lower() == want]
        if not matched:
            names = ", ".join(f"{s.get('name')} ({s.get('eventShiftId')})" for s in shifts)
            raise ToolInputError(f"no shift {shift!r} on this event; shifts are: {names}")
        keep = {s["eventShiftId"] for s in matched}
        rows = [r for r in rows if (r.get("shift") or {}).get("eventShiftId") in keep]
        shifts = matched
    out_shifts = []
    for s in shifts:
        srows = [r for r in rows if (r.get("shift") or {}).get("eventShiftId") == s.get("eventShiftId")]
        statuses = Counter((r.get("status") or {}).get("name") for r in srows)
        entry: dict[str, Any] = {
            "shift_id": s.get("eventShiftId"),
            "name": s.get("name"),
            "start": s.get("startTime"),
            "end": s.get("endTime"),
            "signed_up": len(srows),
            "committed": sum(statuses[k] for k in COMMITTED),
            "by_status": dict(statuses.most_common()),
        }
        if include_roster:
            entry["roster"] = [
                {"van_id": (r.get("person") or {}).get("vanId"),
                 "name": tb.name((r.get("person") or {}).get("firstName"), (r.get("person") or {}).get("lastName")),
                 "role": (r.get("role") or {}).get("name"), "status": (r.get("status") or {}).get("name")}
                for r in sorted(srows, key=lambda r: ((r.get("status") or {}).get("name") or "",
                                                     (r.get("person") or {}).get("lastName") or ""))
            ]
        out_shifts.append(entry)
    all_status = Counter((r.get("status") or {}).get("name") for r in rows)
    return {
        "event_id": event.get("eventId"),
        "event": event.get("name"),
        "date": (event.get("startDate") or "")[:10],
        "signed_up": len(rows),
        "committed": sum(all_status[k] for k in COMMITTED),
        "by_status": dict(all_status.most_common()),
        "shifts": out_shifts,
        "note": "committed counts Scheduled, Confirmed, Walk In and Completed; Invited and Declined are not committed.",
        "source": _src("event_signups_summary", f"GET /events/{int(event_id)}", f"GET /signups?eventId={int(event_id)}"),
    }


# -- lists and turf -------------------------------------------------------


async def list_folders(tb: Toolbox) -> dict[str, Any]:
    items, count = await tb.van.get_paged("/folders", limit=tb.config.limits.max_items)
    return {
        "count": count if count is not None else len(items),
        "folders": [{"folder_id": f.get("folderId"), "name": f.get("name")} for f in items],
        "source": _src("list_folders", "GET /folders"),
    }


async def turf_door_counts(
    tb: Toolbox, folder_id: int | None = None, folder_name: str | None = None
) -> dict[str, Any]:
    folders, _ = await tb.van.get_paged("/folders", limit=200)
    if folder_name and folder_id is None:
        want = folder_name.strip().lower()
        hits = [f for f in folders if want in (f.get("name") or "").lower()]
        if not hits:
            raise ToolInputError(
                f"no folder matching {folder_name!r}; folders are: " + ", ".join(f.get("name", "") for f in folders)
            )
        if len(hits) > 1:
            raise ToolInputError("more than one folder matches: " + ", ".join(f.get("name", "") for f in hits))
        folder_id = hits[0]["folderId"]
    targets = [f for f in folders if folder_id is None or f.get("folderId") == folder_id]
    if folder_id is not None and not targets:
        raise ToolInputError(f"no folder with id {folder_id}")
    out = []
    for f in targets:
        lists, _ = await tb.van.get_paged("/savedLists", limit=200, page_size=200, folderId=f["folderId"])
        out.append({
            "folder_id": f["folderId"],
            "folder": f.get("name"),
            "lists": len(lists),
            "doors": sum(int(sl.get("doorCount") or 0) for sl in lists),
            "people": sum(int(sl.get("listCount") or 0) for sl in lists),
            "saved_lists": [
                {"saved_list_id": sl.get("savedListId"), "name": sl.get("name"),
                 "doors": sl.get("doorCount"), "people": sl.get("listCount")}
                for sl in lists
            ],
        })
    return {
        "folders": out,
        "total_doors": sum(f["doors"] for f in out),
        "total_people": sum(f["people"] for f in out),
        "note": "VAN's API has no turf endpoint. Turf here means the saved lists in a folder, "
                "with VAN's own doorCount and listCount for each.",
        "source": _src("turf_door_counts", "GET /folders", "GET /savedLists?folderId=..."),
    }


# -- codes and questions --------------------------------------------------


async def list_survey_questions(
    tb: Toolbox, include_archived: bool = False, name: str | None = None
) -> dict[str, Any]:
    statuses = "Active,Archived,Inactive" if include_archived else "Active"
    items, count = await tb.van.get_paged(
        "/surveyQuestions", limit=tb.config.limits.max_items, statuses=statuses, name=name
    )
    return {
        "count": count if count is not None else len(items),
        "survey_questions": [
            {"id": q.get("surveyQuestionId"), "name": q.get("name"), "type": q.get("type"), "cycle": q.get("cycle"),
             "status": q.get("status"), "script": q.get("scriptQuestion"),
             "responses": [{"id": r.get("surveyResponseId"), "name": r.get("name")} for r in q.get("responses") or []]}
            for q in items
        ],
        "source": _src("list_survey_questions", "GET /surveyQuestions"),
    }


async def list_activist_codes(
    tb: Toolbox, include_archived: bool = False, code_type: str | None = None, name: str | None = None
) -> dict[str, Any]:
    statuses = "Active,Archived,Inactive" if include_archived else "Active"
    items, count = await tb.van.get_paged(
        "/activistCodes", limit=tb.config.limits.max_items, statuses=statuses, type=code_type, name=name
    )
    return {
        "count": count if count is not None else len(items),
        "activist_codes": [
            {"id": a.get("activistCodeId"), "name": a.get("name"), "type": a.get("type"),
             "status": a.get("status"), "description": a.get("description")}
            for a in items
        ],
        "source": _src("list_activist_codes", "GET /activistCodes"),
    }


async def canvass_codes(tb: Toolbox) -> dict[str, Any]:
    results = await tb.cached("rc", "/canvassResponses/resultCodes")
    contacts = await tb.cached("ct", "/canvassResponses/contactTypes")
    return {
        "result_codes": [{"id": r.get("resultCodeId"), "name": r.get("name")} for r in results],
        "contact_types": [{"id": c.get("contactTypeId"), "name": c.get("name")} for c in contacts],
        "source": _src("canvass_codes", "GET /canvassResponses/resultCodes", "GET /canvassResponses/contactTypes"),
    }


# -- the one write --------------------------------------------------------


def _canvass_body(
    survey_question_id: int | None,
    survey_response_id: int | None,
    activist_code_id: int | None,
    result_code_id: int | None,
    contact_type_id: int,
    date_canvassed: str | None,
) -> dict[str, Any]:
    responses: list[dict[str, Any]] = []
    if survey_question_id is not None or survey_response_id is not None:
        if survey_question_id is None or survey_response_id is None:
            raise ToolInputError("a survey response needs both survey_question_id and survey_response_id")
        responses.append({"type": "SurveyResponse", "surveyQuestionId": int(survey_question_id),
                          "surveyResponseId": int(survey_response_id)})
    if activist_code_id is not None:
        responses.append({"type": "ActivistCode", "activistCodeId": int(activist_code_id), "action": "Apply"})
    if result_code_id is not None and responses:
        raise ToolInputError("VAN takes either a result code (not home, refused...) or responses, not both")
    if result_code_id is None and not responses:
        raise ToolInputError("give a result_code_id, or a survey response, or an activist code")
    context: dict[str, Any] = {"contactTypeId": int(contact_type_id), "inputTypeId": 11}
    if date_canvassed:
        context["dateCanvassed"] = _iso(date_canvassed, "date_canvassed").isoformat()
    return {"canvassContext": context, "resultCodeId": result_code_id, "responses": responses or None}


async def apply_canvass_response(
    tb: Toolbox,
    van_id: int,
    contact_type_id: int,
    survey_question_id: int | None = None,
    survey_response_id: int | None = None,
    activist_code_id: int | None = None,
    result_code_id: int | None = None,
    date_canvassed: str | None = None,
    dry_run: bool = True,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    if not tb.config.write_enabled("apply_canvass_response"):
        # The tool isn't registered unless enabled; this is the second lock.
        raise WriteDisabledError("apply_canvass_response is turned off in this server's config")
    body = _canvass_body(survey_question_id, survey_response_id, activist_code_id, result_code_id,
                         contact_type_id, date_canvassed)
    path = f"/people/{int(van_id)}/canvassResponses"
    digest = hmac.new(tb._confirm_secret, json.dumps([path, body], sort_keys=True).encode(), hashlib.sha256)
    token = digest.hexdigest()[:16]
    if dry_run:
        return {
            "dry_run": True,
            "would_send": {"method": "POST", "path": path, "body": body},
            "confirm_token": token,
            "next_step": "Show this to the person you're helping. If they approve, call again with "
                         "dry_run=false and this confirm_token.",
            "source": _src("apply_canvass_response", f"POST {path} (not sent)"),
        }
    if not confirm_token or not hmac.compare_digest(confirm_token, token):
        raise ToolInputError("run with dry_run=true first and pass back its confirm_token for this exact change")
    await tb.van.post(path, body)
    return {
        "dry_run": False,
        "applied": True,
        "sent": {"method": "POST", "path": path, "body": body},
        "source": _src("apply_canvass_response", f"POST {path}"),
    }
