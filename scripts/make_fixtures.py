"""Generate the fake Riverbend data in src/ngpvan_mcp/demo/riverbend.json.

Everything here is made up: the program, the people, their phone numbers
(all in the 555-01xx fictional range), streets and events. Records follow
the response shapes in the NGP VAN API docs. Run with a fixed seed so the
file, and the eval ground truth computed from it, are reproducible:

    uv run python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "ngpvan_mcp" / "demo" / "riverbend.json"
rng = random.Random(20261120)

FIRST = [
    "Maria", "Dana", "Jordan", "Priya", "Marcus", "Elena", "Tobias", "Keisha", "Owen", "Lucia",
    "Samir", "Hannah", "Diego", "Grace", "Malik", "Ingrid", "Theo", "Aisha", "Rowan", "Beatriz",
    "Felix", "Noor", "Calvin", "Yesenia", "Arjun", "Wren", "Desmond", "Tamsin", "Ezra", "Lena",
]
LAST = [
    "Galloway", "Okafor", "Lindqvist", "Ramirez", "Thornbury", "Achebe", "Castellanos", "Whitfield",
    "Nakamura", "Brennan", "Oduya", "Haverford", "Delacroix", "Pemberton", "Quintero", "Ashdown",
    "Marchetti", "Ellery", "Vasquez", "Holloway", "Kowalczyk", "Sorensen", "Abernathy", "Fairbanks",
]
STREETS = ["Maple", "Juniper", "Willow", "Cedar", "Larkspur", "Foundry", "Quarry", "Ferry", "Pier", "Beacon"]
OFFICES = {
    "Northside": {"city": "Riverbend", "zip": "80901", "streets": STREETS[:4]},
    "Eastgate": {"city": "Eastgate", "zip": "80902", "streets": STREETS[4:7]},
    "Harbor": {"city": "Harbor Point", "zip": "80903", "streets": STREETS[7:]},
}

ACTIVIST_CODES = [
    {"activistCodeId": 4101, "type": "Volunteer", "name": "Volunteer", "mediumName": "Vol", "shortName": "VOL",
     "description": "Has volunteered at least once", "scriptQuestion": "Would you volunteer with us?", "status": "Active"},
    {"activistCodeId": 4102, "type": "Volunteer", "name": "Phone Banker", "mediumName": "PhoneB", "shortName": "PB",
     "description": "Makes calls", "scriptQuestion": "Can you make calls from home?", "status": "Active"},
    {"activistCodeId": 4103, "type": "Volunteer", "name": "Canvasser", "mediumName": "Canv", "shortName": "CV",
     "description": "Knocks doors", "scriptQuestion": "Can you knock doors with us?", "status": "Active"},
    {"activistCodeId": 4104, "type": "Volunteer", "name": "Event Host", "mediumName": "Host", "shortName": "EH",
     "description": "Has hosted an event", "scriptQuestion": "Would you host a house meeting?", "status": "Active"},
    {"activistCodeId": 4105, "type": "Constituency", "name": "Renter", "mediumName": "Renter", "shortName": "RN",
     "description": "Self-identified renter", "scriptQuestion": None, "status": "Active"},
    {"activistCodeId": 4099, "type": "Volunteer", "name": "Spring Petition Signer", "mediumName": "SprPet",
     "shortName": "SP", "description": "Signed the spring petition", "scriptQuestion": None, "status": "Archived"},
]

SURVEY_QUESTIONS = [
    {"surveyQuestionId": 5201, "type": "Ballot Measure", "cycle": 2026, "name": "Measure 3 Support",
     "mediumName": "M3", "shortName": "M3", "scriptQuestion": "Can we count on you to vote yes on Measure 3, the transit levy?",
     "status": "Active", "responses": [
        {"surveyResponseId": 52011, "name": "1 Strong Yes", "mediumName": "1", "shortName": "1"},
        {"surveyResponseId": 52012, "name": "2 Lean Yes", "mediumName": "2", "shortName": "2"},
        {"surveyResponseId": 52013, "name": "3 Undecided", "mediumName": "3", "shortName": "3"},
        {"surveyResponseId": 52014, "name": "4 Lean No", "mediumName": "4", "shortName": "4"},
        {"surveyResponseId": 52015, "name": "5 Strong No", "mediumName": "5", "shortName": "5"}]},
    {"surveyQuestionId": 5202, "type": "Volunteer", "cycle": 2026, "name": "Volunteer Ask",
     "mediumName": "VolAsk", "shortName": "VA", "scriptQuestion": "Would you join us for a shift this month?",
     "status": "Active", "responses": [
        {"surveyResponseId": 52021, "name": "Yes", "mediumName": "Y", "shortName": "Y"},
        {"surveyResponseId": 52022, "name": "Maybe", "mediumName": "M", "shortName": "M"},
        {"surveyResponseId": 52023, "name": "No", "mediumName": "N", "shortName": "N"}]},
    {"surveyQuestionId": 5203, "type": "Issue", "cycle": 2026, "name": "Top Issue",
     "mediumName": "Issue", "shortName": "TI", "scriptQuestion": "What issue matters most to you this year?",
     "status": "Active", "responses": [
        {"surveyResponseId": 52031, "name": "Housing", "mediumName": "Hous", "shortName": "H"},
        {"surveyResponseId": 52032, "name": "Transit", "mediumName": "Trans", "shortName": "T"},
        {"surveyResponseId": 52033, "name": "Schools", "mediumName": "Sch", "shortName": "S"},
        {"surveyResponseId": 52034, "name": "Jobs", "mediumName": "Jobs", "shortName": "J"}]},
    {"surveyQuestionId": 5199, "type": "Event", "cycle": 2026, "name": "Spring Town Hall RSVP",
     "mediumName": "THRSVP", "shortName": "TH", "scriptQuestion": "Will you come to the spring town hall?",
     "status": "Archived", "responses": [
        {"surveyResponseId": 51991, "name": "Yes", "mediumName": "Y", "shortName": "Y"},
        {"surveyResponseId": 51992, "name": "No", "mediumName": "N", "shortName": "N"}]},
]

RESULT_CODES = [
    {"resultCodeId": 1, "name": "Not Home", "mediumName": "NH", "shortName": "NH"},
    {"resultCodeId": 2, "name": "Refused", "mediumName": "Ref", "shortName": "RF"},
    {"resultCodeId": 5, "name": "Moved", "mediumName": "Moved", "shortName": "MV"},
    {"resultCodeId": 18, "name": "Busy", "mediumName": "Busy", "shortName": "BZ"},
    {"resultCodeId": 19, "name": "Left Message", "mediumName": "LM", "shortName": "LM"},
    {"resultCodeId": 20, "name": "Wrong Number", "mediumName": "Wr#", "shortName": "WN"},
    {"resultCodeId": 25, "name": "Disconnected", "mediumName": "Disc", "shortName": "WX"},
]
CONTACT_TYPES = [
    {"contactTypeId": 1, "name": "Phone"},
    {"contactTypeId": 2, "name": "Walk"},
    {"contactTypeId": 37, "name": "SMS Text"},
]
SIGNUP_STATUSES = [
    {"statusId": 1, "name": "Scheduled"},
    {"statusId": 11, "name": "Confirmed"},
    {"statusId": 2, "name": "Completed"},
    {"statusId": 3, "name": "Declined"},
    {"statusId": 4, "name": "Invited"},
    {"statusId": 6, "name": "No Show"},
    {"statusId": 15, "name": "Walk In"},
]
EVENT_TYPES = {
    "Canvass": {"eventTypeId": 801, "name": "Canvass"},
    "Phone Bank": {"eventTypeId": 802, "name": "Phone Bank"},
    "Training": {"eventTypeId": 803, "name": "Training"},
}
ROLES = {"Volunteer": {"roleId": 901, "name": "Volunteer"}, "Host": {"roleId": 902, "name": "Host"}}
LOCATIONS = {
    "Northside": {"locationId": 601, "name": "Northside Office",
                  "displayName": "Northside Office, 410 Maple St Riverbend, ZZ 80901 "},
    "Eastgate": {"locationId": 602, "name": "Eastgate Office",
                 "displayName": "Eastgate Office, 88 Foundry Ave Eastgate, ZZ 80902 "},
    "Harbor": {"locationId": 603, "name": "Harbor Office",
               "displayName": "Harbor Office, 12 Pier Rd Harbor Point, ZZ 80903 "},
}


def phone(n: int) -> str:
    # 555-0100 to 555-0199 is the range reserved for fictional use.
    return f"970555{100 + n:04d}"


def make_people() -> list[dict]:
    people = []
    used = set()
    van_id = 100200101
    office_names = list(OFFICES)
    for i in range(48):
        while True:
            first, last = rng.choice(FIRST), rng.choice(LAST)
            if (first, last) not in used:
                used.add((first, last))
                break
        office = office_names[i % 3]
        o = OFFICES[office]
        street = f"{rng.randint(10, 2999)} {rng.choice(o['streets'])} St"
        year = rng.randint(1950, 2004)
        person = {
            "vanId": van_id,
            "firstName": first,
            "middleName": rng.choice([None, "J", "Ann", "Lee", None]),
            "lastName": last,
            "suffix": None,
            "dateOfBirth": f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T00:00:00Z",
            "contactSource": "Demo",
            "contactMode": "Person",
            "salutation": first,
            "envelopeName": f"{first} {last}",
            "isDeceased": False,
            "dateCreated": "2026-03-02T16:00:00Z",
            "emails": None,
            "phones": None,
            "addresses": None,
        }
        emails = []
        if rng.random() < 0.7:
            emails.append({"type": "P", "email": f"{first.lower()}.{last.lower()}@example.org",
                           "dateCreated": "2026-03-02T16:00:00Z", "isPreferred": True,
                           "isSubscribed": True, "subscriptionStatus": "S"})
        phones = []
        if rng.random() < 0.8:
            cell = rng.random() < 0.75
            phones.append({"phoneId": 700000 + i, "phoneNumber": phone(i), "dialingPrefix": "1",
                           "countryCode": "US", "ext": None, "dateCreated": "2026-03-02T16:00:00Z",
                           "phoneType": "Cell" if cell else "Home", "deviceType": "Cell" if cell else "Landline",
                           "isPreferred": True, "isBest": True, "isDisclosure": False,
                           "smsOptInStatus": rng.choice(["Opt-In", "Opt-In", "Unknown", "Opt-Out"]) if cell else "Unknown",
                           "phoneOptInStatus": "Unknown", "isCellStatus": None})
        addresses = [{"addressId": 800000 + i, "addressLine1": street, "addressLine2": None, "addressLine3": None,
                      "unitNo": None, "city": o["city"], "stateOrProvince": "ZZ", "zipOrPostalCode": o["zip"],
                      "geoLocation": None, "countryCode": "US", "preview": street, "type": "Voting",
                      "isPreferred": True, "isBest": True, "streetAddress": None, "displayMode": "Standardized"}]
        person["_contact"] = {"emails": emails, "phones": phones, "addresses": addresses}
        person["_office"] = office
        people.append(person)
        van_id += 1
    return people


def make_activist_code_links(people: list[dict]) -> dict[str, list[dict]]:
    links = {}
    names = {c["activistCodeId"]: c["name"] for c in ACTIVIST_CODES}
    for p in people:
        chosen = [c for c in (4101, 4102, 4103, 4104, 4105) if rng.random() < 0.3]
        if 4102 in chosen or 4103 in chosen or 4104 in chosen:
            chosen = sorted(set(chosen) | {4101})
        links[str(p["vanId"])] = [
            {"activistCodeId": c, "activistCodeName": names[c],
             "dateCreated": f"2026-{rng.randint(4, 10):02d}-{rng.randint(1, 28):02d}T15:00:00Z",
             "canvassedBy": None,
             "dateCanvassed": f"2026-{rng.randint(4, 10):02d}-{rng.randint(1, 28):02d}T15:00:00Z"}
            for c in chosen
        ]
    return links


def make_canvass_history(people: list[dict]) -> dict[str, list[dict]]:
    """Per-person contact history.

    The docs publish the POST body for canvass responses but not the GET
    response, so each item mirrors the POST body (canvassContext,
    resultCodeId, responses) plus the canvassResponseId the docs say GET
    returns.
    """
    hist = {}
    next_id = 910001
    for p in people:
        items = []
        for _ in range(rng.choice([0, 1, 1, 2, 2, 3, 4])):
            ctype = rng.choice([1, 2, 2, 37])
            day = rng.randint(1, 19)
            if rng.random() < 0.45:
                result = rng.choice([1, 1, 2, 5]) if ctype == 2 else rng.choice([18, 19, 20, 25, 2])
                responses = []
            else:
                result = None
                responses = [{"type": "SurveyResponse", "surveyQuestionId": 5201,
                              "surveyResponseId": rng.choice([52011, 52011, 52012, 52013, 52014, 52015])}]
                if rng.random() < 0.4:
                    responses.append({"type": "SurveyResponse", "surveyQuestionId": 5202,
                                      "surveyResponseId": rng.choice([52021, 52022, 52023])})
                if rng.random() < 0.3:
                    responses.append({"type": "SurveyResponse", "surveyQuestionId": 5203,
                                      "surveyResponseId": rng.choice([52031, 52032, 52033, 52034])})
            items.append({"canvassResponseId": next_id,
                          "canvassContext": {"contactTypeId": ctype, "inputTypeId": 11,
                                             "dateCanvassed": f"2026-11-{day:02d}"},
                          "resultCodeId": result, "responses": responses})
            next_id += 1
        items.sort(key=lambda r: r["canvassContext"]["dateCanvassed"])
        hist[str(p["vanId"])] = items
    return hist


def shift(sid: int, name: str, day: str, start: str, end: str) -> dict:
    return {"eventShiftId": sid, "name": name, "startTime": f"{day}T{start}:00-07:00", "endTime": f"{day}T{end}:00-07:00"}


def make_events() -> list[dict]:
    spec = [
        (3001, "Northside Weekend Canvass", "NS Canv", "Canvass", "Northside", "2026-11-21",
         [(7001, "Morning", "09:00", "12:00"), (7002, "Afternoon", "13:00", "16:00")],
         "Meet at the Northside Office. Questions? Text Dana at (970) 555-0188."),
        (3002, "Eastgate Phone Bank", "EG PB", "Phone Bank", "Eastgate", "2026-11-21",
         [(7003, "Evening", "17:00", "20:00")], "Bring a charged phone and headphones."),
        (3003, "Harbor Canvass Launch", "HB Canv", "Canvass", "Harbor", "2026-11-22",
         [(7004, "Morning", "10:00", "13:00"), (7005, "Afternoon", "14:00", "17:00")],
         "Launch at the Harbor Office, 12 Pier Rd. Host contact: harbor.lead@example.org."),
        (3004, "Volunteer Training", "Training", "Training", "Northside", "2026-11-19",
         [(7006, "Evening", "18:00", "20:00")], "New volunteer orientation."),
        (3005, "Eastgate Weekend Canvass", "EG Canv", "Canvass", "Eastgate", "2026-11-22",
         [(7007, "Morning", "09:30", "12:30")], "Turf packets ready at 9:15."),
        (3006, "Harbor Phone Bank", "HB PB", "Phone Bank", "Harbor", "2026-11-24",
         [(7008, "Evening", "17:30", "20:30")], "Calls for the Measure 3 ID program."),
    ]
    events = []
    for eid, name, short, etype, office, day, shifts, desc in spec:
        sh = [shift(sid, sname, day, s, e) for sid, sname, s, e in shifts]
        events.append({
            "eventId": eid, "name": name, "shortName": short, "description": desc,
            "startDate": sh[0]["startTime"], "endDate": sh[-1]["endTime"],
            "eventType": EVENT_TYPES[etype], "isOnlyEditableByCreatingUser": False,
            "isPubliclyViewable": None, "locations": [LOCATIONS[office]], "codes": None, "notes": None,
            "shifts": sh, "roles": list(ROLES.values()), "districtFieldValue": None,
            "voterRegistrationBatches": None, "createdDate": "2026-11-01T17:00:00Z",
            "isActive": True, "dotNetTimeZoneId": "Mountain Standard Time", "_office": office,
        })
    return events


def make_signups(events: list[dict], people: list[dict]) -> list[dict]:
    status = {s["name"]: s for s in SIGNUP_STATUSES}
    future = ["Scheduled", "Scheduled", "Confirmed", "Confirmed", "Confirmed", "Invited", "Declined"]
    past = ["Completed", "Completed", "Completed", "No Show", "Walk In"]
    signups = []
    sid = 2450001
    for ev in events:
        office_people = [p for p in people if p["_office"] == ev["_office"]]
        is_past = ev["startDate"] < "2026-11-20"
        for sh in ev["shifts"]:
            n = rng.randint(4, 9)
            for p in rng.sample(office_people, n):
                st = rng.choice(past if is_past else future)
                role = ROLES["Host"] if rng.random() < 0.1 else ROLES["Volunteer"]
                signups.append({
                    "eventSignupId": sid,
                    "person": {"vanId": p["vanId"], "firstName": p["firstName"], "middleName": p["middleName"],
                               "lastName": p["lastName"]},
                    "event": {"eventId": ev["eventId"], "name": ev["name"], "shortName": ev["shortName"]},
                    "shift": {"eventShiftId": sh["eventShiftId"], "name": sh["name"]},
                    "role": role,
                    "status": status[st],
                    "location": {k: LOCATIONS[ev["_office"]][k] for k in ("locationId", "name", "displayName")},
                    "startTimeOverride": sh["startTime"],
                    "endTimeOverride": sh["endTime"],
                })
                sid += 1
    return signups


def make_lists() -> tuple[list[dict], list[dict]]:
    folders = [
        {"folderId": 44, "name": "Northside Turfs"},
        {"folderId": 45, "name": "Eastgate Turfs"},
        {"folderId": 46, "name": "Harbor Turfs"},
        {"folderId": 47, "name": "Phone Bank Lists"},
        {"folderId": 48, "name": "Archive"},
    ]
    lists = []
    lid = 990100
    turf_streets = {44: STREETS[:4], 45: STREETS[4:7], 46: STREETS[7:]}
    prefixes = {44: "NS", 45: "EG", 46: "HB"}
    for fid, n in ((44, 5), (45, 4), (46, 4)):
        for t in range(1, n + 1):
            doors = rng.randint(60, 140)
            people_count = doors + rng.randint(20, 80)
            street = turf_streets[fid][(t - 1) % len(turf_streets[fid])]
            lists.append({"savedListId": lid, "name": f"{prefixes[fid]} Turf {t:02d} {street}",
                          "description": f"Weekend canvass turf {t}", "listCount": people_count,
                          "doorCount": doors, "_folderId": fid})
            lid += 1
    for name, count in (("EG Phone Bank Measure 3 IDs", 640), ("HB Phone Bank Measure 3 IDs", 410)):
        lists.append({"savedListId": lid, "name": name, "description": "Undecided and lean voters with a phone",
                      "listCount": count, "doorCount": int(count * 0.7), "_folderId": 47})
        lid += 1
    lists.append({"savedListId": lid, "name": "Spring Petition Followup", "description": "Old list",
                  "listCount": 220, "doorCount": 150, "_folderId": 48})
    return folders, lists


def main() -> None:
    people = make_people()
    events = make_events()
    folders, lists = make_lists()
    data = {
        "_note": "Fictional demo data for ngpvan-mcp. No real people, programs or organizations.",
        "today": "2026-11-20",
        "apiKeyProfile": {"databaseName": "Demo VAN (fake data)", "hasMyVoters": False, "hasMyCampaign": True,
                          "committeeName": "Riverbend Votes (demo)", "apiKeyTypeName": "Custom Integration",
                          "keyReference": "0000", "userFirstName": "riverbend", "userLastName": "api",
                          "username": "riverbend.api", "userId": 1},
        "people": people,
        "activistCodes": ACTIVIST_CODES,
        "personActivistCodes": make_activist_code_links(people),
        "surveyQuestions": SURVEY_QUESTIONS,
        "canvassHistory": make_canvass_history(people),
        "resultCodes": RESULT_CODES,
        "contactTypes": CONTACT_TYPES,
        "signupStatuses": SIGNUP_STATUSES,
        "events": events,
        "signups": make_signups(events, people),
        "folders": folders,
        "savedLists": lists,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {OUT} ({len(people)} people, {len(data['signups'])} signups, {len(lists)} saved lists)")


if __name__ == "__main__":
    main()
