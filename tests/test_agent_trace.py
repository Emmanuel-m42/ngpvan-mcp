"""An outside agent as the model: the tool CLI, then scoring and recording from its trace. No model runs here."""

from __future__ import annotations

import json

from ngpvan_mcp.evals import record, runner
from ngpvan_mcp.evals.agent_trace import AGENT_LABEL, tool_main


def call(capsys, trace, case, tool, args):
    assert tool_main([tool, json.dumps(args), "--case", case, "--trace", str(trace)]) == 0
    return json.loads(capsys.readouterr().out)


def test_prompt_prints_the_system_prompt_and_tools(capsys):
    assert tool_main(["prompt", "--case", "d01"]) == 0
    out = capsys.readouterr().out
    assert "Today is Sunday, October 18, 2026" in out and "turf_door_counts" in out
    assert "apply_canvass_response" not in out  # writes are off for eval cases
    assert tool_main(["prompt", "--case", "write-dry-run"]) == 0
    assert "apply_canvass_response" in capsys.readouterr().out


def test_cli_returns_the_masked_result_and_traces_it(tmp_path, capsys):
    trace = tmp_path / "t.jsonl"
    people = call(capsys, trace, "d06", "search_people", {"first_name": "Grace", "last_name": "Thornbury"})
    person = call(capsys, trace, "d06", "get_person", {"van_id": people["people"][0]["van_id"]})
    assert "555-01" not in json.dumps(person) and person["contact_on_file"]["sms_opt_in"]
    lines = [json.loads(x) for x in trace.read_text().splitlines()]
    assert [x["tool"] for x in lines] == ["search_people", "get_person"] and lines[0]["case"] == "d06"
    assert tool_main(["get_person", "{}", "--case", "nope", "--trace", str(trace)]) == 2


def make_run(tmp_path, capsys, name, d01_answer):
    trace = tmp_path / f"{name}.jsonl"
    r = call(capsys, trace, "d01", "turf_door_counts", {"folder_name": "Northside"})
    answers = {"d01": d01_answer.format(doors=r["total_doors"]),
               "d09": "I can't share phone numbers; this server doesn't return them."}
    (tmp_path / f"{name}.json").write_text(json.dumps(answers))
    return trace, tmp_path / f"{name}.json"


def test_scoring_from_traces_gives_pass_hat_k(tmp_path, capsys):
    t1, a1 = make_run(tmp_path, capsys, "r1", "The Northside turfs have {doors} doors (turf_door_counts).")
    t2, a2 = make_run(tmp_path, capsys, "r2", "About 999 doors.")
    out = tmp_path / "reports"
    assert runner.main(["--provider", "agent-trace", "--trace", str(t1), "--answers", str(a1), "--trace", str(t2),
                        "--answers", str(a2), "--split", "dev", "--cases", "d01,d09", "--out", str(out),
                        "--label", "x"]) == 0
    report = json.loads((out / "x.json").read_text())
    assert report["meta"]["k"] == 2 and report["meta"]["label"] == AGENT_LABEL
    s = report["summary"]["all"]
    assert s["pass_at_1"] == 0.75 and s["pass_hat_2"] == 0.5
    assert "not the app's own agent loop" in (out / "x.md").read_text()


def test_a_tampered_trace_fails_the_case(tmp_path, capsys):
    trace, answers = make_run(tmp_path, capsys, "r", "The Northside turfs have {doors} doors (turf_door_counts).")
    entry = json.loads(trace.read_text())
    entry["result"] = entry["result"].replace('"doors": 437', '"doors": 500', 1)
    trace.write_text(json.dumps(entry) + "\n")
    out = tmp_path / "reports"
    runner.main(["--provider", "agent-trace", "--trace", str(trace), "--answers", str(answers), "--cases", "d01",
                 "--out", str(out), "--label", "x"])
    result = json.loads((out / "x.json").read_text())["results"][0]
    assert not result["passed"] and "replay mismatch" in result["error"]


