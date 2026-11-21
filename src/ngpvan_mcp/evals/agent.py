"""A small tool-calling loop: a model, the MCP server and nothing else.

The model sees the server's instructions and tool list exactly as an MCP
client would present them, calls tools through a real MCP `Client`, and
answers. Two providers:

- `OllamaChat`: any local open model with tool calling, over Ollama's
  /api/chat.
- `AnthropicChat`: Claude through the Anthropic API, or through Vertex AI
  with `vertex=True`. Needs the `anthropic` package.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Protocol

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
    started = time.monotonic()
    try:
        for step in range(1, max_steps + 1):
            reply = await model.chat(messages, tools)
            if not reply["tool_calls"]:
                return Run(answer=reply["content"], tool_calls=calls, seconds=time.monotonic() - started, steps=step)
            messages.append({"role": "assistant", "content": reply["content"], "tool_calls": reply["tool_calls"]})
            for call in reply["tool_calls"]:
                result = await client.call_tool(call["name"], call["arguments"])
                text = result.content[0].text if result.content else ""
                calls.append({"name": call["name"], "arguments": call["arguments"], "is_error": result.is_error})
                messages.append({"role": "tool", "id": call["id"], "name": call["name"], "content": text})
        return Run(answer="", tool_calls=calls, error="ran out of steps", seconds=time.monotonic() - started,
                   steps=max_steps)
    except Exception as exc:
        return Run(answer="", tool_calls=calls, error=f"{type(exc).__name__}: {exc}"[:300],
                   seconds=time.monotonic() - started)
