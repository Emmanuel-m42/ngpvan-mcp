"""Each tool against the fake VAN. Expected values are computed here straight from the
fixture data, not from the tools, so a tool bug can't make its own test pass."""

from __future__ import annotations

from collections import Counter

import pytest

from ngpvan_mcp import tools as t
from ngpvan_mcp.client import VanApiError
from ngpvan_mcp.config import Config


async def test_check_connection(tb):
    out = await t.check_connection(tb)
    assert out["committee"] == "Riverbend Votes (demo)"
    assert out["database_mode"] == "My Campaign"
    assert out["write_tools_enabled"] == []
    assert "username" not in out and "userId" not in out


async def test_search_people_prefix_and_count(tb, demo_data):
    expected = [p for p in demo_data["people"] if p["lastName"].lower().startswith("ok")]
    out = await t.search_people(tb, last_name="ok")
    assert out["total_matches"] == len(expected)
    assert {p["van_id"] for p in out["people"]} == {p["vanId"] for p in expected}
    assert all(p["name"].endswith(".") for p in out["people"])


async def test_search_people_count_is_vans_total_not_the_page(tb, demo_data):
    expected = sum(1 for p in demo_data["people"] if p["_office"] == "Northside")
    out = await t.search_people(tb, city="Riverbend", limit=3)
    assert out["returned"] == 3
    assert out["total_matches"] == expected


async def test_search_people_needs_a_criterion(tb):
    with pytest.raises(t.ToolInputError):
        await t.search_people(tb)


async def test_search_people_by_name_is_off_when_names_are_off(van):
    tb = t.Toolbox(van=van, config=Config(audit_log=None, names="none"))
    with pytest.raises(t.ToolInputError):
        await t.search_people(tb, first_name="Maria")


async def test_name_policy_full(van, demo_data):
    tb = t.Toolbox(van=van, config=Config(audit_log=None, names="full"))
    p = demo_data["people"][0]
    out = await t.get_person(tb, p["vanId"])
    assert out["name"] == f"{p['firstName']} {p['lastName']}"


async def test_get_person_flags(tb, demo_data):
    for p in demo_data["people"]:
        out = await t.get_person(tb, p["vanId"])
        c = p["_contact"]
        assert out["contact_on_file"]["has_phone"] == bool(c["phones"])
        assert out["contact_on_file"]["has_email"] == bool(c["emails"])
        assert out["city"] == c["addresses"][0]["city"]
        cell = [ph for ph in c["phones"] if ph["phoneType"] == "Cell"]
        assert out["contact_on_file"]["sms_opt_in"] == (cell[0]["smsOptInStatus"] if cell else None)


async def test_get_person_missing(tb):
    with pytest.raises(VanApiError) as err:
        await t.get_person(tb, 1)
    assert err.value.kind == "not_found"


async def test_person_activist_codes(tb, demo_data):
    vid, links = next((k, v) for k, v in demo_data["personActivistCodes"].items() if len(v) >= 2)
    out = await t.person_activist_codes(tb, int(vid))
    assert out["count"] == len(links)
    assert {a["name"] for a in out["activist_codes"]} == {link["activistCodeName"] for link in links}


async def test_person_signups(tb, demo_data):
    counts = Counter(s["person"]["vanId"] for s in demo_data["signups"])
    vid, n = counts.most_common(1)[0]
    out = await t.person_signups(tb, vid)
    assert out["count"] == n


async def test_contact_history_summary_matches_fixture(tb, demo_data):
    ids = [p["vanId"] for p in demo_data["people"][:20]]
    walk = phone = canvassed = 0
    m3: Counter[int] = Counter()
    for vid in ids:
        for item in demo_data["canvassHistory"][str(vid)]:
            ct = item["canvassContext"]["contactTypeId"]
            walk += ct == 2
            phone += ct == 1
            canvassed += item["resultCodeId"] is None
            for r in item["responses"]:
                if r["surveyQuestionId"] == 5201:
                    m3[r["surveyResponseId"]] += 1
    out = await t.contact_history_summary(tb, ids)
    assert out["by_contact_type"].get("Walk", 0) == walk
    assert out["by_contact_type"].get("Phone", 0) == phone
    assert out["by_result"].get("Canvassed", 0) == canvassed
    assert out["survey_responses"]["Measure 3 Support"].get("1 Strong Yes", 0) == m3[52011]


