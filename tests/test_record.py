"""The demo recorder, run with the scripted stand-in (no model)."""

from __future__ import annotations

import json
import re

import pytest

from ngpvan_mcp.demo.fake_van import load_demo_data
from ngpvan_mcp.evals.record import PROMPTS, SCRIPTED_INFO, ScriptedRecorder, find_masked_fields, main, record
from ngpvan_mcp.evals.truth import pii_values


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    out = tmp_path_factory.mktemp("rec") / "recorded_runs.json"
    assert main(["--provider", "scripted", "--out", str(out)]) == 0
    return json.loads(out.read_text())


def resolve(obj, path: str):
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        obj = obj[int(part[1:-1])] if part.startswith("[") else obj[part]
    return obj


def test_top_level(doc):
    assert set(doc) >= {"program", "generated_at", "model", "hardware", "data_note", "card", "questions"}
    assert doc["model"]["tag"] == "scripted" and "not a model" in doc["model"]["note"]
    assert 4 <= len(doc["card"]["points"]) <= 6
    assert "fictional" in doc["data_note"].lower()


def test_every_question_has_the_page_fields(doc):
    ids = [q["id"] for q in doc["questions"]]
    assert ids == [p["id"] for p in PROMPTS] and len(set(ids)) == len(ids)
    for q in doc["questions"]:
        assert {"id", "category", "office", "question", "tool_calls", "write", "answer", "latency_ms", "audit"} <= set(q)
        assert q["office"] in {"Northside", "Eastgate", "Harbor", "All offices"}
        assert q["answer"] and not q["error"]
        for call in q["tool_calls"]:
            assert {"name", "args", "raw", "masked", "masked_fields", "latency_ms"} <= set(call)


def test_audit_lines_are_the_servers_lines_one_per_call(doc):
    for q in doc["questions"]:
        lines = [json.loads(line) for line in q["audit"]]
        assert [line["tool"] for line in lines] == [c["name"] for c in q["tool_calls"]]


def test_masked_field_paths_resolve(doc):
    for q in doc["questions"]:
        for call in q["tool_calls"]:
            for field in call["masked_fields"]:
                resolve(call, field["path"])


def test_phone_is_in_raw_and_highlighted_but_not_in_what_the_model_saw(doc):
    q = next(q for q in doc["questions"] if q["id"] == "volunteer-phone")
    get_person = next(c for c in q["tool_calls"] if c["name"] == "get_person")
    phones = resolve(get_person, "raw[0].body.phones")
    assert phones and phones[0]["phoneNumber"].startswith("970555")
    assert {"path": "raw[0].body.phones", "kind": "phone", "action": "removed"} in get_person["masked_fields"]
    assert phones[0]["phoneNumber"] not in json.dumps(get_person["masked"])
    assert phones[0]["phoneNumber"] not in q["answer"]


def test_no_personal_value_reaches_any_masked_result_or_answer(doc):
    pii = pii_values(load_demo_data())
    for q in doc["questions"]:
        seen = json.dumps([c["masked"] for c in q["tool_calls"]]) + q["answer"] + "".join(q["audit"])
        assert not [v for v in pii if v in seen], q["id"]


def test_raw_phones_are_in_the_fictional_range(doc):
    text = json.dumps([c["raw"] for q in doc["questions"] for c in q["tool_calls"]])
    for number in re.findall(r"\b\d{10}\b", text):
        assert number[3:7] == "5550" and 100 <= int(number[6:]) <= 199, number


def test_write_blocked_then_dry_run(doc):
    blocked = next(q for q in doc["questions"] if q["id"] == "write-blocked")
    assert "apply_canvass_response" not in blocked["tools_offered"]
    assert blocked["write"]["blocked"] and not blocked["write"]["attempted"] and blocked["write"]["dry_run"] is None
    dry = next(q for q in doc["questions"] if q["id"] == "write-dry-run")
    assert "apply_canvass_response" in dry["tools_offered"]
    assert dry["write"]["blocked"] and dry["write"]["attempted"] and not dry["write"]["sent"]
    body = dry["write"]["dry_run"]["would_send"]["body"]
    assert body["responses"][0]["surveyQuestionId"] == 5201
    # Nothing went to VAN: no POST among the raw exchanges.
    assert not [r for c in dry["tool_calls"] for r in c["raw"] if r["method"] == "POST"]


def test_free_text_masking_is_marked_on_both_sides(doc):
    q = next(q for q in doc["questions"] if q["id"] == "tomorrow-shift")
    events = q["tool_calls"][0]
    paths = {f["path"] for f in events["masked_fields"]}
    assert any(p.startswith("raw[0].body.items") and p.endswith("description") for p in paths)
    assert any(p.startswith("masked.events") and p.endswith("description") for p in paths)


def test_find_masked_fields_names_policy():
    raw = [{"body": {"firstName": "Maria", "lastName": "Lopez", "emails": [{"email": "m@example.org"}]}}]
    found = find_masked_fields(raw, {"name": "Maria L."}, "initial")
    assert {"path": "raw[0].body.lastName", "kind": "name", "action": "shortened to initial"} in found
    assert {"path": "raw[0].body.emails", "kind": "email", "action": "removed"} in found
    assert not [f for f in find_masked_fields(raw, {}, "full") if f["kind"] == "name"]


async def test_record_function_writes_the_file(tmp_path):
    scripted = ScriptedRecorder()
    out = tmp_path / "r.json"
    await record(scripted, SCRIPTED_INFO, "none", PROMPTS[:1], out, scripted.use)
    assert json.loads(out.read_text())["questions"][0]["id"] == PROMPTS[0]["id"]