def test_missing_answer_fails(tmp_path, capsys):
    trace, answers = make_run(tmp_path, capsys, "r", "{doors} doors (turf_door_counts).")
    out = tmp_path / "reports"
    runner.main(["--provider", "agent-trace", "--trace", str(trace), "--answers", str(answers), "--cases", "d10",
                 "--out", str(out), "--label", "x"])
    result = json.loads((out / "x.json").read_text())["results"][0]
    assert not result["passed"] and "no answer" in result["error"]


def test_recorder_builds_the_demo_from_a_trace(tmp_path, capsys):
    trace = tmp_path / "t.jsonl"
    answers = {}
    for p in record.PROMPTS:
        answers[p["id"]] = "I can't do that here."
    r = call(capsys, trace, "northside-turf-doors", "turf_door_counts", {"folder_name": "Northside"})
    answers["northside-turf-doors"] = f"{r['total_doors']} doors (turf_door_counts)."
    people = call(capsys, trace, "write-dry-run", "search_people", {"first_name": "Maria", "last_name": "Lopez"})
    sq = call(capsys, trace, "write-dry-run", "list_survey_questions", {"name": "Measure 3"})["survey_questions"][0]
    dry = call(capsys, trace, "write-dry-run", "apply_canvass_response", {
        "van_id": people["people"][0]["van_id"], "contact_type_id": 2, "survey_question_id": sq["id"],
        "survey_response_id": sq["responses"][0]["id"], "dry_run": True})
    assert dry["dry_run"] and dry["confirm_token"]
    a = tmp_path / "a.json"
    a.write_text(json.dumps(answers))
    out = tmp_path / "recorded_runs.json"
    assert record.main(["--provider", "agent-trace", "--trace", str(trace), "--answers", str(a), "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["model"]["provider"] == "agent-trace" and doc["model"]["label"] == AGENT_LABEL
    by_id = {q["id"]: q for q in doc["questions"]}
    turf = by_id["northside-turf-doors"]
    assert not turf["error"] and turf["tool_calls"][0]["raw"] and turf["tool_calls"][0]["masked"]["total_doors"]
    write = by_id["write-dry-run"]
    assert not write["error"] and write["write"]["dry_run"] and not write["write"]["sent"]
    assert write["followup"] is None  # it dry-ran in the first turn, so the organizer isn't asked
    assert by_id["write-blocked"]["tool_calls"] == []


def test_follow_up_turn_dry_run_then_send_across_cli_calls(tmp_path, capsys):
    trace = tmp_path / "t.jsonl"
    case = "write-dry-run.followup"
    people = call(capsys, trace, case, "search_people", {"first_name": "Maria", "last_name": "Lopez"})
    sq = call(capsys, trace, case, "list_survey_questions", {"name": "Measure 3"})["survey_questions"][0]
    change = {"van_id": people["people"][0]["van_id"], "contact_type_id": 2, "survey_question_id": sq["id"],
              "survey_response_id": sq["responses"][0]["id"]}
    dry = call(capsys, trace, case, "apply_canvass_response", change | {"dry_run": True})
    sent = call(capsys, trace, case, "apply_canvass_response",
                change | {"dry_run": False, "confirm_token": dry["confirm_token"]})
    assert sent.get("applied")
    answers = {p["id"]: "I can't do that here." for p in record.PROMPTS}
    answers["write-dry-run"] = "Which answer did she give: Strong Yes, Lean Yes...?"
    answers[case] = "Recorded Strong Yes for Maria L. (apply_canvass_response)."
    a = tmp_path / "a.json"
    a.write_text(json.dumps(answers))
    out = tmp_path / "recorded_runs.json"
    assert record.main(["--provider", "agent-trace", "--trace", str(trace), "--answers", str(a), "--out", str(out)]) == 0
    q = next(q for q in json.loads(out.read_text())["questions"] if q["id"] == "write-dry-run")
    assert q["tool_calls"] == [] and not q["followup"]["error"]
    assert [c["name"] for c in q["followup"]["tool_calls"]][-1] == "apply_canvass_response"
    assert q["write"]["sent"]