async def test_contact_history_since_filters(tb, demo_data):
    ids = [p["vanId"] for p in demo_data["people"][:25]]
    expected = sum(1 for v in ids for i in demo_data["canvassHistory"][str(v)]
                   if i["canvassContext"]["dateCanvassed"] >= "2026-11-15")
    out = await t.contact_history_summary(tb, ids, since="2026-11-15")
    assert out["total_attempts"] == expected


async def test_contact_history_caps_people(tb):
    with pytest.raises(t.ToolInputError, match="Export Job"):
        await t.contact_history_summary(tb, list(range(100200101, 100200140)))


async def test_list_events_is_inclusive_of_both_dates(tb, demo_data):
    expected = {e["eventId"] for e in demo_data["events"] if "2026-11-21" <= e["startDate"][:10] <= "2026-11-22"}
    out = await t.list_events(tb, "2026-11-21", "2026-11-22")
    assert {e["event_id"] for e in out["events"]} == expected
    assert all(e["shifts"] for e in out["events"])


async def test_list_events_type_filter(tb):
    out = await t.list_events(tb, "2026-11-01", "2026-11-30", event_type="phone bank")
    assert {e["type"] for e in out["events"]} == {"Phone Bank"}


async def test_list_events_bad_dates(tb):
    with pytest.raises(t.ToolInputError):
        await t.list_events(tb, "tomorrow")
    with pytest.raises(t.ToolInputError):
        await t.list_events(tb, "2026-11-22", "2026-11-21")


async def test_event_signups_summary_by_shift(tb, demo_data):
    rows = [s for s in demo_data["signups"] if s["shift"]["eventShiftId"] == 7004]
    committed = sum(1 for s in rows if s["status"]["name"] in t.COMMITTED)
    out = await t.event_signups_summary(tb, 3003, shift="Morning")
    (shift,) = out["shifts"]
    assert shift["signed_up"] == len(rows)
    assert shift["committed"] == committed
    assert {r["van_id"] for r in shift["roster"]} == {s["person"]["vanId"] for s in rows}


async def test_event_signups_unknown_shift_lists_the_real_ones(tb):
    with pytest.raises(t.ToolInputError, match="Morning"):
        await t.event_signups_summary(tb, 3001, shift="Overnight")


async def test_event_signups_without_roster(tb):
    out = await t.event_signups_summary(tb, 3001, include_roster=False)
    assert all("roster" not in s for s in out["shifts"])


async def test_turf_door_counts_one_folder(tb, demo_data):
    lists = [sl for sl in demo_data["savedLists"] if sl["_folderId"] == 45]
    out = await t.turf_door_counts(tb, folder_name="eastgate")
    (folder,) = out["folders"]
    assert folder["doors"] == sum(sl["doorCount"] for sl in lists)
    assert folder["people"] == sum(sl["listCount"] for sl in lists)
    assert folder["lists"] == len(lists)


async def test_turf_door_counts_all(tb, demo_data):
    out = await t.turf_door_counts(tb)
    assert out["total_doors"] == sum(sl["doorCount"] for sl in demo_data["savedLists"])


async def test_turf_door_counts_unknown_or_ambiguous_folder(tb):
    with pytest.raises(t.ToolInputError, match="Northside Turfs"):
        await t.turf_door_counts(tb, folder_name="Westside")
    with pytest.raises(t.ToolInputError, match="more than one"):
        await t.turf_door_counts(tb, folder_name="Turfs")


async def test_list_survey_questions(tb, demo_data):
    active = [q for q in demo_data["surveyQuestions"] if q["status"] == "Active"]
    out = await t.list_survey_questions(tb)
    assert out["count"] == len(active)
    archived = await t.list_survey_questions(tb, include_archived=True)
    assert archived["count"] == len(demo_data["surveyQuestions"])


async def test_list_activist_codes(tb, demo_data):
    vol = [a for a in demo_data["activistCodes"] if a["status"] == "Active" and a["type"] == "Volunteer"]
    out = await t.list_activist_codes(tb, code_type="Volunteer")
    assert {a["name"] for a in out["activist_codes"]} == {a["name"] for a in vol}


async def test_canvass_codes(tb, demo_data):
    out = await t.canvass_codes(tb)
    assert len(out["result_codes"]) == len(demo_data["resultCodes"])
    assert {"id": 2, "name": "Walk"} in out["contact_types"]
