from __future__ import annotations

import json

import pytest

from ngpvan_mcp import tools as t
from ngpvan_mcp.privacy import display_name, mask_args, mask_text, scrub


def test_scrub_drops_personal_keys_at_any_depth():
    record = {
        "vanId": 1,
        "firstName": "Maria",
        "phones": [{"phoneNumber": "9705550101"}],
        "nested": {"emails": [{"email": "m@example.org"}], "dateOfBirth": "1980-01-01", "ok": 3},
        "rows": [{"addressLine1": "12 Pier Rd", "city": "Harbor Point"}],
    }
    out = scrub(record)
    assert out == {"vanId": 1, "firstName": "Maria", "nested": {"ok": 3}, "rows": [{"city": "Harbor Point"}]}


def test_scrub_key_match_ignores_case():
    assert scrub({"PhoneNumber": "x", "EMAIL": "y", "Keep": 1}) == {"Keep": 1}


@pytest.mark.parametrize(
    "text",
    [
        "call me at (970) 555-0188",
        "call me at 970-555-0188",
        "call me at 9705550188",
        "call me at +1 970.555.0188",
    ],
)
def test_mask_text_phones(text):
    assert "555" not in mask_text(text)
    assert "[phone removed]" in mask_text(text)


def test_mask_text_email_address_and_dob():
    text = "Write harbor.lead@example.org, meet at 12 Pier Rd, DOB 03/14/1988."
    out = mask_text(text)
    assert "example.org" not in out
    assert "12 Pier Rd" not in out
    assert "1988" not in out
    assert "[email removed]" in out and "[address removed]" in out and "[birth date removed]" in out


def test_mask_text_leaves_ordinary_numbers():
    text = "Turf 03 has 103 doors and 147 people; shift starts 2026-10-19T09:00:00-06:00"
    assert mask_text(text) == text


@pytest.mark.parametrize(
    "policy,expected",
    [("full", "Maria Marlowe"), ("initial", "Maria M."), ("none", None)],
)
def test_display_name_policies(policy, expected):
    assert display_name("Maria", "Marlowe", policy) == expected


def test_mask_args_redacts_names_but_keeps_ids():
    out = mask_args({"first_name": "Maria", "last_name": None, "van_id": 100200101, "limit": 5})
    assert out == {"first_name": "[redacted, 5 chars]", "last_name": "[none]", "van_id": 100200101, "limit": 5}


def _pii_values(data) -> set[str]:
    """Every phone, email, street, ZIP and birth date in the demo data, collected straight from the fixture."""
    values: set[str] = set()
    for p in data["people"]:
        values.add(p["dateOfBirth"][:10])
        for ph in p["_contact"]["phones"]:
            n = ph["phoneNumber"]
            values.update({n, f"{n[:3]}-{n[3:6]}-{n[6:]}", f"{n[3:6]}-{n[6:]}"})
        for e in p["_contact"]["emails"]:
            values.add(e["email"])
        for a in p["_contact"]["addresses"]:
            values.add(a["addressLine1"])
    return values


async def _all_tool_outputs(tb, data) -> list[dict]:
    ids = [p["vanId"] for p in data["people"]]
    outs = [
        await t.check_connection(tb),
        await t.search_people(tb, city="Riverbend", limit=25),
        await t.search_people(tb, last_name="O"),
        await t.list_events(tb, "2026-10-01", "2026-10-31"),
        await t.list_folders(tb),
        await t.turf_door_counts(tb),
        await t.list_survey_questions(tb, include_archived=True),
        await t.list_activist_codes(tb, include_archived=True),
        await t.canvass_codes(tb),
        await t.contact_history_summary(tb, ids[:25]),
        await t.contact_history_summary(tb, ids[25:]),
    ]
    for vid in ids:
        outs.append(await t.get_person(tb, vid))
        outs.append(await t.person_activist_codes(tb, vid))
        outs.append(await t.person_signups(tb, vid))
    for ev in data["events"]:
        outs.append(await t.event_signups_summary(tb, ev["eventId"]))
    return outs


async def test_no_personal_value_from_the_data_reaches_any_tool_output(tb, demo_data):
    """The end-to-end privacy check: run every read tool over every person and event,
    apply the server's scrub, and look for any phone, email, street or birth date."""
    pii = _pii_values(demo_data)
    assert len(pii) > 100
    for out in await _all_tool_outputs(tb, demo_data):
        text = json.dumps(scrub(out))
        leaked = [v for v in pii if v in text]
        assert not leaked, f"{out['source']['tool']} leaked {leaked[:3]}"


async def test_tools_dont_rely_on_scrub_for_person_contact_values(tb, demo_data):
    """Layer 1 on its own: even before scrub, get_person carries no contact values."""
    pii = _pii_values(demo_data)
    for p in demo_data["people"]:
        text = json.dumps(await t.get_person(tb, p["vanId"]))
        assert not [v for v in pii if v in text]


async def test_scrub_keeps_the_fields_tools_mean_to_return(tb):
    """The key filter must not silently eat a tool's own output fields."""
    raw = await t.get_person(tb, 100200102)
    assert scrub(raw) == raw
    assert set(raw["contact_on_file"]) == {
        "has_phone", "has_cell_phone", "sms_opt_in", "has_email", "email_subscribed", "has_address"
    }


async def test_free_text_in_event_descriptions_is_masked(tb):
    out = await t.list_events(tb, "2026-10-19", "2026-10-25")
    text = json.dumps(out)
    assert "555-0188" not in text
    assert "harbor.lead@example.org" not in text
    assert "[phone removed]" in text
