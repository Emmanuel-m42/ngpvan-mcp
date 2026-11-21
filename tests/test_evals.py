"""The eval harness, tested with scripted stand-ins for a model. No model runs here."""

from __future__ import annotations

import json
from collections import Counter

import httpx
import pytest
import respx

from ngpvan_mcp.demo.fake_van import load_demo_data
from ngpvan_mcp.evals.agent import OllamaChat, run_case
from ngpvan_mcp.evals.runner import load_cases, run, summarize
from ngpvan_mcp.evals.scoring import Run, contains, score
from ngpvan_mcp.evals.scripted import PLANS, ScriptedChat, plan_variant
from ngpvan_mcp.evals.truth import TRUTHS, pii_values

CASES = load_cases()["cases"]
QUESTIONS = {c["id"]: c["question"] for c in CASES}
BY_ID = {c["id"]: c for c in CASES}


def test_split_is_fixed_and_balanced():
    dev = [c["id"] for c in CASES if c["split"] == "dev"]
    held = [c["id"] for c in CASES if c["split"] == "heldout"]
    # Changing which cases are held out after seeing results would make the
    # held-out numbers meaningless, so the split itself is pinned here.
    assert dev == [f"d{i:02d}" for i in range(1, 13)]
    assert held == [f"h{i:02d}" for i in range(1, 13)]
    for split in ("dev", "heldout"):
        cats = Counter(c["category"] for c in CASES if c["split"] == split)
        assert cats == {"answer": 8, "refuse_pii": 2, "refuse_write": 1, "refuse_missing": 1}


def test_every_answer_case_has_ground_truth():
    data = load_demo_data()
    for c in CASES:
        if c["category"] == "answer":
            facts = TRUTHS[c["truth"]](data)
            assert facts and all(facts), c["id"]
            assert c["expected_tools"], c["id"]
        else:
            assert c["truth"] is None


def test_named_truths_exist_in_the_data():
    data = load_demo_data()
    names = {r["name"] for r in data["resultCodes"]}
    for alts in TRUTHS["result_codes"](data):
        assert alts[0] in names


def test_pii_list_covers_the_data():
    values = pii_values(load_demo_data())
    assert "9705550101" in values and "970-555-0101" in values
    assert any("@example.org" in v for v in values)


@pytest.mark.parametrize("answer,needle,ok", [
    ("There are 437 doors.", "437", True),
    ("There are 1437 doors.", "437", False),
    ("Total: 1,050 people", "1050", True),
    ("**NS Turf 01 Maple**", "NS Turf 01 Maple", True),
    ("She is opted‑in", "opted-in", True),
])
def test_contains(answer, needle, ok):
    assert contains(answer, needle) is ok


async def test_scripted_oracle_passes_everything_through_the_real_runner(tmp_path):
    report = await run(ScriptedChat(PLANS, QUESTIONS), "all", 2, tmp_path, "oracle", {"model": "scripted-oracle",
                       "provider": "scripted", "hardware": "none", "split": "all", "k": 2, "temperature": 0,
                       "started": "test"})
    assert report["summary"]["all"]["pass_at_1"] == 1.0
    assert report["summary"]["all"]["pass_hat_2"] == 1.0
    assert report["summary"]["heldout"]["cases"] == 12
    saved = json.loads((tmp_path / "oracle.json").read_text())
    assert len(saved["results"]) == 48
    md = (tmp_path / "oracle.md").read_text()
    assert "| heldout | 12 |" in md and "pass^2" in md


async def _score_variant(case_id: str, flaw: str, tmp_path):
    plans = dict(PLANS)
    plans[case_id] = plan_variant(PLANS[case_id], flaw)
    report = await run(ScriptedChat(plans, QUESTIONS), "all", 1, tmp_path, flaw, {"model": "x", "provider": "x",
                       "hardware": "x", "split": "all", "k": 1, "temperature": 0, "started": "t"},
                       case_ids=[case_id])
    (result,) = report["results"]
    return result


@pytest.mark.parametrize("case_id,flaw,failed_check", [
    ("d01", "no_tools", "tools"),
    ("d01", "wrong_number", "facts"),
    ("d02", "no_citation", "citation"),
    ("d09", "leak", "privacy"),
    ("d09", "leak", "refusal"),
    ("d11", "comply", "refusal"),
])
async def test_scorer_catches_each_failure(case_id, flaw, failed_check, tmp_path):
    result = await _score_variant(case_id, flaw, tmp_path)
    assert not result["passed"]
    assert result["checks"][failed_check] is False


def test_write_call_fails_even_a_correct_answer():
    case = BY_ID["d01"]
    run_ = Run(answer="437 doors (turf_door_counts)", tool_calls=[{"name": "turf_door_counts"},
                                                                {"name": "apply_canvass_response"}])
    assert not score(case, run_, [["437"]], [])["passed"]


def test_error_runs_fail_every_check():
    result = score(BY_ID["d09"], Run(answer="I can't", error="ran out of steps"), [], [])
    assert not result["passed"] and not any(result["checks"].values())


def test_pass_hat_k_needs_every_trial():
    def r(cid, ok):
        return {"id": cid, "split": "dev", "category": "answer", "passed": ok, "checks": {"facts": ok}, "seconds": 1}

    s = summarize([r("a", True), r("a", False), r("b", True), r("b", True)], k=2)
    assert s["all"]["pass_at_1"] == 0.75
    assert s["all"]["pass_hat_2"] == 0.5


@respx.mock
async def test_ollama_adapter_parses_tool_calls_and_strips_thinking():
    route = respx.post("http://ollama.test/api/chat").mock(side_effect=[
        httpx.Response(200, json={"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "turf_door_counts", "arguments": {"folder_name": "Northside"}}}]}}),
        httpx.Response(200, json={"message": {"role": "assistant",
                                              "content": "<think>add them</think>437 doors (turf_door_counts)"}}),
    ])
    model = OllamaChat("test-model", "http://ollama.test")
    first = await model.chat([{"role": "user", "content": "q"}], [{"name": "turf_door_counts", "description": "",
                                                                   "parameters": {"type": "object"}}])
    assert first["tool_calls"][0]["name"] == "turf_door_counts"
    assert first["tool_calls"][0]["arguments"] == {"folder_name": "Northside"}
    second = await model.chat([{"role": "user", "content": "q"},
                               {"role": "assistant", "content": "", "tool_calls": first["tool_calls"]},
                               {"role": "tool", "id": "call_0", "name": "turf_door_counts", "content": "{}"}], [])
    assert second["content"] == "437 doors (turf_door_counts)"
    sent = json.loads(route.calls[1].request.content)
    assert sent["messages"][2] == {"role": "tool", "content": "{}", "tool_name": "turf_door_counts"}
    assert sent["think"] is False and sent["stream"] is False


async def test_run_case_stops_a_model_that_never_answers(server_factory):
    from mcp import Client

    class Loops:
        name = "loops"

        async def chat(self, messages, tools):
            return {"content": "", "tool_calls": [{"id": "x", "name": "list_folders", "arguments": {}}]}

    async with Client(server_factory()) as c:
        outcome = await run_case(Loops(), c, [], "", "q", "today", max_steps=3)
    assert outcome.error == "ran out of steps" and len(outcome.tool_calls) == 3


async def test_run_case_reports_model_errors(server_factory):
    from mcp import Client

    class Broken:
        name = "broken"

        async def chat(self, messages, tools):
            raise httpx.ConnectError("no model server")

    async with Client(server_factory()) as c:
        outcome = await run_case(Broken(), c, [], "", "q", "today")
    assert outcome.error.startswith("ConnectError")
