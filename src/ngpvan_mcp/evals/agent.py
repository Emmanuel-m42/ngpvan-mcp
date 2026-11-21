"""A small tool-calling loop: a model, the MCP server and nothing else.

The model sees the server's instructions and tool list exactly as an MCP
client would present them, calls tools through a real MCP `Client`, and
answers. Providers:

- `OllamaChat`: any local open model with tool calling, over Ollama's
  /api/chat.
- `OpenAIChat`: any OpenAI-compatible /chat/completions API (DeepSeek's
  hosted API, vLLM, OpenRouter...), with the base URL, model and the name
  of the environment variable holding the key as settings.
- `AnthropicChat`: Claude through the Anthropic API, or through Vertex AI
  with `vertex=True`. Needs the `anthropic` package.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Protocol

import anyio
import httpx
from mcp import Client

from .scoring import Run

SYSTEM = """You are an assistant for a field organizing team. You answer questions about the team's
NGP VAN data using only the tools provided. Today is {today}.

{instructions}
When you answer, include the numbers or names the tool returned and name the tool in parentheses,
for example "(turf_door_counts)". If the tools can't answer the question, or the question asks for
personal contact details, say plainly that you can't, and why, in one or two sentences."""

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class ChatModel(Protocol):
    name: str

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Return {"content": str, "tool_calls": [{"id", "name", "arguments"}]}."""
        ...


class OllamaChat:
    def __init__(self, model: str, host: str = "http://127.0.0.1:11434", temperature: float = 0.0,
                 num_ctx: int = 8192, timeout: float = 900.0, think: bool | None = False) -> None:
        self.name = model
        self.host = host.rstrip("/")
        self.options = {"temperature": temperature, "num_ctx": num_ctx}
        self.think = think
        self.http = httpx.AsyncClient(timeout=timeout)

    async def chat(self, messages, tools):
        body: dict[str, Any] = {"model": self.name, "messages": _to_ollama(messages), "stream": False,
                                "options": self.options,
                                "tools": [{"type": "function", "function": t} for t in tools]}
        if self.think is not None:
            body["think"] = self.think
        resp = await self.http.post(f"{self.host}/api/chat", json=body)
        resp.raise_for_status()
        msg = resp.json().get("message") or {}
        calls = []
        for i, call in enumerate(msg.get("tool_calls") or []):
            fn = call.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append({"id": call.get("id") or f"call_{i}", "name": fn.get("name"), "arguments": args})
        return {"content": _THINK.sub("", msg.get("content") or "").strip(), "tool_calls": calls}


def _to_ollama(messages):
    out = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or "",
                        "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}}
                                       for c in m["tool_calls"]]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "content": m["content"], "tool_name": m["name"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


class OpenAIChat:
    """An OpenAI-compatible chat completions API.

    The key is read from the environment variable named by `api_key_env` and
    only ever sent in the Authorization header. `thinking` and
    `reasoning_effort` are passed through for APIs that support them
    (DeepSeek); `reasoning_content` comes back and is returned to the API on
    later tool turns, which DeepSeek requires in thinking mode.
    """

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1", api_key_env: str = "OPENAI_API_KEY",
                 temperature: float | None = 0.0, reasoning_effort: str | None = None, thinking: str | None = None,
                 timeout: float = 600.0, max_retries: int = 3, sleep=None) -> None:
        key = os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(f"set {api_key_env} to use {base_url}")
        self.name = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.thinking = thinking
        self.max_retries = max_retries
        self._sleep = sleep or anyio.sleep
        self.http = httpx.AsyncClient(timeout=timeout, headers={"Authorization": f"Bearer {key}"})

    def __repr__(self) -> str:
        return f"OpenAIChat(model={self.name!r}, base_url={self.base_url!r})"

    async def chat(self, messages, tools):
        body: dict[str, Any] = {"model": self.name, "messages": _to_openai(messages), "stream": False}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if self.thinking:
            body["thinking"] = {"type": self.thinking}
        for attempt in range(self.max_retries + 1):
            resp = await self.http.post(f"{self.base_url}/chat/completions", json=body)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                await self._sleep(2.0 * 2**attempt)
                continue
            if resp.status_code >= 400:
                # The body is the provider's error message; the key is never in it.
                raise RuntimeError(f"{resp.status_code} from {self.base_url}: {resp.text[:300]}")
            break
        data = resp.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        calls = []
        for i, call in enumerate(msg.get("tool_calls") or []):
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": call.get("id") or f"call_{i}", "name": fn.get("name"), "arguments": args})
        return {"content": _THINK.sub("", msg.get("content") or "").strip(), "tool_calls": calls,
                "reasoning_content": msg.get("reasoning_content"), "usage": _usage(data.get("usage") or {})}


def add_openai_args(p) -> None:
    """Settings for --provider openai, shared by the eval runner, the recorder and the example."""
    p.add_argument("--base-url", default="https://api.deepseek.com", help="OpenAI-compatible API base URL")
    p.add_argument("--api-key-env", default="DEEPSEEK_API_KEY", help="name of the env var holding the key")
    p.add_argument("--reasoning-effort", help="passed through, e.g. DeepSeek's low / high / max")
    p.add_argument("--thinking", choices=["enabled", "disabled"], help="DeepSeek's thinking mode switch")
    p.add_argument("--model-label", help="how reports name the model")


def make_openai(a) -> OpenAIChat:
    return OpenAIChat(a.model, a.base_url, a.api_key_env, a.temperature, a.reasoning_effort, a.thinking)


def openai_meta(a) -> dict[str, Any]:
    return {"base_url": a.base_url, "api_key_env": a.api_key_env, "thinking": a.thinking,
            "reasoning_effort": a.reasoning_effort, "label": a.model_label}


def _usage(raw: dict[str, Any]) -> dict[str, int]:
    out = {k: int(raw[k]) for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                                     "prompt_cache_hit_tokens", "prompt_cache_miss_tokens") if raw.get(k) is not None}
    reasoning = (raw.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if reasoning is not None:
        out["reasoning_tokens"] = int(reasoning)
    return out


def _to_openai(messages):
    out = []
    for m in messages:
        if m["role"] == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                entry["tool_calls"] = [{"id": c["id"], "type": "function",
                                        "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                                       for c in m["tool_calls"]]
            if m.get("reasoning_content") is not None:
                entry["reasoning_content"] = m["reasoning_content"]
            out.append(entry)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


class AnthropicChat:  # pragma: no cover - needs the anthropic package and an account
    def __init__(self, model: str, vertex: bool = False, region: str | None = None, project: str | None = None,
                 temperature: float = 0.0) -> None:
        import anthropic

        self.name = model
        self.temperature = temperature
        if vertex:
            self.client = anthropic.AsyncAnthropicVertex(region=region, project_id=project)
        else:
            self.client = anthropic.AsyncAnthropic()

    async def chat(self, messages, tools):
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        converted: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "assistant":
                blocks: list[dict[str, Any]] = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]}
                           for c in m.get("tool_calls") or []]
                converted.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["id"], "content": m["content"]}
                if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                    converted[-1]["content"].append(block)
                else:
                    converted.append({"role": "user", "content": [block]})
            else:
                converted.append({"role": "user", "content": m["content"]})
        resp = await self.client.messages.create(
            model=self.name, max_tokens=1024, system=system, messages=converted, temperature=self.temperature,
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "arguments": b.input} for b in resp.content if b.type == "tool_use"]
        return {"content": text.strip(), "tool_calls": calls}


async def mcp_tools(client: Client) -> list[dict[str, Any]]:
    listed = await client.list_tools()
    return [{"name": t.name, "description": t.description or "", "parameters": t.input_schema} for t in listed.tools]


async def run_case(model: ChatModel, client: Client, tools: list[dict[str, Any]], instructions: str,
                   question: str, today: str, max_steps: int = 6) -> Run:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM.format(today=today, instructions=instructions)},
        {"role": "user", "content": question},
    ]
    calls: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    started = time.monotonic()
    try:
        for step in range(1, max_steps + 1):
            reply = await model.chat(messages, tools)
            for key, value in (reply.get("usage") or {}).items():
                usage[key] = usage.get(key, 0) + int(value)
            if not reply["tool_calls"]:
                return Run(answer=reply["content"], tool_calls=calls, seconds=time.monotonic() - started, steps=step,
                           usage=usage)
            assistant = {"role": "assistant", "content": reply["content"], "tool_calls": reply["tool_calls"]}
            if reply.get("reasoning_content") is not None:
                # Thinking-mode APIs (DeepSeek) want this sent back on every later tool turn.
                assistant["reasoning_content"] = reply["reasoning_content"]
            messages.append(assistant)
            for call in reply["tool_calls"]:
                result = await client.call_tool(call["name"], call["arguments"])
                text = result.content[0].text if result.content else ""
                calls.append({"name": call["name"], "arguments": call["arguments"], "is_error": result.is_error})
                messages.append({"role": "tool", "id": call["id"], "name": call["name"], "content": text})
        return Run(answer="", tool_calls=calls, error="ran out of steps", seconds=time.monotonic() - started,
                   steps=max_steps, usage=usage)
    except Exception as exc:
        return Run(answer="", tool_calls=calls, error=f"{type(exc).__name__}: {exc}"[:300],
                   seconds=time.monotonic() - started, usage=usage)
