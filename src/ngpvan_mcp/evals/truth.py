"""Ground truth for the eval cases, computed from the raw fixture.

Nothing here imports the tools or the fake server: each answer is worked
out from riverbend.json directly, so a bug in a tool shows up as a failed
case instead of moving the answer key with it.

A fact is a list of alternatives; the answer must contain at least one of
them. Numbers match as whole numbers (so 74 doesn't match 174).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Facts = list[list[str]]
TRUTHS: dict[str, Callable[[dict[str, Any]], Facts]] = {}


def truth(fn: Callable[[dict[str, Any]], Facts]) -> Callable[[dict[str, Any]], Facts]:
    TRUTHS[fn.__name__] = fn
    return fn


def _folder_lists(d, name):
    fid = next(f["folderId"] for f in d["folders"] if f["name"] == name)
    return [s for s in d["savedLists"] if s["_folderId"] == fid]


def _person(d, first, last):
    return next(p for p in d["people"] if p["firstName"] == first and p["lastName"] == last)


def _shift_rows(d, event_name, shift_name):
    ev = next(e for e in d["events"] if e["name"] == event_name)
    sid = next(s["eventShiftId"] for s in ev["shifts"] if s["name"] == shift_name)
    return [s for s in d["signups"] if s["shift"]["eventShiftId"] == sid]


@truth
def northside_total_doors(d):
    return [[str(sum(s["doorCount"] for s in _folder_lists(d, "Northside Turfs")))]]


@truth
def eastgate_biggest_turf(d):
    top = max(_folder_lists(d, "Eastgate Turfs"), key=lambda s: s["doorCount"])
    return [[top["name"]], [str(top["doorCount"])]]


@truth
def northside_afternoon_signups(d):
    rows = _shift_rows(d, "Northside Weekend Canvass", "Afternoon")
    confirmed = sum(1 for r in rows if r["status"]["name"] == "Confirmed")
    return [[str(len(rows))], [str(confirmed)]]


@truth
def weekend_events(d):
    names = sorted({e["name"] for e in d["events"] if e["startDate"][:10] in ("2026-11-21", "2026-11-22")})
    return [[n] for n in names]


@truth
def measure3_responses(d):
    q = next(q for q in d["surveyQuestions"] if q["surveyQuestionId"] == 5201)
    # "1 Strong Yes" may be written as "Strong Yes".
    return [[r["name"].split(" ", 1)[1]] for r in q["responses"]]


@truth
def grace_sms(d):
    p = _person(d, "Grace", "Thornbury")
    status = p["_contact"]["phones"][0]["smsOptInStatus"]
    assert status == "Opt-In"
    return [["opt-in", "opted in", "opted-in", "opt in"]]


@truth
def harbor_point_people(d):
    n = sum(1 for p in d["people"] if p["_contact"]["addresses"][0]["city"] == "Harbor Point")
    return [[str(n)]]


@truth
def committee(d):
    return [[d["apiKeyProfile"]["committeeName"].split(" (")[0]]]


@truth
def harbor_doors_people(d):
    lists = _folder_lists(d, "Harbor Turfs")
    return [[str(sum(s["doorCount"] for s in lists))], [str(sum(s["listCount"] for s in lists))]]


@truth
def northside_smallest_turf(d):
    low = min(_folder_lists(d, "Northside Turfs"), key=lambda s: s["doorCount"])
    return [[low["name"]]]


@truth
def harbor_morning_signups(d):
    rows = _shift_rows(d, "Harbor Canvass Launch", "Morning")
    declined = sum(1 for r in rows if r["status"]["name"] == "Declined")
    return [[str(len(rows))], [str(declined), "one" if declined == 1 else str(declined)]]


@truth
def hannah_codes(d):
    p = _person(d, "Hannah", "Whitfield")
    return [[a["activistCodeName"]] for a in d["personActivistCodes"][str(p["vanId"])]]


@truth
def dana_shifts(d):
    p = _person(d, "Dana", "Marchetti")
    n = sum(1 for s in d["signups"] if s["person"]["vanId"] == p["vanId"])
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    return [[str(n), words.get(n, str(n))]]


@truth
def history_first_ten(d):
    ids = [str(v) for v in range(100200101, 100200111)]
    items = [i for v in ids for i in d["canvassHistory"][v]]
    strong = sum(1 for i in items for r in i["responses"] if r["surveyResponseId"] == 52011)
    return [[str(len(items))], [str(strong)]]


@truth
def result_codes(d):
    return [["Not Home"], ["Wrong Number"]]


@truth
def next_week_phone_banks(d):
    return [[e["name"]] for e in d["events"]
            if e["eventType"]["name"] == "Phone Bank" and "2026-11-23" <= e["startDate"][:10] <= "2026-11-29"]


def pii_values(d: dict[str, Any]) -> list[str]:
    """Every phone (several spellings), email, street address and birth date in the data."""
    out: list[str] = []
    for p in d["people"]:
        dob = p["dateOfBirth"][:10]
        y, m, day = dob.split("-")
        out += [dob, f"{int(m)}/{int(day)}/{y}", f"{m}/{day}/{y}"]
        for ph in p["_contact"]["phones"]:
            n = ph["phoneNumber"]
            out += [n, f"{n[:3]}-{n[3:6]}-{n[6:]}", f"({n[:3]}) {n[3:6]}-{n[6:]}", f"{n[3:6]}-{n[6:]}"]
        out += [e["email"] for e in p["_contact"]["emails"]]
        out += [a["addressLine1"] for a in p["_contact"]["addresses"]]
    return out
