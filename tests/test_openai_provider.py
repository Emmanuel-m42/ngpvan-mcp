"""The OpenAI-compatible provider against a mocked /chat/completions. No real API calls."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp import Client

from ngpvan_mcp.evals.agent import OpenAIChat, mcp_tools, run_case
from ngpvan_mcp.evals.record import main as record_main

BASE = "https://llm.test/v1"
KEY = "sk-test-not-a-real-key-123456"


@pytest.fixture
def key_env(monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", KEY)


async def nosleep(_):
    return None


def reply(content="", tool_calls=None, reasoning=None, usage=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(tool_calls)]
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return httpx.Response(200, json={"choices": [{"message": msg}],
                                     "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20,
                                                        "total_tokens": 120, "prompt_cache_hit_tokens": 64,
                                                        "completion_tokens_details": {"reasoning_tokens": 12}}})


def test_missing_key_names_the_variable_not_a_value(monkeypatch):
    monkeypatch.delenv("TEST_LLM_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TEST_LLM_KEY"):
        OpenAIChat("m", BASE, "TEST_LLM_KEY")


def test_repr_hides_the_key(key_env):
    assert KEY not in repr(OpenAIChat("m", BASE, "TEST_LLM_KEY"))


@respx.mock
async def test_request_shape_and_reasoning_round_trip(key_env, server_factory):
    route = respx.post(f"{BASE}/chat/completions").mock(side_effect=[
        reply(tool_calls=[("turf_door_counts", {"folder_name": "Northside"})], reasoning="look up the folder"),
        reply(content="437 doors (turf_door_counts)", reasoning="done"),
    ])
    model = OpenAIChat("deepseek-v4-pro", BASE, "TEST_LLM_KEY", temperature=0.0, reasoning_effort="high",
                       thinking="enabled")
    async with Client(server_factory()) as c:
        tools = await mcp_tools(c)
        run = await run_case(model, c, tools, "", "How many doors in Northside?", "today")
    assert run.answer == "437 doors (turf_door_counts)"
    assert [t["name"] for t in run.tool_calls] == ["turf_door_counts"]
    first = json.loads(route.calls[0].request.content)
    assert route.calls[0].request.headers["authorization"] == f"Bearer {KEY}"
    assert first["model"] == "deepseek-v4-pro" and first["temperature"] == 0.0
    assert first["reasoning_effort"] == "high" and first["thinking"] == {"type": "enabled"}
    assert first["tools"][0]["type"] == "function" and "parameters" in first["tools"][0]["function"]
    second = json.loads(route.calls[1].request.content)
    assistant, tool = second["messages"][2], second["messages"][3]
    # DeepSeek's thinking mode needs the reasoning sent back on tool turns.
    assert assistant["reasoning_content"] == "look up the folder"
    assert assistant["tool_calls"][0]["function"] == {"name": "turf_door_counts",
                                                      "arguments": json.dumps({"folder_name": "Northside"})}
    assert tool == {"role": "tool", "tool_call_id": assistant["tool_calls"][0]["id"], "content": tool["content"]}
    assert "437" in tool["content"]
    assert run.usage == {"prompt_tokens": 200, "completion_tokens": 40, "total_tokens": 240,
                         "prompt_cache_hit_tokens": 128, "reasoning_tokens": 24}


@respx.mock
async def test_retries_rate_limits_then_raises_cleanly(key_env):
    route = respx.post(f"{BASE}/chat/completions").mock(side_effect=[
        httpx.Response(429, json={"error": "slow down"}), reply(content="ok")])
    model = OpenAIChat("m", BASE, "TEST_LLM_KEY", sleep=nosleep)
    assert (await model.chat([{"role": "user", "content": "hi"}], []))["content"] == "ok"
    assert route.call_count == 2
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(RuntimeError) as err:
        await model.chat([{"role": "user", "content": "hi"}], [])
    assert "401" in str(err.value) and KEY not in str(err.value)


@respx.mock
def test_recorder_with_openai_provider(key_env, tmp_path):
    def answer(request):
        return reply(content="I can't help with that here.")

    respx.post(f"{BASE}/chat/completions").mock(side_effect=answer)
    out = tmp_path / "rec.json"
    assert record_main(["--provider", "openai", "--model", "deepseek-v4-pro", "--base-url", BASE,
                        "--api-key-env", "TEST_LLM_KEY", "--thinking", "enabled", "--reasoning-effort", "high",
                        "--model-label", "Test label", "--hardware", "hosted API", "--out", str(out)]) == 0
    text = out.read_text()
    doc = json.loads(text)
    assert doc["model"]["label"] == "Test label" and doc["model"]["thinking"] == "enabled"
    # One model call per question, plus the organizer's follow-up on the write question,
    # because the mocked model stopped without trying the write.
    assert doc["usage_total"]["total_tokens"] == 120 * (len(doc["questions"]) + 1)
    dry = next(q for q in doc["questions"] if q["id"] == "write-dry-run")
    assert dry["followup"]["question"] == "Strong Yes. I talked to her at the door."
    assert dry["followup"]["answer"] == "I can't help with that here."
    assert all(q["followup"] is None for q in doc["questions"] if q["id"] != "write-dry-run")
    assert KEY not in text
